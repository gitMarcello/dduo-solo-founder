"""Native installer transactions with disposable clients, homes and runtimes."""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def test_windows_fixture_batch_launcher_preserves_exact_crlf_bytes(tmp_path, monkeypatch):
    script = ROOT / "scripts/native_installer_smoke.py"
    spec = importlib.util.spec_from_file_location("native_installer_smoke_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    launcher = tmp_path / "uv"
    module.write_cli(launcher, ["--runtime", "uv"])
    contents = launcher.with_suffix(".cmd").read_bytes()
    command = subprocess.list2cmdline([sys.executable, str(script), "--runtime", "uv"])
    assert contents == ("@echo off\r\n" + command + " %*\r\n").encode("utf-8")


def test_native_installer_bootstrap_update_rollback_hooks_and_mcp(tmp_path):
    # Build a checked fixture distribution from the current source, so changes
    # under development need not mutate the release's checksum manifest.
    source = tmp_path / "checked source"
    source.mkdir()
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    ).decode("utf-8").split("\0")
    entries = []
    for relative in sorted(set(tracked)):
        if not relative or relative == "checksums.sha256":
            continue
        origin = ROOT / relative
        if not origin.is_file():
            continue
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        entries.append(f"{digest}  {relative}\n")
    (source / "checksums.sha256").write_text("".join(entries), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/native_installer_smoke.py"),
         "--simulate-runtime", "--source", str(source)],
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS native install, adapters, rollback, update, hooks, MCP, uninstall" in result.stdout


def test_native_smoke_preserves_failure_traceback_under_legacy_stdio_encoding(tmp_path):
    missing_source = tmp_path / "missing source è"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/native_installer_smoke.py"),
         "--simulate-runtime", "--source", str(missing_source)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
        capture_output=True, timeout=30,
    )
    # Decode strictly after collection: a CP1252 traceback used to lose stderr
    # in Windows' subprocess reader thread and hide the original failure.
    stderr = result.stderr.decode("utf-8")
    assert result.returncode != 0
    assert "AssertionError: Smoke command failed (exit 1)" in stderr
    assert "missing source è" in stderr
    assert "MODULE_NOT_FOUND" in stderr


def test_headless_installer_flags_are_explicit_and_incompatible_with_adapters():
    help_result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--help"], capture_output=True, text=True,
    )
    assert help_result.returncode == 0
    for option in ("--headless", "--no-setup", "--only core"):
        assert option in help_result.stdout
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--headless", "--only", "codex", "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr
