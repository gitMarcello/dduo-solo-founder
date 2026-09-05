from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
import webbrowser
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import typer

from dduo_solo_founder.backup import BackupError, verify_archive
from dduo_solo_founder.authority_receipts import (
    AuthorityReceipt,
    AuthorityReceiptError,
    verify_authority_receipt,
)
from dduo_solo_founder.backup_config import (
    configure_project_backup,
    compose_backup_environment,
    load_backup_registry,
    load_backup_settings,
    read_recovery_key,
)
from dduo_solo_founder.bridge_auth import BRIDGE_PROTOCOL_VERSION, project_bridge_token
from dduo_solo_founder.client_readiness import (
    client_readiness as inspect_client_readiness,
    detached_cli_environment,
    resolve_codex_executable,
    subscription_auth_status,
)
from dduo_solo_founder.client_binding import (
    ProjectBinding,
    REMOTE_CREDENTIALS_DIR,
    ProvisionalRemoteApproval,
    begin_provisional_remote_approval,
    binding_from_project,
    canonical_https_url,
    finish_provisional_remote_approval,
    load_binding,
    write_remote_project_config,
)
from dduo_solo_founder.client_http import ProjectHttpClient
from dduo_solo_founder.invitations import (
    InvitationPayloadError,
    create_invitation_bundle,
    decode_invite_payload,
    encode_invite_payload,
    invitation_setup_prompt,
    invitation_urls,
)
from dduo_solo_founder.manual_cache import store_verified_manual
from dduo_solo_founder.project_activation import allow_setup, decline_setup
from dduo_solo_founder.project_config import (
    CONFIG_PATH,
    REGISTRY_PATH,
    canonical_project_root,
    compose_name,
    find_workspace_root,
    is_git_worktree,
    load_project,
    new_project_config,
    project_dashboard_url,
    register_project_config,
    restore_project_config,
    set_local_deployment_mode,
    unregister_project_config,
)
from dduo_solo_founder.project_secrets import (
    LEGACY_ENV_FILE,
    RETIRED_LEGACY_ENV_FILE,
    SECRET_ENV_KEYS,
    codex_environment,
    clear_pending_manager_bootstrap,
    ensure_project_codex_home,
    ensure_project_secret_environment,
    ensure_remote_runtime_secrets,
    load_legacy_secret_environment,
    load_project_secrets,
    load_pending_manager_bootstrap,
    parse_secret_environment,
    project_codex_home,
    project_env_file,
    replace_project_secrets,
    save_project_secrets,
    save_pending_manager_bootstrap,
)
from dduo_solo_founder.runtime_settings import (
    read_runtime_settings,
    restore_project_runtime_settings,
)
from dduo_solo_founder.remote_gateway import (
    FIRST_HTTPS_PORT,
    GATEWAY_CADDYFILE,
    GATEWAY_REGISTRY,
    load_gateway_registry,
    register_gateway_project,
    unregister_gateway_project,
    write_caddyfile,
)

app = typer.Typer(help="Install and run the local dDuo Solo Founder project memory.")
backup_app = typer.Typer(help="Configure, create, verify, and restore encrypted backups.")
app.add_typer(backup_app, name="backup")
RUNTIME_POINTER = Path.home() / ".config" / "dduo-solo-founder" / "runtime-path"
BRIDGE_DIR = Path.home() / ".config" / "dduo-solo-founder" / "bridge"
BRIDGE_TOKEN_FILE = BRIDGE_DIR / "token"
BRIDGE_PID_FILE = BRIDGE_DIR / "pid"
BRIDGE_LOG_FILE = BRIDGE_DIR / "bridge.log"
BRIDGE_LOCK_FILE = BRIDGE_DIR / "agent.lock"
BRIDGE_PORT_FILE = BRIDGE_DIR / "port"
BRIDGE_SYSTEMD_ENV_FILE = BRIDGE_DIR / "agent.env"
BRIDGE_SYSTEMD_UNIT_NAME = "dduo-solo-founder-agent.service"
BRIDGE_SYSTEMD_UNIT_FILE = (
    Path.home() / ".config" / "systemd" / "user" / BRIDGE_SYSTEMD_UNIT_NAME
)
HOOK_STATE_DIR = Path.home() / ".config" / "dduo-solo-founder" / "hook-state"
MCP_OBSERVABILITY_DIR = (
    Path.home() / ".config" / "dduo-solo-founder" / "mcp-observability"
)
DEVICE_ID_FILE = Path.home() / ".config" / "dduo-solo-founder" / "device-id"
REMOTE_NODE_ID_FILE = (
    Path.home() / ".config" / "dduo-solo-founder" / "remote-node-id"
)
RETIRED_NODE_FILE = Path(".dduo-solo-founder/retired-node.json")
DISASTER_RECOVERY_FILE = Path(".dduo-solo-founder/verified-recovery.json")
SHA256_DIGEST = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_EVIDENCE_FIELDS = (
    "project_id",
    "backup_id",
    "schema_version",
    "credentials_complete",
    "archive_sha256",
    "manifest_sha256",
)
AUTHORITY_RECEIPT = re.compile(
    r"^dduo_authority_v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
)
MIB = 1024**2
GIB = 1024**3
# A nominal 1 GiB VPS exposes slightly less memory through /proc/meminfo after
# firmware and kernel reservations. Keep the product requirement at 1 GiB
# while accepting the kernel-visible floor of a genuine 1 GiB machine.
REMOTE_MIN_PHYSICAL_MEMORY_BYTES = 900 * MIB
REMOTE_MIN_SWAP_BYTES = 2 * GIB
REMOTE_MIN_FREE_DISK_BYTES = 5 * GIB


def repo_root() -> Path:
    override = os.getenv("DDUO_SOLO_FOUNDER_HOME")
    if override:
        return Path(override)
    if RUNTIME_POINTER.exists():
        return Path(RUNTIME_POINTER.read_text().strip())
    return Path(__file__).resolve().parents[2]


def docker_ready() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def require_docker() -> None:
    docker_product = "Docker Engine" if sys.platform.startswith("linux") else "Docker Desktop"
    if not shutil.which("docker"):
        typer.echo(
            f"MEMORY_UNAVAILABLE: {docker_product} is not installed. Install and start it, then retry."
        )
        raise typer.Exit(2)
    if not docker_ready():
        typer.echo(
            f"MEMORY_UNAVAILABLE: {docker_product} is installed but not running. Start it, then retry."
        )
        raise typer.Exit(3)


def _linux_memory_totals(meminfo: Path = Path("/proc/meminfo")) -> tuple[int, int]:
    """Return physical and active swap bytes reported by a Linux host."""
    values: dict[str, int] = {}
    try:
        lines = meminfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("remote-host could not inspect Linux memory resources") from exc
    for line in lines:
        key, separator, raw = line.partition(":")
        if not separator or key not in {"MemTotal", "SwapTotal"}:
            continue
        fields = raw.strip().split()
        if not fields or not fields[0].isdigit():
            continue
        multiplier = 1024 if len(fields) == 1 or fields[1].lower() == "kb" else 1
        values[key] = int(fields[0]) * multiplier
    if "MemTotal" not in values or "SwapTotal" not in values:
        raise RuntimeError("remote-host received incomplete Linux memory information")
    return values["MemTotal"], values["SwapTotal"]


def _linux_disk_swap_total(swaps: Path = Path("/proc/swaps")) -> int:
    """Return active disk-backed swap, excluding RAM-backed zram devices."""
    try:
        lines = swaps.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("remote-host could not inspect active Linux swap") from exc
    total = 0
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 3 or not fields[2].isdigit():
            continue
        source = fields[0]
        if source.startswith("/dev/zram"):
            continue
        total += int(fields[2]) * 1024
    return total


