from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from dduo_solo_founder import launcher
from dduo_solo_founder.bridge_auth import BRIDGE_PROTOCOL_VERSION, project_bridge_token


def test_bridge_token_is_private_and_reused(monkeypatch, tmp_path):
    bridge = tmp_path / "bridge"
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", bridge / "token")
    first = launcher.ensure_bridge_token()
    second = launcher.ensure_bridge_token()
    assert first == second and len(first) > 32
    assert launcher.existing_bridge_token() == first


def test_bridge_starts_once_and_waits_for_authenticated_health(monkeypatch, tmp_path):
    bridge = tmp_path / "bridge"
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", bridge / "token")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", bridge / "pid")
    monkeypatch.setattr(launcher, "BRIDGE_LOG_FILE", bridge / "bridge.log")
    monkeypatch.setattr(launcher, "BRIDGE_LOCK_FILE", bridge / "agent.lock")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", bridge / "port")
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "/bin/dduo-solo-founder-agent")
    states = iter([False, False])
    monkeypatch.setattr(launcher, "bridge_ready", lambda _: next(states))
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: 41999)
    process = SimpleNamespace(pid=123, poll=lambda: None)
    calls = []
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)) or process,
    )
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )
    token = launcher.ensure_cli_bridge()
    assert token and (bridge / "pid").read_text().strip() == "123"
    assert (bridge / "port").read_text().strip() == "41999"
    assert calls[0][0][0] == "/bin/dduo-solo-founder-agent"
    assert calls[0][0][-4:-2] == ["--host", "0.0.0.0"]
    assert calls[0][0][-2:] == ["--port", "41999"]
    assert calls[0][1]["env"]["DDUO_CLI_BRIDGE_TOKEN"] == token
    assert token not in calls[0][0]


def test_bridge_existing_process_is_not_restarted(monkeypatch):
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "bridge_ready", lambda _: True)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    assert launcher.ensure_cli_bridge() == "token"


def test_bridge_shutdown_is_authenticated_and_removes_pid(monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    port_file = tmp_path / "port"
    pid_file.write_text("123")
    port_file.write_text("43567")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", pid_file)
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", port_file)
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "secret")
    seen = []
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda url, **kwargs: seen.append((url, kwargs)) or SimpleNamespace(status_code=202),
    )
    assert launcher.stop_cli_bridge() is True
    assert not pid_file.exists()
    assert port_file.read_text().strip() == "43567"
    assert seen[0][0] == "http://127.0.0.1:43567/shutdown"
    assert seen[0][1]["headers"]["Authorization"] == "Bearer secret"


def test_persistent_vps_bridge_requires_linger_before_install(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(launcher.getpass, "getuser", lambda: "dduo")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="no\n", stderr=""),
    )

    with pytest.raises(RuntimeError, match="sudo loginctl enable-linger dduo"):
        launcher._require_persistent_bridge_host()


def test_persistent_vps_bridge_uses_private_env_and_stable_launcher(
    monkeypatch, tmp_path
):
    bridge = tmp_path / "bridge"
    unit_file = tmp_path / "systemd" / launcher.BRIDGE_SYSTEMD_UNIT_NAME
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", bridge / "token")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", bridge / "port")
    monkeypatch.setattr(launcher, "BRIDGE_SYSTEMD_ENV_FILE", bridge / "agent.env")
    monkeypatch.setattr(launcher, "BRIDGE_SYSTEMD_UNIT_FILE", unit_file)
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda name: "/home/dduo/.local/bin/dduo-solo-founder-agent"
        if name == "dduo-solo-founder-agent"
        else None,
    )
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: False)
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: 43125)
    commands = []
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *args: commands.append(args)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ready = []
    monkeypatch.setattr(
        launcher,
        "_wait_for_persistent_bridge",
        lambda token: ready.append(token),
    )

    token = launcher._install_persistent_bridge()

    unit = unit_file.read_text()
    environment = (bridge / "agent.env").read_text()
    assert "ExecStart=/home/dduo/.local/bin/dduo-solo-founder-agent --host 0.0.0.0" in unit
    assert "--port" not in unit and token not in unit and "43125" not in unit
    assert f"DDUO_CLI_BRIDGE_TOKEN={token}" in environment
    assert "DDUO_CLI_BRIDGE_PORT=43125" in environment
    assert unit_file.stat().st_mode & 0o777 == 0o600
    assert (bridge / "agent.env").stat().st_mode & 0o777 == 0o600
    assert commands == [
        ("daemon-reload",),
        ("enable", "--now", launcher.BRIDGE_SYSTEMD_UNIT_NAME),
    ]
    assert ready == [token]


