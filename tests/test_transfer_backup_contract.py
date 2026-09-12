from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest
import pytest_asyncio

from conftest import project_payload
from dduo_solo_founder import backup, launcher, main
from dduo_solo_founder.backup_service import BackupResult
from dduo_solo_founder.models import BackupRecord


AUTHORITY_SECRET = "isolated-transfer-contract-authority"


@pytest_asyncio.fixture
async def endpoint_backup(api_client, db_factory, monkeypatch, tmp_path):
    """Exercise the real route, ORM and serializer with an authenticated v2 archive.

    Only the external backup producer is substituted: its PostgreSQL payload is
    synthetic, so this is not a PostgreSQL restore test. Archive creation,
    encryption, integrity verification and the HTTP response are all real.
    """
    client, _ = api_client
    payload = project_payload()
    created = await client.post("/projects", json=payload)
    assert created.status_code == 200
    project_id = payload["id"]
    recovery_key = backup.generate_recovery_key()

    class IsolatedBackupProducer:
        def create(self, requested_id, project_name, app_version):
            assert requested_id == project_id
            dump = tmp_path / "postgres.dump"
            dump.write_bytes(b"PGDMP-isolated-contract-fixture")
            config = tmp_path / "project.toml"
            config.write_text(f'id = "{project_id}"\n', encoding="utf-8")
            runtime = tmp_path / "runtime-settings.json"
            runtime.write_text('{"schema_version":1,"settings":{}}', encoding="utf-8")
            secrets = tmp_path / "dduo.env"
            secrets.write_text(f"DDUO_NODE_AUTHORITY_SECRET={AUTHORITY_SECRET}\n", encoding="utf-8")
            archive = tmp_path / "final-transfer.dduobackup"
            backup.create_archive(
                archive,
                recovery_key,
                app_version=app_version,
                project_id=project_id,
                project_name=project_name,
                postgres_dump=dump,
                project_config=config,
                runtime_settings=runtime,
                supplemental_files={"secrets/dduo.env": secrets},
                supplement_metadata={
                    "complete": True,
                    "codex_auth": {"included": False, "credential_store": "unavailable"},
                    "claude_auth": {"included": False, "credential_store": "unavailable"},
                    "dduo_secrets": {
                        "included": True,
                        "keys": ["DDUO_NODE_AUTHORITY_SECRET"],
                        "fingerprints": {
                            "DDUO_NODE_AUTHORITY_SECRET": sha256(
                                AUTHORITY_SECRET.encode()
                            ).hexdigest()
                        },
                    },
                },
            )
            verified = backup.verify_archive(archive, recovery_key)
            return BackupResult(
                archive_name=archive.name,
                size_bytes=archive.stat().st_size,
                includes_qdrant=False,
                manifest=verified.manifest,
                pruned_archives=(),
            )

    monkeypatch.setattr(main, "backup_engine", IsolatedBackupProducer)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            backup_project_id=project_id,
            backup_configured=True,
            backup_configuration_error="",
        ),
    )
    response = await client.post(f"/projects/{project_id}/backups?trigger=manual")
    assert response.status_code == 200, response.text
    result = response.json()
    async with db_factory() as db:
        record = await db.get(BackupRecord, result["id"])
        assert record.manifest_json == result["manifest"]
    assert result["status"] == "verified"
    assert "manifest_json" not in result
    return result, project_id


async def test_real_backup_endpoint_response_is_accepted_by_transfer_validator(endpoint_backup):
    result, project_id = endpoint_backup
    assert launcher._validate_final_transfer_backup(result, project_id, AUTHORITY_SECRET) is result


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "running"),
        (("status",), "failed"),
        (("archive_name",), "not-an-encrypted-archive.zip"),
        (("archive_name",), None),
        (("manifest",), None),
        (("manifest", "schema_version"), 1),
        (("manifest", "recovery_contract"), "partial-project"),
        (("manifest", "project", "id"), "another-project"),
        (("manifest", "credentials", "complete"), False),
        (("manifest", "credentials", "complete"), "true"),
        (("manifest", "credentials", "dduo_secrets", "keys"), []),
        (("manifest", "credentials", "dduo_secrets", "fingerprints"), {}),
        (
            (
                "manifest",
                "credentials",
                "dduo_secrets",
                "fingerprints",
                "DDUO_NODE_AUTHORITY_SECRET",
            ),
            "invalid-sha256",
        ),
        (
            (
                "manifest",
                "credentials",
                "dduo_secrets",
                "fingerprints",
                "DDUO_NODE_AUTHORITY_SECRET",
            ),
            sha256(b"another-authority").hexdigest(),
        ),
    ],
)
async def test_transfer_validator_still_rejects_invalid_backup_contract(
    endpoint_backup, path, value
):
    original, project_id = endpoint_backup
    result = deepcopy(original)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(RuntimeError, match="verified, complete full-project-v2"):
        launcher._validate_final_transfer_backup(result, project_id, AUTHORITY_SECRET)


async def test_transfer_validator_rejects_wrong_local_identity_or_authority(endpoint_backup):
    result, project_id = endpoint_backup
    for expected_project, secret in (
        ("another-project", AUTHORITY_SECRET),
        (project_id, "another-authority"),
        (project_id, ""),
    ):
        with pytest.raises(RuntimeError, match="verified, complete full-project-v2"):
            launcher._validate_final_transfer_backup(result, expected_project, secret)


async def test_transfer_validator_does_not_accept_internal_orm_alias(endpoint_backup):
    original, project_id = endpoint_backup
    result = deepcopy(original)
    result["manifest_json"] = result.pop("manifest")
    with pytest.raises(RuntimeError, match="verified, complete full-project-v2"):
        launcher._validate_final_transfer_backup(result, project_id, AUTHORITY_SECRET)
