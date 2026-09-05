from __future__ import annotations

import ipaddress
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


pytestmark = [
    pytest.mark.distribution,
    pytest.mark.onboarding,
    pytest.mark.skipif(os.name == "nt", reason="the distribution shell hooks are POSIX-only"),
]

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_PLUGIN_VERSION = json.loads(
    (
        ROOT
        / "it.dduo.client-support/claude-code/.claude-plugin/plugin.json"
    ).read_text(encoding="utf-8")
)["version"]
PROJECT_ID = "remote-onboarding-smoke"
INVITATION_CODE = "one-time-smoke-invitation"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _runtime_wrapper(python: Path, entrypoint: str, *, setup_noop: bool = False) -> str:
    setup_guard = (
        'if [ "$1" = "setup" ] || [ "$1" = "bridge-stop" ]; then exit 0; fi\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        "  printf '%s\\n' "
        "'"
        '{"client":"codex","ready":false,"authentication":{"ready":true},'
        '"hooks":{"ready":false,"reason":"authorization_required","hook_count":3,'
        '"trust_statuses":["untrusted"],"events":["sessionStart","stop",'
        '"userPromptSubmit"]},"actions":[]}'
        "'\n"
        "  exit 8\n"
        "fi\n"
        if setup_noop
        else ""
    )
    return f"""#!/bin/sh
{setup_guard}runtime=$(sed -n '1p' "$HOME/.config/dduo-solo-founder/runtime-path")
export PYTHONPATH="$runtime/backend${{PYTHONPATH:+:$PYTHONPATH}}"
exec {json.dumps(str(python))} -c {json.dumps(entrypoint)} "$@"
"""


def _install_test_commands(home: Path, client: str) -> tuple[dict[str, str], Path]:
    binaries = home / "bin"
    binaries.mkdir(parents=True)
    commands = home / "client-commands.log"
    # Keep the virtual-environment launcher path: resolving its symlink would
    # bypass the already-installed test dependencies used by the real runtime.
    python = Path(sys.executable)
    entries = {
        "dduo-solo-founder": (
            "from dduo_solo_founder.launcher import app; app()",
            True,
        ),
        "dduo-solo-founder-hook-session-start": (
            "from dduo_solo_founder.hooks import session_start; session_start()",
            False,
        ),
        "dduo-solo-founder-hook-prompt": (
            "from dduo_solo_founder.hooks import user_prompt_submit; user_prompt_submit()",
            False,
        ),
        "dduo-solo-founder-hook-stop": (
            "from dduo_solo_founder.hooks import stop; stop()",
            False,
        ),
        "dduo-solo-founder-hook-dispatch": (
            "from dduo_solo_founder.client_dispatch import hook_dispatch_main; "
            "raise SystemExit(hook_dispatch_main())",
            False,
        ),
        "dduo-solo-founder-mcp": (
            "from dduo_solo_founder.mcp_server import main; main()",
            False,
        ),
        "dduo-solo-founder-mcp-dispatch": (
            "from dduo_solo_founder.client_dispatch import mcp_dispatch_main; "
            "mcp_dispatch_main()",
            False,
        ),
        "dduo-solo-founder-agent": (
            "from dduo_solo_founder.cli_bridge import main; main()",
            False,
        ),
        "dduo-solo-founder-bridge": (
            "from dduo_solo_founder.cli_bridge import main; main()",
            False,
        ),
        "dduo-solo-founder-claude-statusline": (
            "from dduo_solo_founder.claude_statusline import main; main()",
            False,
        ),
        "dduo-solo-founder-claude-statusline-restore": (
            "from dduo_solo_founder.claude_statusline import restore_main; restore_main()",
            False,
        ),
    }
    for name, (entrypoint, setup_noop) in entries.items():
        _write_executable(
            binaries / name,
            _runtime_wrapper(python, entrypoint, setup_noop=setup_noop),
        )

    _write_executable(
        binaries / "uv",
        f"""#!/bin/sh
printf 'uv %s\\n' "$*" >> {json.dumps(str(commands))}
if [ "$1" = "tool" ] && [ "$2" = "dir" ] && [ "$3" = "--bin" ]; then
  printf '%s\\n' {json.dumps(str(binaries))}
fi
if [ "$1" = "sync" ]; then
  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
  for executable in {" ".join(entries)}; do
    cp {json.dumps(str(binaries))}/$executable "$UV_PROJECT_ENVIRONMENT/bin/$executable"
  done
fi
""",
    )
    _write_executable(
        binaries / "codex",
        f"""#!/bin/sh
printf 'codex %s\\n' "$*" >> {json.dumps(str(commands))}
if [ "$1" = "--version" ]; then echo 'codex-cli 0.150.0'; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo 'hooks stable true'; fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo '{{"installed":[],"available":[]}}'; fi
""",
    )
    _write_executable(
        binaries / "claude",
        f"""#!/bin/sh
printf 'claude %s\\n' "$*" >> {json.dumps(str(commands))}
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
  if [ "$3" = "--json" ]; then echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}"}}]';
  else echo 'dduo-solo-founder@dduo-solo-founder'; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then
  echo '[{{"name":"dduo-solo-founder"}}]'
fi
""",
    )
    browser_log = home / "browser.log"
    _write_executable(
        binaries / "browser-stub",
        f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> {json.dumps(str(browser_log))}\n",
    )

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "XDG_CONFIG_HOME": str(home / "xdg-config"),
            "XDG_CACHE_HOME": str(home / "xdg-cache"),
            "XDG_DATA_HOME": str(home / "xdg-data"),
            "UV_CACHE_DIR": str(home / "xdg-cache/uv"),
            "PATH": f"{binaries}{os.pathsep}{environment['PATH']}",
            "BROWSER": str(binaries / "browser-stub"),
            "DDUO_SOLO_FOUNDER_CODEX_DESKTOP": str(binaries / "codex"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    environment.pop("PYTHONPATH", None)
    for credential in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
        "CODEX_ACCESS_TOKEN",
        "DDUO_AUTH_SIGNING_SECRET",
        "DDUO_CLI_BRIDGE_TOKEN",
        "DDUO_INFRASTRUCTURE_TOKEN",
        "DDUO_NODE_AUTHORITY_SECRET",
        "DDUO_SESSION_SECRET",
        "DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE",
    ):
        environment.pop(credential, None)
    return environment, browser_log


