from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dduo_solo_founder import project_secrets


@pytest.fixture
def isolated_secrets(monkeypatch, tmp_path: Path):
    config = tmp_path / "config"
    monkeypatch.setattr(project_secrets, "CONFIG_DIR", config)
    monkeypatch.setattr(project_secrets, "LEGACY_ENV_FILE", config / "env")
    monkeypatch.setattr(
        project_secrets, "RETIRED_LEGACY_ENV_FILE", config / "env.alpha-retired"
    )
    monkeypatch.setattr(project_secrets, "PROJECT_SECRETS_DIR", config / "project-secrets")
    return config


def test_project_secrets_require_explicit_legacy_migration_and_project_values_win(
    isolated_secrets: Path,
):
    isolated_secrets.mkdir()
    project_secrets.LEGACY_ENV_FILE.write_text(
        "OPENAI_API_KEY=legacy\nUNRELATED_PASSWORD=must-not-copy\n"
    )
    project_id = "11111111-1111-4111-8111-111111111111"
    path = project_secrets.ensure_project_secret_environment(project_id)
    assert path.read_text() == ""
    assert path.stat().st_mode & 0o777 == 0o600

    project_secrets.save_project_secrets(
        project_id,
        {"OPENAI_API_KEY": "project", "UNRELATED_PASSWORD": "ignored"},
    )
    project_secrets.migrate_legacy_project_secrets(project_id)
    assert project_secrets.load_project_secrets(project_id) == {"OPENAI_API_KEY": "project"}


def test_replace_project_secrets_removes_stale_values_and_rejects_foreign_data(
    isolated_secrets: Path,
):
    project_id = "11111111-1111-4111-8111-111111111111"
    project_secrets.save_project_secrets(
        project_id,
        {
            "OPENAI_API_KEY": "old-key",
            "DDUO_DATABASE_PASSWORD": "stale-database-password",
        },
    )
    path = project_secrets.replace_project_secrets(
        project_id,
        {"OPENAI_API_KEY": "restored-key"},
    )
    assert path.read_text() == "OPENAI_API_KEY=restored-key\n"
    assert project_secrets.load_project_secrets(project_id, include_legacy=False) == {
        "OPENAI_API_KEY": "restored-key"
    }
    assert path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(ValueError, match="unsupported"):
        project_secrets.replace_project_secrets(
            project_id,
            {"OPENAI_API_KEY": "new", "SSH_PRIVATE_KEY": "forbidden"},
        )
    assert path.read_text() == "OPENAI_API_KEY=restored-key\n"


