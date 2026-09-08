from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from dduo_solo_founder import cli_bridge, launcher, project_config, project_secrets
from dduo_solo_founder.client_installation import ClientInstallationError


@pytest.fixture
def setup_host(monkeypatch, tmp_path):
    """Exercise real isolated identities/secrets, never the maintainer's services."""
    root = tmp_path / "new-project"
    root.mkdir()
    host = SimpleNamespace(
        root=root,
        browser=[],
        commands=[],
        probes=[],
        docker=True,
        hooks=True,
        authenticated=False,
        services_ready=False,
        post_init_status=None,
    )

    def status(selected_root):
        assert selected_root == root
        host.probes.append(selected_root)
        if host.commands and host.post_init_status is not None:
            return host.post_init_status
        try:
            project = project_config.load_project(root)
        except FileNotFoundError:
            project = None
        key_ready = bool(
            project
            and project_secrets.load_project_secrets(project["id"]).get("OPENAI_API_KEY")
        )
        auth_ready = host.authenticated or bool(
            project and (project_secrets.project_codex_home(project["id"]) / "auth.json").is_file()
        )
        ready = host.services_ready and key_ready and auth_ready and host.hooks
        return {
            "project": {"ready": host.services_ready},
            "embeddings": {"ready": key_ready},
            "docker": {"ready": host.docker},
            "codex_hooks": {"ready": host.hooks},
            "clients": {"codex": {"ready": auth_ready}, "claude": {"ready": False}},
            "ready_for_client": {"codex": ready},
        }

    def missing_runtime(*_args, **_kwargs):
        raise ClientInstallationError("synthetic runtime unavailable")

    def run(command, **kwargs):
        host.commands.append((command, kwargs))
        host.services_ready = True
        return SimpleNamespace(returncode=0, stdout="private output", stderr="private stderr")

    monkeypatch.setattr(launcher, "get_setup_status", status)
    monkeypatch.setattr(project_config, "is_git_worktree", lambda _root: False)
    monkeypatch.setattr(launcher, "resolve_client_installation", missing_runtime)
    monkeypatch.setattr(launcher, "setup_url", lambda _root: "http://127.0.0.1/test-setup")
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: host.browser.append(url) or True)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    return host


def configured_source(tmp_path, name="source", key="sk-synthetic-embedding-key"):
    root = tmp_path / name
    root.mkdir()
    project_config.new_project_config(root, name)
    project = project_config.load_project(root)
    project_secrets.save_project_secrets(
        project["id"],
        {"OPENAI_API_KEY": key, "DDUO_DATABASE_PASSWORD": "source-only-database-password"},
    )
    return root, project


def test_first_call_offers_one_choice_per_key_without_writes_or_browser(setup_host, tmp_path):
    _, source = configured_source(tmp_path)
    _, same_key = configured_source(tmp_path, "same-key")
    registry_before = project_config.REGISTRY_PATH.read_bytes()
    secrets_before = {
        str(path): path.read_bytes()
        for path in project_secrets.PROJECT_SECRETS_DIR.rglob("*")
        if path.is_file()
    }

    result = launcher.start_local_setup(setup_host.root, provider="codex")

    assert result["opened"] is False
    assert result["requires_key_choice"] is True
    assert len(result["key_choices"]) == 1
    assert result["key_choices"][0] in [
        {"project_id": project["id"], "name": project["name"]}
        for project in (source, same_key)
    ]
    assert not (setup_host.root / project_config.CONFIG_PATH).exists()
    assert project_config.REGISTRY_PATH.read_bytes() == registry_before
    assert {
        str(path): path.read_bytes()
        for path in project_secrets.PROJECT_SECRETS_DIR.rglob("*")
        if path.is_file()
    } == secrets_before
    assert setup_host.browser == setup_host.commands == []
    assert "sk-synthetic" not in json.dumps(result)


