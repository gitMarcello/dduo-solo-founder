from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from dduo_solo_founder import __version__, client_binding, launcher, project_secrets
from dduo_solo_founder.authority_receipts import (
    AuthorityReceipt,
    issue_authority_receipt,
)
from dduo_solo_founder.backup import BackupError, create_archive, generate_recovery_key
from dduo_solo_founder.invitations import (
    REMOTE_REPOSITORY_URL,
    create_invitation_bundle,
)
from conftest import assert_private_file


runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_launcher_project_secrets(monkeypatch, tmp_path):
    config = tmp_path / "host-config"
    legacy = config / "env"
    retired = config / "env.alpha-retired"
    monkeypatch.setattr(project_secrets, "CONFIG_DIR", config)
    monkeypatch.setattr(project_secrets, "LEGACY_ENV_FILE", legacy)
    monkeypatch.setattr(project_secrets, "RETIRED_LEGACY_ENV_FILE", retired)
    monkeypatch.setattr(
        project_secrets, "PROJECT_SECRETS_DIR", config / "project-secrets"
    )
    monkeypatch.setattr(launcher, "LEGACY_ENV_FILE", legacy)
    monkeypatch.setattr(launcher, "RETIRED_LEGACY_ENV_FILE", retired)
    monkeypatch.setattr(
        client_binding,
        "validate_project_registration",
        lambda *_args, **_kwargs: {},
    )


def test_runtime_binding_rejects_a_copied_local_project_claim(monkeypatch, tmp_path):
    monkeypatch.setattr(
        client_binding,
        "validate_project_registration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("already claimed by another checkout")
        ),
    )
    project = {
        "id": "p1",
        "name": "Copied",
        "binding": "local",
        "api_port": 18001,
        "web_port": 20001,
    }

    with pytest.raises(client_binding.BindingError, match="already claimed"):
        launcher._runtime_binding(tmp_path, project, require_approval=False)


def invitation_response(
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    code: str,
    language: str = "it",
    release_version: str = __version__,
) -> dict:
    payload, prompt = create_invitation_bundle(
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
        invitation_code=code,
        language=language,
        release_version=release_version,
    )
    return {
        "invitation": {
            "invite_payload": payload,
            "setup_prompt": prompt,
            "release_version": release_version,
        }
    }


def isolate_remote_client_state(monkeypatch, tmp_path):
    approvals = tmp_path / "client-state" / "remote-bindings.json"
    credentials = tmp_path / "client-state" / "remote-credentials"
    monkeypatch.setattr(client_binding, "REMOTE_APPROVALS_PATH", approvals)
    monkeypatch.setattr(client_binding, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "is_git_worktree", lambda _root: True)
    return approvals, credentials


def finalization_proof(*, secret: str = "authority-secret") -> tuple[str, dict]:
    receipt = AuthorityReceipt(
        kind="source_finalized",
        project_id="p1",
        source_node_id="node-old",
        target_node_id="node-new",
        source_generation=5,
        nonce="a" * 64,
    )
    return issue_authority_receipt(secret, receipt), {
        "project_id": "p1",
        "state": "transferred",
        "generation": 5,
        "node_id": "node-old",
        "target_node_id": "node-new",
    }


def test_repo_root_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("DDUO_SOLO_FOUNDER_HOME", str(tmp_path / "home"))
    assert launcher.repo_root() == tmp_path / "home"
    monkeypatch.delenv("DDUO_SOLO_FOUNDER_HOME")
    pointer = tmp_path / "pointer"
    pointer.write_text(str(tmp_path / "runtime"))
    monkeypatch.setattr(launcher, "RUNTIME_POINTER", pointer)
    assert launcher.repo_root() == tmp_path / "runtime"
    pointer.unlink()
    assert launcher.repo_root() == Path(launcher.__file__).resolve().parents[2]


def test_private_host_identity_gateway_and_remote_control_helpers(monkeypatch, tmp_path, capsys):
    device_file = tmp_path / "identity" / "device-id"
    node_file = tmp_path / "identity" / "node-id"
    monkeypatch.setattr(launcher, "DEVICE_ID_FILE", device_file)
    monkeypatch.setattr(launcher, "REMOTE_NODE_ID_FILE", node_file)

    device = launcher.machine_device_id()
    node = launcher.remote_node_id()
    assert device.startswith("device-")
    assert node.startswith("node-")
    assert launcher.machine_device_id() == device
    assert launcher.remote_node_id() == node
    assert_private_file(device_file)
    assert_private_file(node_file)

    launcher.remote_node_id_command()
    assert capsys.readouterr().out.strip() == node

    assert launcher._host_control_headers({"id": "p1"}) == {}
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *_args, **_kwargs: {"DDUO_INFRASTRUCTURE_TOKEN": "host-token"},
    )
    assert launcher._host_control_headers({"id": "p1", "deployment": "remote"}) == {
        "Authorization": "Bearer host-token"
    }
    monkeypatch.setattr(launcher, "load_project_secrets", lambda *_args, **_kwargs: {})
    with pytest.raises(RuntimeError, match="infrastructure credential"):
        launcher._host_control_headers({"id": "p1", "deployment": "remote"})

    captured = {}
    monkeypatch.setattr(launcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, **kwargs: captured.update(command=command, **kwargs)
        or SimpleNamespace(returncode=0),
    )
    launcher._gateway_compose("ps", capture_output=True)
    assert captured["command"][-1] == "ps"
    assert captured["env"]["COMPOSE_PROJECT_NAME"] == "dduo-solo-founder-gateway"
    assert captured["capture_output"] is True

    monkeypatch.setattr(
        launcher,
        "load_gateway_registry",
        lambda *_args: {
            "public_ip": "2001:db8::10",
            "projects": {"p1": {"https_port": 9443, "name": "Remote"}},
        },
    )
    gateway = launcher._gateway_project("p1")
    assert gateway["api_url"] == "https://[2001:db8::10]:9443/api"
    assert launcher._gateway_project("missing") is None
    monkeypatch.setattr(launcher, "load_gateway_registry", lambda *_args: {"projects": {}})
    assert launcher._gateway_project("p1") is None

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", 43_123)

    monkeypatch.setattr(launcher.socket, "socket", lambda *_args: Socket())
    assert launcher._available_agent_port() == 43_123


def test_docker_checks(monkeypatch):
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    assert launcher.docker_ready()
    monkeypatch.setattr(launcher.shutil, "which", lambda _: None)
    with pytest.raises(typer.Exit) as missing:
        launcher.require_docker()
    assert missing.value.exit_code == 2
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "/docker")
    monkeypatch.setattr(launcher, "docker_ready", lambda: False)
    with pytest.raises(typer.Exit) as stopped:
        launcher.require_docker()
    assert stopped.value.exit_code == 3


def _remote_resource_probe(
    monkeypatch,
    *,
    memory,
    swap,
    free_disk,
    cpus=1,
    shared_storage=True,
):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "_linux_memory_totals", lambda: (memory, swap))
    monkeypatch.setattr(launcher, "_linux_disk_swap_total", lambda: swap)
    monkeypatch.setattr(launcher.os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(launcher, "_docker_storage_root", lambda: Path("/docker-data"))

    def filesystem_free(path):
        if shared_storage:
            return Path("/"), "0:25", free_disk
        if path == Path("/docker-data"):
            return Path("/docker-data"), "0:26", free_disk
        return Path("/"), "0:25", free_disk

    monkeypatch.setattr(launcher, "_linux_filesystem_free", filesystem_free)


def test_linux_memory_totals_parses_kernel_kib(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       1048576 kB\nMemAvailable: 500000 kB\nSwapTotal:      2097152 kB\n"
    )

    assert launcher._linux_memory_totals(meminfo) == (
        1024 * launcher.MIB,
        2 * launcher.GIB,
    )


def test_linux_memory_totals_rejects_missing_fields(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1048576 kB\n")

    with pytest.raises(RuntimeError, match="incomplete Linux memory information"):
        launcher._linux_memory_totals(meminfo)


def test_linux_disk_swap_total_excludes_zram(tmp_path):
    swaps = tmp_path / "swaps"
    swaps.write_text(
        "Filename Type Size Used Priority\n"
        "/dev/zram0 partition 2097152 0 100\n"
        "/swapfile file 2097152 0 -2\n"
    )

    assert launcher._linux_disk_swap_total(swaps) == 2 * launcher.GIB


def test_docker_storage_root_is_reported_by_docker(monkeypatch):
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="/srv/docker\n",
            stderr="",
        ),
    )

    assert launcher._docker_storage_root() == Path("/srv/docker")


def test_linux_filesystem_free_uses_accessible_mountpoint(monkeypatch, tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("29 1 0:25 / / rw,relatime - ext4 /dev/root rw\n")
    inspected = []
    monkeypatch.setattr(
        launcher.shutil,
        "disk_usage",
        lambda path: inspected.append(path)
        or SimpleNamespace(total=8 * launcher.GIB, used=0, free=8 * launcher.GIB),
    )

    mount, device_id, free = launcher._linux_filesystem_free(
        Path("/var/lib/docker"), mountinfo
    )

    assert mount == Path("/")
    assert device_id == "0:25"
    assert free == 8 * launcher.GIB
    assert inspected == [Path("/")]


def test_linux_filesystem_free_recognizes_bind_mount_backing_device(monkeypatch, tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "29 1 0:25 / / rw,relatime - ext4 /dev/root rw\n"
        "30 29 0:25 /var/lib/docker /var/lib/docker rw,relatime - ext4 /dev/root rw\n"
    )
    monkeypatch.setattr(
        launcher.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=8 * launcher.GIB, used=0, free=8 * launcher.GIB),
    )

    docker_mount, docker_device, _free = launcher._linux_filesystem_free(
        Path("/var/lib/docker/overlay2"), mountinfo
    )
    root_mount, root_device, _free = launcher._linux_filesystem_free(Path("/"), mountinfo)

    assert docker_mount == Path("/var/lib/docker")
    assert root_mount == Path("/")
    assert docker_device == root_device == "0:25"


def test_remote_host_resource_preflight_accepts_provider_neutral_minimum(monkeypatch, tmp_path):
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES,
        swap=launcher.REMOTE_MIN_SWAP_BYTES,
        free_disk=launcher.REMOTE_MIN_FREE_DISK_BYTES,
    )

    launcher._require_remote_host_resources()


def test_remote_host_resource_preflight_requires_physical_memory(monkeypatch, tmp_path):
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES - 1,
        swap=launcher.REMOTE_MIN_SWAP_BYTES,
        free_disk=launcher.REMOTE_MIN_FREE_DISK_BYTES,
    )

    with pytest.raises(RuntimeError, match="1 GiB of physical RAM") as failure:
        launcher._require_remote_host_resources()
    assert "droplet" not in str(failure.value).lower()
    assert "digitalocean" not in str(failure.value).lower()


def test_remote_host_resource_preflight_requires_one_cpu(monkeypatch, tmp_path):
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES,
        swap=launcher.REMOTE_MIN_SWAP_BYTES,
        free_disk=launcher.REMOTE_MIN_FREE_DISK_BYTES,
        cpus=0,
    )

    with pytest.raises(RuntimeError, match="at least 1 vCPU"):
        launcher._require_remote_host_resources()


def test_remote_host_resource_preflight_reserves_missing_swap_space(monkeypatch, tmp_path):
    required_before_setup = launcher.REMOTE_MIN_FREE_DISK_BYTES + launcher.REMOTE_MIN_SWAP_BYTES
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES,
        swap=0,
        free_disk=required_before_setup - 1,
    )

    with pytest.raises(RuntimeError, match="5 GiB free in Docker storage after swap") as failure:
        launcher._require_remote_host_resources()
    assert "7.0 GiB is required there before setup" in str(failure.value)


def test_remote_host_resource_preflight_requires_active_disk_swap(monkeypatch, tmp_path):
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES,
        swap=0,
        free_disk=launcher.REMOTE_MIN_FREE_DISK_BYTES + launcher.REMOTE_MIN_SWAP_BYTES,
    )

    with pytest.raises(RuntimeError, match="2 GiB of active disk-backed swap"):
        launcher._require_remote_host_resources()


def test_remote_host_resource_preflight_rejects_separate_docker_filesystem(monkeypatch):
    _remote_resource_probe(
        monkeypatch,
        memory=launcher.REMOTE_MIN_PHYSICAL_MEMORY_BYTES,
        swap=launcher.REMOTE_MIN_SWAP_BYTES,
        free_disk=launcher.REMOTE_MIN_FREE_DISK_BYTES,
        shared_storage=False,
    )

    with pytest.raises(RuntimeError, match="share one backing filesystem"):
        launcher._require_remote_host_resources()


def test_remote_host_checks_platform_before_secret_mutation(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "binding": "local"}
    mutations = []
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "_runtime_binding",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(
        launcher,
        "_require_persistent_bridge_host",
        lambda: (_ for _ in ()).throw(RuntimeError("Linux VPS required")),
    )
    monkeypatch.setattr(
        launcher,
        "ensure_project_secret_environment",
        lambda *_: mutations.append("secret"),
    )

    result = runner.invoke(
        launcher.app,
        ["remote-host", "--public-ip", "203.0.113.10", "--project-root", str(tmp_path)],
    )

    assert isinstance(result.exception, RuntimeError)
    assert "Linux VPS required" in str(result.exception)
    assert mutations == []


def test_compose_loads_environment_without_overwriting_process(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *_args, **_kwargs: {
            "OPENAI_API_KEY": "project-key",
            "EMBEDDING_MODEL": "model",
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "process-key")
    captured = {}

    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-test")
    launcher.compose({"id": "p1", "api_port": 1, "web_port": 2}, "ps")
    assert captured["env"]["OPENAI_API_KEY"] == "project-key"
    assert captured["env"]["EMBEDDING_MODEL"] == "model"
    assert captured["args"][-1] == "ps"


def test_launcher_small_fail_closed_branches(monkeypatch, tmp_path):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    token_file = bridge / "token"
    token_file.write_text("\n")
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge)
    monkeypatch.setattr(launcher, "BRIDGE_TOKEN_FILE", token_file)
    generated = launcher.ensure_bridge_token()
    assert generated and generated == token_file.read_text().strip()

    monkeypatch.setenv("DDUO_CLI_BRIDGE_TOKEN", "environment-token")
    assert launcher.existing_bridge_token() == "environment-token"
    monkeypatch.delenv("DDUO_CLI_BRIDGE_TOKEN")
    token_file.unlink()
    assert launcher.existing_bridge_token() == ""

    monkeypatch.setattr(launcher, "agent_url", lambda *_args: "http://127.0.0.1:43123")
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503),
    )
    assert launcher.bridge_ready("token") is False

    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {},
        ),
    )
    with pytest.raises(RuntimeError, match="invalid access ticket"):
        launcher.setup_url(tmp_path)

    with pytest.raises(RuntimeError, match="remote client binding"):
        launcher.compose({"id": "p1", "binding": "remote"}, "ps")

    captured = {}
    monkeypatch.setattr(launcher, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-one")
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "bridge-token")
    monkeypatch.setattr(launcher, "agent_port", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *_args, **_kwargs: {"OPENAI_API_KEY": "secret-key"},
    )
    monkeypatch.setattr(
        launcher,
        "compose_backup_environment",
        lambda *_args: {"DDUO_BACKUP": "enabled"},
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda command, **kwargs: captured.update(command=command, **kwargs)
        or SimpleNamespace(returncode=0),
    )
    launcher.compose(
        {
            "id": "p1",
            "api_port": 18001,
            "web_port": 20001,
            "deployment": "remote",
            "root_path": str(tmp_path),
        },
        "ps",
    )
    assert "KEY" not in captured["env"]
    assert captured["env"]["OPENAI_API_KEY"] == "secret-key"
    assert captured["env"]["DDUO_BACKUP"] == "enabled"
    assert str(tmp_path / "compose.remote.yaml") in captured["command"]

    opened = []
    monkeypatch.setattr(launcher, "open_setup", lambda root: opened.append(root))
    launcher.setup(tmp_path)
    assert opened == [tmp_path]


def test_configure_openai(monkeypatch, tmp_path):
    monkeypatch.setattr(
        launcher,
        "load_project",
        lambda _root: {
            "id": "p1",
            "name": "Demo",
            "api_port": 18001,
            "web_port": 20001,
        },
    )
    saved = []
    destination = tmp_path / "project-secrets"
    monkeypatch.setattr(
        launcher,
        "save_project_secrets",
        lambda project_id, values: saved.append((project_id, values)) or destination,
    )
    monkeypatch.setattr(launcher.typer, "prompt", lambda *args, **kwargs: " secret ")
    launcher.configure_openai(project_root=tmp_path)
    assert saved == [("p1", {"OPENAI_API_KEY": "secret"})]
    monkeypatch.setattr(launcher.typer, "prompt", lambda *args, **kwargs: "")
    with pytest.raises(typer.Exit):
        launcher.configure_openai(project_root=tmp_path)


def test_doctor_success_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "/docker")
    monkeypatch.setattr(launcher, "docker_ready", lambda: True)
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "bridge_ready", lambda _: True)
    monkeypatch.setattr(
        launcher,
        "load_project",
        lambda _: {"id": "p1", "api_port": 18001, "web_port": 20001},
    )
    result = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert result.exit_code == 0 and json.loads(result.stdout)["project_id"] == "p1"
    monkeypatch.setattr(
        launcher, "load_project", lambda _: (_ for _ in ()).throw(FileNotFoundError())
    )
    result = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert result.exit_code == 1


def test_remote_doctor_start_stop_status_and_backup_paths(monkeypatch, tmp_path):
    project = {
        "id": "p1",
        "name": "Remote",
        "binding": "remote",
        "api_url": "https://203.0.113.10/api",
        "dashboard_url": "https://203.0.113.10",
    }
    binding = SimpleNamespace(
        remote=True,
        project_id="p1",
        api_url=project["api_url"],
        bearer_token="device-token",
    )
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _root: project)
    monkeypatch.setattr(launcher, "binding_from_project", lambda *_args, **_kwargs: binding)
    monkeypatch.setattr(launcher, "load_binding", lambda _root: binding)
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)

    healthy = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert healthy.exit_code == 0
    assert json.loads(healthy.stdout)["docker_required"] is False

    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: False)
    unavailable = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert unavailable.exit_code == 1
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)

    started = []
    monkeypatch.setattr(
        launcher,
        "start_stack",
        lambda root, value, **kwargs: started.append((root, value["id"], kwargs)),
    )
    monkeypatch.setattr(
        launcher,
        "require_docker",
        lambda: pytest.fail("remote commands must not require local Docker"),
    )
    assert runner.invoke(launcher.app, ["start", "--project-root", str(tmp_path)]).exit_code == 0
    stopped = runner.invoke(launcher.app, ["stop", "--project-root", str(tmp_path)])
    assert stopped.exit_code == 2
    assert "cannot be stopped" in stopped.stdout
    status = runner.invoke(launcher.app, ["status", "--project-root", str(tmp_path)])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["ready"] is True

    calls = []
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda context, method, path, **kwargs: calls.append((method, path))
        or {"path": path},
    )
    created = runner.invoke(
        launcher.app,
        ["backup", "create", "--project-root", str(tmp_path)],
    )
    backup_status = runner.invoke(
        launcher.app,
        ["backup", "status", "--project-root", str(tmp_path)],
    )
    configured = runner.invoke(
        launcher.app,
        ["backup", "configure", str(tmp_path / "backups"), "--project-root", str(tmp_path)],
    )
    assert created.exit_code == 0
    assert backup_status.exit_code == 0
    assert configured.exit_code == 2
    assert calls == [
        ("POST", "/projects/p1/backups?trigger=manual"),
        ("GET", "/projects/p1/backups"),
    ]
    assert len(started) == 3

    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid binding")),
    )
    broken = runner.invoke(launcher.app, ["doctor", "--project-root", str(tmp_path)])
    assert broken.exit_code == 1
    assert "invalid binding" in broken.stdout


