"""Execute the native Node adapter, including real .exe shims on Windows."""

import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "it.dduo.client-support/codex/hooks/codex-runtime-hook.mjs"
NODE = shutil.which("node")


def native_fixture(path: Path, source: str) -> Path:
    if os.name == "nt":
        # pip ships the same distlib launcher format used by console entrypoints.
        from pip._vendor.distlib.scripts import ScriptMaker

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("__main__.py", source)
        launcher = ScriptMaker(None, None)._get_launcher("t")
        executable = path.with_suffix(".exe")
        executable.write_bytes(
            launcher + f'#!"{sys.executable}"\n'.encode() + archive.getvalue()
        )
        return executable
    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    path.chmod(0o755)
    return path


def environment(home: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}


@pytest.mark.parametrize("event", ["session-start", "prompt", "stop", "unknown"])
def test_native_hook_missing_runtime_always_returns_client_json(tmp_path, event):
    result = subprocess.run(
        [NODE, str(RUNNER), event], input="{}", text=True, capture_output=True,
        check=True, env=environment(tmp_path),
    )
    assert result.stderr == ""
    output = json.loads(result.stdout)
    if event in {"stop", "unknown"}:
        assert output == {"continue": True}
    else:
        assert "temporarily unavailable" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("event", ["session-start", "prompt", "stop"])
@pytest.mark.parametrize("response_mode", ["context", "noop", "invalid"])
def test_native_hook_resolves_installed_executable_and_preserves_stdin(tmp_path, event, response_mode):
    runtime = tmp_path / "runtime with spaces & accents è"
    runtime.mkdir()
    event_name = {"session-start": "SessionStart", "prompt": "UserPromptSubmit"}.get(event)
    expected = {"hookSpecificOutput": {
        "hookEventName": event_name, "additionalContext": "Contesto: caffè e qualità 🚀",
    }} if event_name else {"continue": True, "fixture": True}
    if response_mode == "noop":
        expected = {"continue": True}
    native_fixture(runtime / "dduo-solo-founder-hook-dispatch", (
        "import json, sys\n"
        f"assert sys.argv[1] == {event!r}\n"
        "assert json.load(sys.stdin) == {'cwd': 'synthetic-project', 'prompt': 'caffè & qualità 🚀'}\n"
        + ("print('broken JSON')\n" if response_mode == "invalid" else f"print({json.dumps(expected, ensure_ascii=False)!r})\n")
    ))
    pointer = tmp_path / ".config/dduo-solo-founder/hook-runtime-bin"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(str(runtime) + "\n", encoding="utf-8")
    pointer.chmod(0o600)
    result = subprocess.run(
        [NODE, str(RUNNER), event],
        input=json.dumps({"cwd": "synthetic-project", "prompt": "caffè & qualità 🚀"}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=True,
        env={**environment(tmp_path), "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
    )
    assert result.stderr == ""
    output = json.loads(result.stdout)
    if response_mode == "invalid":
        assert output != expected
        assert "fixture" not in result.stdout
    else:
        assert output == expected


@pytest.mark.parametrize("contents", ["relative/path", "/one\n/two", "x" * 5000])
def test_native_hook_rejects_malformed_pointer(tmp_path, contents):
    pointer = tmp_path / ".config/dduo-solo-founder/hook-runtime-bin"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(contents)
    pointer.chmod(0o600)
    result = subprocess.run(
        [NODE, str(RUNNER), "prompt"], input="{}", text=True, capture_output=True,
        check=True, env=environment(tmp_path),
    )
    assert "temporarily unavailable" in result.stdout
    assert result.stderr == ""
