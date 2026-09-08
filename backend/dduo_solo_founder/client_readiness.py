"""Verify subscription authentication and Codex hook authorization."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

from dduo_solo_founder.client_installation import (
    ClientInstallation,
    ClientInstallationError,
)
from dduo_solo_founder.native_process import client_command, native_codex_executable

DETACHED_SESSION_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
    "CLAUDE_CODE_AGENT",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "CLAUDE_CODE_COORDINATOR_MODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_KIND",
    "CLAUDE_CODE_SESSION_NAME",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_TASK_LIST_ID",
    "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
    "CLAUDE_PLUGIN_DATA",
    "CLAUDE_PLUGIN_ROOT",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_API_KEY",
    "PLUGIN_DATA",
    "PLUGIN_ROOT",
}

MINIMUM_CODEX_VERSION = (0, 150, 0)
DEFAULT_CODEX_DESKTOP_EXECUTABLE = Path(
    "/Applications/ChatGPT.app/Contents/Resources/codex"
)
CODEX_DESKTOP_EXECUTABLE_ENV = "DDUO_SOLO_FOUNDER_CODEX_DESKTOP"
EXPECTED_CODEX_HOOK_EVENTS = frozenset(
    {"sessionStart", "userPromptSubmit", "stop"}
)


class CodexVersionError(RuntimeError):
    """No discovered Codex executable supports dDuo's hook configuration."""


@dataclass(frozen=True)
class SubscriptionAuthStatus:
    client: str
    ready: bool
    reason: str
    login_command: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodexHookStatus:
    ready: bool
    reason: str
    hook_count: int
    trust_statuses: list[str]
    detail: str | None = None
    events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def detached_cli_environment() -> dict[str, str]:
    """Remove transient session and API credentials before checking subscription auth."""
    environment = os.environ.copy()
    for key in DETACHED_SESSION_ENV_KEYS:
        environment.pop(key, None)
    return environment


def parse_codex_version(output: str) -> tuple[int, int, int] | None:
    """Parse the numeric Codex release triplet while tolerating prerelease suffixes."""
    import re

    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", output)
    if not match:
        return None
    return tuple(int(value) for value in match.groups())


def _codex_candidates() -> list[tuple[str, bool]]:
    """Return unique PATH/Desktop candidates, marking the Desktop executable."""
    candidates: list[tuple[str, bool]] = []
    path_executable = shutil.which("codex")
    if path_executable:
        native = native_codex_executable(str(Path(path_executable).resolve()))
        if native:
            candidates.append((native, False))

    configured_desktop = os.getenv(CODEX_DESKTOP_EXECUTABLE_ENV)
    desktop = Path(configured_desktop) if configured_desktop else DEFAULT_CODEX_DESKTOP_EXECUTABLE
    consider_desktop = sys.platform == "darwin" or configured_desktop is not None
    if consider_desktop and desktop.is_file() and os.access(desktop, os.X_OK):
        resolved_desktop = native_codex_executable(str(desktop.resolve()))
        if resolved_desktop is None:
            return candidates
        if all(candidate != resolved_desktop for candidate, _ in candidates):
            candidates.append((resolved_desktop, True))
        else:
            candidates = [
                (candidate, is_desktop or candidate == resolved_desktop)
                for candidate, is_desktop in candidates
            ]
    return candidates


