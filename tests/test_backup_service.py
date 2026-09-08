from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dduo_solo_founder import backup_service, restore_index
from dduo_solo_founder.backup import BackupError, generate_recovery_key
from dduo_solo_founder.config import Settings


def settings(tmp_path: Path, **overrides) -> Settings:
    destination = tmp_path / "backups"
    destination.mkdir(parents=True, exist_ok=True)
    key = tmp_path / "key"
    key.write_text(generate_recovery_key())
    config = tmp_path / "project.toml"
    config.write_text('id = "p1"\n')
    values = {
        "database_url": "postgresql+asyncpg://user:secret@db:5433/memory",
        "qdrant_url": "http://qdrant:6333",
        "backup_configured": True,
        "backup_directory": destination,
        "backup_key_file": key,
        "project_config_file": config,
        "backup_include_qdrant": True,
        "backup_retention_daily": 2,
        "backup_retention_weekly": 1,
        "backup_retention_monthly": 1,
    }
    values.update(overrides)
    return Settings(**values)


def supplement_item(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "content_base64": base64.b64encode(content).decode(),
        "sha256": sha256(content).hexdigest(),
    }


def valid_supplement(*extra_files: dict) -> dict:
    return {
        "version": 1,
        "project_id": "p1",
        "captured_at": "2026-08-28T00:00:00+00:00",
        "files": [
            supplement_item("secrets/dduo.env", b"OPENAI_API_KEY=host-key\n"),
            supplement_item(
                "secrets/codex/auth.json",
                b'{"tokens":{"access_token":"secret"}}',
            ),
            *extra_files,
        ],
        "codex_auth": {"included": True, "credential_store": "file"},
        "credentials_complete": True,
        "warnings": [],
    }


class WorkingEngine(backup_service.BackupEngine):
    def _validate_single_project_database(self, project_id: str) -> None:
        assert project_id == "p1"

    def _dump_postgres(self, output: Path) -> None:
        output.write_bytes(b"PGDMP-valid")

    def _validate_postgres_dump(self, dump: Path) -> None:
        assert dump.read_bytes().startswith(b"PGDMP")

    def _snapshot_qdrant(self, project_id: str, output: Path):
        output.write_bytes(b"snapshot")
        return f"collection-{project_id}", None

    def _snapshot_task_qdrant(self, project_id: str, output: Path):
        output.write_bytes(b"task snapshot")
        return f"task-collection-{project_id}", None

    def _fetch_host_supplement(self, project_id: str, output: Path):
        secret = output / "secrets/dduo.env"
        secret.parent.mkdir(parents=True)
        secret.write_text("OPENAI_API_KEY=test-key\n")
        codex_auth = output / "secrets/codex/auth.json"
        codex_auth.parent.mkdir(parents=True)
        codex_auth.write_text('{"tokens":{"access_token":"test-only"}}')
        return (
            {
                "secrets/dduo.env": secret,
                "secrets/codex/auth.json": codex_auth,
            },
            {
                "codex_auth": {"included": True, "credential_store": "file"},
                "complete": True,
            },
            [],
        )

    def _export_backup_history(self, project_id: str, output: Path):
        assert project_id == "p1"
        output.write_text("[]")
        return None


def test_backup_engine_creates_verifies_and_prunes(tmp_path: Path):
    engine = WorkingEngine(settings(tmp_path), collection_resolver=lambda value: value)
    for index in range(6):
        old = engine.settings.backup_directory / (
            f"dduo-solo-founder-old-20260{index + 1}01T000000Z-{index:08x}.dduobackup"
        )
        old.write_text("old")
    result = engine.create("p1", "Example", "1.0")
    assert result.archive_name.endswith(".dduobackup")
    assert result.size_bytes > 0 and result.includes_qdrant
    assert result.manifest["qdrant"]["collection"] == "collection-p1"
    assert result.manifest["schema_version"] == 2
    assert result.manifest["qdrant"]["collections"]["tasks"]["collection"] == ("task-collection-p1")
    assert result.pruned_archives
    key = engine.settings.backup_key_file.read_text()
    assert (
        engine.verify(engine.settings.backup_directory / result.archive_name, key)["project"]["id"]
        == "p1"
    )


def test_backup_engine_without_qdrant_and_runtime_guards(tmp_path: Path):
    class NoIndexEngine(WorkingEngine):
        def _snapshot_qdrant(self, project_id: str, output: Path):
            return None, "index unavailable"

        def _snapshot_task_qdrant(self, project_id: str, output: Path):
            return None, "task index unavailable"

    engine = NoIndexEngine(settings(tmp_path))
    result = engine.create("p1", "Example", "1.0")
    assert not result.includes_qdrant
    assert result.manifest["warnings"] == ["index unavailable", "task index unavailable"]

    with pytest.raises(BackupError, match="not configured"):
        WorkingEngine(settings(tmp_path, backup_configured=False)).create("p1", "P", "1")
    with pytest.raises(BackupError, match="destination"):
        WorkingEngine(settings(tmp_path, backup_directory=tmp_path / "missing")).create(
            "p1", "P", "1"
        )
    missing_key = tmp_path / "missing-key"
    with pytest.raises(BackupError, match="key"):
        WorkingEngine(settings(tmp_path, backup_key_file=missing_key)).create("p1", "P", "1")
    invalid_key = tmp_path / "invalid-key"
    invalid_key.write_text("invalid")
    with pytest.raises(BackupError, match="recovery key"):
        WorkingEngine(settings(tmp_path, backup_key_file=invalid_key)).create("p1", "P", "1")
    wrong_config = tmp_path / "wrong-project.toml"
    wrong_config.write_text('id = "another"\n')
    with pytest.raises(BackupError, match="does not match"):
        WorkingEngine(settings(tmp_path, project_config_file=wrong_config)).create("p1", "P", "1")
    with pytest.raises(BackupError, match="identity file is unavailable"):
        WorkingEngine(
            settings(tmp_path, project_config_file=tmp_path / "missing-project.toml")
        ).create("p1", "P", "1")
    malformed_config = tmp_path / "malformed-project.toml"
    malformed_config.write_text("id = [invalid")
    with pytest.raises(BackupError, match="identity file is malformed"):
        WorkingEngine(settings(tmp_path, project_config_file=malformed_config)).create(
            "p1", "P", "1"
        )
    backup_service._backup_lock.acquire()
    try:
        with pytest.raises(BackupError, match="already running"):
            engine.create("p1", "P", "1")
    finally:
        backup_service._backup_lock.release()


def test_backup_engine_skips_projections_and_keeps_history_warning(tmp_path: Path):
    class HistoryWarningEngine(WorkingEngine):
        def _export_backup_history(self, project_id: str, output: Path):
            assert project_id == "p1"
            return "portable history unavailable"

        def _snapshot_qdrant(self, project_id: str, output: Path):
            raise AssertionError("Qdrant must not be called when disabled")

        def _snapshot_task_qdrant(self, project_id: str, output: Path):
            raise AssertionError("Qdrant must not be called when disabled")

    engine = HistoryWarningEngine(settings(tmp_path, backup_include_qdrant=False))
    result = engine.create("p1", "No projections", "1.0")
    assert not result.includes_qdrant
    assert result.manifest["warnings"] == ["portable history unavailable"]


def test_backup_engine_never_publishes_an_incomplete_recovery_bundle(tmp_path: Path):
    class IncompleteEngine(WorkingEngine):
        def _fetch_host_supplement(self, project_id: str, output: Path):
            secret = output / "secrets/dduo.env"
            secret.parent.mkdir(parents=True)
            secret.write_text("OPENAI_API_KEY=test-key\n")
            return (
                {"secrets/dduo.env": secret},
                {
                    "codex_auth": {
                        "included": False,
                        "credential_store": "unavailable",
                    },
                    "complete": False,
                },
                ["codex_auth_unavailable"],
            )

    engine = IncompleteEngine(settings(tmp_path))
    with pytest.raises(BackupError, match="credentials are incomplete"):
        engine.create("p1", "Example", "1.0")
    assert list(engine.settings.backup_directory.glob("*.dduobackup")) == []


