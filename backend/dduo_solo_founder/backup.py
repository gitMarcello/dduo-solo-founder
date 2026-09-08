from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from dduo_solo_founder.project_secrets import SECRET_ENV_KEYS

MAGIC = b"DDUOBACKUP\x00\x01"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
FORMAT_NAME = "dduo-solo-founder-backup"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
ARCHIVE_SUFFIX = ".dduobackup"
V1_REQUIRED_FILES = {"manifest.json", "checksums.sha256", "postgres.dump", "project.toml"}
V1_OPTIONAL_FILES = {"qdrant.snapshot"}
V2_REQUIRED_FILES = {
    "manifest.json",
    "checksums.sha256",
    "postgres.dump",
    "project.toml",
    "runtime-settings.json",
    "secrets/dduo.env",
}
# Kept as compatibility aliases for callers and tests that inspect the v1 contract.
REQUIRED_FILES = V1_REQUIRED_FILES
OPTIONAL_FILES = V1_OPTIONAL_FILES
_SAFE_MEMBER = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)$")
_BACKUP_TIMESTAMP = re.compile(r"-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.dduobackup$")
MAX_ARCHIVE_MEMBERS = 1_024
PRIVATE_PREFIXES = ("secrets/", "binding/", "host-state/")
V2_FIXED_OPTIONAL_FILES = frozenset(
    {
        "secrets/codex/auth.json",
        "secrets/claude/.credentials.json",
        "binding/project.toml",
        "binding/remote-credential.token",
        "host-state/mcp-observability.json",
        "qdrant/memory.snapshot",
        "qdrant/tasks.snapshot",
        "history/backup-records.json",
    }
)
# Accepted only while reading backups created before machine-global updater
# state was removed from the project recovery contract. New archives cannot
# export these files and restore deliberately ignores them.
V2_LEGACY_IGNORED_FILES = frozenset(
    {
        "host-state/update-trust.json",
        "host-state/update-queue.json",
        "host-state/client-current.json",
        "host-state/client-previous.json",
    }
)


class BackupError(RuntimeError):
    """Raised when an encrypted backup is malformed, unverifiable, or cannot be decrypted."""


@dataclass(frozen=True)
class ArchiveVerification:
    manifest: dict
    extracted_to: Path | None = None


@dataclass(frozen=True)
class RetentionResult:
    retained: tuple[Path, ...]
    deleted: tuple[Path, ...]


