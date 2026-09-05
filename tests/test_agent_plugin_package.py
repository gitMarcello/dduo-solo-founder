from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

from dduo_solo_founder.client_support import (
    UnsupportedClientError,
    client_family,
    require_supported_client,
)

ROOT = Path(__file__).resolve().parents[1]
CLIENT_SUPPORT = "it.dduo.client-support"
CODEX_ADAPTER = f"{CLIENT_SUPPORT}/codex/.codex-plugin/plugin.json"
CLAUDE_ADAPTER = f"{CLIENT_SUPPORT}/claude-code/.claude-plugin/plugin.json"
_RELEASE_VALIDATOR = runpy.run_path(str(ROOT / "scripts/validate_release.py"))
ReleaseValidationError = _RELEASE_VALIDATOR["ReleaseValidationError"]
load_agent_plugin_schema = _RELEASE_VALIDATOR["load_agent_plugin_schema"]
validate_agent_plugin_document = _RELEASE_VALIDATOR["validate_agent_plugin_document"]


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text())


def test_agent_plugins_manifest_is_strict_and_declares_the_supported_surface():
    manifest = _json("plugin.json")
    assert manifest["$schema"] == ("https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
    assert manifest["name"] == "dduo-solo-founder"
    assert manifest["version"] == (ROOT / "VERSION").read_text().strip()
    assert set(manifest) <= {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
    support = manifest["extensions"]["it.dduo.client-support"]
    assert support == {
        "supportedClients": ["codex", "claude-code"],
        "unsupportedClientBehavior": "disabled",
        "lifecycleAdapters": {
            "codex": CODEX_ADAPTER,
            "claude-code": CLAUDE_ADAPTER,
        },
    }
    assert (ROOT / CLIENT_SUPPORT).is_dir()
    for legacy_root in (".codex-plugin", ".claude-plugin", "hooks"):
        assert not (ROOT / legacy_root).exists()


def test_published_agent_plugin_schemas_match_their_pinned_provenance():
    assert load_agent_plugin_schema("plugin")["$id"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )
    assert load_agent_plugin_schema("mcp")["$id"] == (
        "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    )


@pytest.mark.parametrize(
    ("schema_name", "document"),
    [
        (
            "plugin",
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "dduo-solo-founder",
                "unsupported": True,
            },
        ),
        (
            "plugin",
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": 42,
            },
        ),
        (
            "mcp",
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {
                    "dduo": {
                        "type": "stdio",
                        "command": "node",
                        "env": {"PLUGIN_ROOT": "forbidden"},
                    }
                },
            },
        ),
        (
            "mcp",
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {"dduo": {"type": "stdio", "command": 42}},
            },
        ),
    ],
)
def test_published_agent_plugin_schemas_reject_invalid_documents(schema_name, document):
    with pytest.raises(ReleaseValidationError, match="violates Agent Plugins 1.0.0"):
        validate_agent_plugin_document(document, schema_name=schema_name, label="fixture")


def test_standard_mcp_manifest_uses_one_package_local_stdio_launcher():
    manifest = _json("mcp.json")
    assert manifest["$schema"] == ("https://agent-plugins.org/schemas/1.0.0/mcp.schema.json")
    assert manifest["mcpServers"] == {
        "dduo-solo-founder": {
            "type": "stdio",
            "command": "node",
            "args": ["${PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
            "cwd": "${PLUGIN_ROOT}",
        }
    }
    assert (ROOT / "bin/agent-plugin-mcp.mjs").is_file()


def test_native_adapters_keep_client_specific_mcp_registration_isolated():
    codex = _json(CODEX_ADAPTER)
    claude = _json(CLAUDE_ADAPTER)
    claude_mcp = claude["mcpServers"]["dduo-solo-founder"]

    assert codex["mcpServers"] == {
        "dduo-solo-founder": {
            "command": "dduo-solo-founder-mcp-dispatch",
            "env": {"DDUO_SOLO_FOUNDER_CLIENT": "codex"},
        }
    }
    assert "skills" not in codex
    assert set(claude["hooks"]) == {
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
    }
    assert claude_mcp == {
        "command": "node",
        "args": ["${CLAUDE_PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
        "env": {
            "DDUO_SOLO_FOUNDER_CLIENT": "claude",
            "DDUO_SOLO_FOUNDER_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR}",
        },
    }
    assert not (ROOT / ".mcp.json").exists()


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("codex", "codex"),
        ("OpenAI Codex", "codex"),
        ("Codex Desktop", "codex"),
        ("codex-mcp-client", "codex"),
        ("claude-code", "claude"),
        ("claude-ai", "claude"),
        ("Claude Code", "claude"),
        ("Anthropic Claude Code", "claude"),
        ("cursor", None),
        ("github-copilot", None),
        (None, None),
    ],
)
def test_client_family_is_deliberately_small(name: str | None, family: str | None):
    assert client_family(name) == family


def test_client_gate_accepts_supported_clients_and_rejects_others_or_mismatches():
    assert require_supported_client("OpenAI Codex") == "codex"
    assert require_supported_client("Claude Code", declared_client="claude") == "claude"
    with pytest.raises(UnsupportedClientError, match="only Codex and Claude Code"):
        require_supported_client("Cursor")
    with pytest.raises(UnsupportedClientError, match="does not match"):
        require_supported_client("Codex", declared_client="claude")


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is required by the plugin package")
def test_package_launcher_delegates_without_writing_its_own_stdout(tmp_path: Path):
    runtime_bin = tmp_path / "bin"
    runtime_bin.mkdir()
    runtime = runtime_bin / "dduo-solo-founder-mcp-dispatch"
    runtime.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$DDUO_SOLO_FOUNDER_PLUGIN_ENTRYPOINT\"\n"
        "printf '%s\\n' \"$*\"\n"
    )
    runtime.chmod(0o755)
    env = os.environ.copy()
    env["DDUO_SOLO_FOUNDER_RUNTIME_BIN"] = str(runtime_bin)
    result = subprocess.run(
        ["node", str(ROOT / "bin/agent-plugin-mcp.mjs"), "--probe"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.splitlines() == ["agent-plugins-1.0", "--probe"]
    assert result.stderr == ""


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is required by the plugin package")
def test_package_launcher_forwards_termination_and_exits_promptly(tmp_path: Path):
    runtime_bin = tmp_path / "bin"
    runtime_bin.mkdir()
    runtime = runtime_bin / "dduo-solo-founder-mcp-dispatch"
    runtime.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n")
    runtime.chmod(0o755)
    env = os.environ.copy()
    env["DDUO_SOLO_FOUNDER_RUNTIME_BIN"] = str(runtime_bin)
    process = subprocess.Popen(
        ["node", str(ROOT / "bin/agent-plugin-mcp.mjs")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert process.returncode in {143, -15}
    assert stdout == ""
    assert stderr == ""