def test_client_readiness_command_reports_ready_and_protected_failure(monkeypatch, tmp_path):
    # The command contract must not depend on a client installed on the test host.
    monkeypatch.setattr(launcher, "resolve_client_installation", lambda *a, **k: object())
    monkeypatch.setattr(
        launcher,
        "inspect_client_readiness",
        lambda client, root, **_kwargs: {
            "client": client,
            "ready": True,
            "authentication": {"ready": True},
            "hooks": None,
            "actions": [],
        },
    )
    result = runner.invoke(
        launcher.app,
        ["client-readiness", "--client", "claude", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["ready"] is True

    monkeypatch.setattr(
        launcher,
        "inspect_client_readiness",
        lambda client, root, **_kwargs: {
            "client": client,
            "ready": False,
            "authentication": {"ready": False},
            "hooks": None,
            "actions": [{"type": "subscription_login"}],
        },
    )
    result = runner.invoke(
        launcher.app,
        ["client-readiness", "--client", "claude", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 8
    assert json.loads(result.stdout)["actions"][0]["type"] == "subscription_login"

    monkeypatch.setattr(
        launcher,
        "inspect_client_readiness",
        lambda client, root, **k: (_ for _ in ()).throw(ValueError("client must be codex or claude")),
    )
    assert (
        runner.invoke(
            launcher.app,
            ["client-readiness", "--client", "other", "--project-root", str(tmp_path)],
        ).exit_code
        != 0
    )


def test_init_requires_key_and_initializes_project(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "open_setup", lambda *_args, **_kwargs: "http://setup.test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (
        runner.invoke(launcher.app, ["init", "--yes", "--project-root", str(tmp_path)]).exit_code
        == 5
    )

    project = launcher.load_project(tmp_path)
    project_secrets.save_project_secrets(
        str(project["id"]), {"OPENAI_API_KEY": "key"}
    )
    monkeypatch.setattr(launcher, "start_stack", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "_api_request", lambda *args, **kwargs: {})
    result = runner.invoke(
        launcher.app, ["init", "--yes", "--name", "Demo", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0 and "dDuo Solo Founder local services are ready" in result.stdout
    assert "?project=" in result.stdout and "&tab=tasks" in result.stdout
    assert "CLIENT_RELOAD_REQUIRED_IF_INSTALLED_OR_UPDATED" in result.stdout
    assert "fully quit and reopen Codex" in result.stdout
    result = runner.invoke(launcher.app, ["init", "--yes", "--project-root", str(tmp_path)])
    assert result.exit_code == 0


def test_start_stack_success_compose_failure_and_timeout(monkeypatch):
    project = {"id": "p", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: False)
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=9))
    with pytest.raises(typer.Exit) as failed:
        launcher.start_stack(Path("."), project)
    assert failed.value.exit_code == 9

    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: True)
    launcher.start_stack(Path("."), project)

    times = iter([0, 181])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: False)
    with pytest.raises(typer.Exit) as timeout:
        launcher.start_stack(Path("."), project)
    assert timeout.value.exit_code == 4


def test_start_stack_respects_remote_boundary_retirement_and_explicit_build(monkeypatch, tmp_path):
    project = {"id": "p", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=True),
    )
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)
    monkeypatch.setattr(
        launcher,
        "ensure_cli_bridge",
        lambda: pytest.fail("remote bindings do not start a local bridge"),
    )
    launcher.start_stack(tmp_path, project)

    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: False)
    with pytest.raises(RuntimeError, match="remote .* memory is unavailable"):
        launcher.start_stack(tmp_path, project)

    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=False),
    )
    retired = tmp_path / launcher.RETIRED_NODE_FILE
    retired.parent.mkdir(parents=True)
    retired.write_text("retired\n")
    with pytest.raises(RuntimeError, match="node was retired"):
        launcher.start_stack(tmp_path, project)
    retired.unlink()

    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    health = iter((False, False, True))
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: next(health))
    commands = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda _project, *args: commands.append(args) or SimpleNamespace(returncode=0),
    )
    clock = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    sleeps = []
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: sleeps.append(seconds))
    launcher.start_stack(tmp_path, project, build=True)
    assert commands == [("up", "-d", "--build")]
    assert sleeps == [2]


def test_setup_url_exchanges_master_token_for_one_time_ticket(monkeypatch, tmp_path):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ticket": "one-time-ticket"}

    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "private-token")
    monkeypatch.setattr(launcher, "agent_url", lambda: "http://127.0.0.1:40123")
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or Response(),
    )
    url = launcher.setup_url(tmp_path)
    assert url == "http://127.0.0.1:40123/setup?ticket=one-time-ticket"
    assert "private-token" not in url and str(tmp_path) not in url
    assert captured == {
        "url": "http://127.0.0.1:40123/v1/setup/ticket",
        "json": {"project_root": str(tmp_path.resolve())},
        "headers": {"Authorization": "Bearer private-token"},
        "timeout": 10,
    }


def test_get_setup_status_uses_a_root_bound_setup_session(monkeypatch, tmp_path):
    calls = []

    class Response:
        def __init__(self, payload=None):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            calls.append(("get", url))
            payload = {"docker": {"ready": True}} if url.endswith("/v1/setup/status") else None
            return Response(payload)

    monkeypatch.setattr(launcher, "setup_url", lambda root: "http://127.0.0.1:40123/setup?ticket=t")
    monkeypatch.setattr(launcher, "agent_url", lambda: "http://127.0.0.1:40123")
    monkeypatch.setattr(launcher.httpx, "Client", Client)
    assert launcher.get_setup_status(tmp_path) == {"docker": {"ready": True}}
    assert calls == [
        ("client", {"timeout": 20}),
        ("get", "http://127.0.0.1:40123/setup?ticket=t"),
        ("get", "http://127.0.0.1:40123/v1/setup/status"),
    ]

    class InvalidResponse(Response):
        def json(self):
            return []

    class InvalidClient(Client):
        def get(self, url):
            return InvalidResponse()

    monkeypatch.setattr(launcher.httpx, "Client", InvalidClient)
    with pytest.raises(RuntimeError, match="invalid status"):
        launcher.get_setup_status(tmp_path)


def test_commands_delegate(monkeypatch, tmp_path):
    project = {"id": "p", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    starts = []
    monkeypatch.setattr(
        launcher,
        "start_stack",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )
    assert runner.invoke(launcher.app, ["start", "--project-root", str(tmp_path)]).exit_code == 0
    assert starts[-1][1]["build"] is False
    assert (
        runner.invoke(launcher.app, ["start", "--project-root", str(tmp_path), "--build"]).exit_code
        == 0
    )
    assert starts[-1][1]["build"] is True
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=7))
    assert runner.invoke(launcher.app, ["stop", "--project-root", str(tmp_path)]).exit_code == 7
    assert runner.invoke(launcher.app, ["status", "--project-root", str(tmp_path)]).exit_code == 7

    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda project, method, path, **kwargs: {"queued": 2, "path": path},
    )
    result = runner.invoke(launcher.app, ["reindex", "--project-root", str(tmp_path)])
    assert result.exit_code == 0 and '"queued": 2' in result.stdout
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda project, method, path, **kwargs: {"path": path},
    )
    slept = runner.invoke(launcher.app, ["sleep", "--project-root", str(tmp_path)])
    status = runner.invoke(launcher.app, ["memory-status", "--project-root", str(tmp_path)])
    assert slept.exit_code == 0 and "/sleep" in slept.stdout
    assert status.exit_code == 0 and "/memory-status" in status.stdout


def test_remote_join_exchanges_one_time_invite_without_starting_docker(monkeypatch, tmp_path):
    project = {
        "id": "project-one",
        "name": "TeamApp",
        "binding": "remote",
        "api_url": "https://8.8.8.8/api",
        "dashboard_url": "https://8.8.8.8",
    }
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "is_git_worktree", lambda _root: True)

    @launcher.contextmanager
    def provisional(*_args, **_kwargs):
        yield project

    monkeypatch.setattr(launcher, "_provisional_remote_binding", provisional)
    monkeypatch.setattr(launcher, "machine_device_id", lambda: "device-test")
    captured = []

    def request(context, method, path, **kwargs):
        captured.append({"context": context, "method": method, "path": path, **kwargs})
        if method == "GET":
            return {"manual": {"version": 1, "content": "Shared procedure"}}
        return {
            "current_member": {
                "id": "member-sam",
                "project_id": "project-one",
                "display_name": "Sam",
                "access_token_id": "token-sam",
            }
        }

    monkeypatch.setattr(launcher, "_api_request", request)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=True),
    )
    monkeypatch.setattr(launcher, "store_verified_manual", lambda *_args: tmp_path / "manual")
    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert [call["path"] for call in captured] == [
        "/projects/project-one/auth/exchange",
        "/projects/project-one/team/manual",
    ]
    assert captured[0]["json_body"]["device_id"] == "device-test"
    assert captured[0]["json_body"]["device_token"].startswith("dduo_dev_")
    assert '"offline_manual_cached": true' in result.stdout
    assert "local_docker_required\": false" in result.stdout


def test_remote_join_accepts_the_shell_inert_invitation_descriptor(monkeypatch, tmp_path):
    payload = launcher._encode_invite_payload(
        project_id="project-one",
        name='TeamApp $(touch /tmp/nope) `whoami` "quoted"\nnext',
        api_url="https://8.8.8.8/api",
        dashboard_url="https://8.8.8.8",
        invitation_code="dduo_inv_" + "a" * 48,
    )
    decoded = launcher._decode_invite_payload(payload)
    assert decoded["project_id"] == "project-one"
    assert decoded["name"].endswith('"quoted"\nnext')
    assert re.fullmatch(r"[A-Za-z0-9_-]+", payload)

    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--invite-payload",
            payload,
            "--project-id",
            "ambiguous",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.stderr


@pytest.mark.parametrize("payload", ["not+base64", "e30", "A" * 8193])
def test_remote_join_rejects_malformed_invitation_descriptor(payload):
    result = runner.invoke(
        launcher.app,
        ["remote-join", "--invite-payload", payload, "--project-root", "."],
    )
    assert result.exit_code == 2
    assert "invitation payload" in result.stderr


def test_remote_join_requires_a_real_git_worktree_before_mutating_state(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "is_git_worktree", lambda _root: False)
    mutations = []
    monkeypatch.setattr(
        launcher,
        "_provisional_remote_binding",
        lambda *_args, **_kwargs: mutations.append(True),
    )

    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "authorized Git worktree" in str(result.exception)
    assert mutations == []
    assert not (tmp_path / ".dduo-solo-founder").exists()


def test_remote_join_keeps_success_when_manual_cache_prewarm_fails(
    monkeypatch, tmp_path
):
    project = {
        "id": "project-one",
        "name": "TeamApp",
        "binding": "remote",
        "api_url": "https://8.8.8.8/api",
        "dashboard_url": "https://8.8.8.8",
    }
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "is_git_worktree", lambda _root: True)

    @launcher.contextmanager
    def provisional(*_args, **_kwargs):
        yield project

    monkeypatch.setattr(launcher, "_provisional_remote_binding", provisional)
    monkeypatch.setattr(launcher, "machine_device_id", lambda: "device-test")

    def request(_context, method, _path, **_kwargs):
        if method == "GET":
            raise RuntimeError("temporary manual outage")
        return {
            "current_member": {
                "id": "member-sam",
                "project_id": "project-one",
                "access_token_id": "token-sam",
            }
        }

    monkeypatch.setattr(launcher, "_api_request", request)
    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["joined"] is True
    assert payload["offline_manual_cached"] is False
    assert "will retry" in payload["warning"]
    assert "temporary manual outage" not in result.stdout


def test_remote_join_promotes_only_matching_local_project_without_replace_flag(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "checkout"
    (project_root / ".git").mkdir(parents=True)
    config = project_root / ".dduo-solo-founder" / "project.toml"
    config.parent.mkdir()
    config.write_text(
        'version = 2\nid = "project-one"\nname = "TeamApp"\n'
        'binding = "local"\napi_port = 18001\nweb_port = 20001\n'
    )
    isolate_remote_client_state(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "machine_device_id", lambda: "device-test")
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: {
            "current_member": {
                "id": "member-sam",
                "project_id": "project-one",
                "display_name": "Sam",
                "access_token_id": "token-sam",
            }
        },
    )

    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(project_root),
        ],
    )

    assert result.exit_code == 0, result.stdout
    binding = client_binding.load_binding(project_root)
    assert binding.remote is True
    assert binding.project_id == "project-one"
    assert binding.api_url == "https://8.8.8.8/api"


def test_remote_join_rejects_a_success_response_without_project_token_attribution(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "checkout"
    (project_root / ".git").mkdir(parents=True)
    approvals, credentials = isolate_remote_client_state(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "machine_device_id", lambda: "device-test")
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: {"current_member": {"id": "member-without-token"}},
    )

    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(project_root),
        ],
    )

    assert result.exit_code == 1
    assert "project-scoped device-token attribution" in str(result.exception)
    assert not project_root.joinpath(".dduo-solo-founder/project.toml").exists()
    assert not approvals.exists()
    # The locally generated device token is harmless while unapproved and is
    # intentionally retained so an idempotent exchange can be retried.
    assert len(list(credentials.glob("*.token"))) == 1


def test_remote_bind_rejects_a_success_response_without_project_token_attribution(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "checkout"
    (project_root / ".git").mkdir(parents=True)
    approvals, credentials = isolate_remote_client_state(monkeypatch, tmp_path)
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: {"current_member": {"id": "manager-without-token"}},
    )

    result = runner.invoke(
        launcher.app,
        [
            "remote-bind",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://8.8.8.8/api",
            "--dashboard-url",
            "https://8.8.8.8",
            "--token",
            "manager-device-token",
            "--project-root",
            str(project_root),
        ],
    )

    assert result.exit_code == 1
    assert "project-scoped device-token attribution" in str(result.exception)
    assert not project_root.joinpath(".dduo-solo-founder/project.toml").exists()
    assert not approvals.exists()
    assert list(credentials.glob("*.token")) == []


@pytest.mark.parametrize(
    "existing_kind",
    ["local-other-project", "remote-other-endpoint", "remote-other-dashboard"],
)
def test_remote_join_does_not_replace_mismatched_or_remote_binding(
    monkeypatch, tmp_path, existing_kind
):
    project_root = tmp_path / "checkout"
    (project_root / ".git").mkdir(parents=True)
    isolate_remote_client_state(monkeypatch, tmp_path)
    config = project_root / ".dduo-solo-founder" / "project.toml"
    config.parent.mkdir()
    if existing_kind == "local-other-project":
        config.write_text(
            'version = 2\nid = "another-project"\nname = "Other"\n'
            'binding = "local"\napi_port = 18001\nweb_port = 20001\n'
        )
    else:
        client_binding.write_remote_project_config(
            project_root,
            project_id="project-one",
            name="TeamApp",
            api_url="https://8.8.8.8/api",
            dashboard_url="https://8.8.8.8",
        )
        client_binding.approve_remote_binding(project_root, "stable-private-token")
    original_config = config.read_text()
    calls = []
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: calls.append(True) or {},
    )

    requested_api = (
        "https://8.8.8.8/api"
        if existing_kind == "remote-other-dashboard"
        else "https://9.9.9.9/api"
    )
    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            requested_api,
            "--dashboard-url",
            "https://9.9.9.9",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(project_root),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert "different memory binding" in str(result.exception)
    assert config.read_text() == original_config
    assert calls == []


def test_failed_remote_binding_restores_checkout_and_approval_state(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    config = project_root / ".dduo-solo-founder" / "project.toml"
    config.parent.mkdir(parents=True)
    config.write_text("version = 1\nid = \"local\"\n")
    retired = project_root / launcher.RETIRED_NODE_FILE
    retired.write_text('{"project_id":"local"}')
    approvals = tmp_path / "host" / "remote-bindings.json"
    approvals.parent.mkdir(parents=True)
    approvals.write_text('{"version":1,"bindings":{}}')
    approvals.chmod(0o600)
    credentials = tmp_path / "host" / "credentials"
    monkeypatch.setattr(client_binding, "REMOTE_APPROVALS_PATH", approvals)
    monkeypatch.setattr(client_binding, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "REMOTE_CREDENTIALS_DIR", credentials)
    with pytest.raises(RuntimeError, match="endpoint refused"):
        with launcher._provisional_remote_binding(
            project_root,
            project_id="remote",
            name="Remote",
            api_url="https://203.0.113.10/api",
            dashboard_url="https://203.0.113.10",
            token="private-token",
            replace_existing=True,
        ):
            raise RuntimeError("endpoint refused")

    assert config.read_text() == 'version = 1\nid = "local"\n'
    assert json.loads(approvals.read_text()) == {"version": 1, "bindings": {}}
    assert json.loads(retired.read_text()) == {"project_id": "local"}
    assert not any(credentials.glob("*.token"))


def test_failed_provisional_binding_preserves_concurrent_project_approval(
    monkeypatch, tmp_path
):
    approvals, credentials = isolate_remote_client_state(monkeypatch, tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / ".git").mkdir(parents=True)
    (second / ".git").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="first endpoint refused"):
        with launcher._provisional_remote_binding(
            first,
            project_id="project-first",
            name="First",
            api_url="https://203.0.113.10/api",
            dashboard_url="https://203.0.113.10",
            token="first-private-token",
            replace_existing=False,
        ):
            with launcher._provisional_remote_binding(
                second,
                project_id="project-second",
                name="Second",
                api_url="https://198.51.100.20/api",
                dashboard_url="https://198.51.100.20",
                token="second-private-token",
                replace_existing=False,
            ):
                pass
            raise RuntimeError("first endpoint refused")

    registry = json.loads(approvals.read_text())
    second_binding = client_binding.load_binding(second)
    assert list(registry["bindings"]) == [second_binding.binding_id]
    assert "provisional_id" not in registry["bindings"][second_binding.binding_id]
    assert second_binding.bearer_token == "second-private-token"
    assert not first.joinpath(".dduo-solo-founder/project.toml").exists()
    assert len(list(credentials.glob("*.token"))) == 1


def test_failed_provisional_binding_cannot_restore_over_newer_token_for_same_binding(
    monkeypatch,
    tmp_path,
):
    approvals, credentials = isolate_remote_client_state(monkeypatch, tmp_path)
    project_root = tmp_path / "same-checkout"
    (project_root / ".git").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="first endpoint refused"):
        with launcher._provisional_remote_binding(
            project_root,
            project_id="same-project",
            name="Same project",
            api_url="https://203.0.113.10/api",
            dashboard_url="https://203.0.113.10",
            token="first-private-token",
            replace_existing=False,
        ):
            with launcher._provisional_remote_binding(
                project_root,
                project_id="same-project",
                name="Same project",
                api_url="https://203.0.113.10/api",
                dashboard_url="https://203.0.113.10",
                token="second-private-token",
                replace_existing=True,
            ):
                pass
            raise RuntimeError("first endpoint refused")

    binding = client_binding.load_binding(project_root)
    registry = json.loads(approvals.read_text())
    assert binding.bearer_token == "second-private-token"
    assert list(registry["bindings"]) == [binding.binding_id]
    assert "provisional_id" not in registry["bindings"][binding.binding_id]
    assert len(list(credentials.glob("*.token"))) == 1