def generate_recovery_key() -> str:
    """Return a printable 256-bit key suitable for a password manager."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


def decode_recovery_key(value: str) -> bytes:
    """Decode and strictly validate a base64url-encoded 256-bit recovery key."""
    normalized = value.strip()
    try:
        decoded = base64.b64decode(
            normalized + "=" * (-len(normalized) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as exc:
        raise BackupError("invalid recovery key encoding") from exc
    if len(decoded) != 32:
        raise BackupError("recovery key must contain exactly 256 bits")
    return decoded


def _atomic_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def encrypt_file(source: Path, target: Path, recovery_key: str) -> None:
    """Encrypt one file with streaming AES-256-GCM and atomically publish it."""
    key = decode_recovery_key(recovery_key)
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    temporary = _atomic_path(target)
    try:
        with source.open("rb") as input_file, temporary.open("wb") as output_file:
            output_file.write(MAGIC)
            output_file.write(nonce)
            while chunk := input_file.read(CHUNK_SIZE):
                output_file.write(encryptor.update(chunk))
            output_file.write(encryptor.finalize())
            output_file.write(encryptor.tag)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def decrypt_file(source: Path, target: Path, recovery_key: str) -> None:
    """Decrypt an archive without exposing a partially authenticated output file."""
    key = decode_recovery_key(recovery_key)
    minimum_size = len(MAGIC) + NONCE_SIZE + TAG_SIZE
    if not source.is_file() or source.stat().st_size <= minimum_size:
        raise BackupError("backup is missing or truncated")
    with source.open("rb") as input_file:
        if input_file.read(len(MAGIC)) != MAGIC:
            raise BackupError("unsupported backup format")
        nonce = input_file.read(NONCE_SIZE)
        ciphertext_size = source.stat().st_size - minimum_size
        input_file.seek(-TAG_SIZE, os.SEEK_END)
        tag = input_file.read(TAG_SIZE)
        input_file.seek(len(MAGIC) + NONCE_SIZE)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        temporary = _atomic_path(target)
        remaining = ciphertext_size
        try:
            with temporary.open("wb") as output_file:
                while remaining:
                    chunk = input_file.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupError("backup ciphertext is truncated")
                    remaining -= len(chunk)
                    output_file.write(decryptor.update(chunk))
                output_file.write(decryptor.finalize())
            os.replace(temporary, target)
        except InvalidTag as exc:
            raise BackupError("recovery key is wrong or the backup was modified") from exc
        finally:
            temporary.unlink(missing_ok=True)


def _file_digest(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "project"


def backup_filename(project_name: str, backup_id: str, created_at: datetime) -> str:
    """Build a sortable, project-readable filename without exposing archive contents."""
    timestamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"dduo-solo-founder-{_safe_slug(project_name)}-{timestamp}-{backup_id[:8]}{ARCHIVE_SUFFIX}"
    )


def create_archive(
    output: Path,
    recovery_key: str,
    *,
    app_version: str,
    project_id: str,
    project_name: str,
    postgres_dump: Path,
    project_config: Path,
    qdrant_snapshot: Path | None = None,
    qdrant_collection: str | None = None,
    task_qdrant_snapshot: Path | None = None,
    task_qdrant_collection: str | None = None,
    runtime_settings: Path | None = None,
    supplemental_files: Mapping[str, Path] | None = None,
    backup_history: Path | None = None,
    supplement_metadata: dict | None = None,
    consistency: dict | None = None,
    warnings: list[str] | None = None,
    created_at: datetime | None = None,
    backup_id: str | None = None,
) -> dict:
    """Build one authenticated portable archive.

    Passing only the original arguments deliberately emits schema v1 so old
    integrations and fixtures remain readable. Runtime settings opt into the
    full-recovery v2 contract; callers must then also provide the allowlisted
    dDuo secret envelope through ``supplemental_files``.
    """
    created_at = created_at or datetime.now(timezone.utc)
    backup_id = backup_id or str(uuid.uuid4())
    payloads = {"postgres.dump": postgres_dump, "project.toml": project_config}
    schema_version = 2 if runtime_settings is not None else 1
    if schema_version == 1:
        if task_qdrant_snapshot or task_qdrant_collection or supplemental_files or backup_history:
            raise BackupError("full recovery inputs require runtime-settings.json")
        if qdrant_snapshot:
            if not qdrant_collection:
                raise BackupError("Qdrant snapshot requires its collection identity")
            payloads["qdrant.snapshot"] = qdrant_snapshot
    else:
        payloads["runtime-settings.json"] = runtime_settings
        supplemental_files = dict(supplemental_files or {})
        if "secrets/dduo.env" not in supplemental_files:
            raise BackupError("full recovery backup requires secrets/dduo.env")
        if not isinstance(supplement_metadata, dict):
            raise BackupError("full recovery backup requires credential metadata")
        for name, path in supplemental_files.items():
            _validate_v2_payload_name(name)
            if name in payloads:
                raise BackupError(f"duplicate backup input: {name}")
            payloads[name] = path
        if backup_history:
            payloads["history/backup-records.json"] = backup_history
        if qdrant_snapshot:
            if not qdrant_collection:
                raise BackupError("memory Qdrant snapshot requires its collection identity")
            payloads["qdrant/memory.snapshot"] = qdrant_snapshot
        if task_qdrant_snapshot:
            if not task_qdrant_collection:
                raise BackupError("task Qdrant snapshot requires its collection identity")
            payloads["qdrant/tasks.snapshot"] = task_qdrant_snapshot
    for name, path in payloads.items():
        if not path.is_file():
            raise BackupError(f"required backup input is missing: {name}")

    with tempfile.TemporaryDirectory(prefix="dduo-backup-build-") as temporary_dir:
        temporary = Path(temporary_dir)
        metadata = {}
        for name, path in payloads.items():
            digest, size = _file_digest(path)
            metadata[name] = {"sha256": digest, "size": size}
        manifest = {
            "format": FORMAT_NAME,
            "schema_version": schema_version,
            "memory_engine_version": 2,
            "app_version": app_version,
            "backup_id": backup_id,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "project": {"id": project_id, "name": project_name},
            "database": {"engine": "postgresql", "dump_format": "custom"},
            "files": metadata,
            "warnings": warnings or [],
        }
        if schema_version == 1:
            manifest["qdrant"] = {
                "included": qdrant_snapshot is not None,
                "collection": qdrant_collection if qdrant_snapshot else None,
                "fallback": "reindex_from_postgresql",
            }
        else:
            memory_included = qdrant_snapshot is not None
            tasks_included = task_qdrant_snapshot is not None
            manifest.update(
                {
                    "recovery_contract": "full-project-v2",
                    "qdrant": {
                        # Compatibility fields remain the memory projection.
                        "included": memory_included,
                        "collection": qdrant_collection if memory_included else None,
                        "fallback": "reindex_from_postgresql",
                        "collections": {
                            "memory": {
                                "included": memory_included,
                                "collection": qdrant_collection if memory_included else None,
                                "file": "qdrant/memory.snapshot" if memory_included else None,
                            },
                            "tasks": {
                                "included": tasks_included,
                                "collection": (
                                    task_qdrant_collection if tasks_included else None
                                ),
                                "file": "qdrant/tasks.snapshot" if tasks_included else None,
                            },
                        },
                    },
                    "credentials": {
                        "dduo_secrets": {"included": True},
                        **(supplement_metadata or {}),
                    },
                    "consistency": consistency
                    or {
                        "authority": "postgresql",
                        "qdrant": "derived_reconcilable_projection",
                    },
                    "history": {
                        "included": backup_history is not None,
                        "file": (
                            "history/backup-records.json" if backup_history is not None else None
                        ),
                    },
                }
            )
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest_digest, _ = _file_digest(manifest_path)
        checksums = {
            **{name: item["sha256"] for name, item in metadata.items()},
            "manifest.json": manifest_digest,
        }
        checksum_path = temporary / "checksums.sha256"
        checksum_path.write_text(
            "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items()))
        )
        inner_zip = temporary / "payload.zip"
        with zipfile.ZipFile(inner_zip, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.write(manifest_path, "manifest.json")
            archive.write(checksum_path, "checksums.sha256")
            for name, path in sorted(payloads.items()):
                archive.write(path, name)
        encrypt_file(inner_zip, output, recovery_key)
    return manifest


def _validate_v2_payload_name(name: str) -> None:
    """Accept only the project-scoped host artifacts defined by format v2."""
    if not _is_safe_member_name(name):
        raise BackupError(f"unsafe backup input name: {name}")
    allowed = (
        name == "secrets/dduo.env"
        or name in V2_FIXED_OPTIONAL_FILES
        or (name.startswith("host-state/hooks/") and name.endswith(".json"))
    )
    if not allowed:
        raise BackupError(f"unexpected full recovery input: {name}")


def _is_safe_member_name(name: str) -> bool:
    if not isinstance(name, str) or not _SAFE_MEMBER.fullmatch(name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _read_checksums(value: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in value.splitlines():
        match = _CHECKSUM_LINE.fullmatch(line)
        if not match or match.group(2) in checksums:
            raise BackupError("checksums.sha256 is malformed")
        checksums[match.group(2)] = match.group(1)
    return checksums


def _validate_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise BackupError("backup contains too many files")
    names = [item.filename for item in members]
    if len(names) != len(set(names)):
        raise BackupError("backup contains duplicate files")
    if not {"manifest.json", "checksums.sha256"}.issubset(names):
        raise BackupError("backup payload has missing or unexpected files")
    for item in members:
        mode = item.external_attr >> 16
        if (
            item.is_dir()
            or not _is_safe_member_name(item.filename)
            or stat.S_ISLNK(mode)
            or item.compress_type != zipfile.ZIP_STORED
        ):
            raise BackupError("backup payload contains an unsafe path")
    return {item.filename: item for item in members}


def _validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise BackupError("backup manifest is unsupported")
    project = manifest.get("project")
    database = manifest.get("database")
    qdrant = manifest.get("qdrant")
    files = manifest.get("files")
    if (
        manifest.get("format") != FORMAT_NAME
        or manifest.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS
        or not isinstance(manifest.get("app_version"), str)
        or not isinstance(manifest.get("backup_id"), str)
        or not isinstance(project, dict)
        or not isinstance(database, dict)
        or not isinstance(qdrant, dict)
        or not isinstance(files, dict)
        or not isinstance(manifest.get("warnings"), list)
    ):
        raise BackupError("backup manifest is unsupported")
    if not all(isinstance(project.get(key), str) and project[key] for key in ("id", "name")):
        raise BackupError("backup manifest project identity is invalid")
    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("backup manifest timestamp is invalid") from exc
    if created_at.tzinfo is None:
        raise BackupError("backup manifest timestamp is invalid")
    if database.get("engine") != "postgresql" or database.get("dump_format") != "custom":
        raise BackupError("backup database format is unsupported")
    if not isinstance(qdrant.get("included"), bool):
        raise BackupError("backup Qdrant metadata is invalid")
    if qdrant["included"] and not isinstance(qdrant.get("collection"), str):
        raise BackupError("backup Qdrant collection identity is invalid")
    if not all(isinstance(item, str) for item in manifest["warnings"]):
        raise BackupError("backup warnings are malformed")
    for name, metadata in files.items():
        if (
            not isinstance(name, str)
            or not _is_safe_member_name(name)
            or not isinstance(metadata, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256", "")))
            or type(metadata.get("size")) is not int
            or metadata["size"] < 0
        ):
            raise BackupError("backup manifest file metadata is invalid")
    if manifest["schema_version"] == 2:
        if manifest.get("recovery_contract") != "full-project-v2":
            raise BackupError("backup recovery contract is unsupported")
        collections = qdrant.get("collections")
        if not isinstance(collections, dict) or set(collections) != {"memory", "tasks"}:
            raise BackupError("backup Qdrant collection inventory is invalid")
        for kind, expected_file in (
            ("memory", "qdrant/memory.snapshot"),
            ("tasks", "qdrant/tasks.snapshot"),
        ):
            item = collections[kind]
            if not isinstance(item, dict) or not isinstance(item.get("included"), bool):
                raise BackupError("backup Qdrant collection inventory is invalid")
            if item["included"]:
                if item.get("file") != expected_file or not isinstance(
                    item.get("collection"), str
                ):
                    raise BackupError("backup Qdrant collection inventory is invalid")
            elif item.get("file") is not None or item.get("collection") is not None:
                raise BackupError("backup Qdrant collection inventory is invalid")
        history = manifest.get("history")
        credentials = manifest.get("credentials")
        consistency = manifest.get("consistency")
        if (
            not isinstance(history, dict)
            or not isinstance(history.get("included"), bool)
            or not isinstance(credentials, dict)
            or not isinstance(consistency, dict)
        ):
            raise BackupError("backup full recovery metadata is invalid")
        codex_auth = credentials.get("codex_auth")
        # v2 archives created before Claude could own sleep have no Claude
        # inventory. Treat that omission as an unavailable optional executor,
        # so old encrypted recovery bundles remain valid.
        claude_auth = credentials.get(
            "claude_auth", {"included": False, "credential_store": "unavailable"}
        )
        dduo_secrets = credentials.get("dduo_secrets")
        if (
            not isinstance(credentials.get("complete"), bool)
            or not isinstance(dduo_secrets, dict)
            or dduo_secrets.get("included") is not True
            or not isinstance(codex_auth, dict)
            or not isinstance(codex_auth.get("included"), bool)
            or codex_auth.get("credential_store") not in {"file", "unavailable"}
            or not isinstance(claude_auth, dict)
            or not isinstance(claude_auth.get("included"), bool)
            or claude_auth.get("credential_store") not in {"file", "unavailable"}
            or (history["included"] and history.get("file") != "history/backup-records.json")
            or (not history["included"] and history.get("file") is not None)
        ):
            raise BackupError("backup full recovery metadata is invalid")
        secret_keys = dduo_secrets.get("keys")
        fingerprints = dduo_secrets.get("fingerprints")
        if secret_keys is not None and (
            not isinstance(secret_keys, list)
            or not all(isinstance(key, str) and key in SECRET_ENV_KEYS for key in secret_keys)
            or len(secret_keys) != len(set(secret_keys))
        ):
            raise BackupError("backup dDuo secret inventory is invalid")
        if fingerprints is not None and (
            not isinstance(fingerprints, dict)
            or set(fingerprints) - {"DDUO_NODE_AUTHORITY_SECRET"}
            or not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in fingerprints.values()
            )
            or any(key not in (secret_keys or []) for key in fingerprints)
        ):
            raise BackupError("backup dDuo secret inventory is invalid")
    return manifest


def _validate_inventory_for_schema(schema_version: int, names: set[str]) -> None:
    if schema_version == 1:
        allowed = V1_REQUIRED_FILES | V1_OPTIONAL_FILES
        if not V1_REQUIRED_FILES.issubset(names) or not names.issubset(allowed):
            raise BackupError("backup payload has missing or unexpected files")
        return
    if not V2_REQUIRED_FILES.issubset(names):
        raise BackupError("backup payload has missing or unexpected files")
    for name in names - V2_REQUIRED_FILES:
        allowed = (
            name in V2_FIXED_OPTIONAL_FILES
            or name in V2_LEGACY_IGNORED_FILES
            or (name.startswith("host-state/hooks/") and name.endswith(".json"))
        )
        if not allowed:
            raise BackupError("backup payload has missing or unexpected files")


def verify_archive(
    archive_path: Path,
    recovery_key: str,
    *,
    extract_to: Path | None = None,
) -> ArchiveVerification:
    """Authenticate, structurally validate, hash, and optionally extract an archive."""
    if extract_to and extract_to.exists() and any(extract_to.iterdir()):
        raise BackupError("backup extraction destination must be empty")
    with tempfile.TemporaryDirectory(prefix="dduo-backup-verify-") as temporary_dir:
        inner_zip = Path(temporary_dir) / "payload.zip"
        decrypt_file(archive_path, inner_zip, recovery_key)
        try:
            archive = zipfile.ZipFile(inner_zip)
        except zipfile.BadZipFile as exc:
            raise BackupError("decrypted backup payload is not a valid ZIP archive") from exc
        with archive:
            members = _validate_members(archive)
            if members["manifest.json"].file_size > 1_000_000:
                raise BackupError("backup manifest is unreasonably large")
            if members["checksums.sha256"].file_size > 1_000_000:
                raise BackupError("backup checksum inventory is unreasonably large")
            try:
                manifest_bytes = archive.read("manifest.json")
                manifest = _validate_manifest(json.loads(manifest_bytes))
                checksums = _read_checksums(archive.read("checksums.sha256").decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError("backup metadata is malformed") from exc
            _validate_inventory_for_schema(manifest["schema_version"], set(members))
            expected_checksum_names = set(members) - {"checksums.sha256"}
            if set(checksums) != expected_checksum_names:
                raise BackupError("backup checksum inventory does not match its payload")
            if sha256(manifest_bytes).hexdigest() != checksums["manifest.json"]:
                raise BackupError("backup manifest checksum failed")
            manifest_files = manifest["files"]
            payload_names = set(members) - {"manifest.json", "checksums.sha256"}
            if set(manifest_files) != payload_names:
                raise BackupError("backup manifest file inventory does not match its payload")
            if manifest["schema_version"] == 1:
                qdrant_inventory_matches = bool(
                    manifest.get("qdrant", {}).get("included")
                ) == ("qdrant.snapshot" in payload_names)
            else:
                collections = manifest["qdrant"]["collections"]
                qdrant_inventory_matches = (
                    collections["memory"]["included"]
                    == ("qdrant/memory.snapshot" in payload_names)
                    and collections["tasks"]["included"]
                    == ("qdrant/tasks.snapshot" in payload_names)
                    and manifest["history"]["included"]
                    == ("history/backup-records.json" in payload_names)
                    and ("secrets/codex/auth.json" in payload_names)
                    == bool(manifest["credentials"].get("codex_auth", {}).get("included"))
                    and ("secrets/claude/.credentials.json" in payload_names)
                    == bool(manifest["credentials"].get("claude_auth", {}).get("included"))
                )
            if not qdrant_inventory_matches:
                raise BackupError("backup Qdrant metadata or recovery metadata is inconsistent")
            for name in sorted(payload_names):
                digest = sha256()
                size = 0
                with archive.open(name) as source:
                    while chunk := source.read(CHUNK_SIZE):
                        digest.update(chunk)
                        size += len(chunk)
                expected = manifest_files.get(name, {})
                if digest.hexdigest() != checksums[name] or digest.hexdigest() != expected.get(
                    "sha256"
                ):
                    raise BackupError(f"backup checksum failed for {name}")
                if size != expected.get("size"):
                    raise BackupError(f"backup size check failed for {name}")
            if extract_to:
                extract_to.mkdir(parents=True, exist_ok=True)
                extract_to.chmod(0o700)
                for name in sorted(payload_names | {"manifest.json", "checksums.sha256"}):
                    target_path = extract_to.joinpath(*PurePosixPath(name).parts)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if any(name.startswith(prefix) for prefix in PRIVATE_PREFIXES):
                        target_path.parent.chmod(0o700)
                    with archive.open(name) as source, target_path.open("wb") as target:
                        shutil.copyfileobj(source, target, CHUNK_SIZE)
                    if any(name.startswith(prefix) for prefix in PRIVATE_PREFIXES):
                        target_path.chmod(0o600)
        return ArchiveVerification(manifest=manifest, extracted_to=extract_to)


def _archive_datetime(path: Path) -> datetime:
    match = _BACKUP_TIMESTAMP.search(path.name)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def select_retained_archives(
    paths: list[Path],
    *,
    daily: int = 7,
    weekly: int = 4,
    monthly: int = 6,
) -> set[Path]:
    """Select one newest archive per retention bucket, always preserving the latest."""
    ordered = sorted(paths, key=_archive_datetime, reverse=True)
    if not ordered:
        return set()
    retained = {ordered[0]}
    tiers = (
        (daily, lambda value: value.date()),
        (weekly, lambda value: value.isocalendar()[:2]),
        (monthly, lambda value: (value.year, value.month)),
    )
    for limit, bucket in tiers:
        if limit <= 0:
            continue
        seen = set()
        for path in ordered:
            key = bucket(_archive_datetime(path))
            if key in seen:
                continue
            seen.add(key)
            retained.add(path)
            if len(seen) >= limit:
                break
    return retained


def prune_archives(
    directory: Path,
    *,
    daily: int = 7,
    weekly: int = 4,
    monthly: int = 6,
    preserve: set[Path] | None = None,
) -> RetentionResult:
    """Apply bucketed retention to archives in one project-specific directory."""
    paths = list(directory.glob(f"dduo-solo-founder-*{ARCHIVE_SUFFIX}"))
    retained = select_retained_archives(paths, daily=daily, weekly=weekly, monthly=monthly)
    retained.update(path for path in (preserve or set()) if path in paths)
    deleted = []
    for path in paths:
        if path not in retained:
            path.unlink()
            deleted.append(path)
    return RetentionResult(
        retained=tuple(sorted(retained)),
        deleted=tuple(sorted(deleted)),
    )