def test_postgres_commands_use_environment_and_report_errors(tmp_path: Path, monkeypatch):
    engine = backup_service.BackupEngine(settings(tmp_path))
    environment = engine._postgres_environment()
    assert environment["PGHOST"] == "db"
    assert environment["PGPORT"] == "5433"
    assert environment["PGPASSWORD"] == "secret"
    with pytest.raises(BackupError, match="incomplete"):
        backup_service.BackupEngine(
            settings(tmp_path, database_url="sqlite+aiosqlite:///memory.db")
        )._postgres_environment()

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    engine._dump_postgres(tmp_path / "dump")
    engine._validate_postgres_dump(tmp_path / "dump")
    assert engine._run_capture(["psql"]) == ""
    assert calls[0][0][0] == "pg_dump" and "PGPASSWORD" in calls[0][1]["env"]
    # Whole-schema backups automatically include sprint membership snapshots
    # and replay receipts; no table allowlist may silently omit new Work data.
    assert not any(arg.startswith("--table") for arg in calls[0][0])
    assert [arg for arg in calls[0][0] if arg.startswith("--exclude")] == [
        "--exclude-table-data=backup_records"
    ]
    assert calls[1][0][:2] == ["pg_restore", "--list"]
    monkeypatch.setattr(
        backup_service.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stderr="failed", stdout=""),
    )
    with pytest.raises(BackupError, match="pg_restore failed"):
        engine._validate_postgres_dump(tmp_path / "dump")
    with pytest.raises(BackupError, match="psql failed"):
        engine._run_capture(["psql"])

    monkeypatch.setenv("PGPASSWORD", "ambient-wrong-password")
    without_password = backup_service.BackupEngine(
        settings(tmp_path, database_url="postgresql+asyncpg://user@db:5433/memory")
    )._postgres_environment()
    assert "PGPASSWORD" not in without_password