def test_installed_persistent_bridge_is_started_and_stopped_through_systemd(
    monkeypatch, tmp_path
):
    unit_file = tmp_path / launcher.BRIDGE_SYSTEMD_UNIT_NAME
    unit_file.write_text("[Service]\n")
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "BRIDGE_SYSTEMD_UNIT_FILE", unit_file)
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "token")
    states = iter([False, True])
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: next(states))
    commands = []
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *args: commands.append(args)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "")

    assert launcher.ensure_cli_bridge() == "token"
    assert launcher.stop_cli_bridge() is True
    assert commands == [
        ("start", launcher.BRIDGE_SYSTEMD_UNIT_NAME),
        ("stop", launcher.BRIDGE_SYSTEMD_UNIT_NAME),
    ]


def test_start_stack_skips_compose_for_a_healthy_project(monkeypatch, tmp_path):
    config = {"id": "p1", "api_port": 18123, "web_port": 20123, "root_path": str(tmp_path)}
    monkeypatch.setattr(
        launcher,
        "_runtime_binding",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: True)
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args: (_ for _ in ()).throw(AssertionError("healthy stack must not rebuild")),
    )
    launcher.start_stack(tmp_path, config)


@pytest.mark.parametrize("compose_status", [0, 23])
def test_explicit_build_rebuilds_even_a_healthy_stack(monkeypatch, tmp_path, compose_status):
    config = {"id": "p1", "api_port": 18123, "web_port": 20123}
    monkeypatch.setattr(
        launcher, "_runtime_binding", lambda *_args, **_kwargs: SimpleNamespace(remote=False)
    )
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)
    calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda project, *args: calls.append((project, args)) or SimpleNamespace(returncode=compose_status),
    )
    if compose_status:
        with pytest.raises(launcher.typer.Exit) as raised:
            launcher.start_stack(tmp_path, config, build=True)
        assert raised.value.exit_code == compose_status
    else:
        launcher.start_stack(tmp_path, config, build=True)
    assert calls == [({**config, "root_path": str(tmp_path.resolve())}, ("up", "-d", "--build"))]


def test_remote_stack_never_falls_back_to_docker_and_retired_local_stays_fenced(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "binding": "remote"}
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=True),
    )
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)
    monkeypatch.setattr(
        launcher,
        "ensure_cli_bridge",
        lambda: pytest.fail("remote binding must not start a local bridge"),
    )
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *_args, **_kwargs: pytest.fail("remote binding must not start Docker"),
    )
    assert launcher.start_stack(tmp_path, project) is None

    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: False)
    with pytest.raises(RuntimeError, match="last verified manual"):
        launcher.start_stack(tmp_path, project)

    retired = tmp_path / launcher.RETIRED_NODE_FILE
    retired.parent.mkdir()
    retired.write_text("{}")
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=False),
    )
    with pytest.raises(RuntimeError, match="local memory node was retired"):
        launcher.start_stack(tmp_path, {"id": "p1"})


def test_api_health_uses_binding_transport_and_degrades_on_network_failure(monkeypatch, tmp_path):
    binding = SimpleNamespace(remote=True)
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "binding_from_project", lambda *_args, **_kwargs: binding)

    class Client:
        def __init__(self, response=None, error=None):
            self.response = response
            self.error = error

        def request(self, method, path, **kwargs):
            assert (method, path, kwargs["timeout"]) == ("GET", "/health", 2)
            if self.error:
                raise self.error
            return self.response

    monkeypatch.setattr(
        launcher,
        "_launcher_client",
        lambda _binding: Client(response=SimpleNamespace(status_code=200)),
    )
    assert launcher._api_healthy({"id": "p1"}, tmp_path) is True

    monkeypatch.setattr(
        launcher,
        "_launcher_client",
        lambda _binding: Client(error=launcher.httpx.ConnectError("offline")),
    )
    assert launcher._api_healthy({"id": "p1"}, tmp_path) is False