def test_remote_rebind_reuses_private_token_and_preserves_member_attribution(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "checkout"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    config_root = tmp_path / "host"
    approvals = config_root / "remote-bindings.json"
    credentials = config_root / "remote-credentials"
    monkeypatch.setattr(client_binding, "REMOTE_APPROVALS_PATH", approvals)
    monkeypatch.setattr(client_binding, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "REMOTE_CREDENTIALS_DIR", credentials)

    client_binding.write_remote_project_config(
        project_root,
        project_id="project-one",
        name="TeamApp",
        api_url="https://203.0.113.10/api",
        dashboard_url="https://203.0.113.10",
    )
    previous = client_binding.approve_remote_binding(
        project_root,
        "stable-private-device-token",
    )
    confirmations = []
    monkeypatch.setattr(
        launcher.typer,
        "confirm",
        lambda message, **_kwargs: confirmations.append(message) or True,
    )

    observed_tokens = []

    def request(_context, method, path, **_kwargs):
        rebound = client_binding.load_binding(project_root)
        observed_tokens.append(rebound.bearer_token)
        assert method == "GET"
        assert path == "/projects/project-one/team"
        return {
            "current_member": {
                "id": "member-stable",
                "project_id": "project-one",
                "access_token_id": "token-stable",
            }
        }

    monkeypatch.setattr(launcher, "_api_request", request)
    result = runner.invoke(
        launcher.app,
        [
            "remote-rebind",
            "--project-id",
            "project-one",
            "--api-url",
            "https://198.51.100.20/api",
            "--dashboard-url",
            "https://198.51.100.20",
            "--project-root",
            str(project_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    rebound = client_binding.load_binding(project_root)
    payload = json.loads(result.stdout)
    assert rebound.binding_id != previous.binding_id
    assert rebound.bearer_token == previous.bearer_token == "stable-private-device-token"
    assert observed_tokens == ["stable-private-device-token"]
    assert payload["current_member"] == {
        "id": "member-stable",
        "project_id": "project-one",
        "access_token_id": "token-stable",
    }
    assert payload["credential_reused"] is True
    assert confirmations and "stable-private-device-token" not in confirmations[0]
    assert "stable-private-device-token" not in result.stdout


def test_remote_bind_success_rebind_idempotence_and_remote_invite(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    (project_root / ".git" / "info").mkdir(parents=True)
    isolate_remote_client_state(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: project_root)
    member = {
        "id": "manager-one",
        "project_id": "project-one",
        "access_token_id": "access-one",
    }
    calls = []

    def request(_context, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith("/invites"):
            return invitation_response(
                project_id="project-one",
                name="TeamApp",
                api_url="https://203.0.113.10/api",
                dashboard_url="https://203.0.113.10",
                code="dduo_inv_" + "i" * 48,
            )
        return {"current_member": member}

    monkeypatch.setattr(launcher, "_api_request", request)
    bound = runner.invoke(
        launcher.app,
        [
            "remote-bind",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://203.0.113.10/api",
            "--dashboard-url",
            "https://203.0.113.10",
            "--token",
            "manager-device-token",
            "--project-root",
            str(project_root),
        ],
    )
    assert bound.exit_code == 0, bound.stdout
    assert json.loads(bound.stdout)["current_member"] == member

    monkeypatch.setattr(
        launcher.typer,
        "confirm",
        lambda *_args, **_kwargs: pytest.fail("unchanged endpoint must be idempotent"),
    )
    rebound = runner.invoke(
        launcher.app,
        [
            "remote-rebind",
            "--project-id",
            "project-one",
            "--api-url",
            "https://203.0.113.10/api",
            "--dashboard-url",
            "https://203.0.113.10",
            "--project-root",
            str(project_root),
        ],
    )
    assert rebound.exit_code == 0, rebound.stdout
    assert json.loads(rebound.stdout)["idempotent"] is True

    monkeypatch.setattr(launcher, "start_stack", lambda *_args, **_kwargs: None)
    invited = runner.invoke(
        launcher.app,
        [
            "team-invite",
            "--display-name",
            "Sam",
            "--project-root",
            str(project_root),
        ],
    )
    assert invited.exit_code == 0, invited.stdout
    command_line = next(
        line
        for line in invited.stdout.splitlines()
        if line.startswith("dduo-solo-founder remote-join")
    )
    descriptor = command_line.split("--invite-payload ", 1)[1].split(" ", 1)[0]
    assert launcher._decode_invite_payload(descriptor) == {
        "project_id": "project-one",
        "name": "TeamApp",
        "api_url": "https://203.0.113.10/api",
        "dashboard_url": "https://203.0.113.10",
        "invitation_code": "dduo_inv_" + "i" * 48,
    }
    assert "--api-url" not in command_line
    assert "--dashboard-url" not in command_line
    assert "--invitation-code" not in invited.stdout
    assert "dduo_inv_" not in invited.stdout
    assert calls[-1][2]["json_body"] == {
        "display_name": "Sam",
        "expires_in_hours": 24,
        "language": "it",
        "api_url": "https://203.0.113.10/api",
        "dashboard_url": "https://203.0.113.10",
    }
    assert [path for _method, path, _kwargs in calls] == [
        "/projects/project-one/team",
        "/projects/project-one/team",
        "/projects/project-one/team/invites",
    ]


def test_remote_rebind_rejects_local_or_mismatched_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _root: {"id": "p1"})
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=False,
            bearer_token=None,
            project_id="p1",
        ),
    )
    local = runner.invoke(
        launcher.app,
        [
            "remote-rebind",
            "--project-id",
            "p1",
            "--api-url",
            "https://203.0.113.10/api",
            "--dashboard-url",
            "https://203.0.113.10",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert local.exit_code == 1
    assert "no approved remote credential" in str(local.exception)

    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=True,
            bearer_token="token",
            project_id="other",
        ),
    )
    mismatched = runner.invoke(
        launcher.app,
        [
            "remote-rebind",
            "--project-id",
            "p1",
            "--api-url",
            "https://203.0.113.10/api",
            "--dashboard-url",
            "https://203.0.113.10",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert mismatched.exit_code == 1
    assert "does not match" in str(mismatched.exception)


def test_failed_invite_retains_only_unapproved_retry_token(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    (project_root / ".dduo-solo-founder").mkdir(parents=True)
    approvals = tmp_path / "host" / "remote-bindings.json"
    credentials = tmp_path / "host" / "credentials"
    monkeypatch.setattr(client_binding, "REMOTE_APPROVALS_PATH", approvals)
    monkeypatch.setattr(client_binding, "REMOTE_CREDENTIALS_DIR", credentials)
    monkeypatch.setattr(launcher, "REMOTE_CREDENTIALS_DIR", credentials)
    with pytest.raises(RuntimeError):
        with launcher._provisional_remote_binding(
            project_root,
            project_id="remote",
            name="Remote",
            api_url="https://203.0.113.10/api",
            dashboard_url="https://203.0.113.10",
            token="retry-token",
            replace_existing=False,
            preserve_new_credential_on_failure=True,
        ):
            raise RuntimeError("response lost")

    credential = launcher._candidate_remote_credential_path(
        project_root,
        project_id="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url="https://203.0.113.10",
    )
    assert credential.read_text().strip() == "retry-token"
    assert not approvals.exists()


def test_remote_join_reuses_pending_private_token_after_lost_response(monkeypatch, tmp_path):
    project_root = tmp_path / "checkout"
    (project_root / ".git" / "info").mkdir(parents=True)
    isolate_remote_client_state(monkeypatch, tmp_path)
    credential = launcher._candidate_remote_credential_path(
        project_root,
        project_id="project-one",
        api_url="https://203.0.113.10/api",
        dashboard_url="https://203.0.113.10",
    )
    credential.parent.mkdir(parents=True)
    credential.write_text("retry-device-token")
    credential.chmod(0o600)
    monkeypatch.setattr(launcher, "machine_device_id", lambda: "device-one")
    observed = {}

    def request(_context, _method, _path, **kwargs):
        observed.update(kwargs["json_body"])
        return {
            "current_member": {
                "id": "member-one",
                "project_id": "project-one",
                "access_token_id": "access-one",
            }
        }

    monkeypatch.setattr(launcher, "_api_request", request)
    result = runner.invoke(
        launcher.app,
        [
            "remote-join",
            "--project-id",
            "project-one",
            "--name",
            "TeamApp",
            "--api-url",
            "https://203.0.113.10/api",
            "--dashboard-url",
            "https://203.0.113.10",
            "--invitation-code",
            "dduo_inv_" + "a" * 48,
            "--project-root",
            str(project_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert observed["device_token"] == "retry-device-token"


def test_remote_binding_snapshot_rejects_symlinks_and_restores_modes(tmp_path):
    source = tmp_path / "state"
    source.write_text("before")
    source.chmod(0o640)
    snapshot = launcher._file_snapshot(source)
    source.write_text("after")
    source.chmod(0o600)

    launcher._restore_file_snapshot(source, snapshot)

    assert source.read_text() == "before"
    if os.name != "nt":
        assert source.stat().st_mode & 0o777 == 0o640

    created = tmp_path / "created"
    created.write_text("keep during invitation retry")
    launcher._restore_file_snapshot(
        created,
        (False, b"", None),
        preserve_new_file=True,
    )
    assert created.exists()
    launcher._restore_file_snapshot(created, (False, b"", None))
    assert not created.exists()

    target = tmp_path / "target"
    target.write_text("target")
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    with pytest.raises(RuntimeError, match="not a regular file"):
        launcher._file_snapshot(symlink)
    with pytest.raises(RuntimeError, match="private dDuo file is unsafe"):
        launcher._atomic_private_bytes(symlink, b"replacement")
    assert target.read_text() == "target"


def test_team_invite_prints_project_scoped_prompt_without_vps_credentials(monkeypatch, tmp_path):
    unsafe_name = 'TeamApp $(touch /tmp/nope) `whoami` "quoted"\nnext'
    project = {"id": "p1", "name": unsafe_name, "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: {
            "invitation": {"invitation_code": "dduo_inv_" + "x" * 48}
        },
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(
        launcher,
        "_gateway_project",
        lambda _: {
            "api_url": "https://8.8.8.8/api",
            "dashboard_url": "https://8.8.8.8",
        },
    )
    result = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "remote-join" in result.stdout
    assert "--invite-payload" in result.stdout
    assert REMOTE_REPOSITORY_URL in result.stdout
    command_line = next(
        line for line in result.stdout.splitlines() if line.startswith("dduo-solo-founder remote-join")
    )
    descriptor = command_line.split("--invite-payload ", 1)[1].split(" ", 1)[0]
    decoded = launcher._decode_invite_payload(descriptor)
    assert decoded["name"] == unsafe_name
    assert decoded["invitation_code"].startswith("dduo_inv_")
    assert "$(touch" not in command_line
    assert "`whoami`" not in command_line
    assert "$(touch" not in result.stdout
    assert "`whoami`" not in result.stdout
    assert "SSH" in result.stdout
    assert "DDUO_INFRASTRUCTURE_TOKEN" not in result.stdout
    assert "dduo-solo-founder dashboard --tab tasks --project-root ." in result.stdout
    assert "nuova chat o sessione nella stessa root" in result.stdout
    assert "chiudere e riaprire completamente Codex" in result.stdout
    assert "/?project=" not in result.stdout


def test_team_invite_rejects_a_bundle_for_different_remote_endpoints(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=True,
            api_url="https://memory.example/api",
            dashboard_url="https://memory.example",
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: invitation_response(
            project_id="p1",
            name="TeamApp",
            api_url="https://other.example/api",
            dashboard_url="https://other.example",
            code="dduo_inv_" + "x" * 48,
        ),
    )

    result = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "does not match this project" in str(result.exception)


def test_team_invite_uses_the_server_release_and_requested_language(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "Old bootstrap name", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=True,
            api_url="https://memory.example/api",
            dashboard_url="https://memory.example",
        ),
    )
    requests = []

    def invite_request(*_args, **kwargs):
        requests.append(kwargs["json_body"])
        return invitation_response(
            project_id="p1",
            name="TeamApp",
            api_url="https://memory.example/api",
            dashboard_url="https://memory.example",
            code="dduo_inv_" + "x" * 48,
            language="en",
            release_version="9.8.7-beta.6",
        )

    monkeypatch.setattr(launcher, "_api_request", invite_request)

    result = runner.invoke(
        launcher.app,
        [
            "team-invite",
            "--display-name",
            "Sam",
            "--language",
            "en",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert requests[0]["language"] == "en"
    assert "You received access to the shared memory" in result.stdout
    assert "release v9.8.7-beta.6" in result.stdout
    assert "fully quit and reopen Codex" in result.stdout

    old_server_response = invitation_response(
        project_id="p1",
        name="TeamApp",
        api_url="https://memory.example/api",
        dashboard_url="https://memory.example",
        code="dduo_inv_" + "y" * 48,
    )
    old_server_response["invitation"].pop("release_version")
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: old_server_response,
    )

    old_server = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
    )

    assert old_server.exit_code == 1
    assert "authority is too old" in str(old_server.exception)


def test_team_invite_rejects_invalid_language_and_untrusted_server_metadata(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=True,
            api_url="https://memory.example/api",
            dashboard_url="https://memory.example",
        ),
    )

    invalid_language = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--language", "fr"],
    )
    assert invalid_language.exit_code != 0
    assert "must be 'en' or 'it'" in invalid_language.output

    payload_response = invitation_response(
        project_id="p1",
        name="TeamApp",
        api_url="https://memory.example/api",
        dashboard_url="https://memory.example",
        code="dduo_inv_" + "z" * 48,
    )

    malformed_responses = [
        ({"invitation": None}, "response is malformed"),
        (
            {
                "invitation": {
                    **payload_response["invitation"],
                    "release_version": "main",
                }
            },
            "invalid invitation release metadata",
        ),
        (
            {
                "invitation": {
                    **payload_response["invitation"],
                    "setup_prompt": "untrusted replacement",
                }
            },
            "invalid setup prompt",
        ),
        ({"invitation": {}}, "response is malformed"),
    ]
    for response, message in malformed_responses:
        monkeypatch.setattr(
            launcher,
            "_api_request",
            lambda *_args, _response=response, **_kwargs: _response,
        )
        result = runner.invoke(
            launcher.app,
            ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert message in str(result.exception)


def test_team_invite_requires_a_gateway_and_dashboard(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _root: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: {
            "invitation": {"invitation_code": "dduo_inv_" + "x" * 48}
        },
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "_gateway_project", lambda _project_id: None)
    missing = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
    )
    assert missing.exit_code == 1
    assert "not registered" in str(missing.exception)

    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: SimpleNamespace(
            remote=True,
            api_url="https://203.0.113.10/api",
            dashboard_url=None,
        ),
    )
    no_dashboard = runner.invoke(
        launcher.app,
        ["team-invite", "--display-name", "Sam", "--project-root", str(tmp_path)],
    )
    assert no_dashboard.exit_code == 1
    assert "dashboard URL is unavailable" in str(no_dashboard.exception)


def test_remote_dashboard_uses_one_time_ticket_and_redacts_output(monkeypatch, tmp_path):
    project = {
        "id": "p1",
        "name": "TeamApp",
        "binding": "remote",
        "api_url": "https://8.8.8.8/api",
        "dashboard_url": "https://8.8.8.8",
    }
    binding = SimpleNamespace(
        remote=True,
        dashboard_link=lambda tab: f"https://8.8.8.8/?project=p1&tab={tab}",
    )
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "binding_from_project", lambda *args, **kwargs: binding)
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: {"ticket": "dduo_web_private-ticket"},
    )
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    result = runner.invoke(
        launcher.app,
        ["dashboard", "--project-root", str(tmp_path), "--tab", "team"],
    )
    assert result.exit_code == 0
    assert "dduo_web_private-ticket" in opened[0]
    assert "dduo_web_private-ticket" not in result.stdout
    assert "ticket=<one-time>" in result.stdout


def test_dashboard_covers_vps_host_local_and_invalid_routes(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    local_binding = SimpleNamespace(remote=False)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *_args, **_kwargs: local_binding,
    )

    host_project = {
        "id": "p1",
        "name": "Hosted",
        "deployment": "remote",
        "api_port": 18001,
        "web_port": 20001,
    }
    monkeypatch.setattr(launcher, "load_project", lambda _root: host_project)
    monkeypatch.setattr(
        launcher,
        "_gateway_project",
        lambda _project_id: {"dashboard_url": "https://203.0.113.10:9443"},
    )
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *_args, **_kwargs: {"ticket": "private-host-ticket"},
    )
    hosted = runner.invoke(
        launcher.app,
        ["dashboard", "--project-root", str(tmp_path), "--tab", "observability"],
    )
    assert hosted.exit_code == 0
    assert "private-host-ticket" in opened[-1]
    assert "private-host-ticket" not in hosted.stdout

    monkeypatch.setattr(launcher, "_gateway_project", lambda _project_id: None)
    missing_gateway = runner.invoke(
        launcher.app,
        ["dashboard", "--project-root", str(tmp_path), "--tab", "tasks"],
    )
    assert missing_gateway.exit_code == 1
    assert "not registered" in str(missing_gateway.exception)

    local_project = {
        "id": "p1",
        "name": "Local",
        "api_port": 18001,
        "web_port": 20001,
    }
    monkeypatch.setattr(launcher, "load_project", lambda _root: local_project)
    monkeypatch.setattr(
        launcher,
        "project_dashboard_url",
        lambda project, tab: f"http://127.0.0.1:20001/?project={project['id']}&tab={tab}",
    )
    local = runner.invoke(
        launcher.app,
        ["dashboard", "--project-root", str(tmp_path), "--tab", "memory"],
    )
    assert local.exit_code == 0
    assert opened[-1].endswith("project=p1&tab=memory")

    invalid = runner.invoke(
        launcher.app,
        ["dashboard", "--project-root", str(tmp_path), "--tab", "secrets"],
    )
    assert invalid.exit_code == 2


def test_remote_host_orchestrates_isolated_stack_and_gateway(monkeypatch, tmp_path):
    local = {"id": "p1", "name": "TeamApp", "binding": "local", "api_port": 1, "web_port": 2}
    hosted = {**local, "deployment": "remote"}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: local)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "_require_remote_host_resources", lambda: None)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "ensure_project_secret_environment", lambda _: None)
    monkeypatch.setattr(launcher, "_require_remote_sleep_auth", lambda _: "codex")
    monkeypatch.setattr(launcher, "_install_persistent_bridge", lambda: "bridge-token")
    runtime = {
        "OPENAI_API_KEY": "key",
        "DDUO_DATABASE_PASSWORD": "safe-password",
    }
    monkeypatch.setattr(launcher, "load_project_secrets", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(launcher, "ensure_remote_runtime_secrets", lambda _: None)
    phases = []
    monkeypatch.setattr(
        launcher,
        "_prepare_remote_database",
        lambda _root, _project, password: phases.append(("database", password)),
    )
    monkeypatch.setattr(
        launcher,
        "_remote_manager_bootstrap",
        lambda *args, **kwargs: phases.append("bootstrap") or ("owner-token", True),
    )
    monkeypatch.setattr(launcher, "set_local_deployment_mode", lambda *args: hosted)
    monkeypatch.setattr(launcher, "_restart_remote_stack", lambda *args: None)
    monkeypatch.setattr(
        launcher,
        "_claim_remote_authority",
        lambda *args, **kwargs: phases.append("claim")
        or {
            "node_id": "node-one",
            "generation": 1,
            "state": "active",
            "writable": True,
        },
    )
    monkeypatch.setattr(
        launcher,
        "_recover_project_indexes",
        lambda *args: {"memory_queued": 0, "memory_deleted": 0, "tasks_queued": 0},
    )
    monkeypatch.setattr(
        launcher,
        "register_gateway_project",
        lambda *args, **kwargs: {
            "api_url": "https://8.8.8.8/api",
            "dashboard_url": "https://8.8.8.8",
            "https_port": 24443,
        },
    )
    monkeypatch.setattr(launcher, "write_caddyfile", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_gateway_compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    result = runner.invoke(
        launcher.app,
        ["remote-host", "--public-ip", "8.8.8.8", "--project-root", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert '"hosted": true' in result.stdout
    assert "owner-token" in result.stdout
    assert '"firewall_ports": [' in result.stdout
    assert "443" in result.stdout and "24443" in result.stdout
    assert "keep TCP 443 open permanently" in result.stdout
    assert phases == [("database", "safe-password"), "claim", "bootstrap"]


def test_remote_host_refuses_promotion_without_reboot_safe_bridge(monkeypatch, tmp_path):
    project = {
        "id": "p1",
        "name": "TeamApp",
        "binding": "local",
        "api_port": 1,
        "web_port": 2,
    }
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "_require_remote_host_resources", lambda: None)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "ensure_project_secret_environment", lambda _: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"OPENAI_API_KEY": "configured"},
    )
    monkeypatch.setattr(launcher, "_require_remote_sleep_auth", lambda _: "codex")
    monkeypatch.setattr(
        launcher,
        "_install_persistent_bridge",
        lambda: (_ for _ in ()).throw(RuntimeError("enable-linger first")),
    )
    monkeypatch.setattr(
        launcher,
        "ensure_remote_runtime_secrets",
        lambda _: pytest.fail("database credentials must not rotate"),
    )
    monkeypatch.setattr(
        launcher,
        "set_local_deployment_mode",
        lambda *args: pytest.fail("project must not be promoted"),
    )

    result = runner.invoke(
        launcher.app,
        ["remote-host", "--public-ip", "8.8.8.8", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "enable-linger first" in str(result.exception)


def test_persistent_bridge_host_preconditions_are_explicit(monkeypatch, tmp_path):
    unit = tmp_path / "dduo.service"
    monkeypatch.setattr(launcher, "BRIDGE_SYSTEMD_UNIT_FILE", unit)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    assert launcher._persistent_bridge_unit_installed() is False
    with pytest.raises(RuntimeError, match="Linux VPS"):
        launcher._require_persistent_bridge_host()

    monkeypatch.setattr(launcher.sys, "platform", "linux")
    unit.write_text("unit")
    assert launcher._persistent_bridge_unit_installed() is True
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="systemd user services"):
        launcher._require_persistent_bridge_host()

    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(launcher.getpass, "getuser", lambda: "dduo-user")
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="no", stderr=""),
    )
    with pytest.raises(RuntimeError, match="enable-linger dduo-user"):
        launcher._require_persistent_bridge_host()

    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="yes\n", stderr=""),
    )
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=1, stdout="", stderr="no manager"),
    )
    with pytest.raises(RuntimeError, match="user manager is unavailable"):
        launcher._require_persistent_bridge_host()

    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    launcher._require_persistent_bridge_host()