def test_reuse_copies_only_embedding_and_imports_selected_profile_before_browserless_init(
    setup_host, monkeypatch, tmp_path,
):
    _, source = configured_source(tmp_path)
    interactive = tmp_path / "selected-codex-profile"
    interactive.mkdir(mode=0o700)
    auth = '{"synthetic_account":"selected-interactive-profile"}'
    (interactive / "auth.json").write_text(auth)
    (interactive / "auth.json").chmod(0o600)
    monkeypatch.setattr(
        launcher, "resolve_client_installation",
        lambda provider: SimpleNamespace(config_dir=interactive),
    )

    result = launcher.start_local_setup(
        setup_host.root, provider="codex", reuse_key_from_project=source["id"],
    )

    destination = project_config.load_project(setup_host.root)
    assert project_secrets.load_project_secrets(destination["id"]) == {
        "OPENAI_API_KEY": "sk-synthetic-embedding-key",
    }
    assert (project_secrets.project_codex_home(destination["id"]) / "auth.json").read_text() == auth
    assert setup_host.browser == []
    assert result["opened"] is False
    assert result["status"]["ready_for_client"]["codex"] is True
    assert len(setup_host.commands) == 1
    command, options = setup_host.commands[0]
    assert command == [
        launcher.sys.executable, "-m", "dduo_solo_founder.launcher", "init",
        "--yes", "--project-root", str(setup_host.root),
    ]
    assert "--headless" not in command
    assert options == {"capture_output": True, "text": True, "timeout": 210, "check": False}
    assert "private output" not in json.dumps(result)


def test_successful_init_returns_actual_post_init_readiness(setup_host, tmp_path):
    _, source = configured_source(tmp_path)
    setup_host.authenticated = True
    setup_host.post_init_status = {
        "project": {"ready": False},
        "ready_for_client": {"codex": False},
        "memory_status": {"state": "connection_required"},
    }

    result = launcher.start_local_setup(
        setup_host.root, provider="codex", reuse_key_from_project=source["id"],
    )

    assert result["status"] == setup_host.post_init_status
    assert result["status"]["ready_for_client"]["codex"] is False
    assert len(setup_host.commands) == 1


def test_new_key_opens_page_with_identity_already_prepared(setup_host, tmp_path):
    configured_source(tmp_path)

    result = launcher.start_local_setup(setup_host.root, provider="codex", use_new_key=True)

    project = project_config.load_project(setup_host.root)
    assert project_config.registered_project_root(project["id"]) == setup_host.root
    assert project_secrets.load_project_secrets(project["id"]) == {}
    assert result == {"opened": True}
    assert setup_host.browser == ["http://127.0.0.1/test-setup"]
    assert setup_host.commands == []


def test_existing_project_repair_never_imports_interactive_auth(setup_host, monkeypatch):
    project_config.new_project_config(setup_host.root)
    project = project_config.load_project(setup_host.root)
    project_secrets.save_project_secrets(project["id"], {"OPENAI_API_KEY": "sk-existing-key"})
    monkeypatch.setattr(
        launcher, "ensure_project_codex_home",
        lambda *_args, **_kwargs: pytest.fail("Repair must not import the ambient account"),
    )

    result = launcher.start_local_setup(setup_host.root, provider="codex")

    assert result == {"opened": True}
    assert setup_host.browser == ["http://127.0.0.1/test-setup"]
    assert setup_host.commands == []


def test_existing_memory_host_can_still_open_its_setup_page(setup_host):
    project_config.new_project_config(setup_host.root)
    config_path = setup_host.root / project_config.CONFIG_PATH
    config_path.write_text(config_path.read_text() + 'deployment = "remote"\n')
    original = config_path.read_bytes()

    launcher.open_setup(setup_host.root)

    assert setup_host.browser == ["http://127.0.0.1/test-setup"]
    assert setup_host.commands == []
    assert config_path.read_bytes() == original


@pytest.mark.parametrize("unavailable", ["authentication", "hooks", "docker"])
def test_missing_prerequisite_opens_only_needed_setup(setup_host, tmp_path, unavailable):
    _, source = configured_source(tmp_path)
    setup_host.authenticated = unavailable != "authentication"
    setup_host.hooks = unavailable != "hooks"
    setup_host.docker = unavailable != "docker"

    result = launcher.start_local_setup(
        setup_host.root, provider="codex", reuse_key_from_project=source["id"],
    )

    assert result == {"opened": True}
    assert setup_host.browser == ["http://127.0.0.1/test-setup"]
    assert setup_host.commands == []