def test_qdrant_snapshot_success_failure_and_cleanup(tmp_path: Path, monkeypatch):
    events = []

    class Response:
        def __init__(self, payload=None):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self):
            return iter([b"one", b"two"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class Client:
        def __init__(self, **kwargs):
            events.append(("init", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path):
            events.append(("post", path))
            return Response({"result": {"name": "snapshot-1"}})

        def stream(self, method, path):
            events.append((method, path))
            return Response()

        def delete(self, path):
            events.append(("delete", path))
            return Response()

    monkeypatch.setattr(backup_service.httpx, "Client", Client)
    engine = backup_service.BackupEngine(settings(tmp_path), collection_resolver=lambda _: "c/1")
    output = tmp_path / "snapshot"
    collection, warning = engine._snapshot_qdrant("p1", output)
    assert collection == "c/1" and warning is None and output.read_bytes() == b"onetwo"
    assert any(event[0] == "delete" for event in events)

    class FailedClient(Client):
        def post(self, path):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(backup_service.httpx, "Client", FailedClient)
    output.write_text("partial")
    collection, warning = engine._snapshot_qdrant("p1", output)
    assert collection is None and "reindex" in warning and not output.exists()

    class MissingNameClient(Client):
        def post(self, path):
            return Response({"result": {}})

    monkeypatch.setattr(backup_service.httpx, "Client", MissingNameClient)
    collection, warning = engine._snapshot_qdrant("p1", output)
    assert collection is None and "snapshot name" in warning

    class DeleteFailedClient(Client):
        def delete(self, path):
            raise httpx.ConnectError("delete failed")

    monkeypatch.setattr(backup_service.httpx, "Client", DeleteFailedClient)
    collection, warning = engine._snapshot_qdrant("p1", output)
    assert collection == "c/1" and warning is None

    monkeypatch.setattr(
        engine,
        "_snapshot_named_collection",
        lambda collection, path: (collection, str(path)),
    )
    task_collection = engine.task_collection_resolver("p1")
    assert engine._snapshot_task_qdrant("p1", output) == (task_collection, str(output))


def test_host_supplement_is_bounded_project_scoped_and_allowlisted(tmp_path: Path):
    engine = backup_service.BackupEngine(
        settings(tmp_path, openai_api_key="fallback-key"),
        collection_resolver=lambda value: value,
    )

    def item(path: str, content: bytes) -> dict:
        return {
            "path": path,
            "content_base64": base64.b64encode(content).decode(),
            "sha256": sha256(content).hexdigest(),
        }

    payload = {
        "version": 1,
        "project_id": "p1",
        "captured_at": "2026-08-28T00:00:00+00:00",
        "files": [
            item(
                "secrets/dduo.env",
                b"OPENAI_API_KEY=host-key\nDDUO_NODE_AUTHORITY_SECRET=authority-key\n",
            ),
            item("secrets/codex/auth.json", b'{"tokens":{"access_token":"secret"}}'),
            item(
                "host-state/hooks/p1-codex.json",
                b'{"project_id":"p1","pending":[]}',
            ),
            item("host-state/mcp-observability.json", b"[]"),
        ],
        "codex_auth": {"included": True, "credential_store": "file"},
        "credentials_complete": True,
        "warnings": [],
    }
    files, metadata, warnings = engine._materialize_host_supplement(
        payload, "p1", tmp_path / "supplement"
    )
    assert warnings == [] and metadata["complete"]
    assert set(files) == {item["path"] for item in payload["files"]}
    secrets = files["secrets/dduo.env"].read_text()
    assert "OPENAI_API_KEY=host-key" in secrets
    assert "DDUO_DATABASE_PASSWORD=secret" in secrets
    assert metadata["dduo_secrets"]["keys"] == [
        "DDUO_DATABASE_PASSWORD",
        "DDUO_NODE_AUTHORITY_SECRET",
        "OPENAI_API_KEY",
    ]
    assert metadata["dduo_secrets"]["fingerprints"] == {
        "DDUO_NODE_AUTHORITY_SECRET": sha256(b"authority-key").hexdigest()
    }
    assert files["secrets/codex/auth.json"].stat().st_mode & 0o777 == 0o600

    incomplete = json.loads(json.dumps(payload))
    incomplete["credentials_complete"] = False
    _, incomplete_metadata, _ = engine._materialize_host_supplement(
        incomplete, "p1", tmp_path / "incomplete"
    )
    assert incomplete_metadata["complete"] is False
    missing_completeness = json.loads(json.dumps(payload))
    missing_completeness.pop("credentials_complete")
    with pytest.raises(BackupError, match="malformed"):
        engine._materialize_host_supplement(
            missing_completeness, "p1", tmp_path / "missing-completeness"
        )
    misleading = json.loads(json.dumps(payload))
    misleading["files"] = [
        item for item in misleading["files"] if item["path"] != "secrets/codex/auth.json"
    ]
    misleading["codex_auth"] = {
        "included": False,
        "credential_store": "unavailable",
    }
    with pytest.raises(BackupError, match="completeness"):
        engine._materialize_host_supplement(misleading, "p1", tmp_path / "misleading-completeness")

    wrong_project = {**payload, "project_id": "another"}
    with pytest.raises(BackupError, match="malformed"):
        engine._materialize_host_supplement(wrong_project, "p1", tmp_path / "wrong")
    arbitrary = json.loads(json.dumps(payload))
    arbitrary["files"][2]["path"] = "secrets/ssh/id_rsa"
    with pytest.raises(BackupError, match="unexpected full recovery input"):
        engine._materialize_host_supplement(arbitrary, "p1", tmp_path / "arbitrary")
    corrupted = json.loads(json.dumps(payload))
    corrupted["files"][0]["sha256"] = "0" * 64
    with pytest.raises(BackupError, match="checksum"):
        engine._materialize_host_supplement(corrupted, "p1", tmp_path / "corrupted")
    other_hook = json.loads(json.dumps(payload))
    other_hook["files"][2]["path"] = "host-state/hooks/other-codex.json"
    with pytest.raises(BackupError, match="another project"):
        engine._materialize_host_supplement(other_hook, "p1", tmp_path / "other-hook")
    disguised_hook = json.loads(json.dumps(payload))
    wrong_content = b'{"project_id":"another","pending":[]}'
    disguised_hook["files"][2] = item(
        "host-state/hooks/p1-disguised.json", wrong_content
    )
    with pytest.raises(BackupError, match="another project"):
        engine._materialize_host_supplement(
            disguised_hook, "p1", tmp_path / "disguised-hook"
        )


def test_host_supplement_accepts_one_file_backed_claude_subscription(tmp_path: Path):
    engine = backup_service.BackupEngine(
        settings(tmp_path, openai_api_key="fallback-key"),
        collection_resolver=lambda value: value,
    )
    payload = valid_supplement()
    credentials = b'{"oauthAccount":{"emailAddress":"test@example.com"}}'
    payload["files"] = [
        item for item in payload["files"] if item["path"] != "secrets/codex/auth.json"
    ]
    payload["codex_auth"] = {"included": False, "credential_store": "unavailable"}
    payload["files"].append(supplement_item("secrets/claude/.credentials.json", credentials))
    payload["claude_auth"] = {"included": True, "credential_store": "file"}

    files, metadata, warnings = engine._materialize_host_supplement(
        payload, "p1", tmp_path / "claude-supplement"
    )

    assert warnings == []
    assert metadata["complete"] is True
    assert metadata["claude_auth"] == {"included": True, "credential_store": "file"}
    assert files["secrets/claude/.credentials.json"].read_bytes() == credentials


def test_host_supplement_rejects_malformed_files_bindings_and_credentials(tmp_path: Path):
    engine = backup_service.BackupEngine(settings(tmp_path), collection_resolver=lambda value: value)

    def rejected(payload: object, directory: str, message: str) -> None:
        with pytest.raises(BackupError, match=message):
            engine._materialize_host_supplement(payload, "p1", tmp_path / directory)

    rejected([], "not-object", "malformed")
    missing_timestamp = valid_supplement()
    missing_timestamp.pop("captured_at")
    rejected(missing_timestamp, "missing-time", "timestamp")

    non_file = valid_supplement()
    non_file["files"].append("not-an-object")
    rejected(non_file, "non-file", "file is malformed")
    malformed_item = valid_supplement()
    malformed_item["files"].append(
        {"path": "host-state/mcp-observability.json", "content_base64": "e30=", "sha256": "bad"}
    )
    rejected(malformed_item, "malformed-item", "file is malformed")
    invalid_base64 = valid_supplement()
    invalid_base64["files"].append(
        {
            "path": "host-state/mcp-observability.json",
            "content_base64": "%%%",
            "sha256": "0" * 64,
        }
    )
    rejected(invalid_base64, "invalid-base64", "invalid base64")

    rejected(
        valid_supplement(supplement_item("host-state/mcp-observability.json", b"not-json")),
        "bad-host-json",
        "malformed JSON",
    )
    rejected(
        valid_supplement(supplement_item("host-state/mcp-observability.json", b"1")),
        "scalar-host-json",
        "malformed JSON",
    )
    rejected(
        valid_supplement(supplement_item("binding/project.toml", b"id = [invalid")),
        "bad-binding",
        "binding configuration",
    )
    rejected(
        valid_supplement(supplement_item("binding/project.toml", b'id = "another"\n')),
        "wrong-binding",
        "another project",
    )
    rejected(
        valid_supplement(supplement_item("binding/remote-credential.token", b" \n")),
        "empty-remote-token",
        "remote credential is empty",
    )

    missing_secret = valid_supplement()
    missing_secret["files"] = [
        entry for entry in missing_secret["files"] if entry["path"] != "secrets/dduo.env"
    ]
    rejected(missing_secret, "missing-secret", "omitted dDuo secrets")

    inconsistent_included = valid_supplement()
    inconsistent_included["codex_auth"]["included"] = False
    rejected(inconsistent_included, "wrong-included", "metadata is inconsistent")
    inconsistent_store = valid_supplement()
    inconsistent_store["codex_auth"]["credential_store"] = "unavailable"
    rejected(inconsistent_store, "wrong-store", "metadata is inconsistent")

    malformed_auth = valid_supplement()
    malformed_auth["files"][1] = supplement_item("secrets/codex/auth.json", b"not-json")
    rejected(malformed_auth, "bad-auth-json", "auth.json")
    scalar_auth = valid_supplement()
    scalar_auth["files"][1] = supplement_item("secrets/codex/auth.json", b"[]")
    rejected(scalar_auth, "scalar-auth", "auth.json")


def test_host_supplement_rejects_oversized_materialized_file(monkeypatch, tmp_path: Path):
    engine = backup_service.BackupEngine(settings(tmp_path), collection_resolver=lambda value: value)
    monkeypatch.setattr(backup_service, "MAX_SUPPLEMENT_FILE_BYTES", 3)
    with pytest.raises(BackupError, match="too large"):
        engine._materialize_host_supplement(
            valid_supplement(),
            "p1",
            tmp_path / "oversized",
        )


def test_portable_secret_envelope_rejects_invalid_or_missing_values(tmp_path: Path):
    engine = backup_service.BackupEngine(
        settings(tmp_path, openai_api_key=""),
        collection_resolver=lambda value: value,
    )
    secret = tmp_path / "secrets.env"
    for content, message in (
        ("NO_SEPARATOR", "invalid"),
        ("UNSUPPORTED=value\n", "invalid"),
        ("OPENAI_API_KEY=one\nOPENAI_API_KEY=two\n", "invalid"),
        ("# comment\n\n", "omitted OPENAI_API_KEY"),
    ):
        secret.write_text(content)
        with pytest.raises(BackupError, match=message):
            engine._normalize_portable_secrets(secret)


def test_host_supplement_fetch_enforces_transport_type_and_size(monkeypatch, tmp_path: Path):
    secret = b"OPENAI_API_KEY=host-key\n"
    payload = json.dumps(
        {
            "version": 1,
            "project_id": "p1",
            "captured_at": "2026-08-28T00:00:00+00:00",
            "files": [
                {
                    "path": "secrets/dduo.env",
                    "content_base64": base64.b64encode(secret).decode(),
                    "sha256": sha256(secret).hexdigest(),
                }
            ],
            "codex_auth": {"included": False, "credential_store": "unavailable"},
            "credentials_complete": False,
            "warnings": ["codex_auth_unavailable"],
        }
    ).encode()
    engine = backup_service.BackupEngine(
        settings(
            tmp_path,
            openai_api_key="fallback",
            cli_bridge_url="http://host.docker.internal:1234",
            cli_bridge_token="token",
        )
    )

    class Response:
        def __init__(
            self,
            content=payload,
            content_type="application/json",
            status_error=None,
            content_length=None,
        ):
            self.content = content
            self.headers = {
                "content-type": content_type,
                "content-length": str(len(content)) if content_length is None else content_length,
            }
            self.status_error = status_error

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            if self.status_error:
                raise self.status_error

        def iter_bytes(self):
            yield self.content

    class Client:
        response = Response()

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, endpoint, **kwargs):
            assert method == "GET" and endpoint.endswith("/v1/backup/supplement")
            assert kwargs["params"] == {"project_id": "p1"}
            assert kwargs["headers"]["Authorization"] == "Bearer token"
            return self.response

    monkeypatch.setattr(backup_service.httpx, "Client", Client)
    files, metadata, warnings = engine._fetch_host_supplement("p1", tmp_path / "fetched")
    assert files["secrets/dduo.env"].is_file()
    assert not metadata["complete"] and warnings == ["codex_auth_unavailable"]

    Client.response = Response(content_type="text/plain")
    with pytest.raises(BackupError, match="unsupported type"):
        engine._fetch_host_supplement("p1", tmp_path / "wrong-type")
    Client.response = Response(status_error=httpx.ConnectError("offline"))
    with pytest.raises(BackupError, match="supplement failed"):
        engine._fetch_host_supplement("p1", tmp_path / "offline")
    Client.response = Response(content_length="invalid")
    with pytest.raises(BackupError, match="size is invalid"):
        engine._fetch_host_supplement("p1", tmp_path / "invalid-size")
    Client.response = Response(content_length=str(backup_service.MAX_SUPPLEMENT_BYTES + 1))
    with pytest.raises(BackupError, match="too large"):
        engine._fetch_host_supplement("p1", tmp_path / "declared-too-large")
    Client.response = Response(content=b"not-json")
    with pytest.raises(BackupError, match="malformed"):
        engine._fetch_host_supplement("p1", tmp_path / "malformed")
    monkeypatch.setattr(backup_service, "MAX_SUPPLEMENT_BYTES", 4)
    Client.response = Response(content=b"12345", content_length="0")
    with pytest.raises(BackupError, match="too large"):
        engine._fetch_host_supplement("p1", tmp_path / "stream-too-large")
    unavailable = backup_service.BackupEngine(settings(tmp_path, cli_bridge_token=""))
    with pytest.raises(BackupError, match="unavailable"):
        unavailable._fetch_host_supplement("p1", tmp_path / "unavailable")


def test_runtime_settings_and_portable_history_export(tmp_path: Path, monkeypatch):
    engine = backup_service.BackupEngine(settings(tmp_path))
    runtime = tmp_path / "runtime.json"
    engine._write_runtime_settings(runtime, app_version="1.2.3")
    value = json.loads(runtime.read_text())
    assert value["app_version"] == "1.2.3"
    assert value["settings"]["embedding_model"] == engine.settings.embedding_model
    assert "openai_api_key" not in value["settings"]

    history = tmp_path / "history.json"
    monkeypatch.setattr(
        engine,
        "_run_capture",
        lambda *args, **kwargs: '[{"id":"b1","project_id":"p1","status":"verified"}]\n',
    )
    assert engine._export_backup_history("p1", history) is None
    assert json.loads(history.read_text())[0]["id"] == "b1"
    monkeypatch.setattr(engine, "_run_capture", lambda *args, **kwargs: "not-json")
    warning = engine._export_backup_history("p1", history)
    assert "omitted" in warning and not history.exists()
    monkeypatch.setattr(
        engine,
        "_run_capture",
        lambda *args, **kwargs: '[{"id":"b2","project_id":"another"}]',
    )
    warning = engine._export_backup_history("p1", history)
    assert "omitted" in warning and not history.exists()


def test_backup_database_scope_must_be_exact_and_history_is_project_filtered(
    tmp_path: Path, monkeypatch
):
    engine = backup_service.BackupEngine(settings(tmp_path))
    calls: list[list[str]] = []

    def capture(command, **kwargs):
        calls.append(command)
        return '["p1"]\n'

    monkeypatch.setattr(engine, "_run_capture", capture)
    engine._validate_single_project_database("p1")
    assert "FROM projects" in calls[0][-1]

    monkeypatch.setattr(engine, "_run_capture", lambda *args, **kwargs: '["p1","p2"]')
    with pytest.raises(BackupError, match="not exactly"):
        engine._validate_single_project_database("p1")
    monkeypatch.setattr(engine, "_run_capture", lambda *args, **kwargs: "not-json")
    with pytest.raises(BackupError, match="validate"):
        engine._validate_single_project_database("p1")

    captured: dict[str, object] = {}

    def history(command, **kwargs):
        captured["command"] = command
        captured["input_text"] = kwargs.get("input_text")
        return "[]"

    monkeypatch.setattr(engine, "_run_capture", history)
    assert engine._export_backup_history("p1", tmp_path / "history.json") is None
    command = captured["command"]
    assert isinstance(command, list)
    assert ["--set", "ON_ERROR_STOP=on", "--set", "project_id=p1"] == command[
        command.index("--set") :
    ][:4]
    assert command[-1] == "--file=-"
    assert "project_id = :'project_id'" in str(captured["input_text"])


def test_backup_history_sql_error_is_not_silently_accepted(tmp_path: Path, monkeypatch):
    engine = backup_service.BackupEngine(settings(tmp_path))
    history = tmp_path / "history.json"

    def run(command, **kwargs):
        assert command[0] == "psql"
        assert "FROM backup_records" in kwargs["input"]
        error_stop = "ON_ERROR_STOP=on" in command
        return SimpleNamespace(
            returncode=3 if error_stop else 0,
            stderr="ERROR: relation backup_records does not exist",
            stdout="",
        )

    monkeypatch.setattr(backup_service.subprocess, "run", run)
    warning = engine._export_backup_history("p1", history)
    assert warning is not None and "psql failed" in warning
    assert not history.exists()


def test_restore_index_uploads_snapshot(tmp_path: Path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.write_bytes(b"index")
    captured = {}

    class Response:
        def raise_for_status(self):
            captured["raised"] = True

    def post(endpoint, **kwargs):
        captured.update(endpoint=endpoint, **kwargs)
        return Response()

    monkeypatch.setattr(restore_index.httpx, "post", post)
    monkeypatch.setattr(
        restore_index,
        "get_settings",
        lambda: SimpleNamespace(qdrant_url="http://qdrant:6333"),
    )
    restore_index.restore_snapshot(snapshot, "collection/one")
    assert "collection%2Fone" in captured["endpoint"] and captured["raised"]
    with pytest.raises(FileNotFoundError):
        restore_index.restore_snapshot(tmp_path / "missing", "collection")