def test_persistent_bridge_readiness_start_and_install_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: False)
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=1, stdout="", stderr="not ready"),
    )
    with pytest.raises(RuntimeError, match="not ready"):
        launcher._wait_for_persistent_bridge("token", timeout=0.5)
    with pytest.raises(RuntimeError, match="could not start"):
        launcher._start_persistent_bridge("token")

    starts = []
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *args: starts.append(args)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_persistent_bridge",
        lambda token: starts.append(("ready", token)),
    )
    launcher._start_persistent_bridge("token")
    assert starts == [
        ("start", launcher.BRIDGE_SYSTEMD_UNIT_NAME),
        ("ready", "token"),
    ]

    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "bridge-token")
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: None)
    monkeypatch.setattr(launcher, "agent_port", lambda: 24567)
    monkeypatch.setattr(launcher, "BRIDGE_SYSTEMD_ENV_FILE", tmp_path / "bridge/agent.env")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", tmp_path / "bridge/agent.port")
    monkeypatch.setattr(
        launcher,
        "BRIDGE_SYSTEMD_UNIT_FILE",
        tmp_path / "systemd/dduo-solo-founder-agent.service",
    )
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="agent is unavailable"):
        launcher._install_persistent_bridge()

    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/bad%agent")
    with pytest.raises(RuntimeError, match="path is unsupported"):
        launcher._install_persistent_bridge()

    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "/usr/bin/dduo-agent")
    monkeypatch.setenv("PATH", "/usr/bin\nunsafe")
    with pytest.raises(RuntimeError, match="PATH cannot be represented"):
        launcher._install_persistent_bridge()
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")

    def systemd_failure(*args):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="reload failed" if args[0] == "daemon-reload" else "enable failed",
        )

    monkeypatch.setattr(launcher, "_systemd_user_command", systemd_failure)
    with pytest.raises(RuntimeError, match="could not be registered"):
        launcher._install_persistent_bridge()

    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *args: SimpleNamespace(
            returncode=0 if args[0] == "daemon-reload" else 1,
            stdout="",
            stderr="enable failed",
        ),
    )
    with pytest.raises(RuntimeError, match="could not be enabled"):
        launcher._install_persistent_bridge()

    waits = []
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_persistent_bridge",
        lambda token: waits.append(token),
    )
    assert launcher._install_persistent_bridge() == "bridge-token"
    assert waits == ["bridge-token"]
    assert "ExecStart=/usr/bin/dduo-agent --host 0.0.0.0" in (
        launcher.BRIDGE_SYSTEMD_UNIT_FILE.read_text()
    )


def test_existing_persistent_bridge_is_started_without_detached_fallback(monkeypatch):
    started = []
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: False)
    monkeypatch.setattr(launcher, "_persistent_bridge_unit_installed", lambda: True)
    monkeypatch.setattr(
        launcher,
        "_start_persistent_bridge",
        lambda token: started.append(token),
    )
    assert launcher.ensure_cli_bridge() == "token"
    assert started == ["token"]


def test_agent_lock_is_private_reclaims_stale_owner_and_times_out(monkeypatch, tmp_path):
    bridge_dir = tmp_path / "bridge"
    lock_file = bridge_dir / "agent.lock"
    monkeypatch.setattr(launcher, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(launcher, "BRIDGE_LOCK_FILE", lock_file)

    with launcher._agent_lock():
        assert lock_file.read_text() == str(os.getpid())
        assert lock_file.stat().st_mode & 0o777 == 0o600
    assert not lock_file.exists()
    assert bridge_dir.stat().st_mode & 0o777 == 0o700

    lock_file.write_text("stale-owner")
    os.utime(lock_file, (1, 1))
    monkeypatch.setattr(launcher.time, "time", lambda: 100.0)
    with launcher._agent_lock(timeout=1):
        assert lock_file.read_text() == str(os.getpid())
    assert not lock_file.exists()

    lock_file.write_text("live-owner")
    monkeypatch.setattr(launcher.time, "time", lambda: 1.0)
    clock = iter((10.0, 12.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="still starting"):
        with launcher._agent_lock(timeout=1):
            pytest.fail("a live owner must keep the lock")


def test_ensure_cli_bridge_activates_fresh_detached_agent_atomically(monkeypatch, tmp_path):
    bridge_dir = tmp_path / "bridge"
    log_file = bridge_dir / "agent.log"
    port_file = bridge_dir / "agent.port"
    pid_file = bridge_dir / "agent.pid"
    bridge_dir.mkdir()
    monkeypatch.setattr(launcher, "BRIDGE_LOG_FILE", log_file)
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", port_file)
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", pid_file)
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "bridge-secret")
    readiness = iter((False, False))
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: next(readiness))
    monkeypatch.setattr(launcher, "_persistent_bridge_unit_installed", lambda: False)
    monkeypatch.setattr(launcher, "_agent_lock", lambda: nullcontext())
    monkeypatch.setattr(launcher, "agent_port", lambda: None)
    ports = iter((41_001, 41_002, 41_003))
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: next(ports))
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda name: "/opt/dduo/bin/dduo-solo-founder-agent"
        if name == "dduo-solo-founder-agent"
        else None,
    )
    captured = {}

    class Process:
        pid = 9876

        def poll(self):
            pytest.fail("a ready process must not be polled for early exit")

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Process()

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        launcher.httpx,
        "get",
        lambda url, **kwargs: captured.update(url=url, request=kwargs)
        or SimpleNamespace(status_code=200),
    )

    assert launcher.ensure_cli_bridge() == "bridge-secret"
    assert captured["command"] == [
        "/opt/dduo/bin/dduo-solo-founder-agent",
        "--host",
        "0.0.0.0",
        "--port",
        "41001",
    ]
    assert captured["env"]["DDUO_CLI_BRIDGE_TOKEN"] == "bridge-secret"
    assert captured["url"] == "http://127.0.0.1:41001/health"
    assert port_file.read_text() == "41001\n"
    assert pid_file.read_text() == "9876\n"
    assert port_file.stat().st_mode & 0o777 == 0o600
    assert pid_file.stat().st_mode & 0o777 == 0o600


def test_ensure_cli_bridge_rechecks_under_lock_and_exhausts_dead_candidates(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(launcher, "ensure_bridge_token", lambda: "token")
    monkeypatch.setattr(launcher, "_persistent_bridge_unit_installed", lambda: False)
    monkeypatch.setattr(launcher, "_agent_lock", lambda: nullcontext())
    readiness = iter((False, True))
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: next(readiness))
    assert launcher.ensure_cli_bridge() == "token"

    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    monkeypatch.setattr(launcher, "BRIDGE_LOG_FILE", bridge_dir / "agent.log")
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", bridge_dir / "agent.port")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", bridge_dir / "agent.pid")
    monkeypatch.setattr(launcher, "bridge_ready", lambda _token: False)
    monkeypatch.setattr(launcher, "agent_port", lambda: 42_000)
    stopped = []
    monkeypatch.setattr(launcher, "stop_cli_bridge", lambda: stopped.append(True) or True)
    monkeypatch.setattr(launcher.shutil, "which", lambda _name: None)
    ports = iter((42_001, 42_002))
    monkeypatch.setattr(launcher, "_available_agent_port", lambda: next(ports))
    commands = []

    class DeadProcess:
        pid = 123

        @staticmethod
        def poll():
            return 1

    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **_kwargs: commands.append(command) or DeadProcess(),
    )
    ticks = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))

    def unavailable(*_args, **_kwargs):
        raise launcher.httpx.ConnectError("not listening")

    monkeypatch.setattr(launcher.httpx, "get", unavailable)
    with pytest.raises(RuntimeError, match="could not start"):
        launcher.ensure_cli_bridge()

    assert stopped == [True]
    assert [command[-1] for command in commands] == ["42000", "42001", "42002"]
    assert all(command[:3] == [launcher.sys.executable, "-m", "dduo_solo_founder.cli_bridge"] for command in commands)


def test_bridge_open_and_stop_paths_are_explicit(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(launcher, "setup_url", lambda _root=None: "http://127.0.0.1/setup?t=x")
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    assert launcher.open_setup(tmp_path) == "http://127.0.0.1/setup?t=x"
    assert opened == ["http://127.0.0.1/setup?t=x"]

    monkeypatch.setattr(launcher.webbrowser, "open", lambda _url: False)
    with pytest.raises(RuntimeError, match="host browser could not open dDuo Setup"):
        launcher.open_setup(tmp_path)

    monkeypatch.setattr(launcher, "_persistent_bridge_unit_installed", lambda: True)
    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=1, stdout="", stderr="permission denied"),
    )
    with pytest.raises(RuntimeError, match="permission denied"):
        launcher.stop_cli_bridge()

    monkeypatch.setattr(
        launcher,
        "_systemd_user_command",
        lambda *_args: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "")
    assert launcher.stop_cli_bridge() is True

    pid_file = tmp_path / "agent.pid"
    pid_file.write_text("99\n")
    monkeypatch.setattr(launcher, "BRIDGE_PID_FILE", pid_file)
    monkeypatch.setattr(launcher, "_persistent_bridge_unit_installed", lambda: False)
    monkeypatch.setattr(launcher, "existing_bridge_token", lambda: "bridge-token")
    monkeypatch.setattr(launcher, "agent_url", lambda: "http://127.0.0.1:4242")
    monkeypatch.setattr(
        launcher.httpx,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=202),
    )
    assert launcher.stop_cli_bridge() is True
    assert not pid_file.exists()

    def unreachable(*_args, **_kwargs):
        raise launcher.httpx.ConnectError("offline")

    monkeypatch.setattr(launcher.httpx, "post", unreachable)
    assert launcher.stop_cli_bridge() is False


def test_agent_and_bound_api_health_checks_validate_protocol_and_transport(
    monkeypatch, tmp_path
):
    port_file = tmp_path / "agent.port"
    monkeypatch.setattr(launcher, "BRIDGE_PORT_FILE", port_file)
    assert launcher.agent_port() is None
    for invalid in ("text", "1023", "65536"):
        port_file.write_text(invalid)
        assert launcher.agent_port() is None
    port_file.write_text("43210\n")
    assert launcher.agent_port() == 43_210
    assert launcher.agent_url("localhost") == "http://localhost:43210"

    responses = iter(
        (
            SimpleNamespace(
                status_code=200,
                json=lambda: {"bridge_protocol_version": launcher.BRIDGE_PROTOCOL_VERSION},
            ),
            SimpleNamespace(status_code=200, json=lambda: {"bridge_protocol_version": 0}),
            SimpleNamespace(
                status_code=200,
                json=lambda: (_ for _ in ()).throw(ValueError("invalid json")),
            ),
        )
    )
    monkeypatch.setattr(launcher.httpx, "get", lambda *_args, **_kwargs: next(responses))
    assert launcher.bridge_ready("secret") is True
    assert launcher.bridge_ready("secret") is False
    assert launcher.bridge_ready("secret") is False

    binding = SimpleNamespace(project_id="p1")
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _root: tmp_path)
    monkeypatch.setattr(
        launcher, "binding_from_project", lambda *_args, **_kwargs: binding
    )

    class Client:
        @staticmethod
        def request(*_args, **_kwargs):
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: Client())
    assert launcher._api_healthy({"id": "p1", "root_path": str(tmp_path)}) is True

    class OfflineClient:
        @staticmethod
        def request(*_args, **_kwargs):
            raise launcher.httpx.ConnectError("offline")

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: OfflineClient())
    assert launcher._api_healthy({"id": "p1"}, tmp_path) is False


def test_remote_database_preparation_is_replay_safe(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 1, "web_port": 2}
    calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda _project, *args, **kwargs: calls.append(("compose", args))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        launcher,
        "_wait_for_postgres",
        lambda context: calls.append(("wait", context["id"])),
    )
    monkeypatch.setattr(
        launcher,
        "_rotate_database_password",
        lambda root, value, password: calls.append(
            ("rotate", root, value["id"], password)
        ),
    )

    launcher._prepare_remote_database(tmp_path, project, "durable-candidate")
    launcher._prepare_remote_database(tmp_path, project, "durable-candidate")

    assert calls == [
        ("compose", ("up", "-d", "postgres", "qdrant")),
        ("wait", "p1"),
        ("rotate", tmp_path, "p1", "durable-candidate"),
        ("compose", ("up", "-d", "postgres", "qdrant")),
        ("wait", "p1"),
        ("rotate", tmp_path, "p1", "durable-candidate"),
    ]


def test_remote_stack_database_index_and_health_failure_paths(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 1, "web_port": 2}

    with pytest.raises(RuntimeError, match="unsupported characters"):
        launcher._rotate_database_password(tmp_path, project, "unsafe password")

    compose_calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda _project, *args, **kwargs: compose_calls.append((args, kwargs))
        or SimpleNamespace(returncode=1, stdout="", stderr="database unavailable"),
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        launcher._prepare_remote_database(tmp_path, project, "safe-password")
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="database password rotation failed"):
        launcher._rotate_database_password(tmp_path, project, "safe-password")

    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "bridge-token")
    with pytest.raises(RuntimeError, match="failed with exit code 1"):
        launcher._restart_remote_stack(tmp_path, project)

    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    clock = iter([0, 0, 181, 0, 181])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: False)
    with pytest.raises(RuntimeError, match="did not become healthy"):
        launcher._restart_remote_stack(tmp_path, project)

    wait_clock = iter([0, 2])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(wait_clock))
    with pytest.raises(RuntimeError, match="within 1 seconds"):
        launcher._wait_for_api(tmp_path, project, timeout=1)

    requests = []
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda _context, method, path, **_kwargs: requests.append((method, path))
        or ({"queued": "2", "deleted": 1} if "memories" in path else {"queued": 3}),
    )
    assert launcher._recover_project_indexes(tmp_path, project) == {
        "memory_queued": 2,
        "memory_deleted": 1,
        "tasks_queued": 3,
    }
    assert requests == [
        ("POST", "/projects/p1/memories/reconcile"),
        ("POST", "/projects/p1/tasks/reindex?origin=restore"),
    ]


def test_remote_authority_preflights_fail_closed_before_mutation(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "load_project_secrets", lambda *args, **kwargs: {})
    with pytest.raises(RuntimeError, match="credentials were not initialized"):
        launcher._remote_manager_bootstrap(tmp_path, project, owner_name="Owner")

    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: None)
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(launcher, "_api_healthy", lambda *_args: True)
    launcher._restart_remote_stack(tmp_path, project)
    launcher._wait_for_api(tmp_path, project, timeout=1)

    with pytest.raises(RuntimeError, match="invalid response"):
        launcher._validate_final_transfer_backup(None, "p1", "authority")
    with pytest.raises(RuntimeError, match="proof is malformed"):
        launcher._verified_retirement_receipt(None, "p1", "authority")
    with pytest.raises(RuntimeError, match="proof is malformed"):
        launcher._verified_retirement_receipt({}, "p1", "authority")

    receipt, finalized = finalization_proof()
    finalized["finalization_receipt"] = receipt
    finalized["generation"] = 6
    with pytest.raises(RuntimeError, match="does not match"):
        launcher._verified_retirement_receipt(finalized, "p1", "authority-secret")

    with pytest.raises(RuntimeError, match="verified full-project-v2 restore"):
        launcher._verified_recovery_marker(tmp_path, "p1")

    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-new")
    with pytest.raises(RuntimeError, match="authority credential is unavailable"):
        launcher._claim_remote_authority(tmp_path, project)

    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/projects/p1/authority/status"
            assert kwargs["headers"]["X-DDUO-Authority"] == "authority-secret"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"state": "uninitialized", "generation": 0},
            )

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: Client())
    with pytest.raises(RuntimeError, match="transfer-pending"):
        launcher._claim_remote_authority(
            tmp_path,
            project,
            finalization_receipt="receipt",
        )


def test_login_codex_imports_only_the_explicitly_selected_profile(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    selected_profile = tmp_path / "selected-profile"
    private_profile = tmp_path / "private-project-profile"
    executable = tmp_path / "codex"
    descriptor = SimpleNamespace(
        config_dir=selected_profile,
        management_command=SimpleNamespace(node_executable=None),
    )
    seen = {}
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "synthetic-project"})
    monkeypatch.setattr(launcher, "_runtime_binding", lambda *_: SimpleNamespace(remote=False))
    def resolve(_family, **kwargs):
        assert kwargs["config_dir"] == selected_profile
        assert kwargs["executable"] == executable
        return descriptor
    monkeypatch.setattr(launcher, "resolve_client_installation", resolve)
    monkeypatch.setattr(launcher, "resolve_codex_executable", lambda **_: str(executable))
    monkeypatch.setattr(launcher, "ensure_project_codex_home", lambda _id, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(launcher, "codex_environment", lambda *_: {"CODEX_HOME": str(private_profile)})
    monkeypatch.setattr(launcher, "client_command", lambda command, args, **_: [command, *args])
    def run(command, **kwargs):
        assert command == [str(executable), "login", "--device-auth"]
        assert kwargs["env"] == {"CODEX_HOME": str(private_profile)}
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    def auth(_client, **kwargs):
        assert kwargs["installation"] is descriptor
        assert kwargs["environment"] == {"CODEX_HOME": str(private_profile)}
        return SimpleNamespace(ready=True)
    monkeypatch.setattr(launcher, "subscription_auth_status", auth)
    result = CliRunner().invoke(launcher.app, [
        "login-codex", "--project-root", str(tmp_path), "--device-auth",
        "--client-config-dir", str(selected_profile), "--client-executable", str(executable),
    ])
    assert result.exit_code == 0, result.output
    assert seen == {"import_global_auth": True, "source_codex_home": selected_profile}


@pytest.mark.parametrize("returncode,authenticated,expected", [(0, True, 0), (7, False, 7), (0, False, 8)])
def test_claude_login_uses_one_descriptor_and_only_project_private_auth(
    monkeypatch, tmp_path, returncode, authenticated, expected
):
    from dduo_solo_founder.client_installation import ClientCommand, ClientInstallation

    descriptor = ClientInstallation(
        "claude", "vscode", tmp_path / "interactive",
        ClientCommand(tmp_path / "claude", node_executable=tmp_path / "node"),
    )
    environment = {"CLAUDE_CONFIG_DIR": str(tmp_path / "private")}
    calls = []
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "synthetic"})
    monkeypatch.setattr(launcher, "_runtime_binding", lambda *_: SimpleNamespace(remote=False))
    monkeypatch.setattr(launcher, "resolve_client_installation", lambda *_, **__: descriptor)
    monkeypatch.setattr(launcher, "ensure_project_claude_config_dir", lambda p: calls.append(("private", p)))
    monkeypatch.setattr(launcher, "claude_environment", lambda *_: environment)
    def command(executable, args, **kwargs):
        assert executable == str(descriptor.management_command.executable)
        assert kwargs == {"client": "claude", "node_executable": str(tmp_path / "node")}
        return [executable, *args]
    def run(argv, **kwargs):
        assert argv[1:] == ["auth", "login", "--claudeai"]
        assert kwargs == {"env": environment, "check": False}
        return SimpleNamespace(returncode=returncode)
    def auth(provider, **kwargs):
        assert provider == "claude"
        assert kwargs == {"environment": environment, "installation": descriptor}
        calls.append(("verified", provider))
        return SimpleNamespace(ready=authenticated)
    monkeypatch.setattr(launcher, "client_command", command)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(launcher, "subscription_auth_status", auth)
    result = runner.invoke(launcher.app, ["login-claude", "--project-root", str(tmp_path)])
    assert result.exit_code == expected, result.output
    assert calls[0] == ("private", "synthetic")
    assert (("verified", "claude") in calls) == (returncode == 0)


