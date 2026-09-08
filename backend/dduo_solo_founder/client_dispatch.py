"""Stable local dispatchers for the installed Codex and Claude adapters.

The explicit installer owns the runtime pointer and switches it atomically.
These entry points only delegate to executables from the same locked virtual
environment; they contain no downloader, release queue, or activation policy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .client_support import client_family
from .connection_health import CONFIGURATION_CHOICE_INSTRUCTION


# UserPromptSubmit may spend the full 30 seconds in its bounded HTTP request.
# Five more seconds cover child-process teardown while staying well inside the
# native Codex and Claude hook contract of 60 seconds.
PROMPT_DISPATCH_TIMEOUT_SECONDS = 35
HOOK_EXECUTABLES = {
    "session-start": ("dduo-solo-founder-hook-session-start", 195),
    "prompt": ("dduo-solo-founder-hook-prompt", PROMPT_DISPATCH_TIMEOUT_SECONDS),
    # Stop first stages the completion locally, then performs one bounded
    # delivery attempt. Leave enough time for the 3s response window and
    # durable state write without approaching the native 20s hook deadline.
    "stop": ("dduo-solo-founder-hook-stop", 12),
}
LEGACY_CONTEXT_KEY = "additional" + "_context"


def _runtime_executable(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    # Console entry points are siblings inside the locked runtime venv.  Do not
    # resolve the Python launcher symlink: that would jump to the base Python
    # installation and make every delegated hook appear missing.
    return Path(sys.executable).parent / f"{name}{suffix}"


def _hook_fallback(event: str) -> bytes:
    if event == "stop":
        return b'{"continue":true}\n'
    event_name = {
        "session-start": "SessionStart",
        "prompt": "UserPromptSubmit",
    }.get(event)
    if event_name is None:
        return b'{"continue":true}\n'
    return (
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": (
                        "dDuo project memory is temporarily unavailable; do not claim this turn was recorded. "
                        "Offer to check the plugin connection and help repair it; do not start local Setup "
                        "or Docker for a remote project. " + CONFIGURATION_CHOICE_INSTRUCTION
                    ),
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _valid_hook_output(event: str, raw: bytes) -> bool:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if LEGACY_CONTEXT_KEY in payload:
        return False
    if payload == {"continue": True}:
        return True
    event_name = {
        "session-start": "SessionStart",
        "prompt": "UserPromptSubmit",
    }.get(event)
    if event_name is None:
        return True
    specific = payload.get("hookSpecificOutput")
    return bool(
        isinstance(specific, dict)
        and specific.get("hookEventName") == event_name
        and isinstance(specific.get("additionalContext"), str)
        and specific["additionalContext"]
    )


def _declared_hook_client(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw or b"{}")
    except (TypeError, ValueError):
        payload = {}
    declared = payload.get("client") if isinstance(payload, dict) else None
    family = client_family(str(declared)) if declared is not None else None
    if family is not None:
        return family
    if declared:
        return None
    if os.getenv("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    if os.getenv("PLUGIN_ROOT") or os.getenv("CODEX_HOME"):
        return "codex"
    # Only the two native hook manifests invoke this dispatcher.  Missing
    # metadata is tolerated for older Codex payloads, while an explicitly
    # unsupported client above is rejected.
    return "codex"


def hook_dispatch_main() -> int:  # pragma: no cover - subprocess integration
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    fallback = _hook_fallback(event)
    target = HOOK_EXECUTABLES.get(event)
    if target is None:
        sys.stdout.buffer.write(fallback)
        return 0
    raw = sys.stdin.buffer.read()
    if _declared_hook_client(raw) is None:
        sys.stdout.buffer.write(fallback)
        return 0
    executable = _runtime_executable(target[0])
    if not executable.is_file():
        sys.stdout.buffer.write(fallback)
        return 0
    try:
        completed = subprocess.run(
            [str(executable)],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=target[1],
            env=os.environ.copy(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        sys.stdout.buffer.write(fallback)
        return 0
    output = completed.stdout
    if completed.returncode != 0 or not output or not _valid_hook_output(event, output):
        output = fallback
    sys.stdout.buffer.write(output)
    return 0


def mcp_dispatch_main() -> None:  # pragma: no cover - replaces console process
    executable = _runtime_executable("dduo-solo-founder-mcp")
    if not executable.is_file():
        raise SystemExit("dDuo MCP runtime is unavailable; run the dDuo installer")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if sys.platform == "win32":
        # Windows exec is a spawn-and-exit emulation, not POSIX process
        # replacement. Keep the process/stdio lifetime owned by the MCP client,
        # and let subprocess quote native paths (including spaces) correctly.
        code = subprocess.run(
            [str(executable)], env=environment, check=False,
        ).returncode
        # CPython's Windows SystemExit accepts a signed C long; preserve the
        # original 32-bit NTSTATUS rather than overflowing it to -1.
        raise SystemExit(code - 2**32 if code >= 2**31 else code)
    os.execvpe(str(executable), [str(executable)], environment)