def test_pending_manager_bootstrap_is_private_recoverable_and_clearable(
    isolated_secrets: Path,
):
    project_id = "11111111-1111-4111-8111-111111111111"
    device_id = "owner-bootstrap-11111111-1111-1111-1111-111111111111"
    device_token = "dduo_dev_" + "x" * 48
    path = project_secrets.save_pending_manager_bootstrap(
        project_id, device_id=device_id, device_token=device_token
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert project_secrets.load_pending_manager_bootstrap(project_id) == {
        "device_id": device_id,
        "device_token": device_token,
    }
    project_secrets.clear_pending_manager_bootstrap(project_id)
    assert project_secrets.load_pending_manager_bootstrap(project_id) is None

    with pytest.raises(ValueError, match="invalid"):
        project_secrets.save_pending_manager_bootstrap(
            project_id, device_id="../escape", device_token=device_token
        )


def test_codex_home_imports_only_file_auth_and_pins_file_storage(
    monkeypatch, isolated_secrets: Path, tmp_path: Path
):
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    global_auth = fake_home / ".codex/auth.json"
    global_auth.write_text('{"token":"secret"}')
    global_auth.chmod(0o600)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    project_id = "11111111-1111-4111-8111-111111111111"
    home = project_secrets.ensure_project_codex_home(project_id)
    assert (home / "config.toml").read_text() == 'cli_auth_credentials_store = "file"\n'
    assert (home / "auth.json").read_text() == '{"token":"secret"}'
    assert (home / "auth.json").stat().st_mode & 0o777 == 0o600
    environment = project_secrets.codex_environment(project_id, {"PATH": "/bin"})
    assert environment["CODEX_HOME"] == str(home)


def test_secret_project_id_is_required_and_non_uuid_is_hashed(isolated_secrets: Path):
    with pytest.raises(ValueError, match="project id"):
        project_secrets.project_secret_dir(" ")
    assert project_secrets.project_secret_dir("legacy-id").name != "legacy-id"


def test_remote_runtime_secrets_are_generated_once_and_keep_embeddings(
    monkeypatch, isolated_secrets
):
    isolated_secrets.mkdir()
    project_secrets.LEGACY_ENV_FILE.write_text("OPENAI_API_KEY=owner-key\n")
    generated = iter(("database", "auth", "session", "authority", "manager"))
    monkeypatch.setattr(project_secrets.secrets, "token_urlsafe", lambda _: next(generated))
    project_id = "11111111-1111-4111-8111-111111111111"
    project_secrets.migrate_legacy_project_secrets(project_id)
    path = project_secrets.ensure_remote_runtime_secrets(project_id)
    values = project_secrets.parse_secret_environment(path.read_text())
    assert values == {
        "DDUO_AUTH_SIGNING_SECRET": "auth",
        "DDUO_DATABASE_PASSWORD": "database",
        "DDUO_INFRASTRUCTURE_TOKEN": "dduo_dev_manager",
        "DDUO_NODE_AUTHORITY_SECRET": "authority",
        "DDUO_SESSION_SECRET": "session",
        "OPENAI_API_KEY": "owner-key",
    }
    # A second invocation never rotates a running stack's authority or database password.
    project_secrets.ensure_remote_runtime_secrets(project_id)
    assert project_secrets.parse_secret_environment(path.read_text()) == values


def test_pending_bootstrap_and_dotenv_parsers_fail_closed(isolated_secrets: Path):
    project_id = "11111111-1111-4111-8111-111111111111"
    path = project_secrets.pending_manager_bootstrap_file(project_id)
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        project_secrets.load_pending_manager_bootstrap(project_id)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        project_secrets.load_pending_manager_bootstrap(project_id)
    path.write_text('{"device_id":"bad","device_token":"bad"}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        project_secrets.load_pending_manager_bootstrap(project_id)

    assert project_secrets.parse_secret_environment(
        "\n# comment\nmalformed\nFOREIGN=value\nOPENAI_API_KEY= accepted \n"
    ) == {"OPENAI_API_KEY": "accepted"}


def test_existing_secret_store_and_codex_home_are_reused(
    monkeypatch, isolated_secrets: Path, tmp_path: Path
):
    project_id = "11111111-1111-4111-8111-111111111111"
    existing = project_secrets.save_project_secrets(
        project_id, {"OPENAI_API_KEY": "existing"}
    )
    existing.chmod(0o644)
    assert project_secrets.ensure_project_secret_environment(project_id) == existing
    assert existing.stat().st_mode & 0o777 == 0o600

    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    codex_home = project_secrets.ensure_project_codex_home(
        project_id, import_global_auth=False
    )
    config = codex_home / "config.toml"
    config.write_text("custom = true\n")
    assert project_secrets.ensure_project_codex_home(
        project_id, import_global_auth=False
    ) == codex_home
    assert config.read_text() == "custom = true\n"
    assert not (codex_home / "auth.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and symlink contract")
def test_project_secret_store_never_follows_directory_or_file_symlinks(
    isolated_secrets: Path, tmp_path: Path
):
    project_id = "11111111-1111-4111-8111-111111111111"
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    project_secrets.PROJECT_SECRETS_DIR.parent.mkdir(parents=True, exist_ok=True)
    project_secrets.PROJECT_SECRETS_DIR.symlink_to(
        outside_directory, target_is_directory=True
    )

    with pytest.raises(ValueError, match="regular directory"):
        project_secrets.save_project_secrets(project_id, {"OPENAI_API_KEY": "secret"})
    assert list(outside_directory.iterdir()) == []

    project_secrets.PROJECT_SECRETS_DIR.unlink()
    secret_path = project_secrets.project_env_file(project_id)
    secret_path.parent.mkdir(parents=True)
    outside_file = tmp_path / "outside.env"
    outside_file.write_text("OPENAI_API_KEY=outside\n")
    outside_file.chmod(0o600)
    secret_path.symlink_to(outside_file)

    with pytest.raises(ValueError, match="regular file"):
        project_secrets.load_project_secrets(project_id)
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.save_project_secrets(project_id, {"OPENAI_API_KEY": "replacement"})
    assert outside_file.read_text() == "OPENAI_API_KEY=outside\n"

    legacy_outside = tmp_path / "outside-legacy.env"
    legacy_outside.write_text("OPENAI_API_KEY=legacy-outside\n")
    legacy_outside.chmod(0o600)
    project_secrets.LEGACY_ENV_FILE.symlink_to(legacy_outside)
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.load_legacy_secret_environment(project_secrets.LEGACY_ENV_FILE)
    assert legacy_outside.read_text() == "OPENAI_API_KEY=legacy-outside\n"


def test_project_secret_store_rejects_non_regular_files_and_foreign_ownership(
    monkeypatch, isolated_secrets: Path
):
    project_id = "11111111-1111-4111-8111-111111111111"
    secret_path = project_secrets.project_env_file(project_id)
    secret_path.parent.mkdir(parents=True)
    secret_path.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.load_project_secrets(project_id)

    secret_path.rmdir()
    saved = project_secrets.save_project_secrets(
        project_id, {"OPENAI_API_KEY": "owned"}
    )
    if os.name == "posix":
        monkeypatch.setattr(
            project_secrets,
            "_current_uid",
            lambda: saved.stat().st_uid + 1,
        )
        with pytest.raises(PermissionError, match="not owned"):
            project_secrets.load_project_secrets(project_id)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_codex_credentials_reject_symlinked_home_auth_and_import_source(
    monkeypatch, isolated_secrets: Path, tmp_path: Path
):
    project_id = "11111111-1111-4111-8111-111111111111"
    project_directory = project_secrets.project_secret_dir(project_id)
    project_directory.mkdir(parents=True)
    outside_home = tmp_path / "outside-codex"
    outside_home.mkdir()
    project_secrets.project_codex_home(project_id).symlink_to(
        outside_home, target_is_directory=True
    )
    with pytest.raises(ValueError, match="regular directory"):
        project_secrets.ensure_project_codex_home(project_id, import_global_auth=False)
    assert list(outside_home.iterdir()) == []

    project_secrets.project_codex_home(project_id).unlink()
    codex_home = project_secrets.ensure_project_codex_home(
        project_id, import_global_auth=False
    )
    outside_auth = tmp_path / "outside-auth.json"
    outside_auth.write_text('{"token":"outside"}')
    outside_auth.chmod(0o600)
    (codex_home / "auth.json").symlink_to(outside_auth)
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.ensure_project_codex_home(project_id, import_global_auth=False)

    (codex_home / "auth.json").unlink()
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex/auth.json").symlink_to(outside_auth)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    with pytest.raises(ValueError, match="regular file"):
        project_secrets.ensure_project_codex_home(project_id)
    assert not (codex_home / "auth.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_private_path_helpers_reject_relative_escape_and_unsafe_ancestor(
    isolated_secrets: Path, tmp_path: Path
):
    with pytest.raises(ValueError, match="absolute"):
        project_secrets._path_chain(Path("relative"))
    with pytest.raises(ValueError, match="escaped"):
        project_secrets._managed_directory_chain(tmp_path / "outside")

    outside = tmp_path / "outside-config"
    outside.mkdir()
    isolated_secrets.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="directory chain"):
        project_secrets.save_project_secrets("project", {"OPENAI_API_KEY": "secret"})
    assert list(outside.iterdir()) == []


def test_private_helpers_have_a_safe_windows_fallback(
    monkeypatch, isolated_secrets: Path
):
    path = isolated_secrets / "mode-probe"
    path.parent.mkdir(parents=True)
    path.write_text("probe")
    path.chmod(0o644)
    with monkeypatch.context() as context:
        context.setattr(project_secrets.os, "name", "nt")
        assert project_secrets._current_uid() is None
        project_secrets._set_private_mode(path, 0o600)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_directory_validation_detects_mode_failure_and_post_check_race(
    monkeypatch, isolated_secrets: Path
):
    managed = project_secrets.PROJECT_SECRETS_DIR
    managed.mkdir(parents=True)
    managed.chmod(0o755)
    with monkeypatch.context() as context:
        context.setattr(
            project_secrets, "_set_private_mode", lambda _path, _mode: None
        )
        with pytest.raises(PermissionError, match="directory permissions"):
            project_secrets._private_directory(managed)

    managed.chmod(0o700)

    def replace_directory(path: Path, _mode: int) -> None:
        if path == managed:
            path.rmdir()
            path.write_text("swapped")

    with monkeypatch.context() as context:
        context.setattr(project_secrets, "_set_private_mode", replace_directory)
        with pytest.raises(ValueError, match="changed during validation"):
            project_secrets._private_directory(managed)


def test_private_file_validation_covers_missing_mode_and_post_check_race(
    monkeypatch, isolated_secrets: Path
):
    missing = project_secrets.project_env_file("missing")
    with pytest.raises(FileNotFoundError):
        project_secrets._private_file_metadata(missing, missing_ok=False)
    missing.parent.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        project_secrets._private_file_metadata(missing, missing_ok=False)

    path = project_secrets.save_project_secrets(
        "mode", {"OPENAI_API_KEY": "secret"}
    )
    path.chmod(0o644)
    with monkeypatch.context() as context:
        context.setattr(
            project_secrets, "_set_private_mode", lambda _path, _mode: None
        )
        with pytest.raises(PermissionError, match="file permissions"):
            project_secrets._private_file_metadata(path, missing_ok=False)

    path.chmod(0o600)

    def replace_file(candidate: Path, mode: int) -> None:
        os.chmod(candidate, mode, follow_symlinks=False)
        if candidate == path and mode == 0o600:
            candidate.unlink()
            candidate.mkdir()

    with monkeypatch.context() as context:
        context.setattr(project_secrets, "_set_private_mode", replace_file)
        with pytest.raises(ValueError, match="changed during validation"):
            project_secrets._private_file_metadata(path, missing_ok=False)


def test_private_readers_detect_inode_replacement_and_close_descriptor(
    monkeypatch, isolated_secrets: Path, tmp_path: Path
):
    path = project_secrets.save_project_secrets(
        "reader", {"OPENAI_API_KEY": "secret"}
    )
    replacement = tmp_path / "replacement"
    replacement.write_text("replacement")
    replacement.chmod(0o600)
    closed: list[int] = []
    real_close = os.close

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    with monkeypatch.context() as context:
        context.setattr(
            project_secrets.os, "fstat", lambda _descriptor: replacement.stat()
        )
        context.setattr(project_secrets.os, "close", record_close)
        with pytest.raises(ValueError, match="changed during read"):
            project_secrets._read_private_text(path)
    assert len(closed) == 1

    external = tmp_path / "external"
    external.write_text("external")
    external.chmod(0o600)
    closed.clear()
    with monkeypatch.context() as context:
        context.setattr(
            project_secrets.os, "fstat", lambda _descriptor: replacement.stat()
        )
        context.setattr(project_secrets.os, "close", record_close)
        with pytest.raises(ValueError, match="changed during read"):
            project_secrets._read_external_private_text(external)
    assert len(closed) == 1


def test_external_credentials_fail_closed_on_modes_and_validation_race(
    monkeypatch, tmp_path: Path
):
    credential = tmp_path / "credential"
    credential.write_text("secret")
    credential.chmod(0o644)
    with pytest.raises(PermissionError, match="permissions"):
        project_secrets._read_external_private_text(credential)

    with monkeypatch.context() as context:
        context.setattr(
            project_secrets, "_set_private_mode", lambda _path, _mode: None
        )
        with pytest.raises(PermissionError, match="permissions"):
            project_secrets._read_external_private_text(
                credential, repair_permissions=True
            )

    def replace_credential(path: Path, _mode: int) -> None:
        path.unlink()
        path.mkdir()

    with monkeypatch.context() as context:
        context.setattr(project_secrets, "_set_private_mode", replace_credential)
        with pytest.raises(ValueError, match="changed during validation"):
            project_secrets._read_external_private_text(
                credential, repair_permissions=True
            )


def test_atomic_write_closes_descriptor_when_stream_open_fails(
    monkeypatch, isolated_secrets: Path
):
    destination = project_secrets.project_env_file("write-error")
    closed: list[int] = []
    real_close = os.close

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(
        project_secrets.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stream failed")),
    )
    monkeypatch.setattr(project_secrets.os, "close", record_close)
    with pytest.raises(OSError, match="stream failed"):
        project_secrets._atomic_private_write(destination, "secret")
    assert len(closed) == 1
    assert not destination.exists()
    assert not any(path.name.endswith(".tmp") for path in destination.parent.iterdir())


def test_legacy_runtime_and_missing_optional_paths_use_private_boundary(
    monkeypatch, isolated_secrets: Path, tmp_path: Path
):
    project_id = "project-boundaries"
    legacy = project_secrets.LEGACY_ENV_FILE
    isolated_secrets.mkdir()
    legacy.write_text("OPENAI_API_KEY=legacy\n")
    current = project_secrets.save_project_secrets(
        project_id, {"DDUO_SESSION_SECRET": "current"}
    )
    assert project_secrets.load_project_secrets(
        project_id, include_legacy=True
    ) == {
        "DDUO_SESSION_SECRET": "current",
        "OPENAI_API_KEY": "legacy",
    }
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o600
    assert current.exists()

    runtime = project_secrets.save_project_runtime_environment(
        project_id, {"SETTING": "value"}
    )
    assert project_secrets.load_project_runtime_environment_text(project_id) == (
        "SETTING=value\n"
    )
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o600

    legacy.unlink()
    assert project_secrets.load_project_secrets(
        "no-legacy", include_legacy=True
    ) == {}
    project_secrets.migrate_legacy_project_secrets("no-legacy")
    assert project_secrets.load_project_secrets("no-legacy") == {}

    project_secrets.clear_pending_manager_bootstrap("missing")
    monkeypatch.setattr(
        project_secrets,
        "_read_private_text",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert project_secrets.load_pending_manager_bootstrap("missing") is None

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    home = project_secrets.ensure_project_codex_home("without-global-auth")
    assert not (home / "auth.json").exists()