def _codex_version(
    executable: str,
    *,
    timeout: float,
    node_executable: str | None = None,
) -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(
            client_command(
                executable,
                ["--version"],
                client="codex",
                node_executable=node_executable,
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=detached_cli_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_codex_version(f"{result.stdout}\n{result.stderr}")


def resolve_codex_executable(
    *,
    timeout: float = 3,
    required: bool = False,
    installation: ClientInstallation | None = None,
) -> str | None:
    """Choose the newest compatible Codex binary, preferring Desktop on a tie."""
    if installation is not None:
        if installation.family != "codex":
            raise ValueError("Codex resolution requires a Codex installation")
        executable = str(installation.management_command.executable)
        version = _codex_version(
            executable,
            timeout=timeout,
            node_executable=(
                str(installation.management_command.node_executable)
                if installation.management_command.node_executable
                else None
            ),
        )
        if version is not None and version >= MINIMUM_CODEX_VERSION:
            return executable
        if not required:
            return None
        minimum = ".".join(str(value) for value in MINIMUM_CODEX_VERSION)
        found = ".".join(str(value) for value in version) if version else "unknown"
        raise CodexVersionError(
            f"Codex {minimum} or newer is required for dDuo lifecycle hooks; found {found} at {executable}"
        )
    candidates = _codex_candidates()
    discovered = [
        (executable, is_desktop, _codex_version(executable, timeout=timeout))
        for executable, is_desktop in candidates
    ]
    compatible = [
        item
        for item in discovered
        if item[2] is not None and item[2] >= MINIMUM_CODEX_VERSION
    ]
    if compatible:
        executable, _, _ = max(compatible, key=lambda item: (item[2], item[1]))
        return executable
    if not required:
        return None
    minimum = ".".join(str(value) for value in MINIMUM_CODEX_VERSION)
    if not candidates:
        raise FileNotFoundError(f"Codex {minimum} or newer is required but was not found")
    found = ", ".join(
        f"{'.'.join(str(value) for value in version) if version else 'unknown'} at {executable}"
        for executable, _, version in discovered
    )
    raise CodexVersionError(
        f"Codex {minimum} or newer is required for dDuo lifecycle hooks; found {found}"
    )


def subscription_auth_status(
    client: str,
    *,
    timeout: float = 10,
    environment: dict[str, str] | None = None,
    installation: ClientInstallation | None = None,
) -> SubscriptionAuthStatus:
    """Check the durable subscription login used by an out-of-session sleep process."""
    client = client.strip().lower()
    if client not in {"codex", "claude"}:
        raise ValueError("client must be codex or claude")
    login_command = "codex login" if client == "codex" else "claude auth login --claudeai"
    if installation is not None and installation.family != client:
        raise ValueError("installation family does not match client")
    if client == "codex":
        try:
            executable = resolve_codex_executable(
                timeout=min(timeout, 3), required=True, installation=installation
            )
        except (FileNotFoundError, CodexVersionError, ClientInstallationError) as error:
            return SubscriptionAuthStatus(
                client, False, "cli_missing", login_command, str(error)
            )
    else:
        try:
            executable = (
                str(installation.management_command.executable)
                if installation is not None
                else shutil.which(client)
            )
        except ClientInstallationError:
            executable = None
    if not executable:
        return SubscriptionAuthStatus(
            client, False, "cli_missing", login_command, f"{client} CLI is not on PATH"
        )
    command = [executable, "login", "status"] if client == "codex" else [
        executable,
        "auth",
        "status",
        "--json",
    ]
    try:
        result = subprocess.run(
            client_command(
                command[0],
                command[1:],
                client=client,
                node_executable=(
                    str(installation.management_command.node_executable)
                    if installation is not None and installation.management_command.node_executable
                    else None
                ),
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=(
                dict(environment)
                if environment is not None
                else installation.environment(detached_cli_environment())
                if installation is not None
                else detached_cli_environment()
            ),
        )
    except subprocess.TimeoutExpired:
        return SubscriptionAuthStatus(
            client, False, "check_timeout", login_command, "authentication check timed out"
        )
    except OSError:
        return SubscriptionAuthStatus(
            client, False, "check_failed", login_command, "authentication check could not start"
        )

    if client == "codex":
        output = f"{result.stdout}\n{result.stderr}".casefold()
        ready = result.returncode == 0 and "chatgpt" in output and "api key" not in output
        return SubscriptionAuthStatus(
            client,
            ready,
            "authenticated" if ready else "login_required",
            login_command,
            None if ready else "Codex is not durably logged in with a ChatGPT subscription",
        )

    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        return SubscriptionAuthStatus(
            client, False, "check_failed", login_command,
            "Claude returned an invalid authentication status",
        )
    method = str(payload.get("authMethod") or "").casefold()
    provider = str(payload.get("apiProvider") or "").casefold()
    uses_api_key = "api" in method and "key" in method
    ready = bool(
        result.returncode == 0
        and payload.get("loggedIn") is True
        and provider in {"", "firstparty"}
        and not uses_api_key
    )
    return SubscriptionAuthStatus(
        client,
        ready,
        "authenticated" if ready else "login_required",
        login_command,
        None if ready else "Claude is not durably logged in with a Claude subscription",
    )


def _stream_json(stream: TextIO, messages: queue.Queue[dict[str, Any]]) -> None:
    for line in stream:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            messages.put(value)
    messages.put({"_stream_closed": True})


def _wait_for_response(
    messages: queue.Queue[dict[str, Any]], request_id: int, deadline: float
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex app-server response timed out")
        try:
            message = messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Codex app-server response timed out") from exc
        if message.get("_stream_closed"):
            raise RuntimeError("Codex app-server closed before replying")
        if message.get("id") == request_id:
            if message.get("error"):
                raise RuntimeError("Codex app-server rejected the hook status request")
            return message


def _codex_app_server_request(
    method: str,
    *,
    project_root: Path,
    params: dict[str, Any] | None = None,
    timeout: float = 15,
    installation: ClientInstallation | None = None,
) -> dict[str, Any]:
    """Make one authenticated, read-only Codex app-server request.

    The process is intentionally short lived.  dDuo uses this helper only for
    hook trust, which lifecycle hooks cannot report themselves; it never
    attaches to or controls the user's active
    Codex conversation.
    """
    # An explicitly selected IDE binary is a diagnostic target only. Neither
    # subscription login nor memory sleep may inherit this command.
    probe_installation = (
        replace(installation, management_command=installation.probe_command, probe_command=None)
        if installation is not None and installation.probe_command is not None
        else installation
    )
    executable = resolve_codex_executable(
        timeout=min(timeout, 3), required=True, installation=probe_installation
    )
    if executable is None:  # required=True guarantees this; keeps the type narrow.
        raise FileNotFoundError("A compatible Codex CLI is unavailable")
    process = subprocess.Popen(
        client_command(
            executable,
            ["app-server", "--stdio"],
            client="codex",
            node_executable=(
                str(probe_installation.management_command.node_executable)
                if probe_installation is not None and probe_installation.management_command.node_executable
                else None
            ),
        ),
        cwd=project_root,
        env=(installation.environment(detached_cli_environment()) if installation else detached_cli_environment()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.terminate()
        raise RuntimeError("Codex app-server stdio is unavailable")
    messages: queue.Queue[dict[str, Any]] = queue.Queue()
    reader = threading.Thread(target=_stream_json, args=(process.stdout, messages), daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout

    def send(value: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "dduo-solo-founder",
                        "title": "dDuo Solo Founder",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        _wait_for_response(messages, 1, deadline)
        send({"method": "initialized", "params": {}})
        request: dict[str, Any] = {"method": method, "id": 2}
        if params is not None:
            request["params"] = params
        send(request)
        response = _wait_for_response(messages, 2, deadline)
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Codex app-server returned an invalid response")
        return result
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def _list_codex_hooks(
    project_root: Path,
    *,
    timeout: float = 15,
    installation: ClientInstallation | None = None,
) -> list[dict[str, Any]]:
    result = _codex_app_server_request(
        "hooks/list",
        project_root=project_root,
        params={"cwds": [str(project_root)]},
        timeout=timeout,
        installation=installation,
    )
    data = result.get("data") or []
    for entry in data:
        if isinstance(entry, dict) and entry.get("cwd") == str(project_root):
            hooks = entry.get("hooks") or []
            return [hook for hook in hooks if isinstance(hook, dict)]
    return []


def codex_hook_status(
    project_root: Path,
    *,
    timeout: float = 15,
    installation: ClientInstallation | None = None,
) -> CodexHookStatus:
    """Read Codex's own trust state without modifying or bypassing it."""
    root = project_root.resolve()
    try:
        hooks = _list_codex_hooks(root, timeout=timeout, installation=installation)
    except (FileNotFoundError, OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
        return CodexHookStatus(
            False,
            "check_failed",
            0,
            [],
            "Codex hook authorization could not be verified",
        )
    plugin_hooks = [
        hook
        for hook in hooks
        if str(hook.get("pluginId") or "").split("@", 1)[0] == "dduo-solo-founder"
    ]
    if not plugin_hooks:
        return CodexHookStatus(False, "hooks_missing", 0, [], "dDuo hooks are not loaded")
    events = sorted(
        {
            str(hook.get("eventName"))
            for hook in plugin_hooks
            if isinstance(hook.get("eventName"), str)
        }
    )
    if len(plugin_hooks) != len(EXPECTED_CODEX_HOOK_EVENTS) or set(events) != set(
        EXPECTED_CODEX_HOOK_EVENTS
    ):
        return CodexHookStatus(
            False,
            "hooks_incomplete",
            len(plugin_hooks),
            sorted({str(hook.get("trustStatus") or "unknown") for hook in plugin_hooks}),
            "dDuo lifecycle hooks are incomplete; reinstall the Codex plugin",
            events,
        )
    statuses = sorted({str(hook.get("trustStatus") or "unknown") for hook in plugin_hooks})
    if any(hook.get("enabled") is not True for hook in plugin_hooks):
        return CodexHookStatus(
            False,
            "hooks_disabled",
            len(plugin_hooks),
            statuses,
            "One or more dDuo lifecycle hooks are disabled; enable or reinstall the Codex plugin",
            events,
        )
    ready = all(status in {"managed", "trusted"} for status in statuses)
    if ready:
        reason = "authorized"
    elif "modified" in statuses:
        reason = "reauthorization_required"
    else:
        reason = "authorization_required"
    return CodexHookStatus(ready, reason, len(plugin_hooks), statuses, events=events)


def client_readiness(
    client: str,
    project_root: Path,
    *,
    installation: ClientInstallation | None = None,
) -> dict[str, Any]:
    """Return all protected actions still required before dDuo can claim readiness."""
    client = client.strip().lower()
    # Keep the no-descriptor path compatible with small callers that only want
    # a legacy diagnostic. Once a descriptor is supplied every native probe is
    # intentionally tied to that exact executable/configuration.
    auth = (
        subscription_auth_status(client, installation=installation)
        if installation is not None
        else subscription_auth_status(client)
    )
    hooks = None
    if client == "codex":
        hooks = (
            codex_hook_status(project_root, installation=installation)
            if installation is not None
            else codex_hook_status(project_root)
        )
    actions: list[dict[str, Any]] = []
    if installation is not None and installation.surface_hint == "vscode":
        reload_instruction = (
            "Reload the VS Code window, then open a new graphical Codex conversation. "
        )
    elif installation is not None and installation.surface_hint == "cli":
        reload_instruction = "Start a new Codex CLI session. "
    else:
        reload_instruction = "Fully restart Codex (quit and reopen it). "
    if not auth.ready:
        actions.append(
            {
                "type": "subscription_login" if auth.reason == "login_required" else "diagnose_cli",
                "requires_user_approval": True,
                "command": auth.login_command if auth.reason == "login_required" else None,
            }
        )
    if hooks is not None and not hooks.ready and hooks.reason in {
        "authorization_required",
        "reauthorization_required",
    }:
        surface = installation.surface_hint if installation else "unknown"
        if surface == "vscode":
            approval_path = None
            review_action = trust_action = None
            review_instruction = (
                "Native hook approval must be completed in the active Codex client. "
                "The approval UI in the VS Code extension has not been verified; "
                "this runtime check does not verify an IDE conversation."
            )
        elif surface == "cli":
            approval_path = ["/hooks"]
            review_action, trust_action = "Review", "Trust all"
            review_instruction = (
                "Open /hooks in the native Codex CLI to review and authorize the dDuo hooks."
            )
        else:
            approval_path = ["Settings", "Hooks"]
            review_action, trust_action = "Review", "Trust all"
            review_instruction = (
                "Then open the verified native Hooks settings, select dDuo Solo Founder, "
                "and choose Review and Trust all."
            )
        actions.append(
            {
                "type": "codex_hook_review",
                "requires_user_action": True,
                "surface": "native_codex_hook_review",
                "path": approval_path,
                "review_action": review_action,
                "trust_action": trust_action,
                "detail": (
                    "If dDuo was just installed or updated, "
                    + reload_instruction
                    + review_instruction
                    + " A chat command cannot grant this permission."
                ),
            }
        )
    elif hooks is not None and not hooks.ready:
        actions.append(
            {
                "type": "codex_plugin_repair",
                "requires_user_action": True,
                "detail": (
                    "Update or reinstall dDuo Solo Founder, "
                    + reload_instruction
                    + "then open a new chat in this project folder."
                ),
            }
        )
    return {
        "client": client,
        "surface": installation.surface_hint if installation else "unknown",
        "config_dir": str(installation.config_dir) if installation else None,
        "verification_scope": "installation",
        "lifecycle_verified": None,
        "ready": auth.ready and (hooks is None or hooks.ready),
        "authentication": auth.as_dict(),
        "hooks": hooks.as_dict() if hooks is not None else None,
        "actions": actions,
    }