@pytest.mark.parametrize("failure", ["missing_runtime", "timeout", "nonzero_exit"])
def test_activation_failure_keeps_secret_and_returns_safe_repair(
    setup_host, monkeypatch, tmp_path, failure,
):
    _, source = configured_source(tmp_path)
    setup_host.authenticated = True

    def fail(command, **kwargs):
        setup_host.commands.append((command, kwargs))
        if failure == "missing_runtime":
            raise FileNotFoundError("sensitive installation path")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 210, output="sensitive process output")
        return SimpleNamespace(returncode=1, stdout="sensitive logs", stderr="sensitive error")

    monkeypatch.setattr(launcher.subprocess, "run", fail)

    result = launcher.start_local_setup(
        setup_host.root, provider="codex", reuse_key_from_project=source["id"],
    )

    assert result == {"opened": True, "activation_failed": True}
    assert setup_host.browser == ["http://127.0.0.1/test-setup"]
    destination = project_config.load_project(setup_host.root)
    assert project_secrets.load_project_secrets(destination["id"])["OPENAI_API_KEY"]
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["copied", "remote"])
def test_setup_rejects_copied_or_remote_identity_without_mutation(setup_host, tmp_path, kind):
    source_root, source = configured_source(tmp_path)
    target_config = setup_host.root / project_config.CONFIG_PATH
    target_config.parent.mkdir()
    if kind == "copied":
        shutil.copyfile(source_root / project_config.CONFIG_PATH, target_config)
    else:
        target_config.write_text(f'id = "{source["id"]}"\nbinding = "remote"\n')
    before_config = target_config.read_bytes()
    before_registry = project_config.REGISTRY_PATH.read_bytes()
    before_secrets = project_secrets.project_env_file(source["id"]).read_bytes()

    with pytest.raises((ValueError, RuntimeError)):
        launcher.start_local_setup(setup_host.root, provider="codex", use_new_key=True)

    assert target_config.read_bytes() == before_config
    assert project_config.REGISTRY_PATH.read_bytes() == before_registry
    assert project_secrets.project_env_file(source["id"]).read_bytes() == before_secrets
    assert setup_host.commands == setup_host.browser == []


def test_key_choices_are_mutually_exclusive_before_any_status_check(setup_host):
    with pytest.raises(ValueError, match="either"):
        launcher.start_local_setup(
            setup_host.root, reuse_key_from_project="source", use_new_key=True,
        )
    assert setup_host.probes == setup_host.commands == setup_host.browser == []
    assert not (setup_host.root / project_config.CONFIG_PATH).exists()


def test_browser_save_on_virgin_folder_prepares_identity_without_starting_services(setup_host):
    service = cli_bridge.SetupService()

    service.save_openai_key("sk-test-new-project-key", str(setup_host.root))

    project = project_config.load_project(setup_host.root)
    assert project_config.registered_project_root(project["id"]) == setup_host.root
    assert project_secrets.load_project_secrets(project["id"]) == {
        "OPENAI_API_KEY": "sk-test-new-project-key",
    }
    assert setup_host.browser == setup_host.commands == []


def test_status_poll_on_virgin_folder_never_prepares_identity(setup_host, monkeypatch):
    service = cli_bridge.SetupService()
    monkeypatch.setattr(
        service, "_subscription_status",
        lambda *_args, **_kwargs: SimpleNamespace(as_dict=lambda: {"ready": False}),
    )
    monkeypatch.setattr(service, "_codex_hook_status", lambda _root: None)
    monkeypatch.setattr(service, "_docker_status", lambda: {"ready": True})
    monkeypatch.setattr(service, "claude_telemetry_status", lambda _root: {"ready": False})

    result = service.status(str(setup_host.root))

    assert result["project"]["ready"] is False
    assert result["embeddings"]["ready"] is False
    assert not (setup_host.root / project_config.CONFIG_PATH).exists()
    assert not project_config.REGISTRY_PATH.exists()
    assert not project_secrets.PROJECT_SECRETS_DIR.exists()
    assert setup_host.commands == setup_host.browser == []


@pytest.mark.parametrize("invalid", ["short", "sk-synthetic\nOTHER=value", "sk-synthetic\r", "sk-synthetic\x00"])
def test_browser_rejects_invalid_key_before_creating_identity(setup_host, invalid):
    with pytest.raises(ValueError, match="valid OpenAI"):
        cli_bridge.SetupService().save_openai_key(invalid, str(setup_host.root))
    assert not (setup_host.root / project_config.CONFIG_PATH).exists()
    assert setup_host.browser == setup_host.commands == []


@pytest.mark.parametrize("kind", ["copied", "remote"])
def test_browser_save_never_writes_into_copied_or_remote_project(setup_host, tmp_path, kind):
    source_root, source = configured_source(tmp_path)
    target_config = setup_host.root / project_config.CONFIG_PATH
    target_config.parent.mkdir()
    if kind == "copied":
        shutil.copyfile(source_root / project_config.CONFIG_PATH, target_config)
    else:
        target_config.write_text(f'id = "{source["id"]}"\nbinding = "remote"\n')
    before = project_secrets.project_env_file(source["id"]).read_bytes()

    with pytest.raises(ValueError):
        cli_bridge.SetupService().save_openai_key("sk-unexpected-replacement", str(setup_host.root))

    assert project_secrets.project_env_file(source["id"]).read_bytes() == before
    assert setup_host.browser == setup_host.commands == []
