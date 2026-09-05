from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
import threading
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy.engine import make_url

from dduo_solo_founder.backup import (
    BackupError,
    _validate_v2_payload_name,
    backup_filename,
    create_archive,
    decode_recovery_key,
    prune_archives,
    verify_archive,
)
from dduo_solo_founder.config import Settings, get_settings
from dduo_solo_founder.embeddings import embedding_service
from dduo_solo_founder.project_secrets import SECRET_ENV_KEYS
from dduo_solo_founder.runtime_settings import write_runtime_settings

_backup_lock = threading.Lock()
MAX_SUPPLEMENT_BYTES = 32 * 1024 * 1024
MAX_SUPPLEMENT_FILE_BYTES = 8 * 1024 * 1024
MAX_SUPPLEMENT_FILES = 512
PORTABLE_SECRET_KEYS = SECRET_ENV_KEYS


@dataclass(frozen=True)
class BackupResult:
    archive_name: str
    size_bytes: int
    includes_qdrant: bool
    manifest: dict
    pruned_archives: tuple[str, ...]


class BackupEngine:
    """Create and verify application-level backups from inside one project API container."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        collection_resolver: Callable[[str], str] | None = None,
        task_collection_resolver: Callable[[str], str] | None = None,
    ):
        self.settings = settings or get_settings()
        embeddings = embedding_service()
        self.collection_resolver = collection_resolver or embeddings.collection
        self.task_collection_resolver = task_collection_resolver or embeddings.task_collection

    def _postgres_environment(self) -> dict[str, str]:
        url = make_url(self.settings.database_url)
        if not url.database or not url.username:
            raise BackupError("PostgreSQL connection settings are incomplete")
        environment = os.environ.copy()
        # Never let a caller's ambient libpq credential silently override the
        # project-scoped connection URL.  A passwordless URL must stay
        # passwordless; otherwise a backup can connect as the wrong authority.
        environment.pop("PGPASSWORD", None)
        environment.update(
            {
                "PGHOST": url.host or "localhost",
                "PGPORT": str(url.port or 5432),
                "PGUSER": url.username,
                "PGDATABASE": url.database,
            }
        )
        if url.password:
            environment["PGPASSWORD"] = url.password
        return environment

    @staticmethod
    def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown error").strip()[-1_000:]
            raise BackupError(f"{command[0]} failed: {detail}")

    @staticmethod
    def _run_capture(
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> str:
        result = subprocess.run(
            command,
            env=environment,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown error").strip()[-1_000:]
            raise BackupError(f"{command[0]} failed: {detail}")
        return result.stdout

    def _validate_single_project_database(self, project_id: str) -> None:
        """Fail closed before pg_dump if this isolated stack contains another project."""
        query = """
SELECT COALESCE(json_agg(id::text ORDER BY id), '[]'::json)::text
FROM projects
""".strip()
        try:
            value = self._run_capture(
                ["psql", "--no-psqlrc", "--tuples-only", "--no-align", "--command", query],
                environment=self._postgres_environment(),
            ).strip()
            project_ids = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BackupError("could not validate the project scope of PostgreSQL") from exc
        if project_ids != [project_id]:
            raise BackupError(
                "PostgreSQL backup scope is not exactly the requested isolated project"
            )

    def _dump_postgres(self, output: Path) -> None:
        self._run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--exclude-table-data=backup_records",
                "--file",
                str(output),
            ],
            environment=self._postgres_environment(),
        )

    def _validate_postgres_dump(self, dump: Path) -> None:
        self._run(["pg_restore", "--list", str(dump)])

    def _export_backup_history(self, project_id: str, output: Path) -> str | None:
        """Export completed records without the circular in-flight backup row."""
        query = """