def _test_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "server.pem"
    key_path = directory / "server-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


@contextmanager
def _authenticated_server(directory: Path):
    certificate, key = _test_certificate(directory)
    state: dict[str, object] = {"requests": [], "token": None, "ticket": "browser-ticket"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _body(self) -> dict:
            size = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(size) or b"{}")

        def _json(self, status: int, value: dict) -> None:
            payload = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-DDUO-Client-Status", "compatible")
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {state['token']}"

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            path = urlsplit(self.path).path
            body = self._body()
            state["requests"].append(
                {
                    "method": "POST",
                    "path": path,
                    "authorization": self.headers.get("Authorization"),
                    "component": self.headers.get("X-DDUO-Client-Component"),
                    "body": body,
                }
            )
            if path == f"/api/projects/{PROJECT_ID}/auth/exchange":
                if body.get("invitation_code") != INVITATION_CODE:
                    self._json(403, {"detail": "invalid invitation"})
                    return
                state["token"] = body.get("device_token")
                if self.headers.get("Authorization") != f"Bearer {state['token']}":
                    self._json(401, {"detail": "device token mismatch"})
                    return
                self._json(
                    200,
                    {
                        "current_member": {
                            "id": "member-smoke",
                            "project_id": PROJECT_ID,
                            "display_name": "CI teammate",
                            "capability": "project_member",
                            "access_token_id": "token-smoke",
                        }
                    },
                )
                return
            if not self._authorized():
                self._json(401, {"detail": "missing project credential"})
                return
            if path == f"/api/projects/{PROJECT_ID}/sessions":
                self._json(200, {"id": "session-smoke"})
            elif path == f"/api/projects/{PROJECT_ID}/auth/browser-ticket":
                self._json(200, {"ticket": state["ticket"]})
            else:
                self._json(404, {"detail": "unknown test endpoint"})

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            parsed = urlsplit(self.path)
            state["requests"].append(
                {
                    "method": "GET",
                    "path": parsed.path,
                    "authorization": self.headers.get("Authorization"),
                    "component": self.headers.get("X-DDUO-Client-Component"),
                }
            )
            if parsed.path == "/":
                query = parse_qs(parsed.query)
                if query.get("ticket") != [state["ticket"]]:
                    self.send_error(401)
                    return
                payload = b"<!doctype html><title>dDuo remote dashboard</title>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if not self._authorized():
                self._json(401, {"detail": "missing project credential"})
                return
            if parsed.path == f"/api/projects/{PROJECT_ID}/briefing":
                self._json(
                    200,
                    {
                        "project": {
                            "id": PROJECT_ID,
                            "name": "Remote onboarding smoke",
                            "context": "Authenticated CI context",
                        },
                        "operational_manual": None,
                        "plans": [],
                        "tasks": [],
                        "task_counts": {},
                        "recent_handoffs": [],
                        "memory_status": {"available": True, "state": "ready"},
                        "onboarding_required": False,
                    },
                )
            elif parsed.path == f"/api/projects/{PROJECT_ID}/team":
                self._json(
                    200,
                    {
                        "current_member": {
                            "id": "member-smoke",
                            "project_id": PROJECT_ID,
                            "display_name": "CI teammate",
                            "capability": "project_member",
                            "access_token_id": "token-smoke",
                        }
                    },
                )
            elif parsed.path == f"/api/projects/{PROJECT_ID}/team/manual":
                self._json(
                    200,
                    {
                        "manual": {
                            "version": 1,
                            "content": "Use the verified remote release procedure.",
                        }
                    },
                )
            else:
                self._json(404, {"detail": "unknown test endpoint"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state, certificate
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _run(command: list[str], environment: dict[str, str], *, input_text: str | None = None):
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        env=environment,
        timeout=60,
        check=True,
    )


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_clean_install_remote_join_hook_mcp_and_dashboard(client: str, tmp_path: Path):
    if not shutil.which("node"):
        pytest.skip("Node.js is required by the distribution installer")
    home = tmp_path / client
    home.mkdir()
    environment, browser_log = _install_test_commands(home, client)
    project = home / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert not (home / ".config").exists()
    assert not (home / "xdg-cache").exists()

    installation = _run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            client,
            "--project-root",
            str(project),
        ],
        environment,
    )
    assert f"PASS  installation verified ({client.title()})" in installation.stdout
    expected_handoff = (
        "CODEX_APP_RESTART_REQUIRED" if client == "codex" else "SESSION_RELOAD_REQUIRED"
    )
    assert expected_handoff in installation.stdout
    runtime = home / ".local/share/dduo-solo-founder/runtime"
    assert runtime.joinpath("backend/dduo_solo_founder/client_binding.py").is_file()

    with _authenticated_server(home) as (server, server_state, certificate):
        port = server.server_address[1]
        api_url = f"https://127.0.0.1:{port}/api"
        dashboard_url = f"https://127.0.0.1:{port}"
        environment["SSL_CERT_FILE"] = str(certificate)
        joined = _run(
            [
                "dduo-solo-founder",
                "remote-join",
                "--project-id",
                PROJECT_ID,
                "--name",
                "Remote onboarding smoke",
                "--api-url",
                api_url,
                "--dashboard-url",
                dashboard_url,
                "--invitation-code",
                INVITATION_CODE,
                "--project-root",
                str(project),
                "--device-label",
                f"CI {client}",
            ],
            environment,
        )
        joined_payload = json.loads(joined.stdout)
        assert joined_payload["joined"] is True
        assert joined_payload["project_id"] == PROJECT_ID
        assert joined_payload["member"]["project_id"] == PROJECT_ID
        assert joined_payload["member"]["access_token_id"] == "token-smoke"
        assert joined_payload["offline_manual_cached"] is True
        assert joined_payload["local_docker_required"] is False
        assert INVITATION_CODE not in joined.stdout
        config = project.joinpath(".dduo-solo-founder/project.toml").read_text()
        assert 'binding = "remote"' in config
        assert api_url in config
        assert "invitation" not in config

        binding_check = _run(
            [
                sys.executable,
                "-c",
                (
                    "import json,sys; from pathlib import Path; "
                    "from dduo_solo_founder.client_binding import load_binding; "
                    "b=load_binding(Path(sys.argv[1])); "
                    "print(json.dumps({'kind':b.kind,'project_id':b.project_id,"
                    "'api_url':b.api_url,'credential':bool(b.bearer_token)}))"
                ),
                str(project),
            ],
            {
                **environment,
                "PYTHONPATH": str(runtime / "backend"),
            },
        )
        assert json.loads(binding_check.stdout) == {
            "kind": "remote",
            "project_id": PROJECT_ID,
            "api_url": api_url,
            "credential": True,
        }

        hook_environment = {**environment, "DDUO_SOLO_FOUNDER_PROJECT_ROOT": str(project)}
        if client == "codex":
            hook_environment["PLUGIN_ROOT"] = str(home / "plugins/dduo-solo-founder")
            hook_command = [
                "/bin/sh",
                str(home / "plugins/dduo-solo-founder/hooks/codex-runtime-hook.sh"),
                "session-start",
            ]
        else:
            hook_environment["CLAUDE_PLUGIN_ROOT"] = str(runtime)
            hook_command = ["dduo-solo-founder-hook-dispatch", "session-start"]
        hook = _run(
            hook_command,
            hook_environment,
            input_text=json.dumps(
                {
                    "cwd": str(project),
                    "client": client,
                    "session_id": f"{client}-session",
                }
            ),
        )
        hook_payload = json.loads(hook.stdout)
        assert "additional_context" not in hook_payload
        hook_specific_output = hook_payload.get("hookSpecificOutput")
        assert hook_specific_output is not None
        assert hook_specific_output["hookEventName"] == "SessionStart"
        context = hook_specific_output["additionalContext"]
        assert "Remote onboarding smoke" in context

        mcp_client_script = """
import json
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


async def smoke():
    server = StdioServerParameters(
        command="dduo-solo-founder-mcp-dispatch",
        env=os.environ.copy(),
    )
    client_info = types.Implementation(name=sys.argv[1], version="test")
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            client_info=client_info,
        ) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            briefing = await session.call_tool("get_project_briefing", {})
            print(json.dumps({
                "server_name": initialized.server_info.name,
                "tools": [tool.name for tool in tools.tools],
                "briefing": briefing.structured_content,
            }))


anyio.run(smoke)
"""
        mcp = _run(
            [sys.executable, "-c", mcp_client_script, client],
            {**environment, "DDUO_SOLO_FOUNDER_PROJECT_ROOT": str(project)},
        )
        mcp_payload = json.loads(mcp.stdout)
        assert mcp_payload["server_name"] == "dduo-solo-founder"
        assert "get_project_briefing" in mcp_payload["tools"]
        assert mcp_payload["briefing"]["project"]["name"] == "Remote onboarding smoke"

        dashboard = _run(
            [
                "dduo-solo-founder",
                "dashboard",
                "--project-root",
                str(project),
                "--tab",
                "team",
            ],
            environment,
        )
        assert "ticket=<one-time>" in dashboard.stdout
        assert "browser-ticket" not in dashboard.stdout
        assert browser_log.is_file()
        dashboard_request = urllib.request.Request(
            f"{dashboard_url}/?project={PROJECT_ID}&tab=team&ticket={server_state['ticket']}"
        )
        context = ssl.create_default_context(cafile=str(certificate))
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
        )
        with opener.open(dashboard_request, timeout=5) as response:
            assert response.status == 200
            assert b"dDuo remote dashboard" in response.read()

    requests = server_state["requests"]
    authenticated = [request for request in requests if request["path"].startswith("/api/")]
    assert {request["component"] for request in authenticated} >= {"launcher", "hook", "mcp"}
    assert all(request["authorization"] == f"Bearer {server_state['token']}" for request in authenticated)
    assert any(request["path"].endswith("/sessions") for request in authenticated)
    assert any(request["path"].endswith("/briefing") for request in authenticated)
    assert any(request["path"].endswith("/auth/browser-ticket") for request in authenticated)
