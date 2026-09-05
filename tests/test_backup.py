from __future__ import annotations

import copy
import json
import os
import stat
import zipfile
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from dduo_solo_founder import backup


def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dump = tmp_path / "postgres.dump"
    config = tmp_path / "project.toml"
    snapshot = tmp_path / "qdrant.snapshot"
    dump.write_bytes(b"PGDMP" + os.urandom(backup.CHUNK_SIZE + 17))
    config.write_text('version = 1\nid = "p1"\nname = "Example"\napi_port = 1\nweb_port = 2\n')
    snapshot.write_bytes(b"qdrant snapshot")
    return dump, config, snapshot


def create_valid(tmp_path: Path, *, qdrant: bool = True) -> tuple[Path, str, dict]:
    dump, config, snapshot = inputs(tmp_path)
    key = backup.generate_recovery_key()
    archive = tmp_path / "valid.dduobackup"
    manifest = backup.create_archive(
        archive,
        key,
        app_version="1.0",
        project_id="p1",
        project_name="Example Project",
        postgres_dump=dump,
        project_config=config,
        qdrant_snapshot=snapshot if qdrant else None,
        qdrant_collection="collection" if qdrant else None,
        warnings=["derived index omitted"] if not qdrant else None,
        created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
        backup_id="01234567-89ab-cdef-0123-456789abcdef",
    )
    return archive, key, manifest


def create_v2_valid(tmp_path: Path) -> tuple[Path, str, dict]:
    dump, config, memory_snapshot = inputs(tmp_path)
    task_snapshot = tmp_path / "tasks.snapshot"
    task_snapshot.write_bytes(b"task projection")
    runtime = tmp_path / "runtime-settings.json"
    runtime.write_text('{"schema_version":1,"settings":{"embedding_model":"model"}}')
    secret = tmp_path / "dduo.env"
    secret.write_text("OPENAI_API_KEY=secret\nDDUO_DATABASE_PASSWORD=internal\n")
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens":{"access_token":"secret"}}')
    hook = tmp_path / "hook.json"
    hook.write_text('{"pending_turns":[]}')
    history = tmp_path / "history.json"
    history.write_text("[]")
    key = backup.generate_recovery_key()
    archive = tmp_path / "valid-v2.dduobackup"
    manifest = backup.create_archive(
        archive,
        key,
        app_version="2.0",
        project_id="p1",
        project_name="Example Project",
        postgres_dump=dump,
        project_config=config,
        qdrant_snapshot=memory_snapshot,
        qdrant_collection="memory-collection",
        task_qdrant_snapshot=task_snapshot,
        task_qdrant_collection="task-collection",
        runtime_settings=runtime,
        supplemental_files={
            "secrets/dduo.env": secret,
            "secrets/codex/auth.json": auth,
            "host-state/hooks/p1-codex.json": hook,
        },
        backup_history=history,
        supplement_metadata={
            "codex_auth": {"included": True, "credential_store": "file"},
            "complete": True,
        },
    )
    return archive, key, manifest


def rewrite_payload(
    source: Path,
    target: Path,
    key: str,
    mutate,
) -> None:
    inner = target.with_suffix(".zip")
    backup.decrypt_file(source, inner, key)
    with zipfile.ZipFile(inner) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    mutate(entries)
    with zipfile.ZipFile(inner, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    backup.encrypt_file(inner, target, key)


def refresh_manifest_checksum(entries: dict[str, bytes]) -> None:
    checksums = backup._read_checksums(entries["checksums.sha256"].decode())
    checksums["manifest.json"] = sha256(entries["manifest.json"]).hexdigest()
    entries["checksums.sha256"] = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(checksums.items())
    ).encode()


def test_encrypted_archive_round_trip_and_optional_qdrant(tmp_path: Path):
    archive, key, manifest = create_valid(tmp_path)
    assert b"Example Project" not in archive.read_bytes()
    extracted = tmp_path / "extracted"
    verified = backup.verify_archive(archive, key, extract_to=extracted)
    assert verified.manifest == manifest
    assert (extracted / "postgres.dump").read_bytes().startswith(b"PGDMP")
    assert (extracted / "qdrant.snapshot").read_bytes() == b"qdrant snapshot"

    archive_without_index, key_without_index, manifest_without_index = create_valid(
        tmp_path / "without-index", qdrant=False
    )
    assert not manifest_without_index["qdrant"]["included"]
    assert backup.verify_archive(archive_without_index, key_without_index).manifest["warnings"]