SELECT COALESCE(json_agg(row_to_json(history) ORDER BY history.created_at), '[]'::json)::text
FROM (
  SELECT id, project_id, trigger, status, archive_name, size_bytes,
         includes_qdrant, retained, source_generation, manifest,
         error, created_at, completed_at, verified_at
  FROM backup_records
  WHERE project_id = :'project_id'
    AND status NOT IN ('scheduled', 'running')
) AS history
""".strip()
        try:
            value = self._run_capture(
                [
                    "psql",
                    "--no-psqlrc",
                    "--tuples-only",
                    "--no-align",
                    "--set",
                    "ON_ERROR_STOP=on",
                    "--set",
                    f"project_id={project_id}",
                    "--file=-",
                ],
                environment=self._postgres_environment(),
                input_text=query,
            ).strip()
            parsed = json.loads(value or "[]")
            if not isinstance(parsed, list) or any(
                not isinstance(item, dict) or item.get("project_id") != project_id
                for item in parsed
            ):
                raise ValueError("history query did not return a list")
            output.write_text(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
            output.chmod(0o600)
            return None
        except (BackupError, json.JSONDecodeError, OSError, ValueError) as exc:
            output.unlink(missing_ok=True)
            return f"Portable backup history omitted: {exc}"

    def _write_runtime_settings(self, output: Path, *, app_version: str) -> None:
        write_runtime_settings(output, self.settings, app_version=app_version)

    def _fetch_host_supplement(
        self, project_id: str, output: Path
    ) -> tuple[dict[str, Path], dict, list[str]]:
        """Fetch one bounded, project-filtered host envelope from the local agent."""
        if not self.settings.cli_bridge_token or self.settings.cli_bridge_url.endswith(":0"):
            raise BackupError("full recovery host supplement is unavailable")
        endpoint = f"{self.settings.cli_bridge_url.rstrip('/')}/v1/backup/supplement"
        try:
            with (
                httpx.Client(timeout=20) as client,
                client.stream(
                    "GET",
                    endpoint,
                    params={"project_id": project_id},
                    headers={
                        "Authorization": f"Bearer {self.settings.cli_bridge_token}",
                        "Accept": "application/json",
                    },
                ) as response,
            ):
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type not in {
                    "application/json",
                    "application/vnd.dduo.backup-supplement+json",
                }:
                    raise BackupError("full recovery host supplement has an unsupported type")
                try:
                    declared_size = int(response.headers.get("content-length", "0"))
                except ValueError as exc:
                    raise BackupError("full recovery host supplement size is invalid") from exc
                if declared_size < 0 or declared_size > MAX_SUPPLEMENT_BYTES:
                    raise BackupError("full recovery host supplement is too large")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_SUPPLEMENT_BYTES:
                        raise BackupError("full recovery host supplement is too large")
        except httpx.HTTPError as exc:
            raise BackupError(f"full recovery host supplement failed: {exc}") from exc
        try:
            supplement = json.loads(chunks)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("full recovery host supplement is malformed") from exc
        return self._materialize_host_supplement(supplement, project_id, output)

    def _materialize_host_supplement(
        self, supplement: object, project_id: str, output: Path
    ) -> tuple[dict[str, Path], dict, list[str]]:
        if not isinstance(supplement, dict):
            raise BackupError("full recovery host supplement is malformed")
        files = supplement.get("files")
        warnings = supplement.get("warnings", [])
        codex_auth = supplement.get("codex_auth")
        credentials_complete = supplement.get("credentials_complete")
        try:
            captured_at = datetime.fromisoformat(supplement["captured_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError("full recovery host supplement timestamp is invalid") from exc
        if (
            supplement.get("version") != 1
            or supplement.get("project_id") != project_id
            or captured_at.tzinfo is None
            or not isinstance(files, list)
            or len(files) > MAX_SUPPLEMENT_FILES
            or not isinstance(warnings, list)
            or len(warnings) > 100
            or not all(isinstance(item, str) and len(item) <= 500 for item in warnings)
            or not isinstance(codex_auth, dict)
            or not isinstance(codex_auth.get("included"), bool)
            or codex_auth.get("credential_store") not in {"file", "unavailable"}
            or not isinstance(credentials_complete, bool)
        ):
            raise BackupError("full recovery host supplement is malformed")

        output.mkdir(parents=True, exist_ok=True)
        output.chmod(0o700)
        materialized: dict[str, Path] = {}
        total = 0
        for item in files:
            if not isinstance(item, dict):
                raise BackupError("full recovery host supplement file is malformed")
            name = item.get("path")
            digest = item.get("sha256")
            encoded = item.get("content_base64")
            if (
                not isinstance(name, str)
                or not isinstance(encoded, str)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or name in materialized
            ):
                raise BackupError("full recovery host supplement file is malformed")
            _validate_v2_payload_name(name)
            if name.startswith("host-state/hooks/") and not Path(name).name.startswith(
                f"{project_id}-"
            ):
                raise BackupError("full recovery host supplement contains another project")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise BackupError("full recovery host supplement contains invalid base64") from exc
            total += len(content)
            if len(content) > MAX_SUPPLEMENT_FILE_BYTES or total > MAX_SUPPLEMENT_BYTES:
                raise BackupError("full recovery host supplement is too large")
            if sha256(content).hexdigest() != digest:
                raise BackupError(f"full recovery host supplement checksum failed for {name}")
            if name.startswith("host-state/"):
                try:
                    host_state = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError(
                        f"full recovery host supplement contains malformed JSON: {name}"
                    ) from exc
                if not isinstance(host_state, (dict, list)):
                    raise BackupError(
                        f"full recovery host supplement contains malformed JSON: {name}"
                    )
                if name.startswith("host-state/hooks/") and (
                    not isinstance(host_state, dict)
                    or str(host_state.get("project_id") or "") != project_id
                ):
                    raise BackupError(
                        "full recovery host supplement contains another project"
                    )
            if name == "binding/project.toml":
                try:
                    binding = tomllib.loads(content.decode("utf-8"))
                except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                    raise BackupError("full recovery binding configuration is malformed") from exc
                if str(binding.get("id") or "") != project_id:
                    raise BackupError("full recovery binding belongs to another project")
            if name == "binding/remote-credential.token" and not content.strip():
                raise BackupError("full recovery remote credential is empty")
            target = output.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o700)
            target.write_bytes(content)
            target.chmod(0o600)
            materialized[name] = target

        secret = materialized.get("secrets/dduo.env")
        if secret is None:
            raise BackupError("full recovery host supplement omitted dDuo secrets")
        normalized_secrets = self._normalize_portable_secrets(secret)
        auth_in_payload = "secrets/codex/auth.json" in materialized
        if auth_in_payload != codex_auth["included"]:
            raise BackupError("full recovery Codex credential metadata is inconsistent")
        expected_store = "file" if auth_in_payload else "unavailable"
        if codex_auth["credential_store"] != expected_store:
            raise BackupError("full recovery Codex credential metadata is inconsistent")
        if credentials_complete and not auth_in_payload:
            raise BackupError("full recovery credential completeness is inconsistent")
        if auth_in_payload:
            try:
                auth = json.loads(materialized["secrets/codex/auth.json"].read_text())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError("Codex auth.json in host supplement is malformed") from exc
            if not isinstance(auth, dict) or codex_auth["credential_store"] != "file":
                raise BackupError("Codex auth.json in host supplement is malformed")

        metadata = {
            "supplement_version": 1,
            "captured_at": captured_at.isoformat(),
            "dduo_secrets": {
                "included": True,
                "keys": sorted(normalized_secrets),
                "fingerprints": {
                    "DDUO_NODE_AUTHORITY_SECRET": sha256(
                        normalized_secrets["DDUO_NODE_AUTHORITY_SECRET"].encode("utf-8")
                    ).hexdigest()
                }
                if normalized_secrets.get("DDUO_NODE_AUTHORITY_SECRET")
                else {},
            },
            "codex_auth": {
                "included": auth_in_payload,
                "credential_store": codex_auth["credential_store"],
            },
            "complete": credentials_complete,
        }
        return materialized, metadata, list(warnings)

    def _normalize_portable_secrets(self, path: Path) -> dict[str, str]:
        """Reject arbitrary environment data and add only runtime-owned values."""
        values: dict[str, str] = {}
        try:
            for raw_line in path.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ValueError("missing separator")
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in PORTABLE_SECRET_KEYS or key in values or "\x00" in value:
                    raise ValueError("unsupported secret")
                values[key] = value
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise BackupError("full recovery dDuo secret envelope is invalid") from exc
        openai_key = values.get("OPENAI_API_KEY") or (self.settings.openai_api_key or "").strip()
        if not openai_key or "\n" in openai_key or "\r" in openai_key:
            raise BackupError("full recovery dDuo secret envelope omitted OPENAI_API_KEY")
        values["OPENAI_API_KEY"] = openai_key
        database_password = make_url(self.settings.database_url).password
        if database_password:
            if "\n" in database_password or "\r" in database_password:
                raise BackupError("database credential cannot be represented safely")
            values.setdefault("DDUO_DATABASE_PASSWORD", database_password)
        path.write_text("".join(f"{key}={values[key]}\n" for key in sorted(values)))
        path.chmod(0o600)
        return values

    def _snapshot_named_collection(
        self, collection: str, output: Path
    ) -> tuple[str | None, str | None]:
        encoded = quote(collection, safe="")
        snapshot_name = None
        try:
            with httpx.Client(base_url=self.settings.qdrant_url, timeout=120) as client:
                response = client.post(f"/collections/{encoded}/snapshots")
                response.raise_for_status()
                result = response.json().get("result") or {}
                snapshot_name = result.get("name")
                if not snapshot_name:
                    raise BackupError("Qdrant did not return a snapshot name")
                with client.stream(
                    "GET", f"/collections/{encoded}/snapshots/{quote(snapshot_name, safe='')}"
                ) as download:
                    download.raise_for_status()
                    with output.open("wb") as target:
                        for chunk in download.iter_bytes():
                            target.write(chunk)
                return collection, None
        except (httpx.HTTPError, AttributeError, ValueError, BackupError) as exc:
            output.unlink(missing_ok=True)
            return None, f"Qdrant snapshot omitted; PostgreSQL reindex remains available: {exc}"
        finally:
            if snapshot_name:
                try:
                    with httpx.Client(base_url=self.settings.qdrant_url, timeout=30) as client:
                        client.delete(
                            f"/collections/{encoded}/snapshots/{quote(snapshot_name, safe='')}"
                        )
                except httpx.HTTPError:
                    pass

    def _snapshot_qdrant(self, project_id: str, output: Path) -> tuple[str | None, str | None]:
        return self._snapshot_named_collection(self.collection_resolver(project_id), output)

    def _snapshot_task_qdrant(self, project_id: str, output: Path) -> tuple[str | None, str | None]:
        return self._snapshot_named_collection(self.task_collection_resolver(project_id), output)

    def verify(self, archive: Path, recovery_key: str) -> dict:
        """Verify archive authentication, inventory, hashes, and PostgreSQL catalog."""
        with tempfile.TemporaryDirectory(prefix="dduo-backup-pg-verify-") as temporary_dir:
            extracted = Path(temporary_dir)
            verification = verify_archive(archive, recovery_key, extract_to=extracted)
            self._validate_postgres_dump(extracted / "postgres.dump")
            return verification.manifest

    def create(self, project_id: str, project_name: str, app_version: str) -> BackupResult:
        """Create, self-verify, publish, and retain one project backup."""
        if not self.settings.backup_configured:
            detail = self.settings.backup_configuration_error or "backup is not configured"
            raise BackupError(detail)
        if not _backup_lock.acquire(blocking=False):
            raise BackupError("another backup is already running")
        try:
            destination = self.settings.backup_directory
            key_file = self.settings.backup_key_file
            project_config = self.settings.project_config_file
            if not destination.is_dir():
                raise BackupError("backup destination is unavailable")
            if not key_file.is_file():
                raise BackupError("project backup key is unavailable")
            recovery_key = key_file.read_text().strip()
            decode_recovery_key(recovery_key)
            if not project_config.is_file():
                raise BackupError("project identity file is unavailable")
            try:
                configured_project = tomllib.loads(project_config.read_text())
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise BackupError("project identity file is malformed") from exc
            if configured_project.get("id") != project_id:
                raise BackupError("project identity file does not match the database project")
            with tempfile.TemporaryDirectory(prefix="dduo-backup-runtime-") as temporary_dir:
                temporary = Path(temporary_dir)
                postgres_dump = temporary / "postgres.dump"
                qdrant_snapshot = temporary / "qdrant-memory.snapshot"
                task_qdrant_snapshot = temporary / "qdrant-tasks.snapshot"
                runtime_settings = temporary / "runtime-settings.json"
                backup_history = temporary / "backup-records.json"
                self._validate_single_project_database(project_id)
                host_files, supplement_metadata, supplement_warnings = self._fetch_host_supplement(
                    project_id, temporary / "host-supplement"
                )
                if supplement_metadata.get("complete") is not True:
                    raise BackupError(
                        "full recovery credentials are incomplete; no backup was published"
                    )
                captured_at = datetime.now(timezone.utc)
                self._dump_postgres(postgres_dump)
                # Detect accidental cross-project writes that raced the
                # pre-dump guard before publishing any archive.
                self._validate_single_project_database(project_id)
                self._write_runtime_settings(runtime_settings, app_version=app_version)
                history_warning = self._export_backup_history(project_id, backup_history)
                collection = None
                task_collection = None
                warnings: list[str] = list(supplement_warnings)
                if history_warning:
                    warnings.append(history_warning)
                if self.settings.backup_include_qdrant:
                    collection, warning = self._snapshot_qdrant(project_id, qdrant_snapshot)
                    if warning:
                        warnings.append(warning)
                    task_collection, warning = self._snapshot_task_qdrant(
                        project_id, task_qdrant_snapshot
                    )
                    if warning:
                        warnings.append(warning)
                backup_id = str(uuid.uuid4())
                created_at = datetime.now(timezone.utc)
                filename = backup_filename(project_name, backup_id, created_at)
                pending = destination / f".{filename}.pending"
                try:
                    manifest = create_archive(
                        pending,
                        recovery_key,
                        app_version=app_version,
                        project_id=project_id,
                        project_name=project_name,
                        postgres_dump=postgres_dump,
                        project_config=project_config,
                        qdrant_snapshot=qdrant_snapshot if collection else None,
                        qdrant_collection=collection,
                        task_qdrant_snapshot=(task_qdrant_snapshot if task_collection else None),
                        task_qdrant_collection=task_collection,
                        runtime_settings=runtime_settings,
                        supplemental_files=host_files,
                        backup_history=backup_history if backup_history.is_file() else None,
                        supplement_metadata=supplement_metadata,
                        consistency={
                            "authority": "postgresql",
                            "barrier": "single_project_backup_lock",
                            "captured_at": captured_at.isoformat(),
                            "qdrant": "derived_reconcilable_projection",
                            "host_spool": "atomic_file_snapshot",
                        },
                        warnings=warnings,
                        created_at=created_at,
                        backup_id=backup_id,
                    )
                    self.verify(pending, recovery_key)
                    archive = destination / filename
                    os.replace(pending, archive)
                finally:
                    pending.unlink(missing_ok=True)
            retention = prune_archives(
                destination,
                daily=self.settings.backup_retention_daily,
                weekly=self.settings.backup_retention_weekly,
                monthly=self.settings.backup_retention_monthly,
                preserve={archive},
            )
            return BackupResult(
                archive_name=archive.name,
                size_bytes=archive.stat().st_size,
                includes_qdrant=bool(manifest["qdrant"]["included"]),
                manifest=manifest,
                pruned_archives=tuple(path.name for path in retention.deleted),
            )
        finally:
            _backup_lock.release()
