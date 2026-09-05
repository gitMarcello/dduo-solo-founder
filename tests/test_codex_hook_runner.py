import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "it.dduo.client-support" / "codex" / "hooks" / "codex-runtime-hook.sh"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires POSIX /bin/sh")


def write_hook(path: Path, output: dict) -> None:
    path.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{json.dumps(output)}'\n")
    path.chmod(0o755)


def context_output(event_name: str, context: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": context,
        }
    }


@pytest.mark.parametrize(
    ("event", "executable", "expected"),
    [
        (
            "session-start",
            "dduo-solo-founder-hook-session-start",
            context_output("SessionStart", "session"),
        ),
        (
            "prompt",
            "dduo-solo-founder-hook-prompt",
            context_output("UserPromptSubmit", "prompt"),
        ),
        ("stop", "dduo-solo-founder-hook-stop", {"continue": True}),
    ],
)
def test_codex_hook_runner_uses_installer_pointer_not_gui_path(
    tmp_path: Path, event: str, executable: str, expected: dict
):
    runtime_bin = tmp_path / "uv-tool-bin"
    runtime_bin.mkdir()
    del executable
    write_hook(runtime_bin / "dduo-solo-founder-hook-dispatch", expected)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    config.joinpath("hook-runtime-bin").write_text(f"{runtime_bin}\n")

    result = subprocess.run(
        ["/bin/sh", str(RUNNER), event],
        input='{"cwd":"/project"}',
        capture_output=True,
        text=True,
        check=True,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "PLUGIN_ROOT": str(ROOT),
        },
    )

    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == expected
    assert "additional_context" not in payload


def test_codex_hook_runner_never_executes_an_unvalidated_legacy_hook(tmp_path: Path):
    runtime_bin = tmp_path / "uv-tool-bin"
    runtime_bin.mkdir()
    write_hook(
        runtime_bin / "dduo-solo-founder-hook-session-start",
        {"additional_context": "legacy"},
    )
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    config.joinpath("hook-runtime-bin").write_text(f"{runtime_bin}\n")

    result = subprocess.run(
        ["/bin/sh", str(RUNNER), "session-start"],
        input="{}",
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PLUGIN_ROOT": str(ROOT)},
    )

    payload = json.loads(result.stdout)
    assert "additional_context" not in payload
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "temporarily unavailable" in payload["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            "session-start",
            context_output(
                "SessionStart",
                "dDuo local memory is temporarily unavailable. Continue normally; do not claim this turn was recorded.",
            ),
        ),
        (
            "prompt",
            context_output(
                "UserPromptSubmit",
                "dDuo local memory is temporarily unavailable. Continue normally; do not claim this turn was recorded.",
            ),
        ),
        ("stop", {"continue": True}),
    ],
)
def test_codex_hook_runner_returns_valid_json_when_runtime_is_missing(
    tmp_path: Path, event: str, expected: dict
):
    result = subprocess.run(
        ["/bin/sh", str(RUNNER), event],
        input="{}",
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PLUGIN_ROOT": str(ROOT)},
    )

    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == expected
    assert "additional_context" not in payload


def test_codex_hook_runner_rejects_unknown_events_without_invalid_json(tmp_path: Path):
    result = subprocess.run(
        ["/bin/sh", str(RUNNER), "unknown"],
        input="{}",
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PLUGIN_ROOT": str(ROOT)},
    )

    assert result.stderr == ""
    assert json.loads(result.stdout) == {"continue": True}