def _docker_storage_root() -> Path:
    """Resolve the filesystem that must retain Docker images and volumes."""
    result = subprocess.run(
        ["docker", "info", "--format", "{{.DockerRootDir}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = result.stdout.strip()
    if result.returncode or not raw:
        raise RuntimeError("remote-host could not inspect Docker storage")
    root = Path(raw)
    if not root.is_absolute():
        raise RuntimeError("remote-host received an invalid Docker storage path")
    return root


def _linux_filesystem_free(
    path: Path,
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> tuple[Path, str, int]:
    """Return mount, backing device ID and free bytes without traversing `path`."""
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("remote-host could not inspect Linux filesystems") from exc
    candidates: list[tuple[Path, str]] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 5:
            continue
        decoded = (
            fields[4]
            .replace("\\040", " ")
            .replace("\\011", "\t")
            .replace("\\012", "\n")
            .replace("\\134", "\\")
        )
        mount = Path(decoded)
        if path == mount or mount in path.parents:
            candidates.append((mount, fields[2]))
    if not candidates:
        raise RuntimeError(f"remote-host could not resolve the filesystem for {path}")
    mount, device_id = max(candidates, key=lambda candidate: len(candidate[0].parts))
    try:
        return mount, device_id, shutil.disk_usage(mount).free
    except OSError as exc:
        raise RuntimeError(f"remote-host could not inspect free space on {mount}") from exc


def _gib(value: int) -> str:
    return f"{value / GIB:.1f} GiB"


def _require_remote_host_resources() -> None:
    """Fail before mutation when a provider-neutral VPS is undersized."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("remote-host resource checks require Linux")
    physical_memory, total_swap = _linux_memory_totals()
    if physical_memory < REMOTE_MIN_PHYSICAL_MEMORY_BYTES:
        raise RuntimeError(
            "remote-host requires a VPS with at least 1 GiB of physical RAM "
            f"(detected {_gib(physical_memory)}); swap does not replace this minimum"
        )
    cpu_count = os.cpu_count() or 0
    if cpu_count < 1:
        raise RuntimeError("remote-host requires at least 1 vCPU")

    disk_swap = _linux_disk_swap_total()
    swap_deficit = max(0, REMOTE_MIN_SWAP_BYTES - disk_swap)
    docker_root = _docker_storage_root()
    docker_mount, docker_device, docker_free = _linux_filesystem_free(docker_root)
    _root_mount, root_device, _root_free = _linux_filesystem_free(Path("/"))
    if docker_device != root_device:
        raise RuntimeError(
            "remote-host Beta requires Docker storage and the root filesystem "
            "to share one backing filesystem"
        )
    docker_required = REMOTE_MIN_FREE_DISK_BYTES + swap_deficit
    if docker_free < docker_required:
        raise RuntimeError(
            "remote-host requires at least 5 GiB free in Docker storage after swap; "
            f"detected {_gib(docker_free)} free on {docker_mount} and "
            f"{_gib(disk_swap)} disk-backed swap, so {_gib(docker_required)} is "
            "required there before setup"
        )
    if disk_swap < REMOTE_MIN_SWAP_BYTES:
        raise RuntimeError(
            "remote-host requires at least 2 GiB of active disk-backed swap "
            f"(detected {_gib(disk_swap)} disk-backed and {_gib(total_swap)} total). "
            "Configure persistent VPS swap, verify it survives reboot, then rerun remote-host"
        )


def embeddings_configured(project_id: str | None = None) -> bool:
    """Check one project's embeddings key without exposing it to Git."""
    if not project_id:
        return False
    return bool(load_project_secrets(project_id, include_legacy=False).get("OPENAI_API_KEY"))


def _runtime_binding(
    project_root: Path,
    project: dict,
    *,
    require_approval: bool = True,
) -> ProjectBinding:
    """Resolve a live binding and enforce ownership of every local project ID."""
    local = str(project.get("binding") or "local").strip().lower() == "local"
    return binding_from_project(
        project_root,
        project,
        require_approval=require_approval,
        enforce_project_claim=local,
    )


def ensure_bridge_token() -> str:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    BRIDGE_DIR.chmod(0o700)
    if BRIDGE_TOKEN_FILE.exists():
        token = BRIDGE_TOKEN_FILE.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(48)
    BRIDGE_TOKEN_FILE.write_text(token + "\n")
    BRIDGE_TOKEN_FILE.chmod(0o600)
    return token


def existing_bridge_token() -> str:
    token = os.getenv("DDUO_CLI_BRIDGE_TOKEN", "").strip()
    if token:
        return token
    try:
        return BRIDGE_TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        return ""


def _stable_private_identifier(path: Path, prefix: str) -> str:
    """Return one non-secret, machine-local identifier without repository state."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing and existing.startswith(prefix) and len(existing) <= 100:
        return existing
    value = f"{prefix}{uuid.uuid4()}"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(value + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def machine_device_id() -> str:
    return _stable_private_identifier(DEVICE_ID_FILE, "device-")


def remote_node_id() -> str:
    return _stable_private_identifier(REMOTE_NODE_ID_FILE, "node-")


@app.command("remote-node-id")
def remote_node_id_command() -> None:
    """Print this VPS identity before binding an authority transfer to it."""
    typer.echo(remote_node_id())


def _new_device_token() -> str:
    return f"dduo_dev_{secrets.token_urlsafe(48)}"


def _encode_invite_payload(
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    invitation_code: str,
) -> str:
    """Return one shell-inert, versioned invitation descriptor."""
    return encode_invite_payload(
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
        invitation_code=invitation_code,
    )


def _decode_invite_payload(value: str) -> dict[str, str]:
    """Strictly decode a manager-generated invitation without shell parsing."""
    try:
        return decode_invite_payload(value)
    except InvitationPayloadError as exc:
        raise typer.BadParameter(str(exc), param_hint="--invite-payload") from exc


def _host_control_headers(project: dict) -> dict[str, str]:
    """Authenticate commands issued on a VPS to its own loopback API."""
    if str(project.get("deployment") or "local") != "remote":
        return {}
    token = load_project_secrets(str(project["id"]), include_legacy=False).get(
        "DDUO_INFRASTRUCTURE_TOKEN", ""
    )
    if not token:
        raise RuntimeError("remote host infrastructure credential is unavailable")
    return {"Authorization": f"Bearer {token}"}


def _gateway_compose(*args: str, capture_output: bool = False) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["COMPOSE_PROJECT_NAME"] = "dduo-solo-founder-gateway"
    environment["DDUO_REMOTE_GATEWAY_CADDYFILE"] = str(GATEWAY_CADDYFILE)
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(repo_root() / "compose.remote-gateway.yaml"),
            *args,
        ],
        cwd=repo_root(),
        env=environment,
        check=False,
        capture_output=capture_output,
        text=capture_output,
    )


def _gateway_project(project_id: str) -> dict | None:
    registry = load_gateway_registry(GATEWAY_REGISTRY)
    project = registry.get("projects", {}).get(project_id)
    host = registry.get("public_ip")
    if not isinstance(project, dict) or not host:
        return None
    port = int(project["https_port"])
    display_host = f"[{host}]" if ":" in str(host) else str(host)
    return {
        **project,
        "api_url": f"https://{display_host}:{port}/api",
        "dashboard_url": f"https://{display_host}:{port}",
    }


def agent_port() -> int | None:
    """Return the dynamic loopback port of the persistent local agent."""
    try:
        port = int(BRIDGE_PORT_FILE.read_text().strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def agent_url(host: str = "127.0.0.1") -> str:
    port = agent_port()
    if port is None:
        raise RuntimeError("dDuo local agent has not started")
    return f"http://{host}:{port}"


def _available_agent_port() -> int:
    """Reserve a fresh loopback port while the process-start lock is held."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _systemd_user_command(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _persistent_bridge_unit_installed() -> bool:
    """Return whether this Linux host opted into the reboot-safe VPS agent."""
    return sys.platform.startswith("linux") and BRIDGE_SYSTEMD_UNIT_FILE.is_file()


def _require_persistent_bridge_host() -> None:
    """Fail before VPS promotion when a reboot-safe user service cannot run."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError(
            "remote-host requires a Linux VPS with systemd; local macOS projects are unchanged"
        )
    if not shutil.which("systemctl") or not shutil.which("loginctl"):
        raise RuntimeError(
            "remote-host requires systemd user services (systemctl and loginctl)"
        )
    username = getpass.getuser()
    linger = subprocess.run(
        ["loginctl", "show-user", username, "-p", "Linger", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    if linger.returncode or linger.stdout.strip().lower() != "yes":
        raise RuntimeError(
            "the VPS user service cannot survive reboot; run "
            f"`sudo loginctl enable-linger {shlex.quote(username)}` once, then rerun remote-host"
        )
    probe = _systemd_user_command("show-environment")
    if probe.returncode:
        raise RuntimeError(
            "the systemd user manager is unavailable; open a login session for this VPS user, "
            "run `systemctl --user status`, then rerun remote-host"
        )


def _wait_for_persistent_bridge(token: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bridge_ready(token):
            return
        time.sleep(0.2)
    status = _systemd_user_command("status", BRIDGE_SYSTEMD_UNIT_NAME, "--no-pager")
    detail = (status.stderr or status.stdout or "service did not become ready").strip()
    raise RuntimeError(f"persistent dDuo host agent failed readiness: {detail[-1_000:]}")


def _start_persistent_bridge(token: str) -> None:
    result = _systemd_user_command("start", BRIDGE_SYSTEMD_UNIT_NAME)
    if result.returncode:
        detail = (result.stderr or result.stdout or "systemd start failed").strip()
        raise RuntimeError(f"persistent dDuo host agent could not start: {detail[-1_000:]}")
    _wait_for_persistent_bridge(token)


def _install_persistent_bridge() -> str:
    """Install one shared, reboot-safe host agent before promoting any VPS project."""
    _require_persistent_bridge_host()
    token = ensure_bridge_token()
    executable = shutil.which("dduo-solo-founder-agent")
    if not executable:
        raise RuntimeError(
            "dduo-solo-founder-agent is unavailable; reinstall dDuo, then rerun remote-host"
        )
    if any(character in executable for character in "\r\n%"):
        raise RuntimeError("the dDuo host-agent executable path is unsupported")

    # Stop either a prior user unit or the legacy detached process before
    # publishing the environment consumed by the replacement service.
    stop_cli_bridge()
    port = agent_port() or _available_agent_port()
    path_value = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    if any(character in path_value for character in "\r\n"):
        raise RuntimeError("the VPS PATH cannot be represented safely")
    _atomic_private_bytes(
        BRIDGE_SYSTEMD_ENV_FILE,
        (
            f"DDUO_CLI_BRIDGE_TOKEN={token}\n"
            f"DDUO_CLI_BRIDGE_PORT={port}\n"
            f"PATH={path_value}\n"
        ).encode("utf-8"),
    )
    _atomic_private_bytes(BRIDGE_PORT_FILE, f"{port}\n".encode("utf-8"))
    unit = (
        "[Unit]\n"
        "Description=dDuo Solo Founder shared host agent\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "EnvironmentFile=%h/.config/dduo-solo-founder/bridge/agent.env\n"
        f"ExecStart={shlex.quote(executable)} --host 0.0.0.0\n"
        "Restart=always\n"
        "RestartSec=2\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    _atomic_private_bytes(BRIDGE_SYSTEMD_UNIT_FILE, unit.encode("utf-8"))
    reload_result = _systemd_user_command("daemon-reload")
    if reload_result.returncode:
        detail = (reload_result.stderr or reload_result.stdout or "daemon-reload failed").strip()
        raise RuntimeError(f"persistent dDuo host agent could not be registered: {detail[-1_000:]}")
    enable_result = _systemd_user_command("enable", "--now", BRIDGE_SYSTEMD_UNIT_NAME)
    if enable_result.returncode:
        detail = (enable_result.stderr or enable_result.stdout or "systemd enable failed").strip()
        raise RuntimeError(f"persistent dDuo host agent could not be enabled: {detail[-1_000:]}")
    _wait_for_persistent_bridge(token)
    return token


def bridge_ready(token: str) -> bool:
    try:
        response = httpx.get(
            f"{agent_url()}/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=1,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        return payload.get("bridge_protocol_version") == BRIDGE_PROTOCOL_VERSION
    except (httpx.HTTPError, RuntimeError, ValueError, TypeError):
        return False


@contextmanager
def _agent_lock(timeout: float = 12):
    """Serialize agent startup across every project on this Mac."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    BRIDGE_DIR.chmod(0o700)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(BRIDGE_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, str(os.getpid()).encode())
        except FileExistsError:
            try:
                if time.time() - BRIDGE_LOCK_FILE.stat().st_mtime > timeout:
                    BRIDGE_LOCK_FILE.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("dDuo local agent is still starting; try again in a moment")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(descriptor)
        BRIDGE_LOCK_FILE.unlink(missing_ok=True)


def ensure_cli_bridge() -> str:
    """Start exactly one persistent dDuo host agent without exposing credentials to Docker."""
    token = ensure_bridge_token()
    if bridge_ready(token):
        return token
    if _persistent_bridge_unit_installed():
        _start_persistent_bridge(token)
        return token
    with _agent_lock():
        if bridge_ready(token):
            return token
        # Retire a healthy pre-scoping bridge before selecting the new protocol.
        # The port remains stable when shutdown completes promptly; otherwise a
        # fresh candidate is used and the legacy process has no live consumers.
        if agent_port() is not None:
            stop_cli_bridge()
        executable = shutil.which("dduo-solo-founder-agent") or shutil.which(
            "dduo-solo-founder-bridge"
        )
        preferred_port = agent_port()
        candidate_ports = (
            [preferred_port, _available_agent_port(), _available_agent_port()]
            if preferred_port is not None
            else [_available_agent_port(), _available_agent_port(), _available_agent_port()]
        )
        for port in candidate_ports:
            command = [executable] if executable else [sys.executable, "-m", "dduo_solo_founder.cli_bridge"]
            command.extend(["--host", "0.0.0.0", "--port", str(port)])
            bridge_environment = os.environ.copy()
            bridge_environment["DDUO_CLI_BRIDGE_TOKEN"] = token
            with BRIDGE_LOG_FILE.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env=bridge_environment,
                )
            deadline = time.monotonic() + 10
            candidate_url = f"http://127.0.0.1:{port}"
            while time.monotonic() < deadline:
                try:
                    response = httpx.get(
                        f"{candidate_url}/health",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=1,
                    )
                    ready = response.status_code == 200
                except httpx.HTTPError:
                    ready = False
                if ready:
                    BRIDGE_PORT_FILE.write_text(f"{port}\n")
                    BRIDGE_PORT_FILE.chmod(0o600)
                    BRIDGE_PID_FILE.write_text(str(process.pid) + "\n")
                    BRIDGE_PID_FILE.chmod(0o600)
                    return token
                if process.poll() is not None:
                    break
                time.sleep(0.1)
    raise RuntimeError("dDuo local agent could not start. Open Setup to repair it.")


def setup_url(project_root: Path | None = None) -> str:
    """Return a short-lived local setup URL; it is never written into project files."""
    token = ensure_cli_bridge()
    response = httpx.post(
        f"{agent_url()}/v1/setup/ticket",
        json={"project_root": str((project_root or Path.cwd()).resolve())},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    ticket = str(payload.get("ticket") or "") if isinstance(payload, dict) else ""
    if not ticket:
        raise RuntimeError("dDuo Setup returned an invalid access ticket")
    query = urlencode({"ticket": ticket})
    return f"{agent_url()}/setup?{query}"


def open_setup(project_root: Path | None = None) -> str:
    """Open the sole user-facing setup surface and return its local URL."""
    allow_setup(find_workspace_root(project_root or Path.cwd()))
    url = setup_url(project_root)
    if not webbrowser.open(url):
        raise RuntimeError("the host browser could not open dDuo Setup")
    return url


def get_setup_status(project_root: Path | None = None) -> dict:
    """Read Setup through the same root-bound browser session used by the local page."""
    root = find_workspace_root(project_root or Path.cwd())
    landing_url = setup_url(root)
    with httpx.Client(timeout=20) as client:
        landing = client.get(landing_url)
        landing.raise_for_status()
        response = client.get(f"{agent_url()}/v1/setup/status")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("dDuo Setup returned an invalid status")
    return payload


def stop_cli_bridge() -> bool:
    unit_stopped = False
    if _persistent_bridge_unit_installed():
        result = _systemd_user_command("stop", BRIDGE_SYSTEMD_UNIT_NAME)
        if result.returncode:
            detail = (result.stderr or result.stdout or "systemd stop failed").strip()
            raise RuntimeError(
                f"persistent dDuo host agent could not be stopped: {detail[-1_000:]}"
            )
        unit_stopped = True
    token = existing_bridge_token()
    if not token:
        return unit_stopped
    try:
        response = httpx.post(
            f"{agent_url()}/shutdown",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3,
        )
        stopped = response.status_code == 202
    except (httpx.HTTPError, RuntimeError):
        stopped = False
    if stopped:
        BRIDGE_PID_FILE.unlink(missing_ok=True)
        # Preserve the dynamically selected port across runtime upgrades. Every
        # isolated Docker stack points to this host agent; reusing it keeps all
        # projects healthy without a mass container restart.
    return stopped or unit_stopped


def compose(
    project: dict,
    *args: str,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run Compose with the isolated environment for one project."""
    if str(project.get("binding") or "local") != "local":
        raise RuntimeError("a remote client binding cannot operate a local Docker stack")
    env = os.environ.copy()
    project_id = str(project["id"])
    for key, value in load_project_secrets(project_id, include_legacy=False).items():
        env[key] = value
    env["COMPOSE_PROJECT_NAME"] = compose_name(project["id"])
    env["DDUO_NODE_ID"] = remote_node_id()
    env["DDUO_SOLO_FOUNDER_API_PORT"] = str(project["api_port"])
    env["DDUO_SOLO_FOUNDER_WEB_PORT"] = str(project["web_port"])
    master_bridge_token = existing_bridge_token()
    env["DDUO_CLI_BRIDGE_TOKEN"] = project_bridge_token(
        master_bridge_token,
        project_id,
    )
    # Supported launcher paths start the agent first. A direct Compose call
    # degrades instead of guessing a shared fixed port owned by another app.
    if agent_port() is not None:
        env.setdefault("DDUO_CLI_BRIDGE_URL", agent_url("host.docker.internal"))
    else:
        env.setdefault("DDUO_CLI_BRIDGE_URL", "http://host.docker.internal:0")
    project_root = find_workspace_root(Path(project.get("root_path") or Path.cwd()))
    env.update(compose_backup_environment(project, project_root))
    compose_files = ["-f", str(repo_root() / "compose.yaml")]
    if str(project.get("deployment") or "local") == "remote":
        compose_files.extend(["-f", str(repo_root() / "compose.remote.yaml")])
    return subprocess.run(
        ["docker", "compose", *compose_files, *args],
        cwd=repo_root(),
        env=env,
        check=False,
        capture_output=capture_output,
        text=capture_output,
    )


@app.command("configure-openai")
def configure_openai(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
) -> None:
    """Store the embeddings-only OpenAI key for exactly one project."""
    root = find_workspace_root(project_root)
    try:
        project = load_project(root)
    except FileNotFoundError as exc:
        raise typer.BadParameter(
            "initialize dDuo for this project before configuring embeddings",
            param_hint="--project-root",
        ) from exc
    binding = _runtime_binding(root, project)
    if binding.remote:
        raise typer.BadParameter(
            "the infrastructure manager configures embeddings on the remote memory host",
            param_hint="--project-root",
        )
    key = typer.prompt("OpenAI API key", hide_input=True).strip()
    if not key:
        typer.echo("No key supplied.")
        raise typer.Exit(1)
    destination = save_project_secrets(str(project["id"]), {"OPENAI_API_KEY": key})
    typer.echo(
        f"OpenAI embeddings key stored for {project['name']} in {destination} "
        "with user-only permissions."
    )


@app.command()
def setup(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    """Open the one-time local setup page for this computer and project."""
    root = find_workspace_root(project_root)
    open_setup(root)
    typer.echo(json.dumps({"opened": True, "project_root": str(root)}))


@app.command("decline-setup")
def decline_setup_command(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    """Remember that this folder must not be offered project memory."""
    decline_setup(find_workspace_root(project_root))
    typer.echo(json.dumps({"status": "declined"}))


@app.command("remote-preflight")
def remote_preflight() -> None:
    """Validate a Linux VPS before creating or moving a memory authority."""
    _require_persistent_bridge_host()
    require_docker()
    _require_remote_host_resources()
    typer.echo(json.dumps({"ready": True, "project_created": False}))


@app.command("login-codex")
def login_codex(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    device_auth: bool = typer.Option(False, "--device-auth", help="Use official browserless device login."),
) -> None:
    """Authenticate server-owned sleep in this project's private Codex home."""
    root = find_workspace_root(project_root)
    project = load_project(root)
    if _runtime_binding(root, project).remote:
        raise typer.BadParameter("authenticate on the memory host, not a remote-bound checkout")
    executable = resolve_codex_executable(required=True)
    project_id = str(project["id"])
    ensure_project_codex_home(project_id)
    environment = codex_environment(project_id, detached_cli_environment())
    # The resolver selects the native Codex executable, including on Windows.
    # Never invoke an npm batch shim through a command shell.
    command = [executable, "login"]
    if device_auth is True:
        command.append("--device-auth")
    result = subprocess.run(command, env=environment, check=False)
    if result.returncode:
        raise typer.Exit(result.returncode)
    status = subscription_auth_status("codex", environment=environment)
    typer.echo(json.dumps({"ready": status.ready, "provider": "codex"}))
    if not status.ready:
        raise typer.Exit(8)


@app.command("client-readiness")
def client_readiness_command(
    client: str = typer.Option(..., "--client"),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
) -> None:
    """Verify protected client authorization before claiming that memory is ready."""
    try:
        result = inspect_client_readiness(client, project_root)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--client") from exc
    typer.echo(json.dumps(result, indent=2))
    if not result["ready"]:
        raise typer.Exit(8)


@app.command()
def doctor(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    root = find_workspace_root(project_root)
    try:
        config = load_project(root)
        binding = _runtime_binding(root, config)
    except FileNotFoundError:
        config = None
        binding = None
    except Exception as exc:
        typer.echo(json.dumps({"project_config": True, "binding_ready": False, "error": str(exc)}))
        raise typer.Exit(1) from exc
    if config is None or binding is None:
        typer.echo(json.dumps({"project_config": False, "binding_ready": False, "status": "unconfigured"}))
        raise typer.Exit(1)
    if binding.remote:
        checks = {
            "project_config": True,
            "project_id": binding.project_id,
            "binding": "remote",
            "binding_ready": True,
            "remote_memory": _api_healthy(config or {}, root),
            "docker_required": False,
            "local_sleep_login_required": False,
        }
        typer.echo(json.dumps(checks, indent=2))
        if not checks["remote_memory"]:
            raise typer.Exit(1)
        return
    token = existing_bridge_token()
    checks = {
        "docker_cli": bool(shutil.which("docker")),
        "docker_daemon": docker_ready(),
        "codex_cli": bool(shutil.which("codex")),
        "claude_cli": bool(shutil.which("claude")),
        "cli_bridge": bool(token) and bridge_ready(token),
    }
    checks["project_config"] = True
    checks["project_id"] = config["id"]
    checks["sleep_cli"] = checks["codex_cli"] or checks["claude_cli"]
    typer.echo(json.dumps(checks, indent=2))
    if not all(
        checks.get(key)
        for key in (
            "docker_cli",
            "docker_daemon",
            "project_config",
            "sleep_cli",
            "cli_bridge",
        )
    ):
        raise typer.Exit(1)


@app.command("init")
def init_project(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    name: str | None = typer.Option(None),
    yes: bool = typer.Option(
        False, "--yes", help="Confirmation was already collected by the agent."
    ),
    headless: bool = typer.Option(False, "--headless", help="Prepare and start this host without a browser."),
) -> None:
    project_root = find_workspace_root(project_root)
    if not yes:
        typer.confirm(
            "Create .dduo-solo-founder/project.toml and start the local Docker stack?", abort=True
        )
    try:
        config = load_project(project_root)
        if _runtime_binding(project_root, config).remote:
            raise typer.BadParameter("this checkout already uses remote memory; no local fallback will be created")
    except FileNotFoundError:
        config = None
    require_docker()
    if headless:
        _require_persistent_bridge_host()
        _require_remote_host_resources()
    allow_setup(project_root)
    if config is not None:
        project_id = config["id"]
        register_project_config(project_root, config)
    else:
        path, project_id = new_project_config(project_root, name)
        typer.echo(f"Created {path}")
        config = load_project(project_root)
    if not embeddings_configured(project_id):
        ensure_project_secret_environment(project_id)
        if not headless:
            open_setup(project_root)
        typer.echo(json.dumps({"setup_required": True, "project_id": project_id,
                              "next_action": "configure-openai, login-codex --device-auth, then rerun init --headless"
                              if headless else "complete_setup"}))
        raise typer.Exit(5)
    if headless:
        _require_remote_codex_auth(str(project_id))
    start_stack(project_root, config, build=True)
    payload = {"id": project_id, "name": config["name"], "root_path": str(project_root.resolve())}
    _api_request(_project_context(project_root, config), "POST", "/projects", timeout=20, json_body=payload)
    typer.echo(
        f"dDuo Solo Founder local services are ready for {config['name']} ({project_id}). "
        f"UI: {project_dashboard_url(config)}"
    )
    typer.echo(
        "CLIENT_RELOAD_REQUIRED_IF_INSTALLED_OR_UPDATED: after a Codex install or update, "
        "fully quit and reopen Codex before opening a new chat in this project folder; after "
        "a Claude update, open a new session before onboarding or using memory."
    )


def _launcher_client(binding: ProjectBinding) -> ProjectHttpClient:
    """Use one compatibility-aware transport for every launcher operation."""
    return ProjectHttpClient(binding, component="launcher")


def _api_healthy(config: dict, project_root: Path | None = None) -> bool:
    try:
        root = find_workspace_root(project_root or Path(config.get("root_path") or Path.cwd()))
        binding = _runtime_binding(root, config)
        return _launcher_client(binding).request(
            "GET", "/health", timeout=2
        ).status_code == 200
    except httpx.HTTPError:
        return False


def start_stack(project_root: Path, config: dict, *, build: bool = False) -> None:
    """Resume a project stack without rebuilding it on every new chat."""
    binding = _runtime_binding(project_root, config)
    retired = project_root / RETIRED_NODE_FILE
    if retired.is_file() and not binding.remote:
        raise RuntimeError(
            "this local memory node was retired; bind the checkout to the new remote node "
            "instead of recreating Docker data"
        )
    if binding.remote:
        if _api_healthy(config, project_root):
            return
        raise RuntimeError(
            "remote dDuo Solo Founder memory is unavailable; work may continue with the last verified manual"
        )
    ensure_cli_bridge()
    if _api_healthy(config, project_root) and not build:
        return
    args = ["up", "-d"]
    if build:
        args.append("--build")
    result = compose({**config, "root_path": str(project_root.resolve())}, *args)
    if result.returncode:
        raise typer.Exit(result.returncode)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if _api_healthy(config, project_root):
            return
        time.sleep(2)
    typer.echo("MEMORY_UNAVAILABLE: dDuo Solo Founder did not become healthy within 180 seconds.")
    raise typer.Exit(4)


def refresh_backup_runtime(project_root: Path, config: dict, *, quiet: bool = False) -> None:
    """Recreate only services that receive the backup bind mounts.

    A healthy API normally makes ``start_stack`` return early. That is correct for
    ordinary chat startup, but insufficient after changing a host backup folder:
    Docker must recreate the API and worker to receive the new mounts.
    """
    ensure_cli_bridge()
    result = compose(
        {**config, "root_path": str(project_root.resolve())},
        "up",
        "-d",
        "--force-recreate",
        "--no-deps",
        "api",
        "worker",
        capture_output=quiet,
    )
    if result.returncode:
        if quiet:
            detail = (result.stderr or result.stdout or "Docker could not refresh backup services.").strip()
            raise RuntimeError(detail[-1_000:])
        raise typer.Exit(result.returncode)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if _api_healthy(config, project_root):
            return
        time.sleep(2)
    if quiet:
        raise RuntimeError("dDuo did not become ready after applying the backup configuration.")
    typer.echo("MEMORY_UNAVAILABLE: dDuo Solo Founder did not become healthy within 180 seconds.")
    raise typer.Exit(4)


@app.command()
def start(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    build: bool = typer.Option(False, "--build", help="Rebuild local service images before starting."),
) -> None:
    project_root = find_workspace_root(project_root)
    config = load_project(project_root)
    if not binding_from_project(project_root, config, require_approval=False).remote:
        require_docker()
        register_project_config(project_root, config)
    start_stack(project_root, config, build=build)
    typer.echo("dDuo Solo Founder memory is ready for this project binding.")


@app.command()
def stop(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    config = load_project(project_root)
    if _runtime_binding(project_root, config, require_approval=False).remote:
        typer.echo("A remote project cannot be stopped from this client.")
        raise typer.Exit(2)
    raise typer.Exit(
        compose({**config, "root_path": str(project_root.resolve())}, "stop").returncode
    )


@app.command()
def status(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    config = load_project(project_root)
    if _runtime_binding(project_root, config, require_approval=False).remote:
        binding = load_binding(project_root)
        ready = _api_healthy(config, project_root)
        typer.echo(json.dumps({"binding": "remote", "endpoint": binding.api_url, "ready": ready}))
        if not ready:
            raise typer.Exit(1)
        return
    raise typer.Exit(compose({**config, "root_path": str(project_root.resolve())}, "ps").returncode)


@app.command()
def reindex(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    """Re-embed active memories into the configured versioned collection."""
    project_root = find_workspace_root(project_root)
    config = load_project(project_root)
    start_stack(project_root, config)
    response = _api_request(
        {**config, "root_path": str(project_root)},
        "POST",
        f"/projects/{config['id']}/memories/reindex",
        timeout=600,
    )
    typer.echo(json.dumps(response, indent=2))


@app.command("sleep")
def sleep_memory(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    """Queue every pending session turn for subscription-backed consolidation."""
    project = load_project(project_root)
    if not _runtime_binding(project_root, project, require_approval=False).remote:
        require_docker()
    start_stack(project_root, project)
    result = _api_request(
        _project_context(project_root, project),
        "POST",
        f"/projects/{project['id']}/sleep",
        json_body={"trigger": "manual"},
    )
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command("bridge-stop", hidden=True)
def bridge_stop_command() -> None:
    """Stop the managed bridge before replacing or removing its runtime."""
    typer.echo("dDuo CLI bridge stopped." if stop_cli_bridge() else "dDuo CLI bridge not running.")


@app.command("memory-status")
def memory_status(project_root: Path = typer.Option(Path.cwd(), "--project-root")) -> None:
    """Show consolidated memories and pending or waiting sleep jobs."""
    project = load_project(project_root)
    if not _runtime_binding(project_root, project, require_approval=False).remote:
        require_docker()
    start_stack(project_root, project)
    result = _api_request(
        _project_context(project_root, project),
        "GET",
        f"/projects/{project['id']}/memory-status",
        timeout=30,
    )
    typer.echo(json.dumps(result, indent=2, default=str))


def _rotate_database_password(project_root: Path, project: dict, password: str) -> None:
    """Rotate the internal PostgreSQL role before containers adopt the new secret."""
    if not password or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in password):
        raise RuntimeError("generated database password contains unsupported characters")
    result = compose(
        {**project, "root_path": str(project_root)},
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "dduo_solo_founder",
        "-d",
        "dduo_solo_founder",
        "-c",
        f"ALTER ROLE dduo_solo_founder WITH PASSWORD '{password}'",
        capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "database password rotation failed").strip()
        raise RuntimeError(detail[-1_000:])


def _prepare_remote_database(project_root: Path, project: dict, password: str) -> None:
    """Converge the database credential before starting password-dependent services.

    The candidate password is already durable in the project secret store.  By
    starting only PostgreSQL/Qdrant and applying ``ALTER ROLE`` idempotently, a
    process crash at any point can simply replay this function; API and worker
    never have to become healthy with a half-rotated credential.
    """
    context = {**project, "root_path": str(project_root)}
    result = compose(context, "up", "-d", "postgres", "qdrant", capture_output=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "data services failed to start").strip()
        raise RuntimeError(detail[-1_000:])
    _wait_for_postgres(context)
    _rotate_database_password(project_root, project, password)


def _remote_manager_bootstrap(
    project_root: Path,
    project: dict,
    *,
    owner_name: str,
) -> tuple[str | None, bool]:
    """Bootstrap one manager and mint a separate client credential exactly once."""
    runtime = load_project_secrets(project["id"], include_legacy=False)
    infrastructure_token = runtime.get("DDUO_INFRASTRUCTURE_TOKEN", "")
    authority_secret = runtime.get("DDUO_NODE_AUTHORITY_SECRET", "")
    if not infrastructure_token or not authority_secret:
        raise RuntimeError("remote project credentials were not initialized")
    pending = load_pending_manager_bootstrap(project["id"])
    pending_existed = pending is not None
    if pending is None:
        pending = {
            "device_token": _new_device_token(),
            "device_id": f"owner-bootstrap-{uuid.uuid4()}",
        }
        # Persist before the first server mutation. If the process, gateway or
        # index recovery fails later, the same credential is replayed and shown
        # on the next remote-host run instead of being lost forever.
        save_pending_manager_bootstrap(
            project["id"],
            device_id=pending["device_id"],
            device_token=pending["device_token"],
        )
    binding = _runtime_binding(project_root, project)
    response = _launcher_client(binding).request(
        "POST",
        f"/projects/{project['id']}/team/bootstrap",
        headers={"X-DDUO-Authority": authority_secret},
        json={
            "display_name": owner_name,
            "device_id": remote_node_id(),
            "device_label": "VPS infrastructure control",
            "device_token": infrastructure_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    bootstrap = response.json()
    if bootstrap.get("idempotent") is True and not pending_existed:
        clear_pending_manager_bootstrap(project["id"])
        return None, False
    owner_token = pending["device_token"]
    owner_device_id = pending["device_id"]
    device_response = _launcher_client(binding).request(
        "POST",
        f"/projects/{project['id']}/team/device-tokens",
        headers={"Authorization": f"Bearer {infrastructure_token}"},
        json={
            "device_id": owner_device_id,
            "device_label": "Infrastructure manager workstation",
            "device_token": owner_token,
        },
        timeout=30,
    )
    device_response.raise_for_status()
    return owner_token, True


def _restart_remote_stack(project_root: Path, project: dict) -> None:
    ensure_cli_bridge()
    result = compose(
        {**project, "root_path": str(project_root)},
        "up",
        "-d",
        "--build",
        "--force-recreate",
    )
    if result.returncode:
        raise RuntimeError(f"remote project stack failed with exit code {result.returncode}")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if _api_healthy(project, project_root):
            return
        time.sleep(2)
    raise RuntimeError("remote project stack did not become healthy within 180 seconds")


def _require_remote_codex_auth(project_id: str) -> None:
    """Fail before promotion if server-owned sleep cannot use its subscription."""
    ensure_project_codex_home(project_id)
    status = subscription_auth_status(
        "codex",
        environment=codex_environment(project_id, os.environ.copy()),
    )
    if status.ready:
        return
    raise RuntimeError(
        "Codex subscription authentication is required on this VPS before remote hosting. "
        "Run `dduo-solo-founder login-codex --device-auth --project-root <server-checkout>`, then retry remote-host "
        f"({status.reason})."
    )


def _recover_project_indexes(project_root: Path, project: dict) -> dict[str, int]:
    """Reconcile both derived semantic projections after authority is claimed."""
    context = _project_context(project_root, project)
    memory = _api_request(
        context,
        "POST",
        f"/projects/{project['id']}/memories/reconcile",
        timeout=600,
    )
    tasks = _api_request(
        context,
        "POST",
        f"/projects/{project['id']}/tasks/reindex?origin=restore",
        timeout=600,
    )
    return {
        "memory_queued": int(memory.get("queued") or 0),
        "memory_deleted": int(memory.get("deleted") or 0),
        "tasks_queued": int(tasks.get("queued") or 0),
    }


def _wait_for_api(project_root: Path, project: dict, *, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _api_healthy(project, project_root):
            return
        time.sleep(2)
    raise RuntimeError(f"project API did not become healthy within {timeout} seconds")


def _validate_final_transfer_backup(
    backup: object,
    project_id: str,
    authority_secret: str,
) -> dict:
    if not isinstance(backup, dict):
        raise RuntimeError("final transfer backup returned an invalid response")
    manifest = backup.get("manifest_json")
    credentials = manifest.get("credentials") if isinstance(manifest, dict) else None
    dduo_secrets = (
        credentials.get("dduo_secrets") if isinstance(credentials, dict) else None
    )
    secret_keys = dduo_secrets.get("keys") if isinstance(dduo_secrets, dict) else None
    fingerprints = (
        dduo_secrets.get("fingerprints") if isinstance(dduo_secrets, dict) else None
    )
    archived_project = manifest.get("project") if isinstance(manifest, dict) else None
    authority_fingerprint = (
        str(fingerprints.get("DDUO_NODE_AUTHORITY_SECRET") or "")
        if isinstance(fingerprints, dict)
        else ""
    )
    if (
        backup.get("status") != "verified"
        or not isinstance(backup.get("archive_name"), str)
        or not str(backup["archive_name"]).endswith(".dduobackup")
        or not isinstance(manifest, dict)
        or manifest.get("schema_version") != 2
        or manifest.get("recovery_contract") != "full-project-v2"
        or not isinstance(archived_project, dict)
        or archived_project.get("id") != project_id
        or not isinstance(credentials, dict)
        or credentials.get("complete") is not True
        or not isinstance(secret_keys, list)
        or "DDUO_NODE_AUTHORITY_SECRET" not in secret_keys
        or not isinstance(fingerprints, dict)
        or not SHA256_DIGEST.fullmatch(authority_fingerprint)
        or not authority_secret
        or not hmac.compare_digest(
            authority_fingerprint,
            hashlib.sha256(authority_secret.encode("utf-8")).hexdigest(),
        )
    ):
        raise RuntimeError(
            "final transfer backup is not a verified, complete full-project-v2 archive"
        )
    return backup


def _verified_retirement_receipt(
    finalized: object,
    project_id: str,
    authority_secret: str,
) -> AuthorityReceipt:
    """Authenticate the durable proof before any source-volume cleanup."""
    if not isinstance(finalized, dict):
        raise RuntimeError("retired-node authority proof is malformed")
    token = finalized.get("finalization_receipt")
    if not isinstance(token, str):
        raise RuntimeError("retired-node authority proof is malformed")
    try:
        receipt = verify_authority_receipt(
            token,
            authority_secret,
            expected_kind="source_finalized",
        )
    except AuthorityReceiptError as exc:
        raise RuntimeError("retired-node authority proof is unauthenticated") from exc
    generation = finalized.get("generation")
    if (
        finalized.get("state") != "transferred"
        or receipt.project_id != project_id
        or finalized.get("project_id", project_id) != project_id
        or finalized.get("node_id") != receipt.source_node_id
        or finalized.get("target_node_id") != receipt.target_node_id
        or isinstance(generation, bool)
        or generation != receipt.source_generation
    ):
        raise RuntimeError("retired-node authority proof does not match this transfer")
    return receipt


def _recovery_evidence_hmac(marker: dict, recovery_key: str) -> str:
    evidence = {field: marker.get(field) for field in RECOVERY_EVIDENCE_FIELDS}
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(recovery_key.encode(), payload, hashlib.sha256).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_recovery_marker(project_root: Path, project_id: str) -> dict:
    path = project_root / DISASTER_RECOVERY_FILE
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "disaster recovery requires a verified full-project-v2 restore on this checkout"
        ) from exc
    if (
        not isinstance(marker, dict)
        or marker.get("project_id") != project_id
        or marker.get("schema_version") != 2
        or marker.get("credentials_complete") is not True
        or not isinstance(marker.get("backup_id"), str)
        or not marker["backup_id"].strip()
        or not SHA256_DIGEST.fullmatch(str(marker.get("archive_sha256") or ""))
        or not SHA256_DIGEST.fullmatch(str(marker.get("manifest_sha256") or ""))
        or not SHA256_DIGEST.fullmatch(str(marker.get("evidence_hmac") or ""))
    ):
        raise RuntimeError(
            "disaster recovery requires a complete verified full-project-v2 restore"
        )
    try:
        expected_hmac = _recovery_evidence_hmac(
            marker,
            read_recovery_key(project_id),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise RuntimeError(
            "disaster recovery requires the recovery key that verified this restore"
        ) from exc
    if not hmac.compare_digest(marker["evidence_hmac"], expected_hmac):
        raise RuntimeError("disaster recovery restore evidence failed authentication")
    return marker


def _claim_remote_authority(
    project_root: Path,
    project: dict,
    *,
    disaster_recovery: bool = False,
    finalization_receipt: str | None = None,
) -> dict:
    """Initialize a new node or activate a database restored from transfer-pending state."""
    context = _project_context(project_root, project)
    status = _api_request(
        context,
        "GET",
        f"/projects/{project['id']}/authority",
        timeout=30,
    )
    node_id = remote_node_id()
    if status["state"] == "active" and status.get("node_id") == node_id:
        (project_root / DISASTER_RECOVERY_FILE).unlink(missing_ok=True)
        return status
    if status["state"] == "active" and status.get("node_id"):
        if not disaster_recovery:
            raise RuntimeError(
                "this memory is still authoritative on another node; prepare a transfer and "
                "restore its final full-recovery backup before hosting it here"
            )
        _verified_recovery_marker(project_root, project["id"])
    if status["state"] == "transferred":
        raise RuntimeError("a retired memory database cannot be reactivated")
    if finalization_receipt and status["state"] != "transfer_pending":
        raise RuntimeError(
            "a finalization receipt can complete only a restored transfer-pending project"
        )
    operation = (
        "recover"
        if disaster_recovery and status["state"] == "active" and status.get("node_id")
        else "complete"
        if status["state"] == "transfer_pending" and finalization_receipt
        else "activate"
        if status["state"] == "transfer_pending"
        else "initialize"
    )
    runtime = load_project_secrets(project["id"], include_legacy=False)
    authority_secret = runtime.get("DDUO_NODE_AUTHORITY_SECRET", "")
    if not authority_secret:
        raise RuntimeError("node authority credential is unavailable")
    binding = _runtime_binding(project_root, project)
    response = _launcher_client(binding).request(
        "POST",
        f"/projects/{project['id']}/authority/{operation}",
        headers={
            **_host_control_headers(project),
            "X-DDUO-Authority": authority_secret,
        },
        json={
            "node_id": node_id,
            "expected_generation": int(status["generation"]),
            **({"old_node_unreachable": True} if operation == "recover" else {}),
            **(
                {"finalization_receipt": finalization_receipt}
                if operation == "complete"
                else {}
            ),
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("state") == "active" and result.get("writable") is True:
        (project_root / DISASTER_RECOVERY_FILE).unlink(missing_ok=True)
    return result


@app.command("remote-host")
def remote_host(
    public_ip: str = typer.Option(..., "--public-ip", help="Public IPv4 or IPv6 of this VPS."),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    owner_name: str = typer.Option("Infrastructure manager", "--owner-name"),
    acme_email: str | None = typer.Option(None, "--acme-email"),
    disaster_recovery: bool = typer.Option(False, "--disaster-recovery"),
    old_node_unreachable: bool = typer.Option(False, "--old-node-unreachable"),
    finalization_receipt: str | None = typer.Option(
        None,
        "--finalization-receipt",
        help="Signed proof returned after the old source was irreversibly finalized.",
    ),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Promote one isolated local project stack to an HTTPS VPS deployment."""
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    recovery_flags = (disaster_recovery, old_node_unreachable, yes)
    if any(recovery_flags) and not all(recovery_flags):
        raise typer.BadParameter(
            "disaster recovery requires --disaster-recovery, --old-node-unreachable and --yes"
        )
    binding = _runtime_binding(project_root, project, require_approval=False)
    if binding.remote:
        raise typer.BadParameter("run remote-host on the VPS checkout, not on a remote client")
    _require_persistent_bridge_host()
    require_docker()
    _require_remote_host_resources()
    ensure_project_secret_environment(project["id"])
    if not load_project_secrets(project["id"]).get("OPENAI_API_KEY"):
        raise RuntimeError("configure the project OpenAI embeddings key before remote hosting")
    _require_remote_codex_auth(project["id"])
    # A VPS project is not usable after reboot unless the shared host-side
    # bridge survives with Docker. Install and prove that service before any
    # database rotation or deployment-mode promotion can begin.
    _install_persistent_bridge()

    # Materialize one durable candidate first, then converge PostgreSQL to it
    # before starting API/worker.  This ordering is replay-safe after a crash.
    ensure_remote_runtime_secrets(project["id"])
    runtime = load_project_secrets(project["id"], include_legacy=False)
    database_password = runtime["DDUO_DATABASE_PASSWORD"]
    _prepare_remote_database(project_root, project, database_password)

    project = set_local_deployment_mode(project_root, "remote")
    _restart_remote_stack(project_root, project)
    authority = _claim_remote_authority(
        project_root,
        project,
        disaster_recovery=disaster_recovery,
        finalization_receipt=finalization_receipt,
    )
    # A restored transfer remains an immutable clone until the old source has
    # finalized. Team bootstrap is a durable mutation, so defer it to the
    # completed/active phase instead of making the pending clone diverge.
    if authority.get("writable") is True:
        owner_token, bootstrapped = _remote_manager_bootstrap(
            project_root,
            project,
            owner_name=owner_name.strip() or "Infrastructure manager",
        )
    else:
        owner_token, bootstrapped = None, False
    index_recovery = (
        _recover_project_indexes(project_root, project)
        if authority.get("writable") is True
        else {"deferred": True, "reason": "authority_handoff_pending"}
    )
    gateway = register_gateway_project(
        project["id"],
        project["name"],
        int(project["web_port"]),
        public_ip,
        email=acme_email,
    )
    write_caddyfile()
    gateway_result = _gateway_compose("up", "-d", "--force-recreate")
    if gateway_result.returncode:
        raise RuntimeError(f"HTTPS gateway failed with exit code {gateway_result.returncode}")
    firewall_ports = sorted({FIRST_HTTPS_PORT, int(gateway["https_port"])})
    firewall_port_list = ", ".join(str(port) for port in firewall_ports)
    payload = {
        "hosted": authority.get("writable") is True,
        "project_id": project["id"],
        "project": project["name"],
        "api_url": gateway["api_url"],
        "dashboard_url": gateway["dashboard_url"],
        "https_port": gateway["https_port"],
        "manager_bootstrapped": bootstrapped,
        "authority": authority,
        "semantic_indexes": index_recovery,
        "firewall_ports": firewall_ports,
        "firewall": (
            f"allow inbound TCP {firewall_port_list}; keep TCP {FIRST_HTTPS_PORT} "
            "open permanently for ACME certificate issuance and renewal"
        ),
    }
    if authority.get("phase") == "destination_ready":
        payload["activation_receipt"] = authority.get("activation_receipt")
        payload["next"] = (
            "on the old source run remote-transfer-retire with this activation receipt; "
            "then rerun remote-host here with the returned finalization receipt"
        )
    typer.echo(json.dumps(payload, indent=2))
    if owner_token:
        typer.echo("\nINITIAL MANAGER CLIENT TOKEN (shown once; store it privately):")
        typer.echo(owner_token)
        typer.echo(
            "Bind the manager's project checkout with `dduo-solo-founder remote-bind`; "
            "the command will ask for this token without putting it in Git."
        )
        clear_pending_manager_bootstrap(project["id"])


@app.command("remote-transfer-prepare")
def remote_transfer_prepare(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    target_node_id: str = typer.Option(..., "--target-node-id"),
) -> None:
    """Freeze the authority and create the final full-recovery archive for a move."""
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    if not target_node_id.startswith("node-") or len(target_node_id) > 100:
        raise typer.BadParameter(
            "--target-node-id must be copied from `dduo-solo-founder remote-node-id` "
            "on the destination VPS"
        )
    # Local projects historically had no node-authority credential. Create only
    # this handoff secret before the final archive; generating the complete
    # remote secret set here could rotate a live local database password.
    ensure_project_secret_environment(project["id"])
    transfer_secrets = load_project_secrets(project["id"], include_legacy=False)
    authority_secret = transfer_secrets.get("DDUO_NODE_AUTHORITY_SECRET", "")
    if not authority_secret:
        authority_secret = secrets.token_urlsafe(48)
        save_project_secrets(
            project["id"],
            {"DDUO_NODE_AUTHORITY_SECRET": authority_secret},
        )
    start_stack(project_root, project)
    context = _project_context(project_root, project)
    stopped = compose(context, "stop", "worker", capture_output=True)
    if stopped.returncode:
        detail = (stopped.stderr or stopped.stdout or "worker drain failed").strip()
        raise RuntimeError(detail[-1_000:])
    try:
        authority = _api_request(
            context,
            "GET",
            f"/projects/{project['id']}/authority",
            timeout=30,
        )
        prepared = _api_request(
            context,
            "POST",
            f"/projects/{project['id']}/authority/prepare?"
            + urlencode(
                {
                    "expected_generation": int(authority["generation"]),
                    "target_node_id": target_node_id,
                }
            ),
            timeout=30,
        )
    except Exception as exc:
        restarted = compose(context, "up", "-d", "worker", capture_output=True)
        if restarted.returncode:
            raise RuntimeError(
                "authority preparation failed and the source worker could not restart"
            ) from exc
        raise
    # The project row fence drains ordinary mutations. Recreating the API then
    # terminates any route that committed early and was still doing provider work.
    recreated = compose(
        context,
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "api",
        capture_output=True,
    )
    if recreated.returncode:
        raise RuntimeError("project is frozen, but the API transfer barrier failed")
    _wait_for_api(project_root, project)
    try:
        backup = _validate_final_transfer_backup(
            _api_request(
                context,
                "POST",
                f"/projects/{project['id']}/backups?trigger=manual",
            ),
            project["id"],
            authority_secret,
        )
    except Exception as exc:
        typer.echo(
            "The project is safely frozen read-only, but the final backup failed. "
            "Repair backup and retry, or run remote-transfer-cancel."
        )
        raise RuntimeError("final transfer backup failed") from exc
    typer.echo(
        json.dumps(
            {
                "prepared": True,
                "authority": prepared,
                "final_backup": backup,
                "next": "restore this full-recovery archive on the new VPS, then run remote-host",
            },
            indent=2,
            default=str,
        )
    )


@app.command("remote-transfer-cancel")
def remote_transfer_cancel(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    new_node_not_activated: bool = typer.Option(False, "--new-node-not-activated"),
) -> None:
    """Cancel an abandoned move only before the restored node is activated."""
    if not new_node_not_activated:
        raise typer.BadParameter(
            "--new-node-not-activated is required: never cancel after the restored node is activated"
        )
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    authority = _api_request(
        _project_context(project_root, project),
        "GET",
        f"/projects/{project['id']}/authority",
        timeout=30,
    )
    result = _api_request(
        _project_context(project_root, project),
        "POST",
        f"/projects/{project['id']}/authority/cancel"
        f"?expected_generation={int(authority['generation'])}",
        timeout=30,
    )
    restarted = compose(
        _project_context(project_root, project),
        "up",
        "-d",
        "worker",
        capture_output=True,
    )
    if restarted.returncode:
        raise RuntimeError("authority was restored, but the worker could not restart")
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command("remote-transfer-retire")
def remote_transfer_retire(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    activation_receipt: str | None = typer.Option(
        None,
        "--activation-receipt",
        help="Signed destination-readiness proof returned by remote-host on the new node.",
    ),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Verify destination readiness, retire the source, and return its completion proof."""
    if not yes:
        raise typer.BadParameter("--yes is required before deleting the old isolated Docker data")
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    if _runtime_binding(project_root, project, require_approval=False).remote:
        raise RuntimeError("retirement must run on the old Docker host, not a remote client")
    require_docker()
    authority_secret = load_project_secrets(
        project["id"], include_legacy=False
    ).get("DDUO_NODE_AUTHORITY_SECRET", "")
    if not authority_secret:
        raise RuntimeError("node authority credential is unavailable; cleanup is fenced")
    retired_path = project_root / RETIRED_NODE_FILE
    cleanup_complete = False
    gateway_cleanup_pending = False
    if retired_path.is_file():
        try:
            retired_state = json.loads(retired_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("retired-node marker is malformed") from exc
        finalized = retired_state.get("authority")
        raw_gateway_cleanup_pending = retired_state.get(
            "gateway_cleanup_pending", False
        )
        if (
            retired_state.get("project_id") != project["id"]
            or not isinstance(finalized, dict)
            or finalized.get("state") != "transferred"
            or not isinstance(finalized.get("finalization_receipt"), str)
            or not AUTHORITY_RECEIPT.fullmatch(finalized["finalization_receipt"])
            or not isinstance(retired_state.get("cleanup_complete"), bool)
            or not isinstance(raw_gateway_cleanup_pending, bool)
        ):
            raise RuntimeError(
                "retired-node marker is malformed or belongs to another project"
            )
        _verified_retirement_receipt(finalized, project["id"], authority_secret)
        cleanup_complete = retired_state.get("cleanup_complete") is True
        gateway_cleanup_pending = raw_gateway_cleanup_pending
    else:
        if not activation_receipt:
            raise typer.BadParameter(
                "--activation-receipt is required before the old authority can be retired"
            )
        authority = _api_request(
            _project_context(project_root, project),
            "GET",
            f"/projects/{project['id']}/authority",
            timeout=30,
        )
        finalized = _api_request(
            _project_context(project_root, project),
            "POST",
            f"/projects/{project['id']}/authority/finalize"
            f"?expected_generation={int(authority['generation'])}",
            timeout=30,
            json_body={"activation_receipt": activation_receipt},
        )
        if not finalized.get("finalization_receipt"):
            raise RuntimeError("old authority did not return a finalization receipt")
        _verified_retirement_receipt(finalized, project["id"], authority_secret)
        # Persist the only completion proof before deleting PostgreSQL. If the
        # process stops during Docker cleanup, the command can resume without
        # contacting the already-retired database.
        _atomic_private_bytes(
            retired_path,
            (
                json.dumps(
                    {
                        "project_id": project["id"],
                        "authority": finalized,
                        "cleanup_complete": False,
                        "gateway_cleanup_pending": False,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            ).encode("utf-8"),
        )
    if not cleanup_complete:
        result = compose(
            {**project, "root_path": str(project_root)},
            "down",
            "--volumes",
            "--remove-orphans",
        )
        if result.returncode:
            raise RuntimeError(
                f"old project Docker cleanup failed with exit code {result.returncode}"
            )
        cleanup_complete = True
        _atomic_private_bytes(
            retired_path,
            (
                json.dumps(
                    {
                        "project_id": project["id"],
                        "authority": finalized,
                        "cleanup_complete": True,
                        "gateway_cleanup_pending": gateway_cleanup_pending,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            ).encode("utf-8"),
        )
    # The signed finalization receipt and completed cleanup marker are durable
    # before releasing this exact project's local port reservation. A retry
    # therefore converges without contacting the retired database, while every
    # unrelated project registration remains untouched.
    unregister_project_config(project["id"])

    gateway_registered = _gateway_project(project["id"]) is not None
    if gateway_cleanup_pending or gateway_registered:
        if not gateway_cleanup_pending:
            # Persist the retry obligation before removing the public route.
            # A crash after this point cannot turn a failed Caddy reconcile
            # into an apparently successful retirement on the next run.
            gateway_cleanup_pending = True
            _atomic_private_bytes(
                retired_path,
                (
                    json.dumps(
                        {
                            "project_id": project["id"],
                            "authority": finalized,
                            "cleanup_complete": True,
                            "gateway_cleanup_pending": True,
                        },
                        indent=2,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                ).encode("utf-8"),
            )
        unregister_gateway_project(project["id"])
        registry = load_gateway_registry()
        if registry["projects"]:
            write_caddyfile()
            gateway_result = _gateway_compose("up", "-d", "--force-recreate")
        else:
            gateway_result = _gateway_compose("down")
        if gateway_result.returncode:
            raise RuntimeError(
                "old project retired, but the shared HTTPS gateway cleanup is pending; "
                "rerun remote-transfer-retire after repairing Caddy"
            )
        gateway_cleanup_pending = False
        _atomic_private_bytes(
            retired_path,
            (
                json.dumps(
                    {
                        "project_id": project["id"],
                        "authority": finalized,
                        "cleanup_complete": True,
                        "gateway_cleanup_pending": False,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            ).encode("utf-8"),
        )
    typer.echo(
        json.dumps(
            {
                "retired": True,
                "authority": finalized,
                "docker_data_removed": cleanup_complete,
                "recoverable_from": "the verified full-recovery archive used by the new node",
                "finalization_receipt": finalized.get("finalization_receipt"),
                "next": (
                    "on the destination rerun remote-host with --finalization-receipt; "
                    "then bind this checkout to the new remote endpoint"
                ),
            },
            indent=2,
        )
    )


def _write_and_approve_remote(
    project_root: Path,
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    token: str,
    replace_existing: bool,
    allow_matching_local: bool = False,
) -> tuple[dict, ProvisionalRemoteApproval]:
    path = project_root / ".dduo-solo-founder" / "project.toml"
    if path.exists():
        current = load_project(project_root)
        same_remote = (
            str(current.get("binding") or "local") == "remote"
            and str(current.get("id") or "") == project_id
            and str(current.get("api_url") or "").rstrip("/") == api_url.rstrip("/")
            and str(current.get("dashboard_url") or "").rstrip("/")
            == dashboard_url.rstrip("/")
        )
        matching_local = (
            str(current.get("binding") or "local") == "local"
            and str(current.get("id") or "") == project_id
        )
        if not same_remote and not replace_existing and not (
            allow_matching_local and matching_local
        ):
            raise RuntimeError(
                "this checkout already has a different memory binding; use --replace-existing "
                "with remote-bind only after a deliberate authority move, or use remote-rebind "
                "when this remote project's endpoint changed"
            )
    write_remote_project_config(
        project_root,
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
    )
    _, approval = begin_provisional_remote_approval(project_root, token)
    (project_root / RETIRED_NODE_FILE).unlink(missing_ok=True)
    return load_project(project_root), approval


def _candidate_remote_credential_path(
    project_root: Path,
    *,
    project_id: str,
    api_url: str,
    dashboard_url: str,
) -> Path:
    """Resolve the private credential path without changing checkout state."""
    canonical_api = canonical_https_url(api_url, field="api_url")
    canonical_dashboard = canonical_https_url(dashboard_url, field="dashboard_url")
    authority = "\0".join((canonical_api, canonical_dashboard))
    material = "\0".join(
        (str(project_root.resolve()), project_id, authority)
    ).encode("utf-8")
    return REMOTE_CREDENTIALS_DIR / f"{hashlib.sha256(material).hexdigest()}.token"


def _remote_member_identity(team: object, *, project_id: str) -> dict:
    member = team.get("current_member") if isinstance(team, dict) else None
    if (
        not isinstance(member, dict)
        or not str(member.get("id") or "").strip()
        or not str(member.get("access_token_id") or "").strip()
        or str(member.get("project_id") or "") != project_id
    ):
        raise RuntimeError(
            "the new endpoint did not preserve project-scoped device-token attribution"
        )
    return member


def _file_snapshot(path: Path) -> tuple[bool, bytes, int | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, b"", None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"private dDuo state is not a regular file: {path}")
    if os.name == "posix" and hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise PermissionError(f"private dDuo state is not owned by the current user: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise RuntimeError(f"private dDuo state changed while it was read: {path}")
        if os.name == "posix" and hasattr(os, "geteuid") and opened.st_uid != os.geteuid():
            raise PermissionError(
                f"private dDuo state is not owned by the current user: {path}"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            contents = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return True, contents, stat.S_IMODE(metadata.st_mode)


def _restore_file_snapshot(
    path: Path,
    snapshot: tuple[bool, bytes, int | None],
    *,
    preserve_new_file: bool = False,
) -> None:
    existed, content, mode = snapshot
    if not existed:
        if not preserve_new_file:
            path.unlink(missing_ok=True)
        return
    _atomic_private_bytes(path, content)
    if mode is not None:
        path.chmod(mode)


def _file_matches_snapshot(
    path: Path,
    snapshot: tuple[bool, bytes, int | None],
) -> bool:
    try:
        current = _file_snapshot(path)
    except (OSError, RuntimeError):
        return False
    return current[:2] == snapshot[:2]


@contextmanager
def _provisional_remote_binding(
    project_root: Path,
    *,
    project_id: str,
    name: str,
    api_url: str,
    dashboard_url: str,
    token: str,
    replace_existing: bool,
    allow_matching_local: bool = False,
    preserve_new_credential_on_failure: bool = False,
):
    """Commit a binding only after the remote endpoint authenticates it.

    A failed invite, revoked token or unreachable VPS restores the checkout,
    its own approval entry and retired-node marker exactly as they were.  For a
    one-time invitation, a newly generated private token is retained without
    an approval so a lost response can be retried without creating a ghost
    server credential.
    """
    config_path = project_root / ".dduo-solo-founder" / "project.toml"
    retired_path = project_root / RETIRED_NODE_FILE
    snapshots = {
        config_path: _file_snapshot(config_path),
        retired_path: _file_snapshot(retired_path),
    }
    approval: ProvisionalRemoteApproval | None = None
    applied_config: tuple[bool, bytes, int | None] | None = None
    try:
        project, approval = _write_and_approve_remote(
            project_root,
            project_id=project_id,
            name=name,
            api_url=api_url,
            dashboard_url=dashboard_url,
            token=token,
            replace_existing=replace_existing,
            allow_matching_local=allow_matching_local,
        )
        applied_config = _file_snapshot(config_path)
        yield project
    except BaseException:
        approval_rolled_back = False
        try:
            if approval is not None:
                approval_rolled_back = finish_provisional_remote_approval(
                    approval,
                    commit=False,
                    preserve_credential=preserve_new_credential_on_failure,
                )
        finally:
            owns_checkout_state = approval is None or (
                approval_rolled_back
                and applied_config is not None
                and _file_matches_snapshot(config_path, applied_config)
            )
            if owns_checkout_state:
                for path, snapshot in reversed(tuple(snapshots.items())):
                    _restore_file_snapshot(path, snapshot)
        raise
    else:
        if not finish_provisional_remote_approval(approval, commit=True):
            raise RuntimeError(
                "remote binding changed concurrently; verify the current project binding"
            )


@app.command("remote-join")
def remote_join(
    project_id: str | None = typer.Option(None, "--project-id"),
    name: str | None = typer.Option(None, "--name"),
    api_url: str | None = typer.Option(None, "--api-url"),
    dashboard_url: str | None = typer.Option(None, "--dashboard-url"),
    invitation_code: str | None = typer.Option(None, "--invitation-code", hidden=True),
    invite_payload: str | None = typer.Option(None, "--invite-payload", hidden=True),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    device_label: str = typer.Option("Codex workstation", "--device-label"),
) -> None:
    """Consume a one-time invitation and bind this checkout to remote memory."""
    direct = (project_id, name, api_url, dashboard_url, invitation_code)
    if invite_payload is not None:
        if any(value is not None for value in direct):
            raise typer.BadParameter(
                "--invite-payload cannot be combined with individual invitation options"
            )
        decoded = _decode_invite_payload(invite_payload)
        project_id = decoded["project_id"]
        name = decoded["name"]
        api_url = decoded["api_url"]
        dashboard_url = decoded["dashboard_url"]
        invitation_code = decoded["invitation_code"]
    elif any(value is None for value in direct):
        raise typer.BadParameter(
            "use --invite-payload or provide every individual invitation option"
        )
    assert project_id is not None
    assert name is not None
    assert api_url is not None
    assert dashboard_url is not None
    assert invitation_code is not None
    project_root = find_workspace_root(project_root)
    if not is_git_worktree(project_root):
        raise RuntimeError(
            "remote-join requires the root of the authorized Git worktree; "
            "clone or open the project repository before consuming the invitation"
        )
    existing_token: str | None = None
    try:
        existing = load_project(project_root)
        candidate = _runtime_binding(project_root, existing, require_approval=False)
        if candidate.remote and candidate.project_id == project_id and candidate.credential_path:
            if candidate.credential_path.is_file():
                existing_token = candidate.credential_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        pass
    candidate_credential = _candidate_remote_credential_path(
        project_root,
        project_id=project_id,
        api_url=api_url,
        dashboard_url=dashboard_url,
    )
    pending_token = None
    if existing_token is None and candidate_credential.is_file():
        pending_token = candidate_credential.read_text(encoding="utf-8").strip() or None
    token = existing_token or pending_token or _new_device_token()
    offline_manual_cached = False
    manual_cache_warning: str | None = None
    with _provisional_remote_binding(
        project_root,
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
        token=token,
        replace_existing=False,
        allow_matching_local=True,
        preserve_new_credential_on_failure=True,
    ) as project:
        result = _api_request(
            _project_context(project_root, project),
            "POST",
            f"/projects/{project_id}/auth/exchange",
            json_body={
                "invitation_code": invitation_code,
                "device_id": machine_device_id(),
                "device_label": device_label,
                "device_token": token,
            },
            timeout=30,
        )
        member = _remote_member_identity(result, project_id=project_id)
        try:
            manual_response = _api_request(
                _project_context(project_root, project),
                "GET",
                f"/projects/{project_id}/team/manual",
                timeout=30,
            )
            remote_binding = _runtime_binding(project_root, project)
            offline_manual_cached = (
                store_verified_manual(remote_binding, manual_response.get("manual"))
                is not None
            )
            if not offline_manual_cached:
                manual_cache_warning = (
                    "remote access is active, but the verified offline manual cache "
                    "is not available yet"
                )
        except Exception:
            manual_cache_warning = (
                "remote access is active, but the verified offline manual cache "
                "could not be prepared; it will retry after the next successful read"
            )
    payload = {
        "joined": True,
        "project_id": project_id,
        "member": member,
        "dashboard_command": "dduo-solo-founder dashboard --tab tasks --project-root .",
        "offline_manual_cached": offline_manual_cached,
        "local_docker_required": False,
        "new_chat_required": True,
    }
    if manual_cache_warning:
        payload["warning"] = manual_cache_warning
    typer.echo(
        json.dumps(
            payload,
            indent=2,
            default=str,
        )
    )


@app.command("remote-bind")
def remote_bind(
    project_id: str = typer.Option(..., "--project-id"),
    name: str = typer.Option(..., "--name"),
    api_url: str = typer.Option(..., "--api-url"),
    dashboard_url: str = typer.Option(..., "--dashboard-url"),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    token: str | None = typer.Option(None, "--token", hidden=True),
    replace_existing: bool = typer.Option(False, "--replace-existing"),
) -> None:
    """Bind an infrastructure manager checkout using an existing device token."""
    project_root = find_workspace_root(project_root)
    supplied = (token or typer.prompt("Project device token", hide_input=True)).strip()
    with _provisional_remote_binding(
        project_root,
        project_id=project_id,
        name=name,
        api_url=api_url,
        dashboard_url=dashboard_url,
        token=supplied,
        replace_existing=replace_existing,
    ) as project:
        team = _api_request(
            _project_context(project_root, project),
            "GET",
            f"/projects/{project_id}/team",
            timeout=30,
        )
        member = _remote_member_identity(team, project_id=project_id)
    typer.echo(
        json.dumps(
            {
                "bound": True,
                "project_id": project_id,
                "current_member": member,
                "dashboard": load_binding(project_root).dashboard_link("tasks"),
            },
            indent=2,
            default=str,
        )
    )


@app.command("remote-rebind")
def remote_rebind(
    project_id: str = typer.Option(..., "--project-id"),
    api_url: str = typer.Option(..., "--api-url"),
    dashboard_url: str = typer.Option(..., "--dashboard-url"),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Move an existing remote checkout to new endpoints without exposing its token."""
    project_root = find_workspace_root(project_root)
    current_project = load_project(project_root)
    current = _runtime_binding(project_root, current_project)
    if not current.remote or not current.bearer_token:
        raise RuntimeError("this checkout has no approved remote credential to reuse")
    if current.project_id != project_id:
        raise RuntimeError("the requested project does not match this checkout binding")

    next_api = canonical_https_url(api_url, field="api_url")
    next_dashboard = canonical_https_url(dashboard_url, field="dashboard_url")
    unchanged = current.api_url == next_api and current.dashboard_url == next_dashboard
    if not unchanged and not yes:
        typer.confirm(
            "Rebind this project from "
            f"{current.api_url} to {next_api} and reuse its private local device credential?",
            abort=True,
        )

    if unchanged:
        project = current_project
        team = _api_request(
            _project_context(project_root, project),
            "GET",
            f"/projects/{project_id}/team",
            timeout=30,
        )
        member = _remote_member_identity(team, project_id=project_id)
    else:
        with _provisional_remote_binding(
            project_root,
            project_id=project_id,
            name=str(current_project.get("name") or current.name),
            api_url=next_api,
            dashboard_url=next_dashboard,
            token=current.bearer_token,
            replace_existing=True,
        ) as project:
            team = _api_request(
                _project_context(project_root, project),
                "GET",
                f"/projects/{project_id}/team",
                timeout=30,
            )
            member = _remote_member_identity(team, project_id=project_id)
    rebound = load_binding(project_root)
    typer.echo(
        json.dumps(
            {
                "rebound": not unchanged,
                "idempotent": unchanged,
                "project_id": project_id,
                "api_url": rebound.api_url,
                "dashboard": rebound.dashboard_link("tasks"),
                "current_member": member,
                "credential_reused": True,
            },
            indent=2,
            default=str,
        )
    )


@app.command("team-invite")
def team_invite(
    display_name: str = typer.Option(..., "--display-name"),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    expires_in_hours: int = typer.Option(24, "--expires-in-hours", min=1, max=168),
    language: str = typer.Option("it", "--language"),
) -> None:
    """Create a project-only invitation and print a self-contained onboarding prompt."""
    if language not in {"en", "it"}:
        raise typer.BadParameter("must be 'en' or 'it'", param_hint="--language")
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    start_stack(project_root, project)
    binding = _runtime_binding(project_root, project)
    if binding.remote:
        api_url = binding.api_url
        dashboard_url = binding.dashboard_url
    else:
        gateway = _gateway_project(project["id"])
        if gateway is None:
            raise RuntimeError("the project is not registered on the VPS HTTPS gateway")
        api_url = gateway["api_url"]
        dashboard_url = gateway["dashboard_url"]
    if not dashboard_url:
        raise RuntimeError("the project dashboard URL is unavailable")
    response = _api_request(
        _project_context(project_root, project),
        "POST",
        f"/projects/{project['id']}/team/invites",
        json_body={
            "display_name": display_name,
            "expires_in_hours": expires_in_hours,
            "language": language,
            "api_url": str(api_url),
            "dashboard_url": str(dashboard_url),
        },
        timeout=30,
    )
    invitation = response.get("invitation") if isinstance(response, dict) else None
    if not isinstance(invitation, dict):
        raise RuntimeError("the project invitation response is malformed")
    descriptor = str(invitation.get("invite_payload") or "")
    if descriptor:
        decoded = _decode_invite_payload(descriptor)
        expected_api_url, expected_dashboard_url = invitation_urls(
            str(api_url), str(dashboard_url)
        )
        if (
            decoded["project_id"] != str(project["id"])
            or decoded["name"] != str(project["name"])
            or decoded["api_url"] != expected_api_url
            or decoded["dashboard_url"] != expected_dashboard_url
        ):
            raise RuntimeError("the project invitation response does not match this project")
        release_version = str(invitation.get("release_version") or "")
        if not release_version:
            raise RuntimeError(
                "the remote dDuo authority is too old to create a release-pinned invitation; "
                "update it before inviting a project member"
            )
        try:
            prompt = invitation_setup_prompt(
                descriptor,
                language=language,
                release_version=release_version,
            )
        except InvitationPayloadError as exc:
            raise RuntimeError(
                "the remote dDuo authority returned invalid invitation release metadata"
            ) from exc
        if invitation.get("setup_prompt") != prompt:
            raise RuntimeError("the project invitation response has an invalid setup prompt")
    else:
        invitation_code = str(invitation.get("invitation_code") or "")
        if not invitation_code:
            raise RuntimeError("the project invitation response is malformed")
        _, prompt = create_invitation_bundle(
            project_id=str(project["id"]),
            name=str(project["name"]),
            api_url=str(api_url),
            dashboard_url=str(dashboard_url),
            invitation_code=invitation_code,
            language=language,
        )
    typer.echo(prompt)


@app.command("dashboard")
def open_dashboard_command(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    tab: str = typer.Option("tasks", "--tab"),
) -> None:
    """Open a local dashboard or exchange a remote bearer for a one-time browser ticket."""
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    binding = _runtime_binding(project_root, project)
    if tab not in {"project", "memory", "tasks", "observability", "team", "activity", "backup", "setup"}:
        raise typer.BadParameter("unsupported dashboard tab", param_hint="--tab")
    if binding.remote:
        response = _api_request(
            _project_context(project_root, project),
            "POST",
            f"/projects/{project['id']}/auth/browser-ticket",
            timeout=30,
        )
        base = binding.dashboard_link(tab)
        separator = "&" if "?" in str(base) else "?"
        url = f"{base}{separator}{urlencode({'ticket': response['ticket']})}"
    elif str(project.get("deployment") or "local") == "remote":
        gateway = _gateway_project(project["id"])
        if gateway is None:
            raise RuntimeError("the project is not registered on the VPS HTTPS gateway")
        response = _api_request(
            _project_context(project_root, project),
            "POST",
            f"/projects/{project['id']}/auth/browser-ticket",
            timeout=30,
        )
        query = urlencode({"project": project["id"], "tab": tab, "ticket": response["ticket"]})
        url = f"{gateway['dashboard_url']}/?{query}"
    else:
        url = project_dashboard_url(project, tab)
    webbrowser.open(url)
    typer.echo(json.dumps({"opened": True, "url": url.split("ticket=", 1)[0] + ("ticket=<one-time>" if "ticket=" in url else "")}))


def _project_context(project_root: Path, project: dict) -> dict:
    return {**project, "root_path": str(canonical_project_root(project_root))}


def _api_request(
    project: dict,
    method: str,
    path: str,
    *,
    timeout: float = 900,
    json_body: dict | None = None,
) -> dict:
    options = {"timeout": timeout}
    if json_body is not None:
        options["json"] = json_body
    raw_root = project.get("root_path")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise RuntimeError("project root context is required for an API request")
    root = canonical_project_root(Path(raw_root))
    binding = _runtime_binding(root, project)
    response = _launcher_client(binding).request(
        method,
        path,
        headers=_host_control_headers(project),
        **options,
    )
    response.raise_for_status()
    return response.json()


@backup_app.command("configure")
def configure_backup_command(
    destination: Path = typer.Argument(..., help="A synced, external, or off-device folder."),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    include_qdrant: bool = typer.Option(True, "--include-qdrant/--no-qdrant"),
    daily: int = typer.Option(7, min=0),
    weekly: int = typer.Option(4, min=0),
    monthly: int = typer.Option(6, min=0),
    json_output: bool = typer.Option(False, "--json", hidden=True),
) -> None:
    """Configure one isolated destination, key, and retention policy."""
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    if _runtime_binding(project_root, project, require_approval=False).remote:
        typer.echo("Remote backup destinations are configured by the infrastructure manager on the VPS.")
        raise typer.Exit(2)
    settings, recovery_key, is_new = configure_project_backup(
        project,
        destination,
        include_qdrant=include_qdrant,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
    )
    if docker_ready():
        refresh_backup_runtime(project_root, project, quiet=json_output)
        runtime_message = "The running project was refreshed with its backup configuration."
    else:
        runtime_message = "Docker is not running; the configuration will apply on the next project start."
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "configured": True,
                    "destination": settings["destination"],
                    "recovery_key": recovery_key if is_new else None,
                    "recovery_key_new": is_new,
                    "runtime_refreshed": docker_ready(),
                }
            )
        )
        return
    typer.echo(f"Backup destination: {settings['destination']}")
    typer.echo(f"Retention: {daily} daily, {weekly} weekly, {monthly} monthly")
    if is_new:
        typer.echo("\nRECOVERY KEY (shown once):")
        typer.echo(recovery_key)
        typer.echo("Store this key in your password manager. It is never included in a backup.")
    else:
        typer.echo("The existing project recovery key was preserved and is not displayed.")
    typer.echo(runtime_message)


@backup_app.command("create")
def create_backup_command(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    trigger: str = typer.Option("manual", help="manual, update, or uninstall"),
) -> None:
    """Create and verify an encrypted backup immediately."""
    if trigger not in {"manual", "update", "uninstall"}:
        raise typer.BadParameter("trigger must be manual, update, or uninstall")
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    remote = _runtime_binding(project_root, project, require_approval=False).remote
    if not remote:
        require_docker()
    if not remote and not load_backup_settings(project["id"]):
        typer.echo("Backup is not configured. Run `dduo-solo-founder backup configure <folder>`.")
        raise typer.Exit(6)
    start_stack(project_root, project)
    result = _api_request(
        _project_context(project_root, project),
        "POST",
        f"/projects/{project['id']}/backups?trigger={trigger}",
    )
    typer.echo(json.dumps(result, indent=2, default=str))


@backup_app.command("status")
def backup_status_command(
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
) -> None:
    """Show local destination and runtime protection status."""
    project_root = find_workspace_root(project_root)
    project = load_project(project_root)
    remote = _runtime_binding(project_root, project, require_approval=False).remote
    if remote:
        start_stack(project_root, project)
        result = _api_request(
            _project_context(project_root, project),
            "GET",
            f"/projects/{project['id']}/backups",
            timeout=30,
        )
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    local_settings = load_backup_settings(project["id"])
    if not local_settings:
        typer.echo(json.dumps({"configured": False}, indent=2))
        return
    require_docker()
    start_stack(project_root, project)
    result = _api_request(
        _project_context(project_root, project),
        "GET",
        f"/projects/{project['id']}/backups",
        timeout=30,
    )
    result["destination"] = local_settings["destination"]
    typer.echo(json.dumps(result, indent=2, default=str))


def _recovery_key(project_root: Path, supplied: str | None) -> str:
    if supplied:
        return supplied.strip()
    try:
        project = load_project(project_root)
        return read_recovery_key(project["id"])
    except (FileNotFoundError, ValueError):
        return typer.prompt("Recovery key", hide_input=True).strip()


def _validate_dump_with_docker(extracted: Path) -> None:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{extracted.resolve()}:/restore:ro",
            "postgres:16-alpine",
            "pg_restore",
            "--list",
            "/restore/postgres.dump",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-1_000:]
        raise BackupError(f"PostgreSQL dump verification failed: {detail}")


@backup_app.command("verify")
def verify_backup_command(
    archive: Path,
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    recovery_key: str | None = typer.Option(None, "--recovery-key", hidden=True),
) -> None:
    """Authenticate an archive and validate its PostgreSQL dump."""
    require_docker()
    key = _recovery_key(project_root, recovery_key)
    with tempfile.TemporaryDirectory(prefix="dduo-backup-cli-verify-") as temporary_dir:
        extracted = Path(temporary_dir)
        verification = verify_archive(archive.expanduser().resolve(), key, extract_to=extracted)
        _validate_dump_with_docker(extracted)
    typer.echo(
        json.dumps(
            {
                "verified": True,
                "project": verification.manifest["project"],
                "created_at": verification.manifest["created_at"],
                "includes_qdrant": verification.manifest["qdrant"]["included"],
            },
            indent=2,
        )
    )


def _latest_backup_archive(project_id: str) -> Path:
    settings = load_backup_settings(project_id)
    if not settings:
        raise BackupError("backup is not configured")
    destination = Path(settings["destination"]).expanduser()
    archives = [path for path in destination.rglob("*.dduobackup") if path.is_file()]
    if not archives:
        raise BackupError(f"no backup archives found under {destination}")
    return max(archives, key=lambda path: path.stat().st_mtime)


def _docker_command(command: list[str], *, failure: str) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-1_000:]
        raise BackupError(f"{failure}: {detail}")
    return result


def _restore_drill(extracted: Path, expected_project_id: str) -> dict[str, int]:
    """Restore PostgreSQL into an expendable container and inspect authoritative rows."""
    container = f"dduo-restore-drill-{uuid.uuid4().hex[:12]}"
    _docker_command(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "-e",
            "POSTGRES_DB=dduo_restore_drill",
            "-e",
            "POSTGRES_USER=dduo",
            "-e",
            "POSTGRES_PASSWORD=dduo",
            "-v",
            f"{extracted.resolve()}:/restore:ro",
            "postgres:16-alpine",
        ],
        failure="could not start the isolated restore database",
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "dduo"],
                capture_output=True,
                text=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise BackupError("isolated restore database did not become ready")
        _docker_command(
            [
                "docker",
                "exec",
                container,
                "pg_restore",
                "-U",
                "dduo",
                "-d",
                "dduo_restore_drill",
                "--no-owner",
                "--no-privileges",
                "/restore/postgres.dump",
            ],
            failure="isolated PostgreSQL restore failed",
        )
        project_rows = [
            row.strip()
            for row in _docker_command(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-U",
                    "dduo",
                    "-d",
                    "dduo_restore_drill",
                    "-Atc",
                    "select id from projects order by id",
                ],
                failure="could not inspect restored projects",
            ).stdout.splitlines()
            if row.strip()
        ]
        if project_rows != [expected_project_id]:
            raise BackupError(
                "restored database must contain exactly the archived project"
            )
        counts = _docker_command(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "dduo",
                "-d",
                "dduo_restore_drill",
                "-At",
                "-F",
                "=",
                "-c",
                (
                    "select 'projects', count(*) from projects union all "
                    "select 'tasks', count(*) from tasks union all "
                    "select 'memories', count(*) from memories union all "
                    "select 'turns', count(*) from turns"
                ),
            ],
            failure="could not inspect restored data",
        ).stdout.splitlines()
        return {
            name: int(value)
            for row in counts
            if "=" in row
            for name, value in [row.split("=", 1)]
        }
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container],
            capture_output=True,
            text=True,
            check=False,
        )


@backup_app.command("drill")
def drill_backup_command(
    archive: Path | None = typer.Argument(None, help="Archive to test; defaults to the latest."),
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    recovery_key: str | None = typer.Option(None, "--recovery-key", hidden=True),
) -> None:
    """Prove a backup by restoring PostgreSQL in a disposable container."""
    require_docker()
    project = load_project(project_root)
    selected = archive.expanduser().resolve() if archive else _latest_backup_archive(project["id"])
    key = _recovery_key(project_root, recovery_key)
    with tempfile.TemporaryDirectory(prefix="dduo-backup-drill-") as temporary_dir:
        extracted = Path(temporary_dir)
        verification = verify_archive(selected, key, extract_to=extracted)
        _validate_dump_with_docker(extracted)
        counts = _restore_drill(extracted, verification.manifest["project"]["id"])
    typer.echo(
        json.dumps(
            {
                "restorable": True,
                "archive": selected.name,
                "project": verification.manifest["project"],
                "authoritative_rows": counts,
                "semantic_index": "rebuildable from PostgreSQL",
            },
            indent=2,
        )
    )


def _wait_for_postgres(project: dict) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = compose(
            project,
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-U",
            "dduo_solo_founder",
            "-d",
            "dduo_solo_founder",
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise BackupError("restored PostgreSQL service did not become ready")


def _checked_compose(project: dict, *args: str) -> None:
    result = compose(project, *args)
    if result.returncode:
        raise BackupError(
            f"docker compose {' '.join(args)} failed with exit code {result.returncode}"
        )


def _restore_postgres_volume(
    project: dict,
    dump: Path,
    on_volume_reset: Callable[[], None] | None = None,
) -> None:
    """Replace one isolated database volume from an already-verified dump."""
    if on_volume_reset is not None:
        # Compose may remove only part of a stack before returning non-zero.
        # Enter the destructive window immediately before attempting teardown,
        # so every ambiguous partial failure receives a full rollback.
        on_volume_reset()
    _checked_compose(project, "down", "--volumes", "--remove-orphans")
    _checked_compose(project, "up", "-d", "postgres", "qdrant")
    _wait_for_postgres(project)
    _checked_compose(project, "cp", str(dump), "postgres:/tmp/restore.dump")
    _checked_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "dropdb",
        "-U",
        "dduo_solo_founder",
        "--if-exists",
        "dduo_solo_founder",
    )
    _checked_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "createdb",
        "-U",
        "dduo_solo_founder",
        "dduo_solo_founder",
    )
    _checked_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "-U",
        "dduo_solo_founder",
        "-d",
        "dduo_solo_founder",
        "--no-owner",
        "--no-privileges",
        "/tmp/restore.dump",
    )


def _restore_qdrant_snapshots(
    project: dict, extracted: Path, manifest: dict
) -> tuple[bool, bool]:
    """Best-effort restore of derived memory/task projections."""
    qdrant = manifest["qdrant"]
    if int(manifest["schema_version"]) == 1:
        items = (
            [("memory", "qdrant.snapshot", qdrant.get("collection"))]
            if qdrant.get("included") and qdrant.get("collection")
            else []
        )
    else:
        items = [
            (kind, item["file"], item["collection"])
            for kind, item in qdrant["collections"].items()
            if item.get("included") and item.get("file") and item.get("collection")
        ]
    restored = {"memory": False, "tasks": False}
    if not items or compose(project, "build", "api").returncode:
        return restored["memory"], restored["tasks"]
    for kind, snapshot_file, collection_name in items:
        result = compose(
            project,
            "run",
            "--rm",
            "--no-deps",
            "-v",
            f"{extracted.resolve()}:/restore:ro",
            "api",
            "python",
            "-m",
            "dduo_solo_founder.restore_index",
            f"/restore/{snapshot_file}",
            collection_name,
        )
        restored[kind] = result.returncode == 0
    return restored["memory"], restored["tasks"]


def _start_and_register_restored_project(project_root: Path, project: dict) -> None:
    """Start the restored application and prove its project API is healthy."""
    start_stack(project_root, project)
    _wait_for_api(project_root, project)
    payload = {
        "id": project["id"],
        "name": project["name"],
        "root_path": str(project_root.resolve()),
    }
    with httpx.Client(timeout=30) as client:
        client.post(
            f"http://127.0.0.1:{project['api_port']}/projects", json=payload
        ).raise_for_status()


def _atomic_private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_metadata = path.parent.lstat()
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise RuntimeError(f"private dDuo directory is unsafe: {path.parent}")
    if (
        os.name == "posix"
        and hasattr(os, "geteuid")
        and parent_metadata.st_uid != os.geteuid()
    ):
        raise PermissionError(f"private dDuo directory is not owned by this user: {path.parent}")
    if os.name == "posix":
        os.chmod(path.parent, 0o700, follow_symlinks=False)
    try:
        target_metadata = path.lstat()
    except FileNotFoundError:
        target_metadata = None
    if target_metadata is not None and (
        stat.S_ISLNK(target_metadata.st_mode)
        or not stat.S_ISREG(target_metadata.st_mode)
        or (
            os.name == "posix"
            and hasattr(os, "geteuid")
            and target_metadata.st_uid != os.geteuid()
        )
    ):
        raise RuntimeError(f"private dDuo file is unsafe: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _restore_full_recovery_supplement(
    extracted: Path,
    project_id: str,
    project_root: Path,
    *,
    force: bool,
    publish: bool = True,
    project_config: dict | None = None,
) -> dict[str, int | bool]:
    """Validate the complete v2 supplement, then publish project-scoped files.

    The validation phase is deliberately free of writes.  A semantically
    malformed late file therefore cannot leave secrets or earlier spool files
    partially restored.  Publication remains atomic for every individual file.
    """

    def read_bytes(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BackupError(f"full recovery {label} is unreadable") from exc

    def read_json(path: Path, label: str) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"full recovery {label} is malformed") from exc

    def json_bytes(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

    prepared_writes: list[tuple[Path, bytes]] = []
    secret_file = extracted / "secrets/dduo.env"
    if not secret_file.is_file():
        raise BackupError("full recovery archive omitted dDuo secrets")
    try:
        secret_text = secret_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupError("full recovery dDuo secrets are unreadable") from exc
    seen_secret_keys: set[str] = set()
    for raw_line in secret_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BackupError("full recovery dDuo secret envelope is malformed")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if (
            key not in SECRET_ENV_KEYS
            or key in seen_secret_keys
            or not raw_value.strip()
            or any(character in raw_value for character in "\x00\r\n")
        ):
            raise BackupError("full recovery dDuo secret envelope is malformed")
        seen_secret_keys.add(key)
    values = parse_secret_environment(secret_text)
    if not values.get("OPENAI_API_KEY"):
        raise BackupError("full recovery archive omitted OPENAI_API_KEY")

    project = project_config or load_project(project_root)
    if str(project.get("id") or "") != project_id:
        raise BackupError("full recovery project configuration is inconsistent")
    binding = binding_from_project(project_root, project, require_approval=False)
    binding_scope = hashlib.sha256(
        f"{project_id}\0{binding.binding_id}".encode("utf-8")
    ).hexdigest()
    archived_binding = extracted / "binding/project.toml"
    if archived_binding.is_file():
        try:
            archived_binding_config = tomllib.loads(archived_binding.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise BackupError("full recovery binding configuration is malformed") from exc
        if str(archived_binding_config.get("id") or "") != project_id:
            raise BackupError("full recovery binding belongs to another project")

    remote_credential = extracted / "binding/remote-credential.token"
    if remote_credential.is_file() and binding.remote and binding.credential_path is not None:
        # The credential is restored for the new binding id, but approval is
        # deliberately not restored. The new checkout must establish authority.
        credential = read_bytes(remote_credential, "remote credential")
        try:
            credential_text = credential.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise BackupError("full recovery remote credential is malformed") from exc
        if not credential_text or any(character.isspace() for character in credential_text):
            raise BackupError("full recovery remote credential is malformed")
        prepared_writes.append((binding.credential_path, credential_text.encode() + b"\n"))

    codex_restored = False
    codex_source = extracted / "secrets/codex/auth.json"
    if codex_source.is_file():
        codex_auth = read_json(codex_source, "Codex auth.json")
        if not isinstance(codex_auth, dict):
            raise BackupError("full recovery Codex auth.json is malformed")
        auth_target = project_codex_home(project_id) / "auth.json"
        if force or not auth_target.exists():
            prepared_writes.append((auth_target, json_bytes(codex_auth)))
        codex_restored = True

    hook_count = 0
    hooks = extracted / "host-state/hooks"
    if hooks.is_dir():
        for source in sorted(hooks.glob("*.json")):
            if not source.name.startswith(f"{project_id}-"):
                raise BackupError("full recovery hook state belongs to another project")
            target = HOOK_STATE_DIR / source.name
            if target.exists() and not force:
                target = target.with_name(
                    f"{target.stem}-restored-{uuid.uuid4().hex[:8]}.json"
                )
            state = read_json(source, "hook state")
            if not isinstance(state, dict) or state.get("project_id") not in {
                None,
                project_id,
            }:
                raise BackupError("full recovery hook state is malformed")
            state["project_id"] = project_id
            if "binding_id" in state:
                state["binding_id"] = binding.binding_id
            prepared_writes.append((target, json_bytes(state)))
            hook_count += 1

    mcp_source = extracted / "host-state/mcp-observability.json"
    mcp_count = 0
    if mcp_source.is_file():
        target = MCP_OBSERVABILITY_DIR / f"{binding_scope}.json"
        restored = read_json(mcp_source, "MCP spool")
        current = read_json(target, "existing MCP spool") if target.is_file() else []
        if not isinstance(restored, list) or not isinstance(current, list):
            raise BackupError("full recovery MCP spool is malformed")
        combined: list[dict] = []
        seen: set[str] = set()
        for item in [*current, *restored]:
            if not isinstance(item, dict):
                raise BackupError("full recovery MCP spool is malformed")
            identity = str(item.get("idempotency_key") or json.dumps(item, sort_keys=True))
            if identity not in seen:
                seen.add(identity)
                combined.append(item)
        prepared_writes.append((target, json_bytes(combined[-100:])))
        mcp_count = len(restored)

    # Legacy v2 archives may contain updater queues, trust, and client pointers.
    # They are deliberately ignored: distribution is now owned by the plugin
    # manager and explicit installer, and restoring one project must never
    # change machine-wide client code.

    # Publication starts only after every archived object passed semantic
    # validation. Each destination is replaced atomically and remains private.
    if publish:
        replace_project_secrets(project_id, values)
        if codex_restored:
            ensure_project_codex_home(project_id, import_global_auth=False)
        for target, content in prepared_writes:
            _atomic_private_bytes(target, content)
    return {
        "codex_auth": codex_restored,
        "hook_spools": hook_count,
        "mcp_observations": mcp_count,
    }


def _portable_backup_history_rows(extracted: Path, project_id: str) -> list[dict]:
    history = extracted / "history/backup-records.json"
    if not history.is_file():
        return []
    try:
        rows = json.loads(history.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("portable backup history is malformed") from exc
    def timestamp_is_valid(value: object, *, nullable: bool) -> bool:
        if value is None:
            return nullable
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
        return parsed.tzinfo is not None

    required = {
        "id",
        "project_id",
        "trigger",
        "status",
        "archive_name",
        "size_bytes",
        "includes_qdrant",
        "retained",
        "source_generation",
        "manifest",
        "error",
        "created_at",
        "completed_at",
        "verified_at",
    }

    def row_is_valid(row: object) -> bool:
        if not isinstance(row, dict) or not required.issubset(row):
            return False
        size_bytes = row["size_bytes"]
        source_generation = row["source_generation"]
        return (
            row["project_id"] == project_id
            and isinstance(row["id"], str)
            and 0 < len(row["id"]) <= 36
            and isinstance(row["trigger"], str)
            and 0 < len(row["trigger"]) <= 30
            and isinstance(row["status"], str)
            and 0 < len(row["status"]) <= 30
            and (
                row["archive_name"] is None
                or isinstance(row["archive_name"], str)
                and len(row["archive_name"]) <= 500
            )
            and (
                size_bytes is None
                or isinstance(size_bytes, int)
                and not isinstance(size_bytes, bool)
                and size_bytes >= 0
            )
            and isinstance(row["includes_qdrant"], bool)
            and isinstance(row["retained"], bool)
            and isinstance(source_generation, int)
            and not isinstance(source_generation, bool)
            and source_generation >= 0
            and isinstance(row["manifest"], dict)
            and (row["error"] is None or isinstance(row["error"], str))
            and timestamp_is_valid(row["created_at"], nullable=False)
            and timestamp_is_valid(row["completed_at"], nullable=True)
            and timestamp_is_valid(row["verified_at"], nullable=True)
        )

    if not isinstance(rows, list) or len(rows) > 10_000 or any(
        not row_is_valid(row) for row in rows
    ):
        raise BackupError("portable backup history is malformed")
    return rows


def _restore_portable_backup_history(extracted: Path, project: dict) -> int:
    rows = _portable_backup_history_rows(extracted, str(project["id"]))
    if not rows:
        return 0
    normalized = extracted / "backup-records.restore.json"
    normalized.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    normalized.chmod(0o600)
    _checked_compose(
        project, "cp", str(normalized), "postgres:/tmp/dduo-backup-history.json"
    )
    statement = """
WITH rows AS (
  SELECT jsonb_array_elements(pg_read_file('/tmp/dduo-backup-history.json')::jsonb) AS item
)
INSERT INTO backup_records (
  id, project_id, trigger, status, archive_name, size_bytes, includes_qdrant,
  retained, source_generation, manifest, error, created_at, completed_at, verified_at
)
SELECT
  item->>'id', item->>'project_id', item->>'trigger', item->>'status',
  item->>'archive_name', NULLIF(item->>'size_bytes', '')::bigint,
  COALESCE((item->>'includes_qdrant')::boolean, false),
  COALESCE((item->>'retained')::boolean, true),
  COALESCE((item->>'source_generation')::integer, 0),
  COALESCE(item->'manifest', '{}'::jsonb)::json, item->>'error',
  (item->>'created_at')::timestamptz,
  NULLIF(item->>'completed_at', '')::timestamptz,
  NULLIF(item->>'verified_at', '')::timestamptz
FROM rows
ON CONFLICT (id) DO NOTHING
""".strip()
    _checked_compose(
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "dduo_solo_founder",
        "-d",
        "dduo_solo_founder",
        "--no-psqlrc",
        "--command",
        statement,
    )
    return len(rows)


def _prepare_restore_target(
    project_root: Path,
    archived_project: dict,
    extracted: Path,
    manifest: dict,
    schema_version: int,
    archive: Path,
    recovery_key: str,
    *,
    force: bool,
) -> tuple[dict, dict[str, int | bool]]:
    """Publish validated host state before replacing any application volume."""
    project_root.mkdir(parents=True, exist_ok=True)
    project = restore_project_config(project_root, archived_project, force=force)
    restored_runtime = (
        restore_project_runtime_settings(
            extracted / "runtime-settings.json",
            project["id"],
        )
        if schema_version == 2
        else None
    )
    supplement_result = (
        _restore_full_recovery_supplement(
            extracted,
            project["id"],
            project_root,
            force=force,
        )
        if schema_version == 2
        else {"codex_auth": False, "hook_spools": 0, "mcp_observations": 0}
    )
    destination_is_namespaced = (
        archive.parent.parent.name == "dduo-solo-founder"
        and archive.parent.name.endswith(f"-{project['id'][:8]}")
    )
    backup_options = (
        restored_runtime.backup_options
        if restored_runtime is not None
        else {"include_qdrant": bool(manifest["qdrant"]["included"])}
    )
    configure_project_backup(
        project,
        archive.parent,
        recovery_key=recovery_key,
        replace_key=force,
        exact_destination=destination_is_namespaced,
        **backup_options,
    )
    return project, supplement_result


def _retire_replaced_restore_target(
    safety: dict | None,
    candidate: dict,
    on_destructive_start: Callable[[], None],
) -> bool:
    """Remove the exact old stack when ``--force`` changes project identity."""
    if safety is None:
        return False
    previous_id = str(safety.get("project_id") or "")
    candidate_id = str(candidate.get("id") or "")
    if not previous_id or previous_id == candidate_id:
        return False
    previous_context = safety.get("context")
    if not isinstance(previous_context, dict) or str(previous_context.get("id") or "") != previous_id:
        raise BackupError("pre-restore safety context is inconsistent")

    # Crossing this line can partially remove the previous Compose stack even
    # when Docker returns non-zero. Mark it first so every ambiguous failure
    # invokes the already-drilled full rollback rather than preserving a
    # potentially incomplete database.
    on_destructive_start()
    _checked_compose(previous_context, "down", "--volumes", "--remove-orphans")
    unregister_project_config(previous_id)
    return True


def _assert_restore_candidate_stack_absent(project_id: str) -> None:
    """Refuse to overwrite an unregistered Compose stack with the same identity."""
    compose_project = compose_name(project_id)
    checks = (
        [
            "docker",
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
        ],
        [
            "docker",
            "volume",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
        ],
    )
    for command in checks:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise BackupError(
                "could not prove that the archived project's isolated Docker stack is absent"
            )
        if result.stdout.strip():
            raise BackupError(
                "the archived project already has an isolated Docker stack on this host; "
                "retire or recover that exact stack before restoring the archive elsewhere"
            )


def _protect_existing_restore_target(project_root: Path) -> dict | None:
    """Create, fully drill, and pin the recovery point used for rollback."""
    try:
        current = load_project(project_root)
    except FileNotFoundError:
        return None
    settings = load_backup_settings(current["id"])
    if not settings:
        raise BackupError(
            "existing project memory has no backup destination; configure backup before restore"
        )
    retention = settings.get("retention") or {}
    include_qdrant = settings.get("include_qdrant", True)
    raw_destination = settings.get("destination")
    try:
        daily = retention.get("daily", 7)
        weekly = retention.get("weekly", 4)
        monthly = retention.get("monthly", 6)
        auto_seconds = settings.get("auto_seconds", 5 * 60)
    except AttributeError as exc:
        raise BackupError("existing project backup settings are malformed") from exc
    if (
        not isinstance(raw_destination, str)
        or not raw_destination.strip()
        or not isinstance(include_qdrant, bool)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (daily, weekly, monthly)
        )
        or not isinstance(auto_seconds, int)
        or isinstance(auto_seconds, bool)
        or auto_seconds < 1
    ):
        raise BackupError("existing project backup settings are malformed")
    rollback_settings = {
        "destination": raw_destination,
        "include_qdrant": include_qdrant,
        "retention": {"daily": daily, "weekly": weekly, "monthly": monthly},
        "auto_seconds": auto_seconds,
    }
    old_key = read_recovery_key(current["id"])
    start_stack(project_root, current)
    result = _api_request(
        _project_context(project_root, current),
        "POST",
        f"/projects/{current['id']}/backups?trigger=manual",
    )
    archive_name = str(result.get("archive_name") or "")
    if (
        result.get("status") != "verified"
        or not archive_name.endswith(".dduobackup")
        or Path(archive_name).name != archive_name
    ):
        raise BackupError("pre-restore safety backup was not verified")
    destination = Path(raw_destination).expanduser().resolve()
    safety_archive = (destination / archive_name).resolve()
    try:
        safety_archive.relative_to(destination)
    except ValueError as exc:
        raise BackupError("pre-restore safety backup escaped its destination") from exc
    try:
        archive_sha256 = _sha256_file(safety_archive)
    except OSError as exc:
        raise BackupError("pre-restore safety backup is unavailable") from exc
    with tempfile.TemporaryDirectory(prefix="dduo-backup-safety-") as temporary_dir:
        extracted = Path(temporary_dir)
        verification = verify_archive(safety_archive, old_key, extract_to=extracted)
        if _sha256_file(safety_archive) != archive_sha256:
            raise BackupError("pre-restore safety backup changed during verification")
        credentials = verification.manifest.get("credentials")
        if (
            verification.manifest.get("schema_version") != 2
            or verification.manifest.get("project", {}).get("id") != current["id"]
            or not isinstance(credentials, dict)
            or credentials.get("complete") is not True
        ):
            raise BackupError(
                "pre-restore safety backup is not a complete full-project archive"
            )
        try:
            archived_project = tomllib.loads(
                (extracted / "project.toml").read_text(encoding="utf-8")
            )
            manifest_sha256 = hashlib.sha256(
                (extracted / "manifest.json").read_bytes()
            ).hexdigest()
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise BackupError(
                "pre-restore safety backup metadata is malformed"
            ) from exc
        if archived_project.get("id") != current["id"]:
            raise BackupError("pre-restore safety backup identity is inconsistent")

        # Every semantic dependency needed by rollback is proved before the
        # existing volume can be destroyed. The pinned digests below make this
        # one-time drill reusable without trusting a mutable archive later.
        _validate_dump_with_docker(extracted)
        _restore_drill(extracted, str(current["id"]))
        read_runtime_settings(extracted / "runtime-settings.json")
        _restore_full_recovery_supplement(
            extracted,
            str(current["id"]),
            project_root,
            force=True,
            publish=False,
            project_config=current,
        )
        _portable_backup_history_rows(extracted, str(current["id"]))
    return {
        "archive": safety_archive,
        "archive_sha256": archive_sha256,
        "manifest_sha256": manifest_sha256,
        "recovery_key": old_key,
        "project_id": current["id"],
        "settings": rollback_settings,
        "context": _project_context(project_root, current),
    }


def _rollback_restore_safety_point(
    project_root: Path,
    failed_context: dict,
    safety: dict,
    restore_volumes: bool = True,
    candidate_project_id: str | None = None,
) -> dict:
    """Restore a safety bundle directly, without invoking the restore command."""
    safety_archive = Path(safety["archive"]).resolve()
    old_key = str(safety["recovery_key"])
    try:
        if _sha256_file(safety_archive) != safety["archive_sha256"]:
            raise BackupError("safety backup changed after its pre-restore drill")
    except OSError as exc:
        raise BackupError("safety backup is unavailable for automatic rollback") from exc
    with tempfile.TemporaryDirectory(prefix="dduo-backup-rollback-") as temporary_dir:
        extracted = Path(temporary_dir)
        verification = verify_archive(safety_archive, old_key, extract_to=extracted)
        if _sha256_file(safety_archive) != safety["archive_sha256"]:
            raise BackupError("safety backup changed during automatic rollback")
        try:
            manifest_sha256 = hashlib.sha256(
                (extracted / "manifest.json").read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise BackupError("safety backup manifest is unavailable") from exc
        if manifest_sha256 != safety["manifest_sha256"]:
            raise BackupError("safety backup manifest changed after its pre-restore drill")
        manifest = verification.manifest
        credentials = manifest.get("credentials")
        if (
            manifest.get("schema_version") != 2
            or manifest.get("project", {}).get("id") != safety["project_id"]
            or not isinstance(credentials, dict)
            or credentials.get("complete") is not True
        ):
            raise BackupError("safety backup is not a complete full-project archive")
        try:
            archived_project = tomllib.loads(
                (extracted / "project.toml").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise BackupError("safety backup project configuration is malformed") from exc
        if archived_project.get("id") != safety["project_id"]:
            raise BackupError("safety backup project identity is inconsistent")

        # Validate every remaining archive-derived write before touching the
        # failed candidate. PostgreSQL itself was already restored in the
        # disposable drill before the destructive window opened.
        read_runtime_settings(extracted / "runtime-settings.json")
        _restore_full_recovery_supplement(
            extracted,
            str(safety["project_id"]),
            project_root,
            force=True,
            publish=False,
            project_config=archived_project,
        )
        _portable_backup_history_rows(extracted, str(safety["project_id"]))

        # A preparation failure has not touched the application volumes.  In
        # that case restore only project-scoped host state: destroying the
        # still-healthy database would turn a validation error into a recovery
        # event. Once volume replacement really started, retain the complete
        # safety-bundle rollback below.
        if restore_volumes:
            _checked_compose(failed_context, "down", "--volumes", "--remove-orphans")
        _atomic_private_bytes(
            project_root / CONFIG_PATH,
            (extracted / "project.toml").read_bytes(),
        )
        project = load_project(project_root)
        failed_project_id = str(
            candidate_project_id or failed_context.get("id") or ""
        )
        if failed_project_id and failed_project_id != str(project["id"]):
            unregister_project_config(failed_project_id)
        register_project_config(project_root, project)
        restored_runtime = restore_project_runtime_settings(
            extracted / "runtime-settings.json", project["id"]
        )
        _restore_full_recovery_supplement(
            extracted,
            project["id"],
            project_root,
            force=True,
        )
        settings = safety["settings"]
        retention = settings.get("retention") or {}
        configure_project_backup(
            project,
            Path(str(settings["destination"])),
            recovery_key=old_key,
            replace_key=True,
            exact_destination=True,
            include_qdrant=bool(settings.get("include_qdrant", True)),
            daily=int(retention.get("daily", 7)),
            weekly=int(retention.get("weekly", 4)),
            monthly=int(retention.get("monthly", 6)),
            auto_seconds=int(
                settings.get(
                    "auto_seconds",
                    restored_runtime.backup_options["auto_seconds"],
                )
            ),
        )
        if restore_volumes:
            context = _project_context(project_root, project)
            _restore_postgres_volume(context, extracted / "postgres.dump")
            _restore_portable_backup_history(extracted, context)
            memory_snapshot_restored, _ = _restore_qdrant_snapshots(
                context, extracted, manifest
            )
            _start_and_register_restored_project(project_root, project)
            try:
                authority = _api_request(
                    context,
                    "GET",
                    f"/projects/{project['id']}/authority",
                    timeout=30,
                )
                if authority.get("writable"):
                    memory_operation = "reconcile" if memory_snapshot_restored else "reindex"
                    _api_request(
                        context,
                        "POST",
                        f"/projects/{project['id']}/memories/{memory_operation}",
                        timeout=600,
                    )
                    _api_request(
                        context,
                        "POST",
                        f"/projects/{project['id']}/tasks/reindex?origin=restore",
                        timeout=600,
                    )
            except Exception as exc:
                typer.echo(
                    "ROLLBACK_DEGRADED: authoritative PostgreSQL and the application were "
                    f"restored, but semantic index recovery must be retried: {exc}"
                )
    (project_root / DISASTER_RECOVERY_FILE).unlink(missing_ok=True)
    return project


@backup_app.command("restore")
def restore_backup_command(
    archive: Path,
    project_root: Path = typer.Option(Path.cwd(), "--project-root"),
    recovery_key: str | None = typer.Option(None, "--recovery-key", hidden=True),
    force: bool = typer.Option(False, "--force", help="Replace an existing project memory."),
    yes: bool = typer.Option(False, "--yes", help="Confirmation was already collected."),
) -> None:
    """Restore a verified archive into fresh, project-isolated volumes."""
    project_root = find_workspace_root(project_root)
    recovery_marker = project_root / DISASTER_RECOVERY_FILE
    previous_recovery_marker = _file_snapshot(recovery_marker)
    archive = archive.expanduser().resolve()
    key = _recovery_key(project_root, recovery_key)
    require_docker()
    with tempfile.TemporaryDirectory(prefix="dduo-backup-restore-") as temporary_dir:
        extracted = Path(temporary_dir)
        verification = verify_archive(archive, key, extract_to=extracted)
        schema_version = int(verification.manifest["schema_version"])
        archive_sha256 = ""
        manifest_sha256 = ""
        if schema_version == 2:
            archive_sha256 = _sha256_file(archive)
            manifest_sha256 = hashlib.sha256(
                (extracted / "manifest.json").read_bytes()
            ).hexdigest()
        archived_project = tomllib.loads((extracted / "project.toml").read_text())
        if verification.manifest["project"]["id"] != archived_project.get("id"):
            raise BackupError("archive project identity is inconsistent")
        credentials = verification.manifest.get("credentials")
        legacy_key_to_persist = ""
        legacy_secret_snapshot: tuple[bool, bytes, int | None] | None = None
        if schema_version == 2 and (
            not isinstance(credentials, dict) or credentials.get("complete") is not True
        ):
            raise BackupError(
                "full-project-v2 restore requires a complete credential envelope"
            )
        if schema_version == 1:
            archived_project_id = str(archived_project["id"])
            project_key = load_project_secrets(
                archived_project_id,
                include_legacy=False,
            ).get("OPENAI_API_KEY", "").strip()
            candidate_key = project_key or os.getenv("OPENAI_API_KEY", "").strip()
            if not candidate_key:
                for legacy_path in (LEGACY_ENV_FILE, RETIRED_LEGACY_ENV_FILE):
                    try:
                        candidate_key = load_legacy_secret_environment(legacy_path).get(
                            "OPENAI_API_KEY", ""
                        ).strip()
                    except FileNotFoundError:
                        continue
                    if candidate_key:
                        break
            if not candidate_key:
                typer.echo(
                    "The OpenAI embeddings key is absent from this legacy backup. "
                    "Activate the project and add its embeddings key in Setup before retrying."
                )
                raise typer.Exit(5)
            if not project_key:
                legacy_key_to_persist = candidate_key
        _validate_dump_with_docker(extracted)
        _restore_drill(extracted, verification.manifest["project"]["id"])
        if not yes:
            typer.confirm(
                "Restore this verified backup into the current repository and replace its memory volumes?",
                abort=True,
            )
        try:
            current_project = load_project(project_root)
        except FileNotFoundError:
            current_project = None
        if current_project is None or str(current_project["id"]) != str(
            archived_project["id"]
        ):
            # Compose project names are derived solely from the archived ID.
            # A stale/missing registry must never let this restore delete a
            # different checkout's containers or data volumes.
            _assert_restore_candidate_stack_absent(str(archived_project["id"]))
        safety_point = _protect_existing_restore_target(project_root)
        if safety_point:
            typer.echo(
                f"Pre-restore safety backup verified: {safety_point['archive']}"
            )
        qdrant = verification.manifest["qdrant"]
        # Preparation also mutates project-scoped host state (configuration,
        # credentials and backup key), so it belongs to the same rollback
        # transaction as the destructive volume replacement.
        context = dict(safety_point.get("context") or {}) if safety_point else {}
        # Keep a previously verified recovery proof intact throughout archive
        # verification, the disposable drill, confirmation and safety-backup
        # creation.  It becomes invalid only when the destructive candidate
        # restore begins.  A successful rollback below restores the exact prior
        # bytes and mode together with the prior project.
        recovery_marker.unlink(missing_ok=True)
        volume_replacement_started = False
        candidate_project_id = str(archived_project["id"])

        def mark_volume_replacement_started() -> None:
            nonlocal volume_replacement_started
            volume_replacement_started = True

        try:
            if legacy_key_to_persist:
                legacy_secret_path = project_env_file(str(archived_project["id"]))
                legacy_secret_snapshot = _file_snapshot(legacy_secret_path)
                save_project_secrets(
                    str(archived_project["id"]),
                    {"OPENAI_API_KEY": legacy_key_to_persist},
                )
            project, supplement_result = _prepare_restore_target(
                project_root,
                archived_project,
                extracted,
                verification.manifest,
                schema_version,
                archive,
                key,
                force=force,
            )
            context = _project_context(project_root, project)
            _retire_replaced_restore_target(
                safety_point,
                project,
                mark_volume_replacement_started,
            )
            _restore_postgres_volume(
                context,
                extracted / "postgres.dump",
                mark_volume_replacement_started,
            )
            history_rows = _restore_portable_backup_history(extracted, context)
            qdrant_restored, task_qdrant_restored = _restore_qdrant_snapshots(
                context, extracted, verification.manifest
            )
            _start_and_register_restored_project(project_root, project)
        except BaseException as restore_error:
            if legacy_secret_snapshot is not None:
                _restore_file_snapshot(
                    project_env_file(str(archived_project["id"])),
                    legacy_secret_snapshot,
                )
            if safety_point is None:
                raise
            try:
                _rollback_restore_safety_point(
                    project_root,
                    context,
                    safety_point,
                    volume_replacement_started,
                    candidate_project_id,
                )
            except BaseException as rollback_error:
                raise BackupError(
                    "restore failed before commit and automatic rollback also failed; "
                    f"recover manually from verified safety archive {safety_point['archive']}: "
                    f"{rollback_error}"
                ) from restore_error
            _restore_file_snapshot(recovery_marker, previous_recovery_marker)
            typer.echo(
                "Restore failed before commit; the previous project was restored "
                f"automatically from {safety_point['archive']}."
            )
            raise BackupError(
                f"restore failed before commit and was rolled back safely: {restore_error}"
            ) from restore_error
        index_errors: list[str] = []
        try:
            authority = _api_request(
                context,
                "GET",
                f"/projects/{project['id']}/authority",
                timeout=30,
            )
            semantic_index_deferred = not bool(authority.get("writable"))
        except Exception as exc:
            semantic_index_deferred = True
            index_errors.append(f"index authority: {exc}")
            typer.echo(
                "RESTORE_DEGRADED: PostgreSQL was restored, but semantic index "
                f"authority could not be determined: {exc}"
            )
        if semantic_index_deferred:
            typer.echo(
                "Semantic index recovery is deferred until this node claims project authority."
            )
        else:
            try:
                backup_status = _api_request(
                    context,
                    "GET",
                    f"/projects/{project['id']}/backups",
                    timeout=30,
                )
                if (
                    qdrant_restored
                    and backup_status["qdrant_collection"] != qdrant.get("collection")
                ):
                    qdrant_restored = False
                if qdrant_restored:
                    reconciled = _api_request(
                        context,
                        "POST",
                        f"/projects/{project['id']}/memories/reconcile",
                        timeout=600,
                    )
                    typer.echo(
                        "Qdrant snapshot reconciled against PostgreSQL "
                        f"({reconciled['queued']} queued, {reconciled['deleted']} deleted)."
                    )
                else:
                    result = _api_request(
                        context,
                        "POST",
                        f"/projects/{project['id']}/memories/reindex",
                        timeout=600,
                    )
                    typer.echo(
                        f"Qdrant will be rebuilt from PostgreSQL ({result['queued']} memories queued)."
                    )
            except Exception as exc:
                qdrant_restored = False
                index_errors.append(f"memory index: {exc}")
                typer.echo(
                    "RESTORE_DEGRADED: PostgreSQL was restored, but semantic index "
                    f"recovery failed: {exc}"
                )
        if not semantic_index_deferred:
            try:
                reconciled = _api_request(
                    context,
                    "POST",
                    f"/projects/{project['id']}/tasks/reindex?origin=restore",
                    timeout=600,
                )
                typer.echo(
                    "Task semantic index will be rebuilt from PostgreSQL "
                    f"({reconciled['queued']} tasks queued)."
                )
            except Exception as exc:
                index_errors.append(f"task index: {exc}")
                typer.echo(
                    "RESTORE_DEGRADED: PostgreSQL was restored, but task semantic index "
                    f"recovery failed: {exc}"
                )
        try:
            registration_manifest = verification.manifest
            if schema_version == 2:
                registration_manifest = {
                    **verification.manifest,
                    "schema_version": 1,
                    "archive_schema_version": 2,
                }
            _api_request(
                context,
                "POST",
                f"/projects/{project['id']}/backups/register-restore",
                timeout=30,
                json_body={
                    "archive_name": archive.name,
                    "size_bytes": archive.stat().st_size,
                    "manifest": registration_manifest,
                },
            )
        except Exception as exc:
            typer.echo(f"Backup provenance could not be recorded: {exc}")
    if not index_errors and schema_version == 2:
        recovery_evidence = {
            "project_id": project["id"],
            "backup_id": verification.manifest["backup_id"],
            "schema_version": 2,
            "credentials_complete": True,
            "archive_sha256": archive_sha256,
            "manifest_sha256": manifest_sha256,
            "restored_at": datetime.now(timezone.utc).isoformat(),
        }
        recovery_evidence["evidence_hmac"] = _recovery_evidence_hmac(
            recovery_evidence,
            key,
        )
        _atomic_private_bytes(
            recovery_marker,
            (
                json.dumps(
                    recovery_evidence,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )
    typer.echo(
        f"Restore verified. UI: {project_dashboard_url(project)} "
        f"(Qdrant memory snapshot restored: {str(qdrant_restored).lower()}, "
        f"task snapshot restored: {str(task_qdrant_restored).lower()}, "
        f"portable backup records: {history_rows}, "
        f"Codex auth restored: {str(bool(supplement_result['codex_auth'])).lower()}, "
        f"semantic index deferred: {str(semantic_index_deferred).lower()})"
    )
    if index_errors:
        raise typer.Exit(8)


@backup_app.command("all")
def backup_all_command(
    trigger: str = typer.Option("manual", help="manual, update, or uninstall"),
) -> None:
    """Back up every registered project that has an explicit destination."""
    if trigger not in {"manual", "update", "uninstall"}:
        raise typer.BadParameter("trigger must be manual, update, or uninstall")
    registry = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {"projects": {}}
    backup_registry = load_backup_registry()
    configured = set(backup_registry["projects"])
    projects = [
        (project_id, Path(value["root_path"]))
        for project_id, value in registry.get("projects", {}).items()
        if project_id in configured
    ]
    if not projects:
        typer.echo("No configured project backups; nothing to do.")
        return
    require_docker()
    failures = []
    for project_id, root in projects:
        try:
            project = load_project(root)
            if str(project.get("id") or "") != project_id:
                raise RuntimeError("registered project identity does not match its configuration")
            start_stack(root, project)
            result = _api_request(
                _project_context(root, project),
                "POST",
                f"/projects/{project_id}/backups?trigger={trigger}",
            )
            typer.echo(f"PASS  {project['name']}: {result['archive_name']}")
        except Exception as exc:
            failures.append(f"{project_id}: {exc}")
            typer.echo(f"FAIL  {project_id}: {exc}")
    if failures:
        raise typer.Exit(7)


if __name__ == "__main__":
    app()