@pytest.mark.parametrize("reason", ["remote", "runtime"])
def test_claude_login_refuses_remote_checkout_and_invalid_runtime_before_auth(monkeypatch, tmp_path, reason):
    from dduo_solo_founder.client_installation import ClientInstallationError

    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "synthetic"})
    monkeypatch.setattr(launcher, "_runtime_binding", lambda *_: SimpleNamespace(remote=reason == "remote"))
    def resolve(*_, **__):
        raise ClientInstallationError("selected runtime needs repair")
    monkeypatch.setattr(launcher, "resolve_client_installation", resolve)
    monkeypatch.setattr(launcher, "ensure_project_claude_config_dir", lambda _: pytest.fail("auth mutated"))
    result = runner.invoke(launcher.app, ["login-claude", "--project-root", str(tmp_path)])
    assert result.exit_code == 2
    assert ("memory host" if reason == "remote" else "needs repair") in result.output


def test_vps_resource_readers_reject_unreadable_or_malformed_host_data(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    for read in (launcher._linux_memory_totals, launcher._linux_disk_swap_total):
        with pytest.raises(RuntimeError, match="could not inspect"):
            read(missing)
    with pytest.raises(RuntimeError, match="could not inspect"):
        launcher._linux_filesystem_free(tmp_path, missing)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: unknown kB\nSwapTotal: 1024 kB\n")
    with pytest.raises(RuntimeError, match="incomplete"):
        launcher._linux_memory_totals(meminfo)
    meminfo.write_text("MemTotal: 4096 bytes\nSwapTotal: 2048\n")
    assert launcher._linux_memory_totals(meminfo) == (4096, 2048 * 1024)
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename Type Size Used Priority\ninvalid\n/dev/zram0 partition 100 0 0\n/swapfile file 2048 0 -1\n")
    assert launcher._linux_disk_swap_total(swaps) == 2048 * 1024
    for code, path in [(1, ""), (0, "relative")]:
        monkeypatch.setattr(launcher.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=code, stdout=path))
        with pytest.raises(RuntimeError, match="Docker storage"):
            launcher._docker_storage_root()
    mounts = tmp_path / "mountinfo"
    mounts.write_text("invalid\n1 2 0:1 / /nonexistent rw\n")
    with pytest.raises(RuntimeError, match="could not resolve"):
        launcher._linux_filesystem_free(tmp_path, mounts)
    mounts.write_text(f"1 2 0:1 / {tmp_path} rw\n")
    def no_disk(_):
        raise OSError("unmounted")
    monkeypatch.setattr(launcher.shutil, "disk_usage", no_disk)
    with pytest.raises(RuntimeError, match="free space"):
        launcher._linux_filesystem_free(tmp_path, mounts)


def test_remote_sleep_auth_is_a_preflight_not_a_runtime_surprise(monkeypatch, tmp_path):
    home = tmp_path / "codex"
    descriptors = {provider: object() for provider in ("codex", "claude")}
    monkeypatch.setattr(launcher, "resolve_client_installation", descriptors.__getitem__)
    monkeypatch.setattr(launcher, "ensure_project_codex_home", lambda _, **kwargs: home)
    monkeypatch.setattr(
        launcher,
        "codex_environment",
        lambda project_id, environment: {**environment, "CODEX_HOME": str(home)},
    )
    seen = []

    def status(client, *, environment, installation):
        assert installation is descriptors[client]
        seen.append({"client": client, "environment": environment})
        return SimpleNamespace(ready=False, reason="login_required")

    monkeypatch.setattr(launcher, "subscription_auth_status", status)
    with pytest.raises(RuntimeError, match="Codex or Claude subscription login") as error:
        launcher._require_remote_sleep_auth("p1")
    assert [item["client"] for item in seen] == ["codex", "claude"]
    assert seen[0]["environment"]["CODEX_HOME"] == str(home)
    assert "login-codex --device-auth --project-root" in str(error.value)

    monkeypatch.setattr(
        launcher,
        "subscription_auth_status",
        lambda *_args, **_kwargs: SimpleNamespace(ready=True, reason="ready"),
    )
    assert launcher._require_remote_sleep_auth("p1") == "codex"


def test_remote_sleep_preflight_uses_available_managed_provider_with_empty_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "")
    descriptor = object()
    def resolve(provider):
        if provider == "codex":
            raise launcher.ClientInstallationError("Codex is absent")
        return descriptor
    monkeypatch.setattr(launcher, "resolve_client_installation", resolve)
    environment = {"CLAUDE_CONFIG_DIR": str(tmp_path / "project-private")}
    monkeypatch.setattr(launcher, "claude_environment", lambda *_: environment)
    def status(provider, **kwargs):
        assert provider == "claude"
        assert kwargs == {"environment": environment, "installation": descriptor}
        return SimpleNamespace(ready=True)
    monkeypatch.setattr(launcher, "subscription_auth_status", status)
    assert launcher._require_remote_sleep_auth("p1") == "claude"


def test_remote_manager_bootstrap_discards_pending_token_for_existing_manager(
    monkeypatch, tmp_path
):
    project = {
        "id": "p1",
        "name": "TeamApp",
        "binding": "local",
        "deployment": "remote",
        "api_port": 1,
        "web_port": 2,
    }
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {
            "DDUO_INFRASTRUCTURE_TOKEN": "infrastructure-token",
            "DDUO_NODE_AUTHORITY_SECRET": "authority-secret",
        },
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-one")
    state = []
    monkeypatch.setattr(launcher, "load_pending_manager_bootstrap", lambda _: None)
    monkeypatch.setattr(
        launcher,
        "save_pending_manager_bootstrap",
        lambda *args, **kwargs: state.append(("saved", kwargs)),
    )
    monkeypatch.setattr(
        launcher,
        "clear_pending_manager_bootstrap",
        lambda _: state.append(("cleared", {})),
    )
    monkeypatch.setattr(
        launcher,
        "_new_device_token",
        lambda: "dduo_dev_" + "x" * 48,
    )
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"idempotent": True}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return Response()

    monkeypatch.setattr(launcher, "ProjectHttpClient", Client)

    token, bootstrapped = launcher._remote_manager_bootstrap(
        tmp_path, project, owner_name="Owner"
    )

    assert token is None and bootstrapped is False
    assert len(calls) == 1
    assert calls[0][0:2] == ("POST", "/projects/p1/team/bootstrap")
    assert calls[0][2]["headers"] == {"X-DDUO-Authority": "authority-secret"}
    assert [item[0] for item in state] == ["saved", "cleared"]


def test_remote_manager_bootstrap_replays_pending_token_after_late_failure(
    monkeypatch, tmp_path
):
    project = {
        "id": "p1",
        "name": "TeamApp",
        "binding": "local",
        "deployment": "remote",
        "api_port": 1,
        "web_port": 2,
    }
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {
            "DDUO_INFRASTRUCTURE_TOKEN": "infrastructure-token",
            "DDUO_NODE_AUTHORITY_SECRET": "authority-secret",
        },
    )
    monkeypatch.setattr(
        launcher,
        "load_pending_manager_bootstrap",
        lambda _: {
            "device_id": "owner-bootstrap-11111111-1111-1111-1111-111111111111",
            "device_token": "dduo_dev_" + "x" * 48,
        },
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-one")
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class Client:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return Response({"idempotent": True})

    monkeypatch.setattr(launcher, "_launcher_client", lambda _: Client())
    token, bootstrapped = launcher._remote_manager_bootstrap(
        tmp_path, project, owner_name="Owner"
    )

    assert token == "dduo_dev_" + "x" * 48
    assert bootstrapped is True
    assert calls[1][1] == "/projects/p1/team/device-tokens"
    assert calls[1][2]["json"]["device_id"].endswith("111111111111")
    assert calls[1][2]["json"]["device_token"] == token


def test_remote_host_defers_manager_bootstrap_while_destination_is_read_only(
    monkeypatch, tmp_path
):
    local = {"id": "p1", "name": "TeamApp", "binding": "local", "api_port": 1, "web_port": 2}
    hosted = {**local, "deployment": "remote"}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: local)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "_require_persistent_bridge_host", lambda: None)
    monkeypatch.setattr(launcher, "_require_remote_host_resources", lambda: None)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "ensure_project_secret_environment", lambda _: None)
    monkeypatch.setattr(launcher, "_require_remote_sleep_auth", lambda _: "codex")
    monkeypatch.setattr(launcher, "_install_persistent_bridge", lambda: "bridge-token")
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {
            "OPENAI_API_KEY": "key",
            "DDUO_DATABASE_PASSWORD": "safe-password",
        },
    )
    monkeypatch.setattr(launcher, "ensure_remote_runtime_secrets", lambda _: None)
    monkeypatch.setattr(launcher, "_prepare_remote_database", lambda *args: None)
    monkeypatch.setattr(launcher, "set_local_deployment_mode", lambda *args: hosted)
    monkeypatch.setattr(launcher, "_restart_remote_stack", lambda *args: None)
    monkeypatch.setattr(
        launcher,
        "_claim_remote_authority",
        lambda *args, **kwargs: {
            "node_id": "node-old",
            "target_node_id": "node-new",
            "generation": 7,
            "state": "transfer_pending",
            "writable": False,
            "phase": "destination_ready",
            "activation_receipt": "dduo_authority_v1.ready.signed",
        },
    )
    monkeypatch.setattr(
        launcher,
        "_remote_manager_bootstrap",
        lambda *args, **kwargs: pytest.fail("read-only destination must not mutate team state"),
    )
    monkeypatch.setattr(
        launcher,
        "_recover_project_indexes",
        lambda *args: pytest.fail("read-only destination must not rebuild indexes"),
    )
    monkeypatch.setattr(
        launcher,
        "register_gateway_project",
        lambda *args, **kwargs: {
            "api_url": "https://8.8.8.8/api",
            "dashboard_url": "https://8.8.8.8",
            "https_port": 443,
        },
    )
    monkeypatch.setattr(launcher, "write_caddyfile", lambda: None)
    probes = []
    monkeypatch.setattr(
        launcher, "_verify_destination_https", lambda *args: probes.append(args)
    )
    monkeypatch.setattr(
        launcher,
        "_gateway_compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    result = runner.invoke(
        launcher.app,
        ["remote-host", "--public-ip", "8.8.8.8", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert '"hosted": false' in result.stdout
    assert '"manager_bootstrapped": false' in result.stdout
    assert "dduo_authority_v1.ready.signed" in result.stdout
    assert "authority_handoff_pending" in result.stdout
    assert probes[0][0] == "https://8.8.8.8/api"
    assert '"https_verified": true' in result.stdout

    monkeypatch.setattr(
        launcher, "_verify_destination_https",
        lambda *args: (_ for _ in ()).throw(RuntimeError("TLS verification failed")),
    )
    failed = runner.invoke(
        launcher.app,
        ["remote-host", "--public-ip", "8.8.8.8", "--project-root", str(tmp_path)],
    )
    assert failed.exit_code != 0
    assert "activation_receipt" not in failed.stdout


def test_remote_transfer_prepare_freezes_before_requesting_final_backup(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    secret_steps = []
    monkeypatch.setattr(
        launcher,
        "ensure_project_secret_environment",
        lambda project_id: secret_steps.append(("ensure", project_id)),
    )
    monkeypatch.setattr(launcher, "load_project_secrets", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        launcher,
        "save_project_secrets",
        lambda project_id, values: secret_steps.append(("save", project_id, values)),
    )
    monkeypatch.setattr(launcher.secrets, "token_urlsafe", lambda _: "transfer-authority")
    monkeypatch.setattr(
        launcher,
        "start_stack",
        lambda *args, **kwargs: secret_steps.append(("start", project["id"])),
    )
    monkeypatch.setattr(launcher, "_wait_for_api", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    calls = []

    def request(_context, method, path, **kwargs):
        calls.append((method, path))
        if path.endswith("/authority"):
            return {"state": "active", "generation": 3, "node_id": "old"}
        if "/authority/prepare" in path:
            return {"state": "transfer_pending", "generation": 3}
        if "/backups" in path:
            return {
                "id": "backup-final",
                "status": "verified",
                "archive_name": "final.dduobackup",
                "manifest": {
                    "schema_version": 2,
                    "recovery_contract": "full-project-v2",
                    "project": {"id": "p1"},
                    "credentials": {
                        "complete": True,
                        "dduo_secrets": {
                            "included": True,
                            "keys": ["DDUO_NODE_AUTHORITY_SECRET"],
                            "fingerprints": {
                                "DDUO_NODE_AUTHORITY_SECRET": hashlib.sha256(
                                    b"transfer-authority"
                                ).hexdigest()
                            },
                        },
                    },
                },
            }
        raise AssertionError(path)

    monkeypatch.setattr(launcher, "_api_request", request)
    result = runner.invoke(
        launcher.app,
        [
            "remote-transfer-prepare",
            "--target-node-id",
            "node-new",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert calls == [
        ("GET", "/projects/p1/authority"),
        (
            "POST",
            "/projects/p1/authority/prepare?expected_generation=3&target_node_id=node-new",
        ),
        ("POST", "/projects/p1/backups?trigger=manual"),
    ]
    assert secret_steps == [
        ("ensure", "p1"),
        ("save", "p1", {"DDUO_NODE_AUTHORITY_SECRET": "transfer-authority"}),
        ("start", "p1"),
    ]
    assert '"prepared": true' in result.stdout


def test_final_transfer_backup_must_contain_the_current_authority_secret():
    authority_secret = "current-authority"
    backup = {
        "status": "verified",
        "archive_name": "final.dduobackup",
        "manifest": {
            "schema_version": 2,
            "recovery_contract": "full-project-v2",
            "project": {"id": "p1"},
            "credentials": {
                "complete": True,
                "dduo_secrets": {
                    "keys": ["DDUO_NODE_AUTHORITY_SECRET"],
                    "fingerprints": {
                        "DDUO_NODE_AUTHORITY_SECRET": hashlib.sha256(
                            authority_secret.encode()
                        ).hexdigest()
                    },
                },
            },
        },
    }
    assert launcher._validate_final_transfer_backup(
        backup, "p1", authority_secret
    ) is backup
    with pytest.raises(RuntimeError, match="full-project-v2"):
        launcher._validate_final_transfer_backup(backup, "p1", "different-secret")


def test_remote_transfer_prepare_restarts_worker_when_authority_read_fails(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "ensure_project_secret_environment", lambda _: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "existing-authority"},
    )
    monkeypatch.setattr(launcher, "start_stack", lambda *args, **kwargs: None)
    compose_calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda _context, *args, **kwargs: compose_calls.append(args)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("authority unavailable")

    monkeypatch.setattr(launcher, "_api_request", unavailable)
    result = runner.invoke(
        launcher.app,
        [
            "remote-transfer-prepare",
            "--target-node-id",
            "node-new",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert compose_calls == [("stop", "worker"), ("up", "-d", "worker")]


def test_remote_transfer_cancel_requires_explicit_pre_activation_attestation(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    calls = []
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda _context, method, path, **kwargs: calls.append((method, path))
        or {"state": "transfer_pending", "generation": 4},
    )
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    refused = runner.invoke(
        launcher.app,
        ["remote-transfer-cancel", "--project-root", str(tmp_path)],
    )
    assert refused.exit_code != 0
    assert calls == []

    cancelled = runner.invoke(
        launcher.app,
        [
            "remote-transfer-cancel",
            "--new-node-not-activated",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert cancelled.exit_code == 0
    assert calls == [
        ("GET", "/projects/p1/authority"),
        ("POST", "/projects/p1/authority/cancel?expected_generation=4"),
    ]


@pytest.mark.parametrize("lose_finalization_ack", [False, True])
def test_remote_transfer_retire_requires_receipt_and_returns_completion_proof(
    monkeypatch, tmp_path, lose_finalization_ack,
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    proof, finalized_payload = finalization_proof()
    calls = []
    source_state = "transfer_pending"
    pending_ack_loss = False

    def request(_context, method, path, **kwargs):
        nonlocal source_state, pending_ack_loss
        calls.append((method, path, kwargs.get("json_body")))
        if path.endswith("/authority"):
            return {"state": source_state, "generation": 5}
        source_state = "transferred"
        if pending_ack_loss:
            pending_ack_loss = False
            raise RuntimeError("connection lost after database committed finalization")
        return {**finalized_payload, "finalization_receipt": proof}

    monkeypatch.setattr(launcher, "_api_request", request)
    def probe(*args):
        assert args[0] == "https://destination.example/api"
        calls.append(("HTTPS", args[0], None))

    monkeypatch.setattr(launcher, "_verify_destination_https", probe)
    compose_calls = []
    def compose_after_receipt_is_durable(*args, **kwargs):
        compose_calls.append(args[1:])
        marker = json.loads((tmp_path / launcher.RETIRED_NODE_FILE).read_text())
        assert marker["authority"]["finalization_receipt"] == proof
        assert marker["cleanup_complete"] is False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher, "compose", compose_after_receipt_is_durable)
    unregistered = []

    def unregister_after_receipt_is_durable(project_id):
        marker = json.loads((tmp_path / launcher.RETIRED_NODE_FILE).read_text())
        assert marker["authority"]["finalization_receipt"] == proof
        assert marker["cleanup_complete"] is True
        unregistered.append(project_id)
        return True

    monkeypatch.setattr(
        launcher,
        "unregister_project_config",
        unregister_after_receipt_is_durable,
    )
    monkeypatch.setattr(launcher, "unregister_gateway_project", lambda _: False)
    refused = runner.invoke(
        launcher.app,
        [
            "remote-transfer-retire",
            "--activation-receipt",
            "dduo_authority_v1.ready.signed",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert refused.exit_code != 0
    assert calls == [] and compose_calls == []

    missing_url = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--activation-receipt", "ready", "--yes",
         "--project-root", str(tmp_path)],
    )
    assert missing_url.exit_code != 0
    assert calls == [] and compose_calls == []

    with monkeypatch.context() as context:
        context.setattr(
            launcher, "_verify_destination_https",
            lambda *args: (_ for _ in ()).throw(RuntimeError("TLS verification failed")),
        )
        failed = runner.invoke(
            launcher.app,
            ["remote-transfer-retire", "--activation-receipt", "ready", "--yes",
             "--destination-api-url", "https://destination.example/api",
             "--project-root", str(tmp_path)],
        )
        assert failed.exit_code != 0
        assert calls == [("GET", "/projects/p1/authority", None)]
        assert compose_calls == []
        assert not (tmp_path / launcher.RETIRED_NODE_FILE).exists()
    calls.clear()

    if lose_finalization_ack:
        pending_ack_loss = True
        lost = runner.invoke(
            launcher.app,
            ["remote-transfer-retire", "--activation-receipt", "ready", "--yes",
             "--destination-api-url", "https://destination.example/api",
             "--project-root", str(tmp_path)],
        )
        assert lost.exit_code != 0
        assert source_state == "transferred"
        assert compose_calls == []
        assert not (tmp_path / launcher.RETIRED_NODE_FILE).exists()
        calls.clear()

    retired = runner.invoke(
        launcher.app,
        [
            "remote-transfer-retire",
            "--activation-receipt",
            "dduo_authority_v1.ready.signed",
            "--destination-api-url",
            "https://destination.example/api",
            "--yes",
            "--project-root",
            str(tmp_path),
        ],
    )
    assert retired.exit_code == 0
    assert calls == [
        ("GET", "/projects/p1/authority", None),
        ("HTTPS", "https://destination.example/api", None),
        (
            "POST",
            "/projects/p1/authority/finalize?expected_generation=5",
            {"activation_receipt": "dduo_authority_v1.ready.signed"},
        ),
    ]
    assert compose_calls == [("down", "--volumes", "--remove-orphans")]
    assert unregistered == ["p1"]
    assert '"docker_data_removed": true' in retired.stdout
    assert proof in retired.stdout
    marker = json.loads((tmp_path / launcher.RETIRED_NODE_FILE).read_text())
    assert marker["cleanup_complete"] is True

    replay = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )
    assert replay.exit_code == 0
    assert compose_calls == [("down", "--volumes", "--remove-orphans")]
    assert unregistered == ["p1", "p1"]


def test_remote_transfer_retire_resumes_cleanup_from_durable_marker(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    retired_path = tmp_path / launcher.RETIRED_NODE_FILE
    retired_path.parent.mkdir(parents=True)
    proof, finalized_payload = finalization_proof()
    retired_path.write_text(
        json.dumps(
            {
                "project_id": "p1",
                "authority": {**finalized_payload, "finalization_receipt": proof},
                "cleanup_complete": False,
            }
        )
    )
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: pytest.fail("retry must not contact the retired database"),
    )
    compose_calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: compose_calls.append(args[1:])
        or SimpleNamespace(returncode=0),
    )
    unregistered = []
    monkeypatch.setattr(
        launcher,
        "unregister_project_config",
        lambda project_id: unregistered.append(project_id) or True,
    )
    monkeypatch.setattr(launcher, "unregister_gateway_project", lambda _: False)

    result = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert compose_calls == [("down", "--volumes", "--remove-orphans")]
    assert unregistered == ["p1"]
    assert proof in result.stdout
    marker = json.loads(retired_path.read_text())
    assert marker["cleanup_complete"] is True


def test_remote_transfer_retire_persists_and_retries_gateway_cleanup(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    retired_path = tmp_path / launcher.RETIRED_NODE_FILE
    retired_path.parent.mkdir(parents=True)
    proof, finalized_payload = finalization_proof()

    def write_marker(pending: bool) -> None:
        retired_path.write_text(
            json.dumps(
                {
                    "project_id": "p1",
                    "authority": {**finalized_payload, "finalization_receipt": proof},
                    "cleanup_complete": True,
                    "gateway_cleanup_pending": pending,
                }
            )
        )

    write_marker(False)
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: pytest.fail("gateway retries must not contact retired storage"),
    )
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: pytest.fail("Docker data was already retired"),
    )
    monkeypatch.setattr(launcher, "unregister_project_config", lambda _project_id: True)
    monkeypatch.setattr(launcher, "_gateway_project", lambda _project_id: {"https_port": 443})
    removed = []
    monkeypatch.setattr(
        launcher,
        "unregister_gateway_project",
        lambda project_id: removed.append(project_id) or True,
    )
    monkeypatch.setattr(
        launcher,
        "load_gateway_registry",
        lambda: {"projects": {"other": {"https_port": 9443}}},
    )
    gateway_actions = []
    monkeypatch.setattr(launcher, "write_caddyfile", lambda: gateway_actions.append("write"))
    monkeypatch.setattr(
        launcher,
        "_gateway_compose",
        lambda *args: gateway_actions.append(args) or SimpleNamespace(returncode=0),
    )

    cleaned = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )
    assert cleaned.exit_code == 0
    assert removed == ["p1"]
    assert gateway_actions == ["write", ("up", "-d", "--force-recreate")]
    assert json.loads(retired_path.read_text())["gateway_cleanup_pending"] is False

    # A failed final gateway shutdown keeps a durable retry obligation, while a
    # later replay finishes without touching PostgreSQL or the deleted volumes.
    write_marker(True)
    monkeypatch.setattr(launcher, "_gateway_project", lambda _project_id: None)
    monkeypatch.setattr(launcher, "load_gateway_registry", lambda: {"projects": {}})
    results = iter((SimpleNamespace(returncode=9), SimpleNamespace(returncode=0)))
    monkeypatch.setattr(launcher, "_gateway_compose", lambda *args: next(results))

    failed = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )
    assert failed.exit_code != 0
    assert "gateway cleanup is pending" in str(failed.exception)
    assert json.loads(retired_path.read_text())["gateway_cleanup_pending"] is True

    retried = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )
    assert retried.exit_code == 0
    assert json.loads(retired_path.read_text())["gateway_cleanup_pending"] is False


def test_remote_transfer_retire_never_cleans_up_from_an_incomplete_marker(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    retired_path = tmp_path / launcher.RETIRED_NODE_FILE
    retired_path.parent.mkdir(parents=True)
    retired_path.write_text(
        json.dumps(
            {
                "project_id": "p1",
                "authority": {"state": "transferred", "generation": 5},
                "cleanup_complete": False,
            }
        )
    )
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: pytest.fail("incomplete proof must fence destructive cleanup"),
    )

    result = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )

    assert result.exit_code != 0
    assert "marker is malformed" in str(result.exception)


def test_remote_transfer_retire_never_cleans_up_from_a_forged_receipt(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "name": "TeamApp", "api_port": 18001, "web_port": 20001}
    proof, finalized_payload = finalization_proof(secret="attacker-secret")
    retired_path = tmp_path / launcher.RETIRED_NODE_FILE
    retired_path.parent.mkdir(parents=True)
    retired_path.write_text(
        json.dumps(
            {
                "project_id": "p1",
                "authority": {**finalized_payload, "finalization_receipt": proof},
                "cleanup_complete": False,
            }
        )
    )
    monkeypatch.setattr(launcher, "find_workspace_root", lambda _: tmp_path)
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: pytest.fail("forged proof must fence destructive cleanup"),
    )

    result = runner.invoke(
        launcher.app,
        ["remote-transfer-retire", "--yes", "--project-root", str(tmp_path)],
    )

    assert result.exit_code != 0
    assert "unauthenticated" in str(result.exception)


def test_claim_remote_authority_is_idempotent_and_rejects_live_or_retired_sources(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-new")
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    statuses = iter(
        [
            {"state": "active", "generation": 2, "node_id": "node-new"},
            {"state": "active", "generation": 2, "node_id": "node-old"},
            {"state": "transferred", "generation": 2, "node_id": "node-old"},
        ]
    )

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/projects/p1/authority/status"
            assert kwargs["json"] == {"node_id": "node-new"}
            assert kwargs["headers"]["X-DDUO-Authority"] == "authority-secret"
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: next(statuses))

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: Client())
    current = launcher._claim_remote_authority(tmp_path, project)
    assert current["node_id"] == "node-new"
    with pytest.raises(RuntimeError, match="another node"):
        launcher._claim_remote_authority(tmp_path, project)
    with pytest.raises(RuntimeError, match="cannot be reactivated"):
        launcher._claim_remote_authority(tmp_path, project)


