from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dduo_solo_founder import project_secrets, runtime_settings
from dduo_solo_founder.backup import BackupError
from dduo_solo_founder.config import Settings


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "project-secrets"
    monkeypatch.setattr(project_secrets, "PROJECT_SECRETS_DIR", root)
    return root


def snapshot(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "runtime-settings.json"
    runtime_settings.write_runtime_settings(
        path,
        Settings(
            embedding_model="portable-model",
            retrieval_episode_limit=7,
            sleep_poll_seconds=3.5,
            artifact_max_bytes=2_000_000,
            backup_retention_daily=11,
            backup_retention_weekly=5,
            backup_retention_monthly=9,
            backup_auto_seconds=123,
            **overrides,
        ),
        app_version="0.1.0-test",
    )
    return path


def test_runtime_settings_round_trip_is_project_scoped_and_compose_ready(
    isolated_runtime: Path, tmp_path: Path
):
    source = snapshot(tmp_path)
    restored = runtime_settings.restore_project_runtime_settings(source, "project-a")
    assert restored.path.is_relative_to(isolated_runtime)
    if os.name != "nt":
        assert restored.path.stat().st_mode & 0o777 == 0o600
    assert restored.environment["EMBEDDING_MODEL"] == "portable-model"
    assert restored.environment["RETRIEVAL_EPISODE_LIMIT"] == "7"
    assert restored.environment["SLEEP_POLL_SECONDS"] == "3.5"
    assert restored.environment["BACKUP_AUTO_SECONDS"] == "123"
    assert restored.backup_options == {
        "include_qdrant": True,
        "daily": 11,
        "weekly": 5,
        "monthly": 9,
        "auto_seconds": 123,
    }
    assert runtime_settings.load_project_runtime_environment("project-a") == (restored.environment)
    assert runtime_settings.load_project_runtime_environment("project-b") == {}


def test_runtime_settings_reject_unknown_truncated_and_invalid_values(tmp_path: Path):
    path = snapshot(tmp_path)
    payload = json.loads(path.read_text())

    unknown = json.loads(json.dumps(payload))
    unknown["settings"]["DATABASE_URL"] = "postgresql://foreign"
    with pytest.raises(BackupError, match="unsupported"):
        runtime_settings.validate_runtime_settings_payload(unknown)

    truncated = json.loads(json.dumps(payload))
    truncated["settings"].pop("embedding_model")
    with pytest.raises(BackupError, match="missing"):
        runtime_settings.validate_runtime_settings_payload(truncated)

    invalid = json.loads(json.dumps(payload))
    invalid["settings"]["task_index_max_utf8_bytes"] = 50_000
    with pytest.raises(BackupError, match="failed validation"):
        runtime_settings.validate_runtime_settings_payload(invalid)

    wrong_type = json.loads(json.dumps(payload))
    wrong_type["settings"]["backup_include_qdrant"] = "true"
    with pytest.raises(BackupError, match="invalid value"):
        runtime_settings.validate_runtime_settings_payload(wrong_type)


def test_runtime_settings_accept_previous_v1_shape_with_safe_defaults(tmp_path: Path):
    path = snapshot(tmp_path)
    payload = json.loads(path.read_text())
    for key in (
        "sleep_poll_seconds",
        "sleep_cli_timeout_seconds",
        "artifact_max_bytes",
        "backup_auto_seconds",
    ):
        payload["settings"].pop(key)
    _, normalized = runtime_settings.validate_runtime_settings_payload(payload)
    assert normalized["sleep_poll_seconds"] == Settings.model_validate({}).sleep_poll_seconds
    assert normalized["backup_auto_seconds"] == Settings.model_validate({}).backup_auto_seconds


def test_runtime_environment_file_rejects_tampering(isolated_runtime: Path):
    path = project_secrets.project_runtime_env_file("project-a")
    path.parent.mkdir(parents=True)
    path.write_text("DATABASE_URL=must-not-load\n")
    with pytest.raises(RuntimeError, match="malformed"):
        runtime_settings.load_project_runtime_environment("project-a")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"schema_version": 9, "app_version": "v", "settings": {}},
        {"schema_version": 1, "app_version": "", "settings": {}},
        {"schema_version": 1, "app_version": "v", "settings": []},
    ],
)
def test_runtime_settings_reject_malformed_envelopes(payload):
    with pytest.raises(BackupError, match="malformed"):
        runtime_settings.validate_runtime_settings_payload(payload)


def test_runtime_settings_reject_nonfinite_and_out_of_range_values(tmp_path: Path):
    payload = json.loads(snapshot(tmp_path).read_text())

    for key, value, message in (
        ("sleep_idle_seconds", 0, "out-of-range"),
        ("retrieval_episode_limit", -1, "out-of-range"),
        ("retrieval_similarity_threshold", 1.1, "invalid value"),
        ("task_retrieval_similarity_threshold", float("nan"), "invalid value"),
        ("embedding_model", "bad\nmodel", "invalid value"),
    ):
        changed = json.loads(json.dumps(payload))
        changed["settings"][key] = value
        with pytest.raises(BackupError, match=message):
            runtime_settings.validate_runtime_settings_payload(changed)


def test_runtime_settings_reader_and_environment_parser_fail_closed(
    isolated_runtime: Path, tmp_path: Path, monkeypatch
):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(BackupError, match="unreadable or malformed"):
        runtime_settings.read_runtime_settings(malformed)

    with pytest.raises(RuntimeError, match="malformed"):
        runtime_settings._values_from_environment({"UNKNOWN_SETTING": "1"})
    with pytest.raises(RuntimeError, match="malformed"):
        runtime_settings._values_from_environment(
            {"DDUO_SOLO_FOUNDER_BACKUP_INCLUDE_QDRANT": "yes"}
        )

    path = project_secrets.project_runtime_env_file("project-a")
    path.parent.mkdir(parents=True)
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed"):
        runtime_settings.load_project_runtime_environment("project-a")

    path.write_text("EMBEDDING_MODEL=model\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime_settings.load_project_runtime_environment("project-a")

    original_runtime_reader = runtime_settings.load_project_runtime_environment_text
    monkeypatch.setattr(
        runtime_settings,
        "load_project_runtime_environment_text",
        lambda _project_id: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(RuntimeError, match="unreadable"):
        runtime_settings.load_project_runtime_environment("project-a")
    monkeypatch.setattr(
        runtime_settings,
        "load_project_runtime_environment_text",
        original_runtime_reader,
    )

    valid = runtime_settings.runtime_environment(
        runtime_settings.read_runtime_settings(snapshot(isolated_runtime.parent))
    )
    valid["TASK_INDEX_MAX_UTF8_BYTES"] = "50000"
    project_secrets.save_project_runtime_environment("project-a", valid)
    with pytest.raises(RuntimeError, match="malformed"):
        runtime_settings.load_project_runtime_environment("project-a")


def test_runtime_environment_is_private_and_never_follows_symlinks(
    isolated_runtime: Path, tmp_path: Path
):
    valid = runtime_settings.runtime_environment(
        runtime_settings.read_runtime_settings(snapshot(tmp_path))
    )
    path = project_secrets.save_project_runtime_environment("project-a", valid)
    path.chmod(0o666)
    path.parent.chmod(0o777)
    isolated_runtime.chmod(0o777)
    assert runtime_settings.load_project_runtime_environment("project-a") == valid
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert isolated_runtime.stat().st_mode & 0o777 == 0o700

    path.unlink()
    outside = tmp_path / "outside-runtime.env"
    outside.write_text("do-not-touch\n")
    outside.chmod(0o600)
    path.symlink_to(outside)
    with pytest.raises(RuntimeError, match="unreadable"):
        runtime_settings.load_project_runtime_environment("project-a")
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.save_project_runtime_environment("project-a", valid)
    assert outside.read_text() == "do-not-touch\n"
