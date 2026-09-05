from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner
import pytest

from dduo_solo_founder import launcher, project_activation, project_config

runner = CliRunner()


def test_headless_preparation_is_browserless_and_resumable(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "_require_remote_host_resources", lambda: None)
    monkeypatch.setattr(launcher, "open_setup", lambda *_: (_ for _ in ()).throw(AssertionError("browser")))
    project_activation.decline_setup(tmp_path)
    command = ["init", "--headless", "--yes", "--project-root", str(tmp_path)]
    first = runner.invoke(launcher.app, command)
    assert first.exit_code == 5, first.output
    original = project_config.load_project(tmp_path)
    assert not project_activation.setup_declined(tmp_path)
    assert "configure-openai" in first.output
    second = runner.invoke(launcher.app, command)
    assert second.exit_code == 5
    assert project_config.load_project(tmp_path)["id"] == original["id"]
    monkeypatch.setattr(launcher, "embeddings_configured", lambda _: True)
    authentications, starts, posts = [], [], []
    monkeypatch.setattr(launcher, "_require_remote_codex_auth", authentications.append)
    monkeypatch.setattr(launcher, "start_stack", lambda *args, **kwargs: starts.append(args))

    monkeypatch.setattr(launcher, "_api_request", lambda *args, **kwargs: posts.append((args, kwargs)) or {})
    ready = runner.invoke(launcher.app, command)
    assert ready.exit_code == 0, ready.output
    assert authentications == [original["id"]]
    assert len(starts) == len(posts) == 1


def test_headless_init_never_creates_a_remote_checkout_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "remote"})
    monkeypatch.setattr(launcher, "_runtime_binding", lambda *_: SimpleNamespace(remote=True))
    monkeypatch.setattr(launcher, "require_docker", lambda: (_ for _ in ()).throw(AssertionError("Docker")))
    result = runner.invoke(launcher.app, ["init", "--yes", "--headless", "--project-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "no local fallback" in result.output
    assert not (tmp_path / project_config.CONFIG_PATH).exists()


def test_headless_init_rejects_an_undersized_host_before_identity_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "_require_remote_host_resources", lambda: (_ for _ in ()).throw(RuntimeError("undersized")))
    result = runner.invoke(launcher.app, ["init", "--headless", "--yes", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert not (tmp_path / project_config.CONFIG_PATH).exists()


def test_unconfigured_doctor_returns_an_explicit_result(tmp_path):
    result = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    assert json.loads(result.output) == {"project_config": False, "binding_ready": False, "status": "unconfigured"}


def test_remote_preflight_checks_without_creating_a_project(monkeypatch):
    checked = []
    for name in ("_require_persistent_bridge_host", "require_docker", "_require_remote_host_resources"):
        monkeypatch.setattr(launcher, name, lambda name=name: checked.append(name))
    result = runner.invoke(launcher.app, ["remote-preflight"])
    assert result.exit_code == 0
    assert len(checked) == 3
    assert json.loads(result.output) == {"ready": True, "project_created": False}


def test_codex_device_login_uses_project_private_auth(monkeypatch, tmp_path):
    project_config.new_project_config(tmp_path, "Server")
    project = project_config.load_project(tmp_path)
    monkeypatch.setattr(launcher, "resolve_codex_executable", lambda **kwargs: "/usr/bin/codex")
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher, "subscription_auth_status", lambda *args, **kwargs: SimpleNamespace(ready=True))
    result = runner.invoke(launcher.app, ["login-codex", "--device-auth", "--project-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0] == ["/usr/bin/codex", "login", "--device-auth"]
    assert calls[0][1]["env"]["CODEX_HOME"] == str(launcher.project_codex_home(project["id"]))
    assert json.loads(result.output)["ready"] is True


@pytest.mark.parametrize("returncode, authenticated, expected", [(7, False, 7), (0, False, 8), (0, True, 0)])
def test_login_failure_never_reports_ready(monkeypatch, tmp_path, returncode, authenticated, expected):
    project_config.new_project_config(tmp_path, "Login")
    monkeypatch.setattr(launcher, "resolve_codex_executable", lambda **kwargs: "/bin/codex")
    commands = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=returncode))
    monkeypatch.setattr(launcher, "subscription_auth_status", lambda *args, **kwargs: SimpleNamespace(ready=authenticated))
    result = runner.invoke(launcher.app, ["login-codex", "--project-root", str(tmp_path)])
    assert result.exit_code == expected
    assert commands[0] == ["/bin/codex", "login"]
    assert '"ready": true' not in result.output or expected == 0


def test_login_on_remote_client_never_runs_a_local_process(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "p"})
    monkeypatch.setattr(launcher, "_runtime_binding", lambda *_: SimpleNamespace(remote=True))
    monkeypatch.setattr(launcher, "resolve_codex_executable", lambda **kwargs: pytest.fail("local login"))
    result = runner.invoke(launcher.app, ["login-codex", "--project-root", str(tmp_path)])
    assert result.exit_code == 2


def test_decline_command_does_not_initialize_a_project(tmp_path):
    result = runner.invoke(launcher.app, ["decline-setup", "--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert project_activation.setup_declined(tmp_path)
    assert not (tmp_path / project_config.CONFIG_PATH).exists()
