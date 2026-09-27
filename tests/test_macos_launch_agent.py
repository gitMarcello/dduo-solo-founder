"""Per-user macOS supervision and migration of the local host agent."""

from __future__ import annotations

import os
import plistlib
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from dduo_solo_founder import launcher


@pytest.fixture
def macos_agent(monkeypatch, tmp_path):
    bridge = tmp_path / "private bridge"
    service = tmp_path / "LaunchAgents" / "it.dduo.solo-founder.agent.plist"
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", bridge / "token")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", bridge / "port")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", bridge / "pid")
    monkeypatch.setattr(launcher, "BRIDGE_LOCK_FILE", bridge / "agent.lock")
    monkeypatch.setattr(launcher, "BRIDGE_MACOS_AGENT_FILE", service)
    executable = tmp_path / "bin" / "dduo agent"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: 42421)
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "private-token")
    return bridge, service, executable


def test_launch_agent_reads_secrets_at_runtime_and_keeps_them_out_of_plist(macos_agent):
    bridge, _service, executable = macos_agent
    bridge.mkdir(mode=0o700)
    (bridge / "token").write_text("private-token\n", encoding="utf-8")
    (bridge / "port").write_text("42421\n", encoding="utf-8")
    capture = executable.parent / "captured"
    executable.write_text(
        f"#!/bin/sh\nprintf '%s/%s' \"$DDUO_CLI_BRIDGE_TOKEN\" "
        f"\"$DDUO_CLI_BRIDGE_PORT\" > '{capture}'\n",
        encoding="utf-8",
    )
    document = launcher._macos_agent_document(str(executable))
    serialized = plistlib.dumps(document)
    assert b"private-token" not in serialized
    assert b"42421" not in serialized
    assert document["RunAtLoad"] is True
    assert document["KeepAlive"] is True
    assert document["Label"] == launcher.BRIDGE_MACOS_AGENT_LABEL
    subprocess.run(document["ProgramArguments"], check=True, env={"PATH": "/usr/bin:/bin"})
    assert capture.read_text(encoding="utf-8") == "private-token/42421"


def test_healthy_detached_agent_is_migrated_to_login_service_and_restarted(
    macos_agent, monkeypatch
):
    bridge, service, _executable = macos_agent
    bridge.mkdir(mode=0o700)
    (bridge / "port").write_text("42421\n", encoding="utf-8")
    state = {"registered": False, "running": True, "stops": 0}
    calls = []

    def launchctl(*arguments):
        calls.append(arguments)
        if arguments[0] == "print":
            return SimpleNamespace(returncode=0 if state["registered"] else 113)
        if arguments[0] == "bootstrap":
            state.update(registered=True, running=True)
        elif arguments[0] == "kickstart":
            state["running"] = True
        return SimpleNamespace(returncode=0)

    def stop():
        state.update(running=False, stops=state["stops"] + 1)
        return True

    monkeypatch.setattr(launcher, "_launchctl_command", launchctl)
    monkeypatch.setattr(launcher, "stop_cli_bridge", stop)
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: state["running"])

    assert launcher.ensure_cli_bridge() == "private-token"
    assert state["stops"] == 1
    assert service.is_file()
    assert service.stat().st_mode & 0o777 == 0o600
    launcher._validate_macos_agent_file()
    assert ("bootstrap", f"gui/{os.getuid()}", str(service)) in calls

    calls.clear()
    assert launcher.ensure_cli_bridge() == "private-token"
    assert calls == [("print", launcher._macos_agent_target())]

    state["running"] = False
    calls.clear()
    assert launcher.ensure_cli_bridge() == "private-token"
    assert ("kickstart", "-k", launcher._macos_agent_target()) in calls

    state.update(registered=False, running=True)
    calls.clear()
    assert launcher.ensure_cli_bridge() == "private-token"
    assert state["stops"] == 2
    assert ("bootstrap", f"gui/{os.getuid()}", str(service)) in calls


@pytest.mark.parametrize("arguments", [[], ["/bin/sh"], ["/bin/sh", "-c"], "invalid"])
def test_malformed_service_is_rejected_without_running_launchctl(macos_agent, arguments):
    _bridge, service, executable = macos_agent
    service.parent.mkdir()
    document = launcher._macos_agent_document(str(executable))
    document["ProgramArguments"] = arguments
    service.write_bytes(plistlib.dumps(document))
    service.chmod(0o600)
    with pytest.raises(RuntimeError, match="not owned"):
        launcher._validate_macos_agent_file()


def test_failed_service_registration_removes_new_plist(macos_agent, monkeypatch):
    _bridge, service, _executable = macos_agent
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: False)
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: False)
    monkeypatch.setattr(launcher, "_launchctl_command", lambda *_args: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match="could not register"):
        launcher.ensure_cli_bridge()
    assert not service.exists()


def test_migration_refuses_to_bind_over_a_still_running_detached_agent(
    macos_agent, monkeypatch
):
    _bridge, service, _executable = macos_agent
    monkeypatch.setattr(launcher, "_agent_lock", lambda: nullcontext())
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: False)
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: True)
    ticks = iter((0.0, 6.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="did not stop"):
        launcher.ensure_cli_bridge()
    assert not service.exists()


def test_rollback_keeps_plist_if_launchd_cannot_unload_partial_registration(
    macos_agent, monkeypatch
):
    _bridge, service, _executable = macos_agent
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: False)
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: False)
    monkeypatch.setattr(
        launcher,
        "_launchctl_command",
        lambda *args: SimpleNamespace(returncode=1 if args[0] == "bootout" else 0),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_macos_bridge",
        lambda _token: (_ for _ in ()).throw(RuntimeError("agent unhealthy")),
    )
    with pytest.raises(RuntimeError, match="configuration retained"):
        launcher.ensure_cli_bridge()
    assert service.exists()


def test_symlinked_service_is_rejected_before_reading_it(macos_agent):
    _bridge, service, _executable = macos_agent
    service.parent.mkdir()
    service.symlink_to(service.parent / "missing-target")
    with pytest.raises(RuntimeError, match="not owned"):
        launcher._validate_macos_agent_file()


def test_uninstall_stops_service_and_removes_only_managed_plist(macos_agent, monkeypatch):
    _bridge, service, executable = macos_agent
    service.parent.mkdir()
    service.write_bytes(plistlib.dumps(launcher._macos_agent_document(str(executable))))
    service.chmod(0o600)
    seen = []
    monkeypatch.setattr(launcher, "_launchctl_command", lambda *args: (
        seen.append(args) or SimpleNamespace(returncode=0)
    ))
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "")
    assert launcher.stop_cli_bridge() is True
    assert ("bootout", launcher._macos_agent_target()) in seen
    launcher._uninstall_macos_bridge()
    assert not service.exists()


def test_unsafe_service_is_left_untouched_during_uninstall(macos_agent):
    _bridge, service, executable = macos_agent
    service.parent.mkdir()
    document = launcher._macos_agent_document(str(executable))
    document["KeepAlive"] = False
    service.write_bytes(plistlib.dumps(document))
    service.chmod(0o600)
    with pytest.raises(RuntimeError, match="not owned"):
        launcher._uninstall_macos_bridge()
    assert service.exists()