@pytest.mark.parametrize(
    ("state", "operation"),
    [("uninitialized", "initialize"), ("transfer_pending", "activate")],
)
def test_claim_remote_authority_uses_secret_for_expected_generation(
    monkeypatch, tmp_path, state, operation
):
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-new")
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    captured = []
    clients = []

    class Client:
        def request(self, method, path, **kwargs):
            captured.append({"method": method, "path": path, **kwargs})
            payload = (
                {"state": state, "generation": 7, "node_id": None}
                if path.endswith("/status")
                else {"state": "active", "generation": 8, "node_id": "node-new"}
            )
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    def client(binding):
        instance = Client()
        clients.append(instance)
        return instance

    monkeypatch.setattr(launcher, "_launcher_client", client)
    result = launcher._claim_remote_authority(tmp_path, project)
    assert result["state"] == "active"
    assert len(clients) == 1
    assert len(captured) == 2
    assert captured[0]["path"] == "/projects/p1/authority/status"
    assert captured[0]["json"] == {"node_id": "node-new"}
    assert captured[1]["path"] == f"/projects/p1/authority/{operation}"
    assert captured[1]["json"] == {"node_id": "node-new", "expected_generation": 7}
    for request in captured:
        assert request["method"] == "POST"
        assert request["headers"]["X-DDUO-Authority"] == "authority-secret"


def test_claim_remote_authority_completes_only_with_source_receipt(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-new")
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(remote=False),
    )
    captured = []

    class Client:
        def request(self, method, path, **kwargs):
            captured.append({"method": method, "path": path, **kwargs})
            payload = (
                {"state": "transfer_pending", "generation": 7, "node_id": "node-old"}
                if path.endswith("/status")
                else {
                    "state": "active", "generation": 8, "node_id": "node-new", "writable": True
                }
            )
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: Client())
    result = launcher._claim_remote_authority(
        tmp_path,
        project,
        finalization_receipt="dduo_authority_v1.final.signed",
    )
    assert result["state"] == "active"
    assert len(captured) == 2
    assert captured[0]["path"] == "/projects/p1/authority/status"
    assert captured[0]["json"] == {"node_id": "node-new"}
    assert captured[1]["path"] == "/projects/p1/authority/complete"
    assert captured[1]["json"] == {
        "node_id": "node-new",
        "expected_generation": 7,
        "finalization_receipt": "dduo_authority_v1.final.signed",
    }


def test_claim_remote_authority_can_read_status_without_a_registered_manager_bearer(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(launcher, "remote_node_id", lambda: "node-new")
    monkeypatch.setattr(
        launcher,
        "load_project_secrets",
        lambda *args, **kwargs: {"DDUO_NODE_AUTHORITY_SECRET": "authority-secret"},
    )
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: pytest.fail("manager-only GET must not precede authority bootstrap"),
    )
    captured = []

    class Client:
        def request(self, method, path, **kwargs):
            assert method == "POST"
            assert kwargs["headers"] == {"X-DDUO-Authority": "authority-secret"}
            captured.append(path)
            payload = (
                {"state": "transfer_pending", "generation": 7, "node_id": "node-old"}
                if path.endswith("/status")
                else {"state": "transfer_pending", "generation": 7, "writable": False}
            )
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(launcher, "_launcher_client", lambda _binding: Client())
    result = launcher._claim_remote_authority(tmp_path, project)
    assert captured == ["/projects/p1/authority/status", "/projects/p1/authority/activate"]
    assert result["writable"] is False


def backup_archive(tmp_path: Path, *, qdrant: bool = True):
    dump = tmp_path / "postgres.dump"
    config = tmp_path / "project.toml"
    snapshot = tmp_path / "qdrant.snapshot"
    dump.write_bytes(b"PGDMP-test")
    config.write_text('id = "p1"\nname = "Project"\napi_port = 1\nweb_port = 2\n')
    snapshot.write_bytes(b"snapshot")
    key = generate_recovery_key()
    archive = tmp_path / "backup.dduobackup"
    create_archive(
        archive,
        key,
        app_version="1",
        project_id="p1",
        project_name="Project",
        postgres_dump=dump,
        project_config=config,
        qdrant_snapshot=snapshot if qdrant else None,
        qdrant_collection="collection" if qdrant else None,
        created_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    return archive, key


def _mock_full_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    health_error: Exception | None = None,
    project_id: str = "p1",
) -> tuple[Path, Path, bytes]:
    """Install a small, deterministic full-v2 restore harness."""
    archive = tmp_path / "full.dduobackup"
    archive.write_bytes(b"authenticated-full-backup")
    target = tmp_path / "target"
    project = {
        "id": project_id,
        "name": "Project",
        "api_port": 18001,
        "web_port": 20001,
    }
    manifest = {
        "schema_version": 2,
        "backup_id": "backup-full-1",
        "project": {"id": project_id, "name": "Project"},
        "credentials": {"complete": True},
        "qdrant": {
            "included": False,
            "collection": None,
            "collections": {
                "memory": {"included": False, "file": None, "collection": None},
                "tasks": {"included": False, "file": None, "collection": None},
            },
        },
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"

    def verify(_archive, _key, *, extract_to):
        (extract_to / "manifest.json").write_bytes(manifest_bytes)
        (extract_to / "project.toml").write_text(
            f'id = "{project_id}"\nname = "Project"\napi_port = 1\nweb_port = 2\n'
        )
        (extract_to / "postgres.dump").write_bytes(b"PGDMP-test")
        return SimpleNamespace(manifest=manifest)

    monkeypatch.setattr(launcher, "verify_archive", verify)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_drill", lambda *args: {"projects": 1})
    monkeypatch.setattr(launcher, "_assert_restore_candidate_stack_absent", lambda _: None)
    monkeypatch.setattr(launcher, "_protect_existing_restore_target", lambda _: None)
    monkeypatch.setattr(launcher, "restore_project_config", lambda *args, **kwargs: project)
    monkeypatch.setattr(
        launcher,
        "restore_project_runtime_settings",
        lambda *args: SimpleNamespace(
            backup_options={"include_qdrant": False, "daily": 7, "weekly": 4, "monthly": 12}
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_restore_full_recovery_supplement",
        lambda *args, **kwargs: {"codex_auth": True, "hook_spools": 0, "mcp_observations": 0},
    )
    monkeypatch.setattr(launcher, "configure_project_backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher, "_wait_for_postgres", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_portable_backup_history", lambda *args: 0)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)

    def wait_for_api(*args, **kwargs):
        if health_error is not None:
            raise health_error

    monkeypatch.setattr(launcher, "_wait_for_api", wait_for_api)

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(launcher.httpx, "Client", Client)

    def api_request(_project, _method, path, **kwargs):
        if path.endswith("/authority"):
            return {"writable": True}
        if path.endswith("/backups"):
            return {"qdrant_collection": "unused"}
        return {"queued": 0, "deleted": 0}

    monkeypatch.setattr(launcher, "_api_request", api_request)
    return archive, target, manifest_bytes


def test_backup_configure_new_existing_and_stopped_docker(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "Project", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    refreshed = []
    monkeypatch.setattr(
        launcher,
        "refresh_backup_runtime",
        lambda *args, **kwargs: refreshed.append((args, kwargs)),
    )
    monkeypatch.setattr(launcher, "docker_ready", lambda: True)
    monkeypatch.setattr(
        launcher,
        "configure_project_backup",
        lambda *args, **kwargs: (
            {"destination": str(tmp_path / "archive")},
            "recovery-key",
            True,
        ),
    )
    result = runner.invoke(
        launcher.app,
        ["backup", "configure", str(tmp_path), "--project-root", str(tmp_path), "--no-qdrant"],
    )
    assert result.exit_code == 0 and "shown once" in result.stdout and refreshed

    monkeypatch.setattr(launcher, "docker_ready", lambda: False)
    monkeypatch.setattr(
        launcher,
        "configure_project_backup",
        lambda *args, **kwargs: ({"destination": str(tmp_path)}, "same", False),
    )
    result = runner.invoke(launcher.app, ["backup", "configure", str(tmp_path)])
    assert result.exit_code == 0 and "preserved" in result.stdout and "not running" in result.stdout


def test_backup_configure_json_is_machine_readable_and_keeps_the_key_private(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "Project", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "docker_ready", lambda: True)
    monkeypatch.setattr(launcher, "refresh_backup_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher,
        "configure_project_backup",
        lambda *args, **kwargs: ({"destination": str(tmp_path / "archive")}, "recovery-key", True),
    )
    result = runner.invoke(
        launcher.app,
        ["backup", "configure", str(tmp_path), "--project-root", str(tmp_path), "--json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload == {
        "configured": True,
        "destination": str(tmp_path / "archive"),
        "recovery_key": "recovery-key",
        "recovery_key_new": True,
        "runtime_refreshed": True,
    }


def test_refresh_backup_runtime_recreates_only_mounted_services_and_reports_failures(
    monkeypatch, tmp_path
):
    project = {"id": "p1", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "ensure_cli_bridge", lambda: "token")
    calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: (
            calls.append((args, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: True)
    launcher.refresh_backup_runtime(tmp_path, project, quiet=True)
    assert calls == [
        (
            (
                {**project, "root_path": str(tmp_path.resolve())},
                "up",
                "-d",
                "--force-recreate",
                "--no-deps",
                "api",
                "worker",
            ),
            {"capture_output": True},
        )
    ]

    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Docker failed"),
    )
    with pytest.raises(RuntimeError, match="Docker failed"):
        launcher.refresh_backup_runtime(tmp_path, project, quiet=True)
    with pytest.raises(typer.Exit):
        launcher.refresh_backup_runtime(tmp_path, project)

    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(launcher, "_api_healthy", lambda *args: False)
    ticks = iter((0.0, 0.0, 181.0))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="did not become ready"):
        launcher.refresh_backup_runtime(tmp_path, project, quiet=True)

    ticks = iter((0.0, 0.0, 181.0))
    messages = []
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(launcher.typer, "echo", lambda message: messages.append(message))
    with pytest.raises(typer.Exit):
        launcher.refresh_backup_runtime(tmp_path, project)
    assert messages == [
        "MEMORY_UNAVAILABLE: dDuo Solo Founder did not become healthy within 180 seconds."
    ]


def test_backup_create_and_status_commands(monkeypatch, tmp_path):
    project = {"id": "p1", "name": "Project", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    monkeypatch.setattr(launcher, "load_backup_settings", lambda _: None)
    missing = runner.invoke(launcher.app, ["backup", "create"])
    assert missing.exit_code == 6 and "not configured" in missing.stdout
    assert runner.invoke(launcher.app, ["backup", "create", "--trigger", "bad"]).exit_code != 0

    monkeypatch.setattr(launcher, "load_backup_settings", lambda _: {"destination": str(tmp_path)})
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: {"archive_name": "archive.dduobackup", "configured": True},
    )
    created = runner.invoke(launcher.app, ["backup", "create", "--trigger", "update"])
    assert created.exit_code == 0 and "archive.dduobackup" in created.stdout
    status = runner.invoke(launcher.app, ["backup", "status"])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["destination"] == str(tmp_path)
    monkeypatch.setattr(launcher, "load_backup_settings", lambda _: None)
    status = runner.invoke(launcher.app, ["backup", "status"])
    assert status.exit_code == 0
    assert json.loads(status.stdout)["configured"] is False


def test_backup_api_key_and_dump_helpers(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 123, "web_port": 124}
    assert launcher._project_context(tmp_path, project)["root_path"] == str(tmp_path.resolve())
    assert launcher._recovery_key(tmp_path, " supplied ") == "supplied"
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "read_recovery_key", lambda _: "stored")
    assert launcher._recovery_key(tmp_path, None) == "stored"
    monkeypatch.setattr(
        launcher,
        "read_recovery_key",
        lambda _: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(launcher.typer, "prompt", lambda *args, **kwargs: " prompted ")
    assert launcher._recovery_key(tmp_path, None) == "prompted"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    captured = {}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(launcher.httpx, "request", request)
    context = launcher._project_context(tmp_path, project)
    assert launcher._api_request(context, "GET", "/path", timeout=1) == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:123/path"
    launcher._api_request(context, "POST", "/path", json_body={"value": 1})
    assert captured["json"] == {"value": 1}
    with pytest.raises(RuntimeError, match="project root context is required"):
        launcher._api_request(project, "GET", "/path")

    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    launcher._validate_dump_with_docker(tmp_path)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="invalid", stdout=""),
    )
    with pytest.raises(BackupError, match="verification failed"):
        launcher._validate_dump_with_docker(tmp_path)


def test_backup_archive_selection_and_docker_failures_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "load_backup_settings", lambda _project_id: None)
    with pytest.raises(BackupError, match="not configured"):
        launcher._latest_backup_archive("p1")

    destination = tmp_path / "backups"
    destination.mkdir()
    monkeypatch.setattr(
        launcher,
        "load_backup_settings",
        lambda _project_id: {"destination": str(destination)},
    )
    with pytest.raises(BackupError, match="no backup archives"):
        launcher._latest_backup_archive("p1")
    older = destination / "older.dduobackup"
    newer = destination / "nested" / "newer.dduobackup"
    older.write_text("old")
    newer.parent.mkdir()
    newer.write_text("new")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    assert launcher._latest_backup_archive("p1") == newer

    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=4,
            stderr="private restore detail",
            stdout="",
        ),
    )
    with pytest.raises(BackupError, match="restore failed: private restore detail"):
        launcher._docker_command(["docker", "restore"], failure="restore failed")


