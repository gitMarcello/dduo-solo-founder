from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from dduo_solo_founder import client_dispatch


def test_runtime_dispatch_keeps_the_virtual_environment_bin_when_python_is_a_symlink(
    monkeypatch,
    tmp_path: Path,
):
    base_python = tmp_path / "base" / "python"
    base_python.parent.mkdir()
    base_python.write_text("")
    venv_python = tmp_path / "runtime" / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    try:
        venv_python.symlink_to(base_python)
    except OSError:
        pytest.skip("this platform cannot create a test symlink")

    monkeypatch.setattr(client_dispatch.sys, "executable", str(venv_python))

    expected_name = "dduo-hook.exe" if client_dispatch.os.name == "nt" else "dduo-hook"
    assert client_dispatch._runtime_executable("dduo-hook") == venv_python.parent / expected_name


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("codex", "codex"),
        ("OpenAI Codex", "codex"),
        ("codex-mcp-client", "codex"),
        ("claude", "claude"),
        ("Claude Code", "claude"),
        ("Anthropic Claude Code", "claude"),
        ("Cursor", None),
        ("GitHub Copilot", None),
    ],
)
def test_hook_dispatch_accepts_only_the_supported_client_families(
    declared: str,
    expected: str | None,
):
    payload = json.dumps({"client": declared}).encode()
    assert client_dispatch._declared_hook_client(payload) == expected


def test_hook_dispatch_rejects_invalid_or_explicitly_unsupported_payloads(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)

    assert client_dispatch._declared_hook_client(b'{"client":"cursor"}') is None
    assert client_dispatch._declared_hook_client(b"not-json") == "codex"


def test_hook_dispatch_infers_the_native_adapter_when_legacy_payload_has_no_client(
    monkeypatch,
):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    monkeypatch.setenv("PLUGIN_ROOT", "/also-present")
    assert client_dispatch._declared_hook_client(b"{}") == "claude"

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    assert client_dispatch._declared_hook_client(b"{}") == "codex"


@pytest.mark.parametrize(
    ("event", "event_name"),
    [("session-start", "SessionStart"), ("prompt", "UserPromptSubmit")],
)
def test_context_hook_fallback_uses_the_native_event_envelope(event, event_name):
    payload = json.loads(client_dispatch._hook_fallback(event))

    assert "additional_context" not in payload
    assert payload["hookSpecificOutput"]["hookEventName"] == event_name
    assert "do not claim this turn was recorded" in payload["hookSpecificOutput"][
        "additionalContext"
    ]
    assert client_dispatch._valid_hook_output(event, json.dumps(payload).encode()) is True


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("session-start", b"not-json", False),
        ("session-start", b"[]", False),
        ("session-start", b'{"additional_context":"legacy"}', False),
        ("stop", b'{"continue":true}', True),
        ("session-start", b'{"continue":true}', True),
        ("prompt", b'{"continue":true}', True),
    ],
)
def test_hook_output_validation_rejects_malformed_and_legacy_envelopes(
    event: str,
    payload: bytes,
    expected: bool,
):
    assert client_dispatch._valid_hook_output(event, payload) is expected


def test_hook_fallback_never_claims_that_memory_was_saved():
    assert client_dispatch._hook_fallback("stop") == b'{"continue":true}\n'
    assert client_dispatch._hook_fallback("unknown") == b'{"continue":true}\n'


def test_prompt_dispatch_timeout_covers_http_budget_inside_native_hook_contract():
    root = Path(__file__).resolve().parents[1]
    codex_hooks = json.loads(
        (root / "it.dduo.client-support/codex/hooks/hooks.json").read_text(encoding="utf-8")
    )["hooks"]
    claude_hooks = json.loads(
        (
            root
            / "it.dduo.client-support/claude-code/.claude-plugin/plugin.json"
        ).read_text(encoding="utf-8")
    )["hooks"]
    manifest_timeouts = [
        hooks["UserPromptSubmit"][0]["hooks"][0]["timeout"]
        for hooks in (codex_hooks, claude_hooks)
    ]
    dispatcher_timeout = client_dispatch.HOOK_EXECUTABLES["prompt"][1]

    assert manifest_timeouts == [60, 60]
    assert dispatcher_timeout == 30 + 5
    assert dispatcher_timeout < min(manifest_timeouts)


def test_dispatcher_replaces_nonzero_or_invalid_runtime_output_with_native_fallback(
    monkeypatch,
    tmp_path: Path,
):
    executable = tmp_path / "dduo-solo-founder-hook-session-start"
    executable.write_text("")
    monkeypatch.setattr(client_dispatch, "_runtime_executable", lambda _name: executable)
    monkeypatch.setattr(
        client_dispatch.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b'{"additional_context":"partial legacy output"}\n'
        ),
    )

    class BinaryStream:
        def __init__(self, content: bytes = b""):
            self.buffer = io.BytesIO(content)

    output = BinaryStream()
    monkeypatch.setattr(client_dispatch.sys, "stdin", BinaryStream(b'{"client":"codex"}'))
    monkeypatch.setattr(client_dispatch.sys, "stdout", output)
    monkeypatch.setattr(client_dispatch.sys, "argv", ["dispatch", "session-start"])

    assert client_dispatch.hook_dispatch_main() == 0
    payload = json.loads(output.buffer.getvalue())
    assert "additional_context" not in payload
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_runtime_sources_never_emit_the_rejected_legacy_context_key():
    root = Path(__file__).resolve().parents[1]
    sources = (
        root / "backend/dduo_solo_founder/hooks.py",
        root / "backend/dduo_solo_founder/client_dispatch.py",
        root / "it.dduo.client-support/codex/hooks/codex-runtime-hook.sh",
    )
    for source in sources:
        assert '"additional_context"' not in source.read_text(encoding="utf-8")


@pytest.mark.parametrize(("exit_code", "expected"), [(0, 0), (7, 7), (3221225477, -1073741819)])
def test_windows_mcp_dispatch_waits_with_inherited_stdio_and_sanitized_environment(
    monkeypatch, tmp_path, exit_code, expected,
):
    executable = tmp_path / "runtime with spaces è" / "dduo-solo-founder-mcp.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(client_dispatch, "_runtime_executable", lambda _name: executable)
    monkeypatch.setattr(client_dispatch.sys, "platform", "win32")
    monkeypatch.setenv("PYTHONPATH", "must-not-reach-runtime")
    monkeypatch.setenv("DDUO_TEST_INHERITED", "keep")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(client_dispatch.subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        client_dispatch.mcp_dispatch_main()
    assert result.value.code == expected
    assert calls[0][0] == [str(executable)]
    assert set(calls[0][1]) == {"env", "check"}  # No shell, pipes or detached process.
    assert "PYTHONPATH" not in calls[0][1]["env"]
    assert calls[0][1]["env"]["DDUO_TEST_INHERITED"] == "keep"