def test_v2_full_recovery_round_trip_is_private_and_keeps_v1_readable(tmp_path: Path):
    archive, key, manifest = create_v2_valid(tmp_path)
    assert manifest["schema_version"] == 2
    assert manifest["qdrant"]["collections"]["tasks"]["included"]
    assert b"OPENAI_API_KEY" not in archive.read_bytes()
    extracted = tmp_path / "full-extracted"
    verified = backup.verify_archive(archive, key, extract_to=extracted)
    assert verified.manifest == manifest
    assert (extracted / "qdrant/memory.snapshot").read_bytes() == b"qdrant snapshot"
    assert (extracted / "qdrant/tasks.snapshot").read_bytes() == b"task projection"
    assert (extracted / "secrets/dduo.env").stat().st_mode & 0o777 == 0o600
    assert (extracted / "secrets/codex/auth.json").stat().st_mode & 0o777 == 0o600

    v1, v1_key, v1_manifest = create_valid(tmp_path / "legacy")
    assert v1_manifest["schema_version"] == 1
    assert backup.verify_archive(v1, v1_key).manifest == v1_manifest


def test_v2_rejects_missing_secrets_and_arbitrary_supplement_paths(tmp_path: Path):
    dump, config, _ = inputs(tmp_path)
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    with pytest.raises(backup.BackupError, match="requires secrets"):
        backup.create_archive(
            tmp_path / "missing-secrets.dduobackup",
            backup.generate_recovery_key(),
            app_version="2",
            project_id="p1",
            project_name="P",
            postgres_dump=dump,
            project_config=config,
            runtime_settings=runtime,
        )
    secret = tmp_path / "secret"
    secret.write_text("OPENAI_API_KEY=x")
    with pytest.raises(backup.BackupError, match="unexpected full recovery input"):
        backup.create_archive(
            tmp_path / "arbitrary.dduobackup",
            backup.generate_recovery_key(),
            app_version="2",
            project_id="p1",
            project_name="P",
            postgres_dump=dump,
            project_config=config,
            runtime_settings=runtime,
            supplemental_files={"secrets/dduo.env": secret, "secrets/ssh/id_rsa": secret},
            supplement_metadata={
                "codex_auth": {"included": False, "credential_store": "unavailable"},
                "complete": True,
            },
        )


def test_archive_creation_rejects_incomplete_v1_and_v2_inputs(tmp_path: Path):
    dump, config, snapshot = inputs(tmp_path)
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    secret = tmp_path / "dduo.env"
    secret.write_text("OPENAI_API_KEY=x")
    metadata = {
        "codex_auth": {"included": False, "credential_store": "unavailable"},
        "complete": True,
    }
    common = {
        "app_version": "2",
        "project_id": "p1",
        "project_name": "P",
        "postgres_dump": dump,
        "project_config": config,
    }

    with pytest.raises(backup.BackupError, match="full recovery inputs"):
        backup.create_archive(
            tmp_path / "v1-with-v2-input.dduobackup",
            backup.generate_recovery_key(),
            **common,
            task_qdrant_snapshot=snapshot,
        )
    with pytest.raises(backup.BackupError, match="credential metadata"):
        backup.create_archive(
            tmp_path / "missing-metadata.dduobackup",
            backup.generate_recovery_key(),
            **common,
            runtime_settings=runtime,
            supplemental_files={"secrets/dduo.env": secret},
        )
    with pytest.raises(backup.BackupError, match="memory Qdrant"):
        backup.create_archive(
            tmp_path / "missing-memory-collection.dduobackup",
            backup.generate_recovery_key(),
            **common,
            runtime_settings=runtime,
            supplemental_files={"secrets/dduo.env": secret},
            supplement_metadata=metadata,
            qdrant_snapshot=snapshot,
        )
    with pytest.raises(backup.BackupError, match="task Qdrant"):
        backup.create_archive(
            tmp_path / "missing-task-collection.dduobackup",
            backup.generate_recovery_key(),
            **common,
            runtime_settings=runtime,
            supplemental_files={"secrets/dduo.env": secret},
            supplement_metadata=metadata,
            task_qdrant_snapshot=snapshot,
        )