@pytest.mark.parametrize("legacy_client", [False, True])
def test_full_recovery_supplement_restores_only_project_scoped_state(monkeypatch, tmp_path, legacy_client):
    from dduo_solo_founder import hooks as hook_module

    extracted = tmp_path / "extracted"
    (extracted / "secrets/codex").mkdir(parents=True)
    (extracted / "secrets/dduo.env").write_text(
        "OPENAI_API_KEY=key\nDDUO_DATABASE_PASSWORD=db-secret\n"
    )
    (extracted / "secrets/codex/auth.json").write_text('{"tokens":{}}')
    hooks = extracted / "host-state/hooks"
    hooks.mkdir(parents=True)
    (hooks / hook_module.state_path("p1", "codex", "session").name).write_text(
        json.dumps(
            {
                "project_id": "p1",
                "binding_id": "a" * 64,
                **({} if legacy_client else {"client": "codex"}),
                "session_external_id": "session",
                "session_off_record": True,
                "context_delivery_baseline": {"stale": True},
                "last_briefing_context_hash": "stale",
                "lifecycle_evidence": {"version": 1, "stop": {"server_committed_at": "old"}},
                "pending_commits": [{
                    "binding_id": "a" * 64, "turn_id": "t1", "revision": "r1",
                    "payload": {"assistant_response": "synthetic completion"},
                }],
                "spooled_turns": [{
                    "binding_id": "a" * 64, "session_external_id": "session",
                    "external_turn_id": "t2", "user_prompt": "synthetic prompt",
                    "assistant_response": "synthetic answer", "off_record": True,
                }],
                "spooled_active": {
                    "binding_id": "a" * 64, "session_external_id": "session",
                    "external_turn_id": "t3", "user_prompt": "active prompt", "off_record": True,
                },
            }
        )
    )
    (extracted / "host-state/mcp-observability.json").write_text(
        '[{"idempotency_key":"one"}]'
    )
    hook_target = tmp_path / "host/hooks"
    mcp_target = tmp_path / "host/mcp"
    codex_home = tmp_path / "host/codex"
    codex_home.mkdir(parents=True)
    saved = {}
    monkeypatch.setattr(launcher, "HOOK_STATE_DIR", hook_target)
    monkeypatch.setattr(hook_module, "HOOK_STATE_DIR", hook_target)
    monkeypatch.setattr(launcher, "MCP_OBSERVABILITY_DIR", mcp_target)
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "p1"})
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(
            binding_id="b" * 64,
            remote=False,
            credential_path=None,
        ),
    )
    monkeypatch.setattr(
        launcher, "replace_project_secrets", lambda project_id, values: saved.update(values)
    )
    monkeypatch.setattr(
        launcher,
        "ensure_project_codex_home",
        lambda project_id, import_global_auth=False: codex_home,
    )
    monkeypatch.setattr(launcher, "project_codex_home", lambda project_id: codex_home)
    result = launcher._restore_full_recovery_supplement(
        extracted, "p1", tmp_path, force=False
    )
    assert saved == {"OPENAI_API_KEY": "key", "DDUO_DATABASE_PASSWORD": "db-secret"}
    assert result == {
        "codex_auth": True,
        "claude_auth": False,
        "hook_spools": 1,
        "mcp_observations": 1,
    }
    assert_private_file(codex_home / "auth.json")
    restored_hook = next(hook_target.glob("p1-codex-*.json"))
    restored_state = json.loads(restored_hook.read_text())
    assert restored_state["binding_id"] == "b" * 64
    assert restored_state["client"] == "codex"
    assert restored_state["session_external_id"] == "session"
    assert "context_delivery_baseline" not in restored_state
    assert "last_briefing_context_hash" not in restored_state
    assert "lifecycle_evidence" not in restored_state
    for key in ("pending_commits", "spooled_turns"):
        assert restored_state[key][0]["binding_id"] == "b" * 64
    assert restored_state["spooled_active"]["binding_id"] == "b" * 64
    assert restored_state["spooled_active"]["off_record"] is True
    assert restored_state["spooled_turns"][0]["off_record"] is True
    calls = []
    def request(_transport, _method, path, **kwargs):
        calls.append((path, kwargs["json"]))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"turn": {"id": "replayed"}} if path.endswith("/begin") else {"committed": True},
        )
    monkeypatch.setattr(hook_module, "_request", request)
    monkeypatch.setattr(hook_module, "_session", lambda *args: {"id": "server-session"})
    assert hook_module.flush_pending_commits("unused", restored_state, "b" * 64, allow_legacy=False) == 1
    assert hook_module.flush_spooled_turns({"id": "p1"}, "unused", "codex", "b" * 64, allow_legacy=False) == 1
    assert len(calls) == 3
    assert next(body for path, body in calls if path.endswith("/begin"))["off_record"] is True
    restored_mcp = next(mcp_target.glob("*.json"))
    assert json.loads(restored_mcp.read_text())[0]["idempotency_key"] == "one"

    (hooks / "other-codex-session.json").write_text("{}")
    with pytest.raises(BackupError, match="another project"):
        launcher._restore_full_recovery_supplement(extracted, "p1", tmp_path, force=True)


@pytest.mark.parametrize(("damage", "error"), [
    ("auth", "Codex auth.json is malformed"),
    ("session", "lacks a bound client session"),
    ("binding", "hook binding is malformed"),
    ("queue", "queue belongs to another binding"),
])
def test_full_recovery_supplement_validates_everything_before_writing(monkeypatch, tmp_path, damage, error):
    extracted = tmp_path / "extracted"
    (extracted / "secrets/codex").mkdir(parents=True)
    (extracted / "secrets/dduo.env").write_text("OPENAI_API_KEY=key\n")
    (extracted / "secrets/codex/auth.json").write_text('{malformed' if damage == "auth" else '{"tokens":{}}')
    hooks = extracted / "host-state/hooks"
    hooks.mkdir(parents=True)
    state = {
        "project_id": "p1", "client": "codex", "binding_id": "old-binding",
        "session_external_id": "session",
    }
    if damage == "session":
        state["session_external_id"] = ["invalid"]
    if damage == "binding":
        state["binding_id"] = {"invalid": True}
    if damage == "queue":
        state["pending_commits"] = [{"binding_id": "unrelated-binding"}]
    (hooks / "p1-codex-session.json").write_text(json.dumps(state))
    (extracted / "host-state/update-queue.json").write_text("{malformed")

    hook_target = tmp_path / "host/hooks"
    codex_home = tmp_path / "host/codex"
    mutations: list[str] = []
    monkeypatch.setattr(launcher, "HOOK_STATE_DIR", hook_target)
    monkeypatch.setattr(launcher, "MCP_OBSERVABILITY_DIR", tmp_path / "host/mcp")
    monkeypatch.setattr(launcher, "load_project", lambda _: {"id": "p1"})
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(
            binding_id="a" * 64,
            remote=False,
            credential_path=None,
        ),
    )
    monkeypatch.setattr(
        launcher,
        "replace_project_secrets",
        lambda *args, **kwargs: mutations.append("secrets"),
    )
    monkeypatch.setattr(
        launcher,
        "ensure_project_codex_home",
        lambda *args, **kwargs: mutations.append("codex-home") or codex_home,
    )
    monkeypatch.setattr(launcher, "project_codex_home", lambda _: codex_home)

    with pytest.raises(BackupError, match=error):
        launcher._restore_full_recovery_supplement(
            extracted, "p1", tmp_path, force=True
        )
    assert mutations == []
    assert not hook_target.exists()
    assert not (codex_home / "auth.json").exists()


def test_full_recovery_restores_project_state_but_ignores_machine_updater_state(
    monkeypatch, tmp_path
):
    extracted = tmp_path / "extracted"
    (extracted / "secrets/codex").mkdir(parents=True)
    (extracted / "secrets/dduo.env").write_text("OPENAI_API_KEY=key\n")
    (extracted / "secrets/codex/auth.json").write_text('{"tokens":{}}')
    (extracted / "binding").mkdir()
    (extracted / "binding/project.toml").write_text('id = "p1"\n')
    (extracted / "binding/remote-credential.token").write_text("remote-project-token\n")

    hooks = extracted / "host-state/hooks"
    hooks.mkdir(parents=True)
    (hooks / "p1-codex-session.json").write_text(
        json.dumps(
            {
                "project_id": "p1",
                "binding_id": "old-binding",
                "client": "codex",
                "session_external_id": "archived-session",
                "queued": [],
            }
        )
    )
    (extracted / "host-state/mcp-observability.json").write_text(
        '[{"idempotency_key":"one","source":"archive"},'
        '{"idempotency_key":"two","source":"archive"}]'
    )
    (extracted / "host-state/update-queue.json").write_text(
        json.dumps({"version": 1, "project_id": "p1", "release_id": "release-1"})
    )
    # Accepted only for backwards compatibility with older v2 archives. These
    # machine-global files must be ignored by a project restore.
    (extracted / "host-state/update-trust.json").write_text("untrusted-machine-policy")
    release = tmp_path / "cached-release"
    release.mkdir()
    pointer = {"release_id": "release-1", "path": str(release.resolve())}
    for name in ("client-current.json", "client-previous.json"):
        (extracted / "host-state" / name).write_text(json.dumps(pointer))

    host = tmp_path / "host"
    hook_target = host / "hooks"
    hook_target.mkdir(parents=True)
    (hook_target / "p1-codex-session.json").write_text('{"project_id":"p1"}')
    mcp_target = host / "mcp"
    update_root = host / "update"
    codex_home = host / "codex"
    remote_credential = host / "remote" / "p1.token"
    binding_id = "b" * 64
    binding_scope = hashlib.sha256(f"p1\0{binding_id}".encode()).hexdigest()
    mcp_target.mkdir(parents=True)
    (mcp_target / f"{binding_scope}.json").write_text(
        '[{"idempotency_key":"one","source":"existing"}]'
    )
    saved = []
    monkeypatch.setattr(launcher, "HOOK_STATE_DIR", hook_target)
    from dduo_solo_founder import hooks as hook_module

    monkeypatch.setattr(hook_module, "HOOK_STATE_DIR", hook_target)
    monkeypatch.setattr(launcher, "MCP_OBSERVABILITY_DIR", mcp_target)
    monkeypatch.setattr(launcher, "load_project", lambda _root: {"id": "p1"})
    monkeypatch.setattr(
        launcher,
        "binding_from_project",
        lambda *args, **kwargs: SimpleNamespace(
            binding_id=binding_id,
            remote=True,
            credential_path=remote_credential,
        ),
    )
    monkeypatch.setattr(
        launcher,
        "replace_project_secrets",
        lambda project_id, values: saved.append((project_id, values)),
    )
    monkeypatch.setattr(launcher, "project_codex_home", lambda _project_id: codex_home)
    monkeypatch.setattr(
        launcher,
        "ensure_project_codex_home",
        lambda _project_id, import_global_auth=False: codex_home,
    )

    result = launcher._restore_full_recovery_supplement(
        extracted,
        "p1",
        tmp_path,
        force=False,
        project_config={"id": "p1"},
    )

    assert result == {
        "codex_auth": True,
        "claude_auth": False,
        "hook_spools": 1,
        "mcp_observations": 2,
    }
    assert saved == [("p1", {"OPENAI_API_KEY": "key"})]
    assert remote_credential.read_text() == "remote-project-token\n"
    restored_hooks = [
        path for path in hook_target.glob("p1-codex-*.json")
        if json.loads(path.read_text()).get("session_external_id") == "archived-session"
    ]
    assert len(restored_hooks) == 1
    assert json.loads(restored_hooks[0].read_text())["binding_id"] == binding_id
    merged = json.loads((mcp_target / f"{binding_scope}.json").read_text())
    assert merged == [
        {"idempotency_key": "one", "source": "existing"},
        {"idempotency_key": "two", "source": "archive"},
    ]
    assert not update_root.exists()


def test_portable_backup_history_restore_is_validated_and_parameter_free(monkeypatch, tmp_path):
    project = {"id": "p1"}
    assert launcher._restore_portable_backup_history(tmp_path, project) == 0
    history = tmp_path / "history/backup-records.json"
    history.parent.mkdir(parents=True)
    row = {
        "id": "b1",
        "project_id": "p1",
        "trigger": "manual",
        "status": "verified",
        "archive_name": "backup.dduobackup",
        "size_bytes": 123,
        "includes_qdrant": True,
        "retained": True,
        "source_generation": 0,
        "manifest": {},
        "error": None,
        "created_at": "2026-08-28T12:00:00+00:00",
        "completed_at": "2026-08-28T12:01:00+00:00",
        "verified_at": "2026-08-28T12:01:00+00:00",
    }
    history.write_text(json.dumps([{**row, "project_id": "another"}]))
    with pytest.raises(BackupError, match="malformed"):
        launcher._restore_portable_backup_history(tmp_path, project)
    history.write_text(json.dumps([{**row, "created_at": "not-a-timestamp"}]))
    with pytest.raises(BackupError, match="malformed"):
        launcher._restore_portable_backup_history(tmp_path, project)
    history.write_text(json.dumps([row]))
    calls = []
    monkeypatch.setattr(
        launcher, "_checked_compose", lambda *args: calls.append(args[1:])
    )
    assert launcher._restore_portable_backup_history(tmp_path, project) == 1
    assert calls[0][0] == "cp"
    assert calls[1][0] == "exec" and "pg_read_file" in calls[1][-1]


def test_existing_restore_target_requires_verified_safety_backup(monkeypatch, tmp_path):
    project = {"id": "p1", "api_port": 1}
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "load_backup_settings", lambda _: None)
    with pytest.raises(BackupError, match="no backup destination"):
        launcher._protect_existing_restore_target(tmp_path)
    destination = tmp_path / "backups"
    destination.mkdir()
    monkeypatch.setattr(
        launcher,
        "load_backup_settings",
        lambda _: {
            "destination": str(destination),
            "include_qdrant": True,
            "retention": {"daily": 7, "weekly": 4, "monthly": 6},
            "auto_seconds": 300,
        },
    )
    monkeypatch.setattr(launcher, "read_recovery_key", lambda _: "old-key")
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    monkeypatch.setattr(
        launcher,
        "_api_request",
        lambda *args, **kwargs: {"status": "verified", "archive_name": "safety.dduobackup"},
    )
    safety_archive = destination / "safety.dduobackup"
    safety_archive.write_bytes(b"encrypted-safety")
    manifest_bytes = b'{"schema_version":2}\n'

    def verify(_archive, _key, *, extract_to):
        (extract_to / "manifest.json").write_bytes(manifest_bytes)
        (extract_to / "project.toml").write_text(
            'id = "p1"\nname = "Project"\napi_port = 1\nweb_port = 2\n'
        )
        return SimpleNamespace(
            manifest={
                "schema_version": 2,
                "project": {"id": "p1"},
                "credentials": {"complete": True},
            }
        )

    monkeypatch.setattr(launcher, "verify_archive", verify)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_drill", lambda *args: {"projects": 1})
    monkeypatch.setattr(launcher, "read_runtime_settings", lambda _: {})
    monkeypatch.setattr(
        launcher,
        "_restore_full_recovery_supplement",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(launcher, "_portable_backup_history_rows", lambda *args: [])
    safety = launcher._protect_existing_restore_target(tmp_path)
    assert safety is not None
    assert safety["archive"] == safety_archive
    assert safety["archive_sha256"] == hashlib.sha256(safety_archive.read_bytes()).hexdigest()
    assert safety["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert safety["recovery_key"] == "old-key"


def test_rollback_helper_restores_bundle_without_recursive_restore(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    safety_archive = tmp_path / "safety.dduobackup"
    safety_archive.write_bytes(b"encrypted")
    archived_config = (
        'id = "p1"\nname = "Old Project"\napi_port = 18001\nweb_port = 20001\n'
    ).encode()
    manifest = {
        "schema_version": 2,
        "project": {"id": "p1"},
        "credentials": {"complete": True},
        "qdrant": {
            "collections": {
                "memory": {"included": False, "file": None, "collection": None},
                "tasks": {"included": False, "file": None, "collection": None},
            }
        },
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()

    def verify(_archive, _key, *, extract_to):
        (extract_to / "manifest.json").write_bytes(manifest_bytes)
        (extract_to / "project.toml").write_bytes(archived_config)
        (extract_to / "postgres.dump").write_bytes(b"PGDMP-old")
        return SimpleNamespace(manifest=manifest)

    monkeypatch.setattr(launcher, "verify_archive", verify)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_drill", lambda *args: {"projects": 1})
    monkeypatch.setattr(
        launcher,
        "restore_backup_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("recursive restore")),
    )
    events = []
    monkeypatch.setattr(
        launcher,
        "_checked_compose",
        lambda _project, *args: events.append(("compose", args)),
    )
    monkeypatch.setattr(
        launcher,
        "register_project_config",
        lambda *args: events.append(("register", args[1]["id"])),
    )
    monkeypatch.setattr(
        launcher,
        "unregister_project_config",
        lambda project_id: events.append(("unregister", project_id)) or True,
    )
    monkeypatch.setattr(
        launcher,
        "restore_project_runtime_settings",
        lambda *args: SimpleNamespace(backup_options={"auto_seconds": 300}),
    )
    monkeypatch.setattr(launcher, "read_runtime_settings", lambda _: {})
    monkeypatch.setattr(
        launcher,
        "_restore_full_recovery_supplement",
        lambda *args, **kwargs: events.append(
            ("supplement", args[1], kwargs.get("publish", True))
        ),
    )
    monkeypatch.setattr(
        launcher,
        "configure_project_backup",
        lambda *args, **kwargs: events.append(("backup-config", kwargs["recovery_key"])),
    )
    monkeypatch.setattr(
        launcher,
        "_restore_postgres_volume",
        lambda _project, dump: events.append(("postgres", dump.read_bytes())),
    )
    monkeypatch.setattr(
        launcher, "_restore_portable_backup_history", lambda *args: 0
    )
    monkeypatch.setattr(
        launcher,
        "_restore_qdrant_snapshots",
        lambda *args: (False, False),
    )
    monkeypatch.setattr(
        launcher,
        "_start_and_register_restored_project",
        lambda _root, project: events.append(("healthy", project["id"])),
    )
    monkeypatch.setattr(
        launcher, "_api_request", lambda *args, **kwargs: {"writable": False}
    )
    safety = {
        "archive": safety_archive,
        "archive_sha256": hashlib.sha256(safety_archive.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "recovery_key": "old-key",
        "project_id": "p1",
        "settings": {
            "destination": str(tmp_path / "backups"),
            "include_qdrant": True,
            "retention": {"daily": 7, "weekly": 4, "monthly": 6},
            "auto_seconds": 120,
        },
    }
    restored = launcher._rollback_restore_safety_point(
        project_root,
        {"id": "p2"},
        safety,
        True,
        "p2",
    )
    assert restored["id"] == "p1"
    assert (project_root / launcher.CONFIG_PATH).read_bytes() == archived_config
    assert events[0] == ("supplement", "p1", False)
    assert events[1] == ("compose", ("down", "--volumes", "--remove-orphans"))
    assert ("unregister", "p2") in events
    assert ("register", "p1") in events
    assert ("supplement", "p1", True) in events
    assert ("postgres", b"PGDMP-old") in events
    assert events[-1] == ("healthy", "p1")

    events.clear()
    host_only = launcher._rollback_restore_safety_point(
        project_root,
        {"id": "p2"},
        safety,
        False,
        "p2",
    )
    assert host_only["id"] == "p1"
    assert (project_root / launcher.CONFIG_PATH).read_bytes() == archived_config
    assert ("supplement", "p1", False) in events
    assert ("supplement", "p1", True) in events
    assert ("unregister", "p2") in events
    assert ("register", "p1") in events
    assert not any(event[0] == "compose" for event in events)
    assert not any(event[0] == "postgres" for event in events)
    assert not any(event[0] == "healthy" for event in events)


def test_rollback_rejects_changed_safety_archive_before_destructive_action(
    monkeypatch, tmp_path
):
    safety_archive = tmp_path / "safety.dduobackup"
    original = b"verified-safety"
    safety_archive.write_bytes(b"changed-after-drill")
    compose_calls = []
    monkeypatch.setattr(
        launcher,
        "_checked_compose",
        lambda *args: compose_calls.append(args),
    )
    with pytest.raises(BackupError, match="changed after its pre-restore drill"):
        launcher._rollback_restore_safety_point(
            tmp_path,
            {"id": "failed"},
            {
                "archive": safety_archive,
                "archive_sha256": hashlib.sha256(original).hexdigest(),
                "manifest_sha256": "a" * 64,
                "recovery_key": "old-key",
                "project_id": "p1",
                "settings": {},
            },
        )
    assert compose_calls == []


def test_backup_verify_command(monkeypatch, tmp_path):
    archive, key = backup_archive(tmp_path)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    result = runner.invoke(
        launcher.app,
        ["backup", "verify", str(archive), "--recovery-key", key],
    )
    assert result.exit_code == 0 and '"verified": true' in result.stdout


def test_backup_restore_drill_uses_disposable_database(monkeypatch, tmp_path):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "pg_isready" in command:
            return SimpleNamespace(returncode=0, stdout="ready", stderr="")
        if "select id from projects order by id" in joined:
            return SimpleNamespace(returncode=0, stdout="p1\n", stderr="")
        if "select 'projects', count(*)" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout="projects=1\ntasks=3\nmemories=2\nturns=4\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    counts = launcher._restore_drill(tmp_path, "p1")
    assert counts == {"projects": 1, "tasks": 3, "memories": 2, "turns": 4}
    assert commands[0][:3] == ["docker", "run", "--detach"]
    assert any("pg_restore" in command for command in commands)
    assert commands[-1][:3] == ["docker", "rm", "--force"]


def test_backup_restore_drill_rejects_a_second_project(monkeypatch, tmp_path):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "pg_isready" in command:
            return SimpleNamespace(returncode=0, stdout="ready", stderr="")
        if "select id from projects order by id" in joined:
            return SimpleNamespace(returncode=0, stdout="p1\np2\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    with pytest.raises(BackupError, match="exactly the archived project"):
        launcher._restore_drill(tmp_path, "p1")
    assert commands[-1][:3] == ["docker", "rm", "--force"]


def test_backup_drill_command_verifies_latest_or_selected_archive(monkeypatch, tmp_path):
    archive, key = backup_archive(tmp_path)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(
        launcher,
        "load_project",
        lambda _: {"id": "p1", "name": "Project", "api_port": 1, "web_port": 2},
    )
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(
        launcher,
        "_restore_drill",
        lambda extracted, project_id: {"projects": 1, "tasks": 3, "memories": 2, "turns": 4},
    )
    result = runner.invoke(
        launcher.app,
        ["backup", "drill", str(archive), "--recovery-key", key],
    )
    assert result.exit_code == 0
    assert '"restorable": true' in result.stdout
    assert '"semantic_index": "rebuildable from PostgreSQL"' in result.stdout

    destination = tmp_path / "destination"
    destination.mkdir()
    older = destination / "older.dduobackup"
    newer = destination / "newer.dduobackup"
    older.write_text("old")
    newer.write_text("new")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setattr(
        launcher, "load_backup_settings", lambda _: {"destination": str(destination)}
    )
    assert launcher._latest_backup_archive("p1") == newer


def test_postgres_wait_and_checked_compose(monkeypatch):
    results = iter([SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)])
    monkeypatch.setattr(launcher, "compose", lambda *args: next(results))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    launcher._wait_for_postgres({"id": "p"})
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=4))
    with pytest.raises(BackupError, match="exit code 4"):
        launcher._checked_compose({"id": "p"}, "up")

    times = iter([0, 121])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=1))
    with pytest.raises(BackupError, match="did not become ready"):
        launcher._wait_for_postgres({"id": "p"})


def test_postgres_restore_marks_destructive_boundary_only_after_volume_reset(
    monkeypatch, tmp_path
):
    dump = tmp_path / "postgres.dump"
    dump.write_bytes(b"PGDMP")
    commands = []
    boundary = []

    monkeypatch.setattr(
        launcher,
        "_checked_compose",
        lambda _project, *args: commands.append(args),
    )
    monkeypatch.setattr(launcher, "_wait_for_postgres", lambda _project: None)
    launcher._restore_postgres_volume(
        {"id": "p"},
        dump,
        lambda: boundary.append(tuple(commands)),
    )

    assert boundary == [()]

    boundary.clear()

    def fail_before_reset(_project, *args):
        raise BackupError("compose down failed")

    monkeypatch.setattr(launcher, "_checked_compose", fail_before_reset)
    with pytest.raises(BackupError, match="compose down failed"):
        launcher._restore_postgres_volume(
            {"id": "p"},
            dump,
            lambda: boundary.append("started"),
        )
    assert boundary == ["started"]


@pytest.mark.parametrize("resource", ["container", "volume"])
def test_restore_refuses_an_orphan_candidate_compose_stack(
    monkeypatch, resource
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        output = "resource-id\n" if command[1] == resource else ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", run)

    with pytest.raises(BackupError, match="already has an isolated Docker stack"):
        launcher._assert_restore_candidate_stack_absent("project-b")

    expected_label = (
        f"label=com.docker.compose.project={launcher.compose_name('project-b')}"
    )
    assert commands[0][-1] == expected_label
    if resource == "volume":
        assert commands[1][-1] == expected_label


def test_cross_project_restore_collision_leaves_existing_target_untouched(
    monkeypatch, tmp_path
):
    archive, target, _ = _mock_full_restore(
        monkeypatch,
        tmp_path,
        project_id="project-b",
    )
    config = target / launcher.CONFIG_PATH
    config.parent.mkdir(parents=True)
    original = (
        'id = "project-a"\nname = "Project A"\napi_port = 18009\nweb_port = 20009\n'
    ).encode()
    config.write_bytes(original)
    monkeypatch.setattr(
        launcher,
        "_assert_restore_candidate_stack_absent",
        lambda _: (_ for _ in ()).throw(BackupError("candidate collision")),
    )
    monkeypatch.setattr(
        launcher,
        "_protect_existing_restore_target",
        lambda _: pytest.fail("safety backup must not start after collision"),
    )

    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )

    assert result.exit_code != 0
    assert "candidate collision" in str(result.exception)
    assert config.read_bytes() == original


def test_force_restore_retires_only_the_previous_project_stack(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(
        monkeypatch,
        tmp_path,
        project_id="project-b",
    )
    safety = {
        "archive": tmp_path / "safety-a.dduobackup",
        "project_id": "project-a",
        "context": {
            "id": "project-a",
            "name": "Project A",
            "root_path": str(target),
            "api_port": 18009,
            "web_port": 20009,
        },
    }
    monkeypatch.setattr(launcher, "_protect_existing_restore_target", lambda _: safety)
    compose_calls = []
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda project, *args, **kwargs: compose_calls.append((project["id"], args))
        or SimpleNamespace(returncode=0),
    )
    removed = []
    monkeypatch.setattr(
        launcher,
        "unregister_project_config",
        lambda project_id: removed.append(project_id) or True,
    )

    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert compose_calls[0] == (
        "project-a",
        ("down", "--volumes", "--remove-orphans"),
    )
    assert compose_calls[1] == (
        "project-b",
        ("down", "--volumes", "--remove-orphans"),
    )
    assert removed == ["project-a"]


def test_force_restore_failure_after_old_cleanup_requests_full_a_to_b_rollback(
    monkeypatch, tmp_path
):
    archive, target, _ = _mock_full_restore(
        monkeypatch,
        tmp_path,
        project_id="project-b",
    )
    safety = {
        "archive": tmp_path / "safety-a.dduobackup",
        "project_id": "project-a",
        "context": {
            "id": "project-a",
            "root_path": str(target),
            "api_port": 18009,
            "web_port": 20009,
        },
    }
    monkeypatch.setattr(launcher, "_protect_existing_restore_target", lambda _: safety)
    monkeypatch.setattr(
        launcher,
        "compose",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(launcher, "unregister_project_config", lambda _: True)

    def fail_candidate_restore(_project, _dump, on_volume_reset):
        on_volume_reset()
        raise BackupError("candidate restore failed")

    monkeypatch.setattr(launcher, "_restore_postgres_volume", fail_candidate_restore)
    rollbacks = []
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: rollbacks.append(args) or {"id": "project-a"},
    )

    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )

    assert result.exit_code != 0
    assert len(rollbacks) == 1
    assert rollbacks[0][1]["id"] == "project-b"
    assert rollbacks[0][3] is True
    assert rollbacks[0][4] == "project-b"