def test_initial_stack_builds_once_then_waits_for_health(monkeypatch, tmp_path):
    config = {"id": "p1", "api_port": 18123, "web_port": 20123, "root_path": str(tmp_path)}
    calls = []
    health = iter([False, True])
    monkeypatch.setattr(
        launcher,
        "_runtime_binding",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: next(health))
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda project, *args: calls.append((project, args)) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    launcher.start_stack(tmp_path, config, build=True)
    assert calls[0][1] == ("up", "-d", "--build")


def test_agent_port_never_falls_back_to_a_fixed_port(monkeypatch, tmp_path):
    port_file = tmp_path / "port"
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", port_file)
    assert launcher.agent_port() is None
    port_file.write_text("not-a-port")
    assert launcher.agent_port() is None
    with pytest.raises(RuntimeError, match="has not started"):
        launcher.agent_url()
    port_file.write_text("43123")
    assert launcher.agent_url() == "http://127.0.0.1:43123"
    assert launcher.agent_url("host.docker.internal") == "http://host.docker.internal:43123"


def test_persistent_agent_lock_recovers_stale_locks_and_fails_cleanly(monkeypatch, tmp_path):
    bridge = tmp_path / "bridge"
    lock = bridge / "agent.lock"
    bridge.mkdir()
    lock.write_text("stale")
    os.utime(lock, (time.time() - 60, time.time() - 60))
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_LOCK_FILE", lock)
    with launcher._agent_lock(timeout=1):
        assert lock.read_text()
    assert not lock.exists()

    lock.write_text("busy")
    os.utime(lock, (1000, 1000))
    moments = iter([0, 2])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(launcher.time, "time", lambda: 1000)
    with pytest.raises(RuntimeError, match="still starting"):
        with launcher._agent_lock(timeout=1):
            pass


def test_persistent_agent_failure_setup_and_compose_degrade_without_shared_port(monkeypatch, tmp_path):
    from dduo_solo_founder import project_config

    monkeypatch.setattr(project_config, "is_git_worktree", lambda _root: False)
    bridge = tmp_path / "bridge"
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", bridge / "token")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", bridge / "pid")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", bridge / "port")
    monkeypatch.setattr(launcher, "BRIDGE_LOG_FILE", bridge / "bridge.log")
    monkeypatch.setattr(launcher, "BRIDGE_LOCK_FILE", bridge / "agent.lock")
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "bridge_ready", lambda _: False)
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: 42123)
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=7, poll=lambda: 1),
    )
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="could not start"):
        launcher.ensure_cli_bridge()

    opened = []
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "private-token")
    monkeypatch.setattr(launcher, "agent_url", lambda host="127.0.0.1": f"http://{host}:43123")
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"ticket": "one-time"},
        ),
    )
    url = launcher.open_setup(tmp_path)
    assert url.endswith("/setup?ticket=one-time") and opened == [url]
    assert "private-token" not in url

    captured = {}
    monkeypatch.setattr(launcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "agent_port", lambda: None)
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-test")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: captured.update(kwargs) or SimpleNamespace(returncode=0),
    )
    launcher.compose({"id": "p", "api_port": 8100, "web_port": 8200, "root_path": str(tmp_path)}, "ps")
    assert captured["env"]["DDUO_CLI_BRIDGE_URL"] == "http://host.docker.internal:0"
    assert captured["env"]["DDUO_CLI_BRIDGE_TOKEN"] == project_bridge_token("token", "p")
    monkeypatch.setattr(launcher, "agent_port", lambda: 43123)
    launcher.compose({"id": "p", "api_port": 8100, "web_port": 8200, "root_path": str(tmp_path)}, "ps")
    assert captured["env"]["DDUO_CLI_BRIDGE_URL"] == "http://host.docker.internal:43123"
    assert captured["env"]["DDUO_CLI_BRIDGE_TOKEN"] != "token"


def test_bridge_ready_requires_project_scoping_protocol(monkeypatch):
    monkeypatch.setattr(launcher, "agent_url", lambda: "http://127.0.0.1:43123")
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {}),
    )
    assert launcher.bridge_ready("master") is False
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"bridge_protocol_version": BRIDGE_PROTOCOL_VERSION},
        ),
    )
    assert launcher.bridge_ready("master") is True


def test_bridge_health_and_shutdown_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "agent_url", lambda: "http://127.0.0.1:43123")
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(launcher.httpx.HTTPError("offline")),
    )
    assert launcher.bridge_ready("token") is False
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "")
    assert launcher.stop_cli_bridge() is False
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "token")
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(launcher.httpx.HTTPError("offline")),
    )
    assert launcher.stop_cli_bridge() is False