def test_v2_payload_and_schema_inventory_are_strict():
    with pytest.raises(backup.BackupError, match="unsafe backup input"):
        backup._validate_v2_payload_name("../secrets/dduo.env")
    for machine_global in backup.V2_LEGACY_IGNORED_FILES:
        with pytest.raises(backup.BackupError, match="unexpected full recovery input"):
            backup._validate_v2_payload_name(machine_global)
        # Existing archives remain readable, but restore ignores these entries.
        backup._validate_inventory_for_schema(
            2, set(backup.V2_REQUIRED_FILES) | {machine_global}
        )
    assert not backup._is_safe_member_name(42)

    with pytest.raises(backup.BackupError, match="missing or unexpected"):
        backup._validate_inventory_for_schema(1, {"manifest.json", "checksums.sha256"})
    with pytest.raises(backup.BackupError, match="missing or unexpected"):
        backup._validate_inventory_for_schema(2, set(backup.V2_REQUIRED_FILES) - {"project.toml"})
    with pytest.raises(backup.BackupError, match="missing or unexpected"):
        backup._validate_inventory_for_schema(
            2,
            set(backup.V2_REQUIRED_FILES) | {"host-state/not-allowlisted.bin"},
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest.update(project={"id": "", "name": "P"}), "identity"),
        (lambda manifest: manifest.pop("created_at"), "timestamp"),
        (
            lambda manifest: manifest.update(created_at="2026-01-01T00:00:00"),
            "timestamp",
        ),
        (lambda manifest: manifest["database"].update(engine="sqlite"), "database format"),
        (lambda manifest: manifest["qdrant"].update(included="yes"), "Qdrant metadata"),
        (
            lambda manifest: manifest["qdrant"].update(included=True, collection=None),
            "collection identity",
        ),
        (lambda manifest: manifest.update(warnings=[1]), "warnings"),
        (
            lambda manifest: manifest["files"]["postgres.dump"].update(sha256="bad"),
            "file metadata",
        ),
        (lambda manifest: manifest.update(recovery_contract="wrong"), "recovery contract"),
        (lambda manifest: manifest["qdrant"].update(collections=[]), "collection inventory"),
        (
            lambda manifest: manifest["qdrant"]["collections"].update(memory=[]),
            "collection inventory",
        ),
        (
            lambda manifest: manifest["qdrant"]["collections"]["memory"].update(
                file="qdrant/wrong.snapshot"
            ),
            "collection inventory",
        ),
        (
            lambda manifest: manifest["qdrant"]["collections"]["memory"].update(
                included=False,
                file="qdrant/memory.snapshot",
                collection=None,
            ),
            "collection inventory",
        ),
        (lambda manifest: manifest.update(history=[]), "full recovery metadata"),
        (lambda manifest: manifest["credentials"].pop("complete"), "full recovery metadata"),
        (
            lambda manifest: manifest["credentials"]["dduo_secrets"].update(
                keys=["OPENAI_API_KEY", "OPENAI_API_KEY"]
            ),
            "secret inventory",
        ),
        (
            lambda manifest: manifest["credentials"]["dduo_secrets"].update(
                keys=["DDUO_NODE_AUTHORITY_SECRET"],
                fingerprints={"DDUO_NODE_AUTHORITY_SECRET": "bad"},
            ),
            "secret inventory",
        ),
    ],
)
def test_v2_manifest_rejects_each_invalid_contract_field(tmp_path: Path, mutate, message: str):
    _, _, valid = create_v2_valid(tmp_path)
    malformed = copy.deepcopy(valid)
    mutate(malformed)
    with pytest.raises(backup.BackupError, match=message):
        backup._validate_manifest(malformed)


def test_manifest_rejects_non_object():
    with pytest.raises(backup.BackupError, match="unsupported"):
        backup._validate_manifest([])