def test_restore_command_rebuilds_isolated_stack(monkeypatch, tmp_path):
    archive, key = backup_archive(tmp_path)
    target = tmp_path / "target"
    project = {"id": "p1", "name": "Project", "api_port": 18001, "web_port": 20001}
    monkeypatch.setenv("OPENAI_API_KEY", "configured-separately")
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_drill", lambda *args: {"projects": 1})
    monkeypatch.setattr(launcher, "_assert_restore_candidate_stack_absent", lambda _: None)
    monkeypatch.setattr(launcher, "restore_project_config", lambda *args, **kwargs: project)
    configured = []
    monkeypatch.setattr(
        launcher, "configure_project_backup", lambda *args, **kwargs: configured.append(kwargs)
    )
    commands = []

    def compose(*args):
        commands.append(args[1:])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher, "compose", compose)
    monkeypatch.setattr(launcher, "_wait_for_postgres", lambda _: None)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    monkeypatch.setattr(launcher, "_wait_for_api", lambda *args, **kwargs: None)
    api_paths = []

    def api_request(project, method, path, **kwargs):
        api_paths.append(path)
        if path.endswith("/authority"):
            return {"writable": True}
        return {
            "qdrant_collection": "collection",
            "queued": 0,
            "deleted": 0,
        }

    monkeypatch.setattr(launcher, "_api_request", api_request)

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(launcher.httpx, "Client", Client)
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            key,
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code == 0 and "Restore verified" in result.stdout
    assert not configured[0]["exact_destination"] and configured[0]["replace_key"]
    assert any(command[:2] == ("down", "--volumes") for command in commands)
    assert any(any("restore_index" in part for part in command) for command in commands)
    assert api_paths.count("/projects/p1/tasks/reindex?origin=restore") == 1
    assert "Task semantic index will be rebuilt from PostgreSQL" in result.stdout
    assert "Qdrant memory snapshot restored: true" in result.stdout


def test_restore_requires_separate_openai_key_and_consistent_identity(monkeypatch, tmp_path):
    archive, key = backup_archive(tmp_path, qdrant=False)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(
        launcher.app,
        ["backup", "restore", str(archive), "--recovery-key", key, "--yes"],
    )
    assert result.exit_code == 5 and "embeddings key is absent" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_legacy_restore_never_reads_an_embeddings_key_through_a_symlink(
    monkeypatch, tmp_path
):
    archive, key = backup_archive(tmp_path, qdrant=False)
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    outside = tmp_path / "outside-alpha.env"
    outside.write_text("OPENAI_API_KEY=outside-secret\n")
    outside.chmod(0o600)
    launcher.LEGACY_ENV_FILE.parent.mkdir(parents=True)
    launcher.LEGACY_ENV_FILE.symlink_to(outside)

    result = runner.invoke(
        launcher.app,
        ["backup", "restore", str(archive), "--recovery-key", key, "--yes"],
    )

    assert result.exit_code != 0
    assert outside.read_text() == "OPENAI_API_KEY=outside-secret\n"


def test_restore_reports_degraded_index_without_hiding_postgres_success(monkeypatch, tmp_path):
    archive, key = backup_archive(tmp_path, qdrant=False)
    project = {"id": "p1", "name": "Project", "api_port": 18001, "web_port": 20001}
    monkeypatch.setenv("OPENAI_API_KEY", "configured-separately")
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    monkeypatch.setattr(launcher, "_validate_dump_with_docker", lambda _: None)
    monkeypatch.setattr(launcher, "_restore_drill", lambda *args: {"projects": 1})
    monkeypatch.setattr(launcher, "_assert_restore_candidate_stack_absent", lambda _: None)
    monkeypatch.setattr(launcher, "restore_project_config", lambda *args, **kwargs: project)
    monkeypatch.setattr(launcher, "configure_project_backup", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "compose", lambda *args: SimpleNamespace(returncode=0))
    monkeypatch.setattr(launcher, "_wait_for_postgres", lambda _: None)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    monkeypatch.setattr(launcher, "_wait_for_api", lambda *args, **kwargs: None)

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(launcher.httpx, "Client", Client)
    safety_archive = tmp_path / "safety.dduobackup"
    safety_point = {"archive": safety_archive}
    rollbacks = []
    monkeypatch.setattr(
        launcher, "_protect_existing_restore_target", lambda _: safety_point
    )
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: rollbacks.append(args),
    )

    api_paths = []

    def api_request(project, method, path, **kwargs):
        api_paths.append(path)
        if path.endswith("/authority"):
            return {"writable": True}
        if path.endswith("/backups"):
            return {"qdrant_collection": "collection"}
        if path.endswith("/memories/reindex"):
            raise RuntimeError("memory qdrant offline")
        if "/tasks/reindex" in path:
            raise RuntimeError("task qdrant offline")
        if path.endswith("/backups/register-restore"):
            raise RuntimeError("provenance unavailable")
        return {}

    monkeypatch.setattr(launcher, "_api_request", api_request)
    result = runner.invoke(
        launcher.app,
        ["backup", "restore", str(archive), "--recovery-key", key, "--yes", "--force"],
    )
    assert result.exit_code == 8
    assert "RESTORE_DEGRADED" in result.stdout
    assert "memory qdrant offline" in result.stdout
    assert "task qdrant offline" in result.stdout
    assert "provenance unavailable" in result.stdout
    assert "Restore verified" in result.stdout
    assert api_paths.count("/projects/p1/tasks/reindex?origin=restore") == 1
    assert rollbacks == []


def test_full_restore_publishes_verified_marker_only_after_health(monkeypatch, tmp_path):
    archive, target, manifest_bytes = _mock_full_restore(monkeypatch, tmp_path)
    recovery_key = generate_recovery_key()
    monkeypatch.setattr(launcher, "read_recovery_key", lambda _: recovery_key)
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            recovery_key,
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.stdout
    marker_path = target / launcher.DISASTER_RECOVERY_FILE
    marker = json.loads(marker_path.read_text())
    assert marker["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert marker["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert launcher._verified_recovery_marker(target, "p1") == marker


def test_restore_normalizes_nested_project_root_before_writing_state(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    (target / ".git").mkdir(parents=True)
    nested = target / "backend" / "module"
    nested.mkdir(parents=True)
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(nested),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (target / launcher.DISASTER_RECOVERY_FILE).is_file()
    assert not (nested / launcher.DISASTER_RECOVERY_FILE).exists()


def test_failed_full_restore_removes_stale_verified_marker(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(
        monkeypatch,
        tmp_path,
        health_error=RuntimeError("API unhealthy"),
    )
    marker_path = target / launcher.DISASTER_RECOVERY_FILE
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text('{"stale":true}')
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code != 0
    assert not marker_path.exists()


def test_restore_validation_failure_preserves_previous_recovery_marker(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    marker_path = target / launcher.DISASTER_RECOVERY_FILE
    marker_path.parent.mkdir(parents=True)
    previous = b'{"existing":"verified-proof"}\n'
    marker_path.write_bytes(previous)
    marker_path.chmod(0o640)
    monkeypatch.setattr(
        launcher,
        "verify_archive",
        lambda *args, **kwargs: (_ for _ in ()).throw(BackupError("bad archive")),
    )

    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )

    assert result.exit_code != 0
    assert marker_path.read_bytes() == previous
    assert marker_path.stat().st_mode & 0o777 == 0o640


def test_successful_restore_rollback_restores_previous_recovery_marker(
    monkeypatch, tmp_path
):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    marker_path = target / launcher.DISASTER_RECOVERY_FILE
    marker_path.parent.mkdir(parents=True)
    previous = b'{"existing":"verified-proof"}\n'
    marker_path.write_bytes(previous)
    marker_path.chmod(0o640)
    safety_archive = tmp_path / "safety-marker.dduobackup"
    safety_point = {
        "archive": safety_archive,
        "context": {"id": "p1", "root_path": str(target)},
    }
    monkeypatch.setattr(
        launcher, "_protect_existing_restore_target", lambda _: safety_point
    )
    monkeypatch.setattr(
        launcher,
        "_prepare_restore_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(BackupError("candidate failed")),
    )
    rollbacks = []
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: rollbacks.append(args) or {"id": "p1"},
    )

    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )

    assert result.exit_code != 0
    assert len(rollbacks) == 1
    assert marker_path.read_bytes() == previous
    assert marker_path.stat().st_mode & 0o777 == 0o640


def test_restore_preparation_failure_rolls_back_host_state(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    safety_archive = tmp_path / "safety-preparation.dduobackup"
    old_context = {"id": "p1", "root_path": str(target), "api_port": 18001}
    safety_point = {"archive": safety_archive, "context": old_context}
    monkeypatch.setattr(
        launcher, "_protect_existing_restore_target", lambda _: safety_point
    )
    monkeypatch.setattr(
        launcher,
        "restore_project_runtime_settings",
        lambda *args: (_ for _ in ()).throw(BackupError("runtime publish failed")),
    )
    rollbacks = []
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: rollbacks.append(args) or {"id": "p1"},
    )
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code != 0
    assert len(rollbacks) == 1
    assert rollbacks[0][1] == old_context
    assert rollbacks[0][3] is False
    assert "restored automatically" in result.stdout
    assert not (target / launcher.DISASTER_RECOVERY_FILE).exists()


def test_pg_restore_failure_rolls_back_to_verified_safety_point(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    safety_archive = tmp_path / "safety-success.dduobackup"
    safety_point = {"archive": safety_archive}
    monkeypatch.setattr(
        launcher, "_protect_existing_restore_target", lambda _: safety_point
    )
    commands = []

    def checked(_project, *args):
        commands.append(args)
        if "pg_restore" in args:
            raise BackupError("pg_restore failed")

    monkeypatch.setattr(launcher, "_checked_compose", checked)
    rolled_back = []
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: rolled_back.append(args) or {"id": "p1"},
    )
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code != 0
    assert any("pg_restore" in command for command in commands)
    assert len(rolled_back) == 1
    assert rolled_back[0][3] is True
    assert "restored automatically" in result.stdout
    assert not (target / launcher.DISASTER_RECOVERY_FILE).exists()


def test_pg_restore_and_rollback_failure_reports_safety_path(monkeypatch, tmp_path):
    archive, target, _ = _mock_full_restore(monkeypatch, tmp_path)
    safety_archive = tmp_path / "safety-manual.dduobackup"
    safety_point = {"archive": safety_archive}
    monkeypatch.setattr(
        launcher, "_protect_existing_restore_target", lambda _: safety_point
    )

    def checked(_project, *args):
        if "pg_restore" in args:
            raise BackupError("pg_restore failed")

    monkeypatch.setattr(launcher, "_checked_compose", checked)
    monkeypatch.setattr(
        launcher,
        "_rollback_restore_safety_point",
        lambda *args: (_ for _ in ()).throw(BackupError("rollback pg_restore failed")),
    )
    result = runner.invoke(
        launcher.app,
        [
            "backup",
            "restore",
            str(archive),
            "--project-root",
            str(target),
            "--recovery-key",
            generate_recovery_key(),
            "--yes",
            "--force",
        ],
    )
    assert result.exit_code != 0
    assert "automatic rollback also failed" in str(result.exception)
    assert str(safety_archive) in str(result.exception)
    assert not (target / launcher.DISASTER_RECOVERY_FILE).exists()


def test_verified_recovery_marker_requires_both_backup_digests(monkeypatch, tmp_path):
    marker_path = tmp_path / launcher.DISASTER_RECOVERY_FILE
    marker_path.parent.mkdir(parents=True)
    marker = {
        "project_id": "p1",
        "backup_id": "backup-1",
        "schema_version": 2,
        "credentials_complete": True,
        "archive_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    recovery_key = "marker-test-key"
    marker["evidence_hmac"] = launcher._recovery_evidence_hmac(marker, recovery_key)
    monkeypatch.setattr(launcher, "read_recovery_key", lambda _: recovery_key)
    marker_path.write_text(json.dumps(marker))
    assert launcher._verified_recovery_marker(tmp_path, "p1") == marker

    marker["manifest_sha256"] = "not-a-digest"
    marker_path.write_text(json.dumps(marker))
    with pytest.raises(RuntimeError, match="complete verified"):
        launcher._verified_recovery_marker(tmp_path, "p1")

    marker["manifest_sha256"] = "c" * 64
    marker_path.write_text(json.dumps(marker))
    with pytest.raises(RuntimeError, match="failed authentication"):
        launcher._verified_recovery_marker(tmp_path, "p1")


def test_backup_all_success_empty_failure_and_invalid_trigger(monkeypatch, tmp_path):
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"p1": {"root_path": str(tmp_path)}}}))
    monkeypatch.setattr(launcher, "REGISTRY_PATH", registry)
    monkeypatch.setattr(launcher, "load_backup_registry", lambda: {"projects": {}})
    empty = runner.invoke(launcher.app, ["backup", "all"])
    assert empty.exit_code == 0 and "nothing to do" in empty.stdout
    assert runner.invoke(launcher.app, ["backup", "all", "--trigger", "bad"]).exit_code != 0

    monkeypatch.setattr(launcher, "load_backup_registry", lambda: {"projects": {"p1": {}}})
    monkeypatch.setattr(launcher, "require_docker", lambda: None)
    project = {"id": "p1", "name": "P", "api_port": 1, "web_port": 2}
    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(launcher, "start_stack", lambda *args: None)
    requests = []

    def api_request(context, *args, **kwargs):
        requests.append(context)
        return {"archive_name": "ok.dduobackup"}

    monkeypatch.setattr(launcher, "_api_request", api_request)
    success = runner.invoke(launcher.app, ["backup", "all", "--trigger", "uninstall"])
    assert success.exit_code == 0 and "PASS" in success.stdout
    assert requests == [{**project, "root_path": str(tmp_path.resolve())}]

    monkeypatch.setattr(
        launcher,
        "load_project",
        lambda _: {**project, "id": "different-project"},
    )
    mismatch = runner.invoke(launcher.app, ["backup", "all"])
    assert mismatch.exit_code == 7
    assert "registered project identity does not match" in mismatch.stdout

    monkeypatch.setattr(launcher, "load_project", lambda _: project)
    monkeypatch.setattr(
        launcher, "_api_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("x"))
    )
    failure = runner.invoke(launcher.app, ["backup", "all"])
    assert failure.exit_code == 7 and "FAIL" in failure.stdout
