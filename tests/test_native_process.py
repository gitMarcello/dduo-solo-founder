from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dduo_solo_founder import native_process


def package(path: Path, name: str, **metadata) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text(json.dumps({"name": name, **metadata}))
    return path


@pytest.mark.parametrize("windows", [False, True])
def test_native_executables_keep_arguments_literal(monkeypatch, windows):
    monkeypatch.setattr(native_process, "_windows", lambda: windows)
    executable = "C:/Users/a&b/client.exe"
    args = ['{"text":"shell & syntax %PATH%"}', "a b", "", 'a"b']
    assert native_process.client_command(executable, args, client="claude") == [executable, *args]
    assert native_process.native_codex_executable(executable) == executable


@pytest.mark.parametrize("architecture,target", [("AMD64", "x86_64"), ("ARM64", "aarch64")])
@pytest.mark.parametrize("layout", ["global", "local", "nested", "bundled"])
def test_windows_codex_npm_resolves_official_native_payload(monkeypatch, tmp_path, architecture, target, layout):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    monkeypatch.setattr(native_process.platform, "machine", lambda: architecture)
    binaries = tmp_path / "npm home & spaces è"
    modules = binaries / "node_modules"
    shim = (modules / ".bin" if layout == "local" else binaries) / "codex.cmd"
    root = package(modules / "@openai/codex", "@openai/codex")
    suffix = "x64" if architecture == "AMD64" else "arm64"
    platform_modules = root / "node_modules" if layout == "nested" else modules
    # npm aliases retain the published package name in package.json.
    vendor = root / "vendor" if layout == "bundled" else package(
        platform_modules / f"@openai/codex-win32-{suffix}", "@openai/codex",
        version=f"0.142.5-win32-{suffix}", os=["win32"], cpu=[suffix],
    ) / "vendor"
    payload = vendor / f"{target}-pc-windows-msvc/bin/codex.exe"
    payload.parent.mkdir(parents=True)
    payload.touch()
    assert native_process.native_codex_executable(str(shim)) == str(payload)
    assert native_process.client_command(str(shim), ["login", "--device-auth"], client="codex") == [
        str(payload), "login", "--device-auth",
    ]


def test_windows_sibling_native_client_precedes_batch_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    for client in ("codex", "claude"):
        executable = tmp_path / f"{client}.exe"
        executable.touch()
        assert native_process.client_command(str(tmp_path / f"{client}.cmd"), ["login"], client=client) == [
            str(executable), "login",
        ]


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_windows_pinned_node_never_falls_back_to_native_sibling(monkeypatch, tmp_path, client):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    (tmp_path / f"{client}.exe").touch()
    node = tmp_path / "node.exe"
    node.touch()
    with pytest.raises(FileNotFoundError, match="no supported npm entry point"):
        native_process.client_command(
            str(tmp_path / f"{client}.cmd"), ["login"], client=client, node_executable=str(node)
        )


def test_windows_claude_npm_uses_declared_node_entrypoint_without_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    root = package(tmp_path / "node_modules/@anthropic-ai/claude-code", "@anthropic-ai/claude-code", bin={"claude": "cli.js"})
    script = root / "cli.js"
    script.touch()
    node = tmp_path / "Node with spaces" / "node.exe"
    node.parent.mkdir()
    node.touch()
    arguments = ['{"type":"object","description":"x & y"}', "", "a%PATH%"]
    assert native_process.client_command(
        str(tmp_path / "claude.cmd"),
        arguments,
        client="claude",
        node_executable=str(node),
    ) == [
        str(node), str(script), *arguments,
    ]


@pytest.mark.parametrize("entry", ["../../outside.js", None, "missing.js"])
def test_windows_claude_rejects_missing_or_escaping_payload(monkeypatch, tmp_path, entry):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    root = package(tmp_path / "node_modules/@anthropic-ai/claude-code", "@anthropic-ai/claude-code", bin={"claude": entry})
    (root.parent.parent / "outside.js").touch()
    with pytest.raises(FileNotFoundError, match="no supported native payload"):
        native_process.client_command(str(tmp_path / "claude.cmd"), [], client="claude")


def test_windows_missing_broken_or_unsupported_codex_payload_fails_explicitly(monkeypatch, tmp_path):
    monkeypatch.setattr(native_process, "_windows", lambda: True)
    root = package(tmp_path / "node_modules/@openai/codex", "@openai/codex")
    shim = str(tmp_path / "codex.cmd")
    assert native_process.native_codex_executable(shim) is None
    (root / "package.json").write_text("broken JSON")
    assert native_process.native_codex_executable(shim) is None
    monkeypatch.setattr(native_process.platform, "machine", lambda: "unsupported")
    assert native_process.native_codex_executable(shim) is None
    with pytest.raises(FileNotFoundError, match="Repair its official"):
        native_process.client_command(shim, [], client="codex")


@pytest.mark.skipif(os.name == "nt", reason="POSIX global npm symlink layout")
@pytest.mark.parametrize("client, package_name, entry", [
    ("codex", "@openai/codex", "bin/codex.js"),
    ("claude", "@anthropic-ai/claude-code", "cli.js"),
])
def test_posix_global_npm_symlink_uses_recorded_node_without_path(monkeypatch, tmp_path, client, package_name, entry):
    monkeypatch.setattr(native_process, "_windows", lambda: False)
    monkeypatch.setenv("PATH", "")
    root = package(tmp_path / "lib/node_modules" / package_name, package_name, bin={client: entry})
    script = root / entry
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env node\n")
    launcher = tmp_path / "bin" / client
    launcher.parent.mkdir()
    launcher.symlink_to(script)
    node = tmp_path / "recorded-node"
    node.touch()

    assert native_process.client_command(
        str(launcher), ["--version"], client=client, node_executable=str(node),
    ) == [str(node), str(script), "--version"]
    assert launcher.is_symlink()


def test_posix_unknown_node_launcher_does_not_return_to_ambient_path(monkeypatch, tmp_path):
    monkeypatch.setattr(native_process, "_windows", lambda: False)
    launcher, node = tmp_path / "codex", tmp_path / "node"
    launcher.write_text("#!/usr/bin/env node\n")
    node.touch()
    with pytest.raises(FileNotFoundError, match="no supported npm entry point"):
        native_process.client_command(str(launcher), [], client="codex", node_executable=str(node))
