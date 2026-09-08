from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import assert_private_file
from dduo_solo_founder import backup_config
from dduo_solo_founder.backup import generate_recovery_key


def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "config/backups.json", tmp_path / "config/keys"


def test_configure_preserves_and_explicitly_replaces_project_key(tmp_path: Path):
    registry, keys = paths(tmp_path)
    project = {"id": "project_1", "name": "Example Project"}
    settings, key, is_new = backup_config.configure_project_backup(
        project, tmp_path / "synced", registry_path=registry, key_dir=keys
    )
    assert is_new and Path(settings["destination"]).is_dir()
    assert settings["destination"].endswith("example-project-project_")
    assert backup_config.read_recovery_key("project_1", keys) == key
    assert_private_file(registry)
    assert_private_file(keys / "project_1.key")

    second, same_key, second_is_new = backup_config.configure_project_backup(
        project,
        tmp_path / "other",
        include_qdrant=False,
        daily=1,
        weekly=2,
        monthly=3,
        registry_path=registry,
        key_dir=keys,
    )
    assert not second_is_new and same_key == key and not second["include_qdrant"]
    replacement = generate_recovery_key()
    with pytest.raises(ValueError, match="different recovery key"):
        backup_config.configure_project_backup(
            project,
            tmp_path / "other",
            recovery_key=replacement,
            registry_path=registry,
            key_dir=keys,
        )
    _, new_key, _ = backup_config.configure_project_backup(
        project,
        tmp_path / "exact",
        recovery_key=replacement,
        replace_key=True,
        exact_destination=True,
        registry_path=registry,
        key_dir=keys,
    )
    assert new_key == replacement


def test_backup_registry_validation_and_key_errors(tmp_path: Path):
    registry, keys = paths(tmp_path)
    assert backup_config.load_backup_registry(registry) == {"version": 1, "projects": {}}
    registry.parent.mkdir(parents=True)
    registry.write_text("not json")
    with pytest.raises(RuntimeError, match="invalid"):
        backup_config.load_backup_registry(registry)
    registry.write_text(json.dumps({"version": 2, "projects": []}))
    with pytest.raises(RuntimeError, match="unsupported"):
        backup_config.load_backup_registry(registry)
    with pytest.raises(FileNotFoundError):
        backup_config.read_recovery_key("missing", keys)
    with pytest.raises(ValueError, match="identifier"):
        backup_config.backup_key_path("../unsafe", keys)
    with pytest.raises(ValueError, match="retention"):
        backup_config.configure_project_backup(
            {"id": "p", "name": "P"},
            tmp_path,
            daily=-1,
            registry_path=registry,
            key_dir=keys,
        )


def test_compose_environment_isolated_configured_and_degraded(tmp_path: Path):
    registry, keys = paths(tmp_path)
    root = tmp_path / "project"
    config = root / ".dduo-solo-founder/project.toml"
    config.parent.mkdir(parents=True)
    config.write_text('id = "p1"\n')
    project = {"id": "p1", "name": "P"}
    backup_config.configure_project_backup(
        project,
        tmp_path / "backups",
        registry_path=registry,
        key_dir=keys,
        daily=3,
        auto_seconds=91,
    )
    environment = backup_config.compose_backup_environment(
        project,
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert environment["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED"] == "true"
    assert environment["DDUO_SOLO_FOUNDER_BACKUP_RETENTION_DAILY"] == "3"
    assert environment["BACKUP_AUTO_SECONDS"] == "91"
    assert environment["DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE"].endswith("p1.key")

    Path(environment["DDUO_SOLO_FOUNDER_BACKUP_SOURCE"]).rmdir()
    unavailable = backup_config.compose_backup_environment(
        project,
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert unavailable["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED"] == "false"
    assert "unavailable" in unavailable["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURATION_ERROR"]

    fresh = backup_config.compose_backup_environment(
        {"id": "p2"},
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert fresh["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED"] == "false"
    assert Path(fresh["DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE"]).is_file()

    destination = Path(environment["DDUO_SOLO_FOUNDER_BACKUP_SOURCE"])
    destination.mkdir(parents=True, exist_ok=True)
    (keys / "p1.key").unlink()
    missing_key = backup_config.compose_backup_environment(
        project,
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert missing_key["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED"] == "false"
    assert "missing" in missing_key["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURATION_ERROR"]

    (keys / "p1.key").write_text("invalid")
    invalid_key = backup_config.compose_backup_environment(
        project,
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert invalid_key["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED"] == "false"
    assert "invalid" in invalid_key["DDUO_SOLO_FOUNDER_BACKUP_CONFIGURATION_ERROR"]


def test_compose_environment_includes_restored_project_runtime(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        backup_config,
        "load_project_runtime_environment",
        lambda project_id: {
            "EMBEDDING_MODEL": f"restored-{project_id}",
            "SLEEP_POLL_SECONDS": "4.5",
        },
    )
    root = tmp_path / "project"
    config = root / ".dduo-solo-founder/project.toml"
    config.parent.mkdir(parents=True)
    config.write_text('id = "p1"\n')
    registry, keys = paths(tmp_path)
    environment = backup_config.compose_backup_environment(
        {"id": "p1"},
        root,
        registry_path=registry,
        key_dir=keys,
        fallback_dir=tmp_path / "fallback",
    )
    assert environment["EMBEDDING_MODEL"] == "restored-p1"
    assert environment["SLEEP_POLL_SECONDS"] == "4.5"


def test_configure_rejects_non_positive_automatic_interval(tmp_path: Path):
    registry, keys = paths(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        backup_config.configure_project_backup(
            {"id": "p", "name": "P"},
            tmp_path,
            auto_seconds=0,
            registry_path=registry,
            key_dir=keys,
        )


def test_concurrent_configuration_cannot_publish_two_different_keys(tmp_path: Path):
    registry, keys = paths(tmp_path)
    project = {"id": "same-project", "name": "Same"}

    def configure(_):
        return backup_config.configure_project_backup(
            project,
            tmp_path / "destination",
            registry_path=registry,
            key_dir=keys,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(configure, range(2)))
    assert results[0][1] == results[1][1]
    assert sorted(result[2] for result in results) == [False, True]
    assert backup_config.read_recovery_key("same-project", keys) == results[0][1]
