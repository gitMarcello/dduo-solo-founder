from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

import pytest

from dduo_solo_founder import launcher
from dduo_solo_founder.backup import BackupError


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "history/backup-records.json"
    path.parent.mkdir()
    row = {
        "id": "backup-1", "project_id": "project-1", "trigger": "manual",
        "status": "verified", "archive_name": "archive.dduobackup", "size_bytes": 123,
        "includes_qdrant": True, "retained": True, "source_generation": 0,
        "manifest": {}, "error": None, "created_at": "2026-09-12T10:00:00+00:00",
        "completed_at": None, "verified_at": None,
    }
    path.write_text(json.dumps([row]), encoding="utf-8")
    return path, row


@pytest.mark.parametrize("padding", [0, 100_000])
def test_history_flows_as_quoted_utf8_data_not_sql_or_files(monkeypatch, tmp_path, history, padding):
    path, row = history
    unusual = "caffè 東京 💾 ' \" \\ \t\r\n\\.\n\\! echo unsafe\n'); DROP TABLE backup_records; --"
    row.update(archive_name=unusual, manifest={"note": unusual + "x" * padding}, error=unusual)
    path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    calls = []

    def compose(project, *args, **kwargs):
        calls.append((project, args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher, "compose", compose)
    assert launcher._restore_portable_backup_history(tmp_path, {"id": "project-1"}) == 1
    assert len(calls) == 1
    project, args, kwargs = calls[0]
    assert project["id"] == "project-1"
    assert args[:3] == ("exec", "-T", "postgres")
    assert "--no-psqlrc" in args and "ON_ERROR_STOP=1" in args
    assert "--single-transaction" in args
    statements = [args[index + 1] for index, value in enumerate(args) if value == "--command"]
    assert len(statements) == 3
    create, copy, insert = statements
    assert create == "CREATE TEMP TABLE dduo_backup_history_import (payload jsonb) ON COMMIT DROP"
    assert copy == "COPY dduo_backup_history_import (payload) FROM STDIN WITH (FORMAT csv)"
    assert insert.startswith("WITH rows AS (")
    assert insert.endswith("ON CONFLICT (id) DO NOTHING")
    # COPY must be its own request on psql 16.15; psql owns BEGIN/COMMIT/ROLLBACK.
    assert all(";" not in statement for statement in statements)
    assert "pg_read_file" not in str(args) and unusual not in str(args)
    assert kwargs["capture_output"] is True
    records = list(csv.reader(io.StringIO(kwargs["input_text"], newline="")))
    assert len(records) == 1 and len(records[0]) == 1
    assert json.loads(records[0][0]) == [row]
    assert path.read_bytes() == before
    assert not (tmp_path / "backup-records.restore.json").exists()


def test_history_empty_or_missing_does_not_start_postgres(monkeypatch, tmp_path, history):
    path, _ = history
    monkeypatch.setattr(launcher, "compose", lambda *a, **kw: pytest.fail("no import needed"))
    path.write_text("[]", encoding="utf-8")
    assert launcher._restore_portable_backup_history(tmp_path, {"id": "project-1"}) == 0
    path.unlink()
    assert launcher._restore_portable_backup_history(tmp_path, {"id": "project-1"}) == 0


@pytest.mark.parametrize("change", [
    {"project_id": "another-project"}, {"source_generation": -1},
    {"size_bytes": True}, {"created_at": "bad timestamp"}, {"manifest": []},
])
def test_history_validation_still_precedes_io(monkeypatch, tmp_path, history, change):
    path, row = history
    path.write_text(json.dumps([{**row, **change}]), encoding="utf-8")
    monkeypatch.setattr(launcher, "compose", lambda *a, **kw: pytest.fail("invalid data reached DB"))
    with pytest.raises(BackupError, match="malformed"):
        launcher._restore_portable_backup_history(tmp_path, {"id": "project-1"})


@pytest.mark.parametrize("exit_code", [2, 3])
def test_history_import_error_does_not_report_success_or_echo_data(
    monkeypatch, tmp_path, history, capsys, exit_code,
):
    path, row = history
    secret = "synthetic-private-history-marker"
    row["manifest"] = {"private": secret}
    path.write_text(json.dumps([row]), encoding="utf-8")
    monkeypatch.setattr(launcher, "compose", lambda *a, **kw: SimpleNamespace(
        returncode=exit_code, stderr="COPY context: " + secret, stdout=secret,
    ))
    with pytest.raises(BackupError, match=f"exit code {exit_code}") as error:
        launcher._restore_portable_backup_history(tmp_path, {"id": "project-1"})
    assert secret not in str(error.value)
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize("payload", [None, "", "caffè 東京 💾\n\\.\n"])
def test_compose_stdin_uses_explicit_utf8_without_shell_or_argv_data(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(launcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "find_workspace_root", lambda p: p)
    monkeypatch.setattr(launcher, "load_project_secrets", lambda *a, **kw: {})
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-test")
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "test-token")
    monkeypatch.setattr(launcher, "agent_port", lambda: None)
    monkeypatch.setattr(launcher, "compose_backup_environment", lambda *a: {})
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: calls.append((a, kw))
                        or SimpleNamespace(returncode=0))
    launcher.compose({"id": "project-1", "api_port": 1, "web_port": 2},
                     "exec", "-T", "postgres", "psql", input_text=payload)
    args, options = calls[0]
    assert "shell" not in options
    assert args[0][-4:] == ["exec", "-T", "postgres", "psql"]
    if payload is None:
        assert "input" not in options and "encoding" not in options
        assert options["text"] is False
    else:
        assert options["input"] == payload and options["encoding"] == "utf-8"
        assert options["text"] is True


def test_compose_stdin_cannot_operate_a_remote_binding(monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **kw: pytest.fail("remote Docker"))
    with pytest.raises(RuntimeError, match="remote client binding"):
        launcher.compose({"binding": "remote"}, "exec", input_text="private")
