"""Isolated installer/update/rollback/MCP/hooks smoke on Windows, macOS and Linux.

Default: materialize the real locked runtime with uv. --simulate-runtime uses
native executable fixtures for fast transactional tests without downloads.
Client CLIs are always synthetic; no subscription, browser or Docker is used.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
EXECUTABLES = (
    "dduo-solo-founder", "dduo-solo-founder-mcp", "dduo-solo-founder-bridge",
    "dduo-solo-founder-agent", "dduo-solo-founder-claude-statusline",
    "dduo-solo-founder-claude-statusline-restore", "dduo-solo-founder-hook-session-start",
    "dduo-solo-founder-hook-prompt", "dduo-solo-founder-hook-stop",
    "dduo-solo-founder-hook-dispatch", "dduo-solo-founder-mcp-dispatch",
)


def check_mcp(command: list[str], environment: dict[str, str], project: Path) -> None:
    """Probe each native transport boundary and retain a failing wire/exit code."""
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "codex-mcp-client", "version": "1"},
        },
    }
    process = subprocess.Popen(
        command, env=environment, cwd=project,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    messages: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=lambda: messages.put(process.stdout.readline()), daemon=True,
    )
    try:
        reader.start()
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        wire = messages.get(timeout=30)
        try:
            response = json.loads(wire)
        except ValueError as exc:
            try:
                remaining, diagnostic = process.communicate(timeout=10)
            except subprocess.TimeoutExpired as timeout:
                remaining, diagnostic = timeout.stdout, timeout.stderr
            raise AssertionError(
                f"MCP initialize failed for {command!r} (exit {process.poll()}): "
                f"first line {wire!r}\nstdout: {remaining!r}\nstderr: {diagnostic!r}"
            ) from exc
        assert response.get("id") == 1 and "result" in response
        assert response["result"]["serverInfo"]["version"]
        process.stdin.close()
        process.wait(timeout=30)
        assert process.returncode == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    print(f"PASS MCP transport: {Path(command[0]).name}", flush=True)


def write_cli(path: Path, arguments: list[str], *, native: bool = False) -> None:
    command = [sys.executable, str(SCRIPT), *arguments]
    if os.name == "nt" and native:
        from pip._vendor.distlib.scripts import ScriptMaker

        archive = io.BytesIO()
        source = (
            f"import runpy, sys\nsys.argv = {command[1:]!r} + sys.argv[1:]\n"
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
        )
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("__main__.py", source)
        path.with_suffix(".exe").write_bytes(
            ScriptMaker(None, None)._get_launcher("t")
            + f'#!"{sys.executable}"\n'.encode("utf-8") + archive.getvalue()
        )
    elif os.name == "nt":
        path.with_suffix(".cmd").write_text(
            "@echo off\r\n" + subprocess.list2cmdline(command) + " %*\r\n",
            encoding="utf-8", newline="",
        )
    else:
        import shlex

        path.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n', encoding="utf-8")
        path.chmod(0o755)


def fixture_client(client: str, args: list[str]) -> None:
    state = Path(os.environ["DDUO_SMOKE_STATE"])
    with (state / "calls.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps([client, *args]) + "\n")
    if args == ["--version"]:
        print("codex-cli 0.150.0" if client == "codex" else "2.0.0")
    elif args[:2] == ["features", "list"]:
        print("hooks stable true")
    elif args[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
    elif args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    elif client == "codex" and args[:1] == ["exec"]:
        assert sys.stdin.read() == "synthetic native smoke"
        destination = Path(args[args.index("--output-last-message") + 1])
        destination.write_text('{"topics":[]}', encoding="utf-8")
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    elif client == "claude" and args[:1] == ["-p"]:
        assert sys.stdin.read() == "synthetic native smoke"
        print(json.dumps({"result": '{"topics":[]}'}))
    elif args[:1] == ["app-server"]:
        for line in sys.stdin:
            request = json.loads(line)
            if "id" not in request:
                continue
            result = {} if request["id"] == 1 else {"data": [{
                "cwd": request["params"]["cwds"][0],
                "hooks": [{
                    "pluginId": "dduo-solo-founder@personal", "eventName": event,
                    "enabled": True, "trustStatus": "untrusted",
                } for event in ("sessionStart", "userPromptSubmit", "stop")],
            }]}
            print(json.dumps({"id": request["id"], "result": result}), flush=True)
            if request["id"] != 1:
                return
    elif args[:2] == ["plugin", "list"]:
        if client == "codex":
            print('{"installed":[],"available":[]}')
        else:
            manifest = Path.home() / ".local/share/dduo-solo-founder/claude-plugin/.claude-plugin/plugin.json"
            version = json.loads(manifest.read_text(encoding="utf-8"))["version"] if manifest.exists() else ""
            print(json.dumps([{
                "id": "dduo-solo-founder@dduo-solo-founder", "enabled": True,
                "version": version,
            }] if (state / "claude-plugin").exists() else []))
    elif args[:3] == ["plugin", "marketplace", "list"]:
        print(json.dumps([{"name": "dduo-solo-founder"}]
                         if (state / "claude-marketplace").exists() else []))
    elif args[:3] == ["plugin", "marketplace", "add"]:
        (state / "claude-marketplace").touch()
    elif args[:4] == ["plugin", "marketplace", "remove", "dduo-solo-founder"]:
        (state / "claude-marketplace").unlink(missing_ok=True)
    elif args[:2] == ["plugin", "install"]:
        (state / "claude-plugin").touch()
    elif args[:3] == ["plugin", "uninstall", "dduo-solo-founder@dduo-solo-founder"]:
        (state / "claude-plugin").unlink(missing_ok=True)
    elif client == "codex" and args[:2] == ["plugin", "add"]:
        fail = state / "fail-next-codex-add"
        if fail.exists():
            fail.unlink()
            raise SystemExit(19)


def fixture_runtime(name: str, args: list[str]) -> None:
    if name == "uv":
        if args[:3] == ["tool", "dir", "--bin"]:
            print(os.environ["UV_TOOL_BIN_DIR"])
        elif args[:1] == ["sync"]:
            directory = Path(os.environ["UV_PROJECT_ENVIRONMENT"]) / (
                "Scripts" if os.name == "nt" else "bin"
            )
            directory.mkdir(parents=True, exist_ok=True)
            for executable in EXECUTABLES:
                write_cli(directory / executable, ["--runtime", executable], native=True)
    elif name == "dduo-solo-founder" and args[:1] == ["client-readiness"]:
        print(json.dumps({"hooks": {
            "reason": "authorization_required", "hook_count": 3,
            "events": ["sessionStart", "userPromptSubmit", "stop"],
        }}))
    elif name == "dduo-solo-founder" and args[:1] == ["setup"]:
        raise SystemExit("The headless smoke must not open Setup")
    elif name == "dduo-solo-founder-hook-dispatch":
        json.load(sys.stdin)
        event = {"session-start": "SessionStart", "prompt": "UserPromptSubmit"}.get(args[0])
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": "synthetic runtime",
        }} if event else {"continue": True}))
    elif name == "dduo-solo-founder-mcp-dispatch":
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("method") == "initialize":
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "serverInfo": {"name": "synthetic dDuo", "version": "1"},
                }}), flush=True)


def smoke(source: Path, *, simulate_runtime: bool) -> None:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required")
    with tempfile.TemporaryDirectory(prefix="dduo-native-smoke-") as temporary:
        home = Path(temporary) / "home with spaces & accents è"
        binaries = home / "bin"
        project = home / "project"
        state = home / "smoke-state"
        for directory in (binaries, project / ".git", state):
            directory.mkdir(parents=True)
        for client in ("codex", "claude"):
            write_cli(binaries / client, ["--client", client])
        if os.name == "nt":
            # Model the official npm package layouts while keeping the client
            # payloads synthetic. Python readiness must execute these native
            # payloads directly, not ask Windows to interpret a .cmd file.
            architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
            target = "aarch64" if architecture == "arm64" else "x86_64"
            codex = binaries / "node_modules/@openai/codex"
            platform_package = binaries / f"node_modules/@openai/codex-win32-{architecture}"
            native_bin = platform_package / f"vendor/{target}-pc-windows-msvc/bin"
            claude = binaries / "node_modules/@anthropic-ai/claude-code"
            for directory in (codex, native_bin, claude):
                directory.mkdir(parents=True)
            (codex / "package.json").write_text('{"name":"@openai/codex"}', encoding="utf-8")
            (platform_package / "package.json").write_text(json.dumps({
                "name": "@openai/codex",
                "version": f"0.142.5-win32-{architecture}",
                "os": ["win32"],
                "cpu": [architecture],
            }), encoding="utf-8")
            write_cli(native_bin / "codex", ["--client", "codex"], native=True)
            (claude / "package.json").write_text(json.dumps({
                "name": "@anthropic-ai/claude-code", "bin": {"claude": "cli.cjs"},
            }), encoding="utf-8")
            (claude / "cli.cjs").write_text(
                "const {spawnSync}=require('node:child_process');"
                f"const r=spawnSync({json.dumps(sys.executable)},"
                f"[...{json.dumps([str(SCRIPT), '--client', 'claude'])},...process.argv.slice(2)],"
                "{stdio:'inherit'});process.exit(r.status??1);",
                encoding="utf-8",
            )
        # Shadow any maintainer installation inherited through PATH before the
        # first install's bridge-stop probe; never touch its real runtime.
        for executable in EXECUTABLES:
            write_cli(binaries / executable, ["--runtime", executable])
        if simulate_runtime:
            write_cli(binaries / "uv", ["--runtime", "uv"])
        environment = {**os.environ,
            "PYTHONFAULTHANDLER": "1",
            "HOME": str(home), "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData/Roaming"),
            "LOCALAPPDATA": str(home / "AppData/Local"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "CODEX_HOME": str(home / ".codex"),
            "UV_TOOL_BIN_DIR": str(binaries), "UV_TOOL_DIR": str(home / "uv-tools"),
            "DDUO_SMOKE_STATE": str(state), "DDUO_SMOKE_PROJECT": str(project),
            "DDUO_SOLO_FOUNDER_CODEX_DESKTOP": str(home / "missing-codex-desktop"),
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        for secret in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DDUO_SOLO_FOUNDER_RUNTIME_BIN"):
            environment.pop(secret, None)

        def run(arguments: list[str], *, success: bool = True, stdin: str | None = None):
            completed = subprocess.run(
                arguments, env=environment, cwd=project, input=stdin, capture_output=True,
                text=True, encoding="utf-8", timeout=900,
            )
            if (completed.returncode == 0) != success:
                raise AssertionError(
                    f"Smoke command failed (exit {completed.returncode}): {arguments!r}"
                    f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                )
            return completed

        installer = [node, str(source / "bin/install.mjs"), "--verbose", "--project-root", str(project)]
        if simulate_runtime:
            # Verify the synthetic tool's capture protocol independently of
            # installation, so a native subprocess failure identifies its
            # executable rather than hiding behind the transaction boundary.
            probe = run([node, "--input-type=module", "-e", (
                "import {execFileSync} from 'node:child_process';"
                f"process.stdout.write(execFileSync({json.dumps(sys.executable)},"
                f"{json.dumps([str(SCRIPT), '--runtime', 'uv', 'tool', 'dir', '--bin'])},"
                "{encoding:'utf8',stdio:'pipe'}));"
            )])
            assert probe.stdout.strip() == str(binaries)
            probe = run([node, "--input-type=module", "-e", (
                "import {execFileSync} from 'node:child_process';"
                f"import {{commandInvocation}} from {json.dumps((source / 'bin/native-command.mjs').as_uri())};"
                "const call=commandInvocation('uv',['tool','dir','--bin']);"
                "process.stderr.write(JSON.stringify(call)+'\\n');"
                "process.stdout.write(execFileSync(call.command,call.args,"
                "{encoding:'utf8',stdio:'pipe',...call.options}));"
            )])
            assert probe.stdout.strip() == str(binaries)
        run([*installer, "--yes", "--headless"])
        assert not (home / "plugins/dduo-solo-founder").exists()
        # Even available clients must not be probed in core-only installation.
        assert not (state / "calls.jsonl").exists()
        adapters = ["--only", "codex", "--only", "claude", "--no-setup"]
        run([*installer, "--yes", *adapters])
        run([*installer, "--verify", *adapters])
        runtime = home / ".local/share/dduo-solo-founder/runtime"
        pointer = home / ".config/dduo-solo-founder/hook-runtime-bin"
        expected_pointer = pointer.read_bytes()
        sentinel = runtime / "transaction-sentinel"
        sentinel.write_text("prior installation remains usable", encoding="utf-8")
        (state / "fail-next-codex-add").touch()
        run([*installer, "--yes", *adapters], success=False)
        assert sentinel.read_text(encoding="utf-8") == "prior installation remains usable"
        assert pointer.read_bytes() == expected_pointer
        run([*installer, "--verify", *adapters])
        run([*installer, "--yes", *adapters])
        assert not sentinel.exists()
        native_bin = Path(pointer.read_text(encoding="utf-8").strip())
        extension = ".exe" if os.name == "nt" else ""
        run([str(native_bin / f"dduo-solo-founder{extension}"), "--help"])
        if not simulate_runtime:
            # The installed Python code must resolve and authenticate the same
            # native payloads used for project-owned sleep. The executables in
            # this PATH are synthetic and cannot consume a real subscription.
            run([str(native_bin / f"python{extension}"), "-c", (
                "import json; from dduo_solo_founder.client_readiness import subscription_auth_status; "
                "from dduo_solo_founder.cli_bridge import CliRunner; "
                "assert subscription_auth_status('codex').ready; "
                "assert subscription_auth_status('claude').ready; "
                "schema={'type':'object','properties':{'topics':{'type':'array','items':{'type':'object'}}},'required':['topics']}; "
                "assert json.loads(CliRunner._run_codex('synthetic native smoke',schema,15)[0])=={'topics':[]}; "
                "assert json.loads(CliRunner._run_claude('synthetic native smoke',schema,15)[0])=={'topics':[]}"
            )])
        client_runners = (
            ("codex", home / "plugins/dduo-solo-founder/hooks/codex-runtime-hook.mjs"),
            ("claude", home / ".local/share/dduo-solo-founder/claude-plugin/bin/agent-plugin-hook.mjs"),
        )
        for client, runner in client_runners:
            for event in ("session-start", "prompt", "stop"):
                response = run([node, str(runner), event], stdin=json.dumps({
                    "cwd": str(project), "client": client, "session_id": "native-smoke",
                }))
                payload = json.loads(response.stdout)
                assert "additional_context" not in payload
                if event == "prompt" and not simulate_runtime:
                    assert payload == {"continue": True}  # No project has been activated.
                elif event != "stop":
                    context = payload["hookSpecificOutput"]["additionalContext"]
                    assert context
                    assert "temporarily unavailable" not in context
        if not simulate_runtime:
            run([str(native_bin / f"dduo-solo-founder{extension}"), "decline-setup", "--project-root", str(project)])
            for client, runner in client_runners:
                response = run([node, str(runner), "session-start"], stdin=json.dumps({
                    "cwd": str(project), "client": client, "session_id": "native-declined",
                }))
                assert json.loads(response.stdout) == {"continue": True}
        mcp_runner = home / ".local/share/dduo-solo-founder/claude-plugin/bin/agent-plugin-mcp.mjs"
        if not simulate_runtime:
            check_mcp([str(native_bin / f"dduo-solo-founder-mcp{extension}")], environment, project)
            check_mcp([str(native_bin / f"dduo-solo-founder-mcp-dispatch{extension}")], environment, project)
        check_mcp([node, str(mcp_runner)], environment, project)
        run([*installer, "--uninstall", "--yes"])
        assert not runtime.exists()
        assert not pointer.exists()
        assert (project / ".git").is_dir()
        print("PASS native install, adapters, rollback, update, hooks, MCP, uninstall")


def main() -> None:
    # Node and the test harness exchange UTF-8 even when Windows defaults the
    # parent Python process to a legacy code page. Keep tracebacks readable too.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["--client"]:
        fixture_client(sys.argv[2], sys.argv[3:])
        return
    if sys.argv[1:2] == ["--runtime"]:
        fixture_runtime(sys.argv[2], sys.argv[3:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--simulate-runtime", action="store_true")
    options = parser.parse_args()
    smoke(options.source.resolve(), simulate_runtime=options.simulate_runtime)


if __name__ == "__main__":
    main()