def test_key_validation_and_authenticated_encryption_failures(tmp_path: Path):
    key = backup.generate_recovery_key()
    source = tmp_path / "source"
    source.write_bytes(os.urandom(backup.CHUNK_SIZE + 11))
    encrypted = tmp_path / "encrypted"
    decrypted = tmp_path / "decrypted"
    backup.encrypt_file(source, encrypted, key)
    backup.decrypt_file(encrypted, decrypted, key)
    assert decrypted.read_bytes() == source.read_bytes()

    for invalid in ("", "not-base64!", backup.generate_recovery_key()[:-2]):
        with pytest.raises(backup.BackupError):
            backup.decode_recovery_key(invalid)
    with pytest.raises(backup.BackupError, match="wrong or the backup was modified"):
        backup.decrypt_file(encrypted, decrypted, backup.generate_recovery_key())
    tampered = bytearray(encrypted.read_bytes())
    tampered[-backup.TAG_SIZE - 1] ^= 1
    encrypted.write_bytes(tampered)
    with pytest.raises(backup.BackupError, match="modified"):
        backup.decrypt_file(encrypted, decrypted, key)
    for content, message in ((b"short", "truncated"), (b"x" * 100, "unsupported")):
        encrypted.write_bytes(content)
        with pytest.raises(backup.BackupError, match=message):
            backup.decrypt_file(encrypted, decrypted, key)


def test_archive_rejects_missing_inputs_and_invalid_zip(tmp_path: Path):
    dump, config, _ = inputs(tmp_path)
    dump.unlink()
    with pytest.raises(backup.BackupError, match="postgres.dump"):
        backup.create_archive(
            tmp_path / "missing.dduobackup",
            backup.generate_recovery_key(),
            app_version="1",
            project_id="p",
            project_name="P",
            postgres_dump=dump,
            project_config=config,
        )
    raw = tmp_path / "raw"
    raw.write_bytes(b"not zip")
    archive = tmp_path / "bad.dduobackup"
    key = backup.generate_recovery_key()
    backup.encrypt_file(raw, archive, key)
    with pytest.raises(backup.BackupError, match="valid ZIP"):
        backup.verify_archive(archive, key)
    _, config, snapshot = inputs(tmp_path / "qdrant-without-identity")
    dump = tmp_path / "qdrant-without-identity/postgres.dump"
    with pytest.raises(backup.BackupError, match="collection identity"):
        backup.create_archive(
            tmp_path / "qdrant-without-identity/archive.dduobackup",
            key,
            app_version="1",
            project_id="p",
            project_name="P",
            postgres_dump=dump,
            project_config=config,
            qdrant_snapshot=snapshot,
        )


def test_archive_rejects_unsafe_duplicate_and_unexpected_members(tmp_path: Path):
    archive, key, _ = create_valid(tmp_path)
    for index, mutation in enumerate(
        (
            lambda entries: entries.__setitem__("../escape", b"x"),
            lambda entries: entries.__setitem__("unexpected.txt", b"x"),
            lambda entries: entries.pop("project.toml"),
        )
    ):
        malformed = tmp_path / f"malformed-{index}.dduobackup"
        rewrite_payload(archive, malformed, key, mutation)
        with pytest.raises(backup.BackupError, match="missing or unexpected|unsafe"):
            backup.verify_archive(malformed, key)

    inner = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(inner, "w") as payload:
            for name in backup.REQUIRED_FILES:
                payload.writestr(name, b"{}" if name == "manifest.json" else b"x")
            payload.writestr("postgres.dump", b"again")
    duplicate = tmp_path / "duplicate.dduobackup"
    backup.encrypt_file(inner, duplicate, key)
    with pytest.raises(backup.BackupError, match="duplicate"):
        backup.verify_archive(duplicate, key)

    symlink = zipfile.ZipInfo("project.toml")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(inner, "w") as payload:
        for name in backup.REQUIRED_FILES - {"project.toml"}:
            payload.writestr(name, b"{}" if name == "manifest.json" else b"x")
        payload.writestr(symlink, "target")
    backup.encrypt_file(inner, duplicate, key)
    with pytest.raises(backup.BackupError, match="unsafe"):
        backup.verify_archive(duplicate, key)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda entries: entries.__setitem__("manifest.json", b"not json"),
            "metadata is malformed",
        ),
        (
            lambda entries: entries.__setitem__("checksums.sha256", b"bad"),
            "checksums.sha256 is malformed",
        ),
        (
            lambda entries: entries.__setitem__("postgres.dump", b"changed"),
            "checksum failed for postgres.dump",
        ),
    ],
)
def test_archive_rejects_malformed_metadata_and_payload(tmp_path: Path, mutation, message: str):
    archive, key, _ = create_valid(tmp_path)
    malformed = tmp_path / "malformed.dduobackup"
    rewrite_payload(archive, malformed, key, mutation)
    with pytest.raises(backup.BackupError, match=message):
        backup.verify_archive(malformed, key)


def test_archive_rejects_unsupported_and_inconsistent_manifests(tmp_path: Path):
    archive, key, _ = create_valid(tmp_path)

    def unsupported(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["schema_version"] = 999
        entries["manifest.json"] = json.dumps(manifest).encode()

    malformed = tmp_path / "unsupported.dduobackup"
    rewrite_payload(archive, malformed, key, unsupported)
    with pytest.raises(backup.BackupError, match="unsupported"):
        backup.verify_archive(malformed, key)

    def wrong_size(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["files"]["postgres.dump"]["size"] += 1
        entries["manifest.json"] = json.dumps(manifest).encode()
        refresh_manifest_checksum(entries)

    rewrite_payload(archive, malformed, key, wrong_size)
    with pytest.raises(backup.BackupError, match="size check"):
        backup.verify_archive(malformed, key)

    def inconsistent_qdrant(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["qdrant"]["included"] = False
        entries["manifest.json"] = json.dumps(manifest).encode()
        refresh_manifest_checksum(entries)

    rewrite_payload(archive, malformed, key, inconsistent_qdrant)
    with pytest.raises(backup.BackupError, match="Qdrant metadata"):
        backup.verify_archive(malformed, key)

    def malformed_qdrant(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["qdrant"] = []
        entries["manifest.json"] = json.dumps(manifest).encode()

    rewrite_payload(archive, malformed, key, malformed_qdrant)
    with pytest.raises(backup.BackupError, match="unsupported"):
        backup.verify_archive(malformed, key)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing").write_text("do not overwrite")
    with pytest.raises(backup.BackupError, match="must be empty"):
        backup.verify_archive(archive, key, extract_to=nonempty)


def test_checksum_parser_and_backup_filename():
    digest = "a" * 64
    assert backup._read_checksums(f"{digest}  postgres.dump\n") == {"postgres.dump": digest}
    with pytest.raises(backup.BackupError):
        backup._read_checksums(f"{digest}  postgres.dump\n{digest}  postgres.dump\n")
    assert (
        backup.backup_filename(
            "A Project!", "01234567-rest", datetime(2026, 7, 12, tzinfo=timezone.utc)
        )
        == "dduo-solo-founder-a-project-20260712T000000Z-01234567.dduobackup"
    )


def test_retention_selects_daily_weekly_monthly_and_prunes(tmp_path: Path):
    paths = []
    start = datetime(2026, 7, 12, 10, tzinfo=timezone.utc)
    for index in range(50):
        created = start - timedelta(days=index)
        path = tmp_path / backup.backup_filename("P", f"{index:08x}", created)
        path.write_text(str(index))
        paths.append(path)
    retained = backup.select_retained_archives(paths, daily=7, weekly=4, monthly=6)
    assert paths[0] in retained
    assert 7 <= len(retained) <= 17
    result = backup.prune_archives(tmp_path, daily=2, weekly=1, monthly=1)
    assert result.deleted
    assert all(path.exists() for path in result.retained)
    assert all(not path.exists() for path in result.deleted)
    assert backup.select_retained_archives([], daily=0, weekly=0, monthly=0) == set()
    only = tmp_path / "manual-name.dduobackup"
    only.write_text("x")
    assert backup.select_retained_archives([only], daily=0, weekly=0, monthly=0) == {only}

    current = tmp_path / backup.backup_filename("P", "ffffffff", start - timedelta(days=365))
    future = tmp_path / backup.backup_filename("P", "eeeeeeee", start + timedelta(days=365))
    current.write_text("current")
    future.write_text("future")
    preserved = backup.prune_archives(tmp_path, daily=0, weekly=0, monthly=0, preserve={current})
    assert current in preserved.retained and current.exists()
