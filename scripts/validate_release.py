#!/usr/bin/env python3
"""Fail closed when a dDuo Beta source package is internally inconsistent."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from skills_ref import validate as validate_agent_skill


ROOT = Path(__file__).resolve().parents[1]
AGENT_PLUGIN_SCHEMA_ROOT = ROOT / "vendor/agent-plugins/1.0.0"
AGENT_PLUGIN_SCHEMA_PROVENANCE = AGENT_PLUGIN_SCHEMA_ROOT / "PROVENANCE.json"
BETA_VERSION = re.compile(
    r"^(?P<base>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
    r"-beta\.(?P<number>0|[1-9]\d*)$"
)
AGENT_PLUGIN_SCHEMAS = {
    "plugin": "schemas/plugin.schema.json",
    "mcp": "schemas/mcp.schema.json",
}
CLIENT_SUPPORT_NAMESPACE = "it.dduo.client-support"
CODEX_ADAPTER_MANIFEST = f"{CLIENT_SUPPORT_NAMESPACE}/codex/.codex-plugin/plugin.json"
CLAUDE_ADAPTER_MANIFEST = f"{CLIENT_SUPPORT_NAMESPACE}/claude-code/.claude-plugin/plugin.json"
CLAUDE_MARKETPLACE_MANIFEST = (
    f"{CLIENT_SUPPORT_NAMESPACE}/claude-code/.claude-plugin/marketplace.json"
)
CODEX_HOOK_MANIFEST = f"{CLIENT_SUPPORT_NAMESPACE}/codex/hooks/hooks.json"
CODEX_HOOK_RUNNER = f"{CLIENT_SUPPORT_NAMESPACE}/codex/hooks/codex-runtime-hook.mjs"


class ReleaseValidationError(RuntimeError):
    """Raised when the candidate cannot be released safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseValidationError(message)


def read_json(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseValidationError(f"{relative_path} is not a readable JSON document") from error
    require(isinstance(value, dict), f"{relative_path} must contain a JSON object")
    return value


def _read_json_path(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseValidationError(f"{label} is not a readable JSON document") from error
    require(isinstance(value, dict), f"{label} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ReleaseValidationError(f"cannot read vendored schema {path}") from error


def load_agent_plugin_schema(name: str) -> dict[str, Any]:
    """Load one immutable Agent Plugins 1.0.0 schema and verify its provenance."""
    relative_path = AGENT_PLUGIN_SCHEMAS.get(name)
    require(relative_path is not None, f"unknown Agent Plugins schema {name!r}")
    provenance = _read_json_path(
        AGENT_PLUGIN_SCHEMA_PROVENANCE,
        label="Agent Plugins schema provenance",
    )
    require(
        provenance.get("specification") == "Agent Plugins Specification 1.0.0",
        "Agent Plugins schema provenance targets the wrong specification",
    )
    entries = provenance.get("files")
    require(isinstance(entries, dict), "Agent Plugins schema provenance has no file map")
    entry = entries.get(relative_path)
    require(isinstance(entry, dict), f"Agent Plugins schema provenance omits {relative_path}")
    path = AGENT_PLUGIN_SCHEMA_ROOT / relative_path
    expected_digest = entry.get("sha256")
    require(
        isinstance(expected_digest, str) and re.fullmatch(r"[0-9a-f]{64}", expected_digest),
        f"Agent Plugins schema provenance has an invalid digest for {relative_path}",
    )
    require(
        _sha256(path) == expected_digest,
        f"vendored Agent Plugins schema {relative_path} does not match its pinned digest",
    )
    schema = _read_json_path(path, label=f"vendored Agent Plugins schema {relative_path}")
    require(schema.get("$id") == entry.get("source"), f"schema source mismatch for {relative_path}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ReleaseValidationError(
            f"vendored Agent Plugins schema {relative_path} is not a valid Draft 2020-12 schema"
        ) from error
    return schema


def validate_agent_plugin_document(
    document: dict[str, Any],
    *,
    schema_name: str,
    label: str,
) -> None:
    """Validate a manifest with the exact published Agent Plugins 1.0.0 schema."""
    validator = Draft202012Validator(load_agent_plugin_schema(schema_name))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    raise ReleaseValidationError(
        f"{label} violates Agent Plugins 1.0.0 at {location}: {error.message}"
    )


def validate_versions(tag: str | None) -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    match = BETA_VERSION.fullmatch(version)
    require(match is not None, "VERSION must use X.Y.Z-beta.N without leading zeroes")
    if tag is not None:
        require(tag == f"v{version}", f"tag {tag!r} must equal 'v{version}'")

    versioned_json = {
        "plugin.json": read_json("plugin.json").get("version"),
        CODEX_ADAPTER_MANIFEST: read_json(CODEX_ADAPTER_MANIFEST).get("version"),
        CLAUDE_ADAPTER_MANIFEST: read_json(CLAUDE_ADAPTER_MANIFEST).get("version"),
        "frontend/package.json": read_json("frontend/package.json").get("version"),
    }
    for path, candidate in versioned_json.items():
        require(candidate == version, f"{path} version {candidate!r} does not equal {version!r}")

    package_lock = read_json("frontend/package-lock.json")
    require(package_lock.get("version") == version, "frontend/package-lock.json version is stale")
    root_package = package_lock.get("packages", {}).get("")
    require(isinstance(root_package, dict), "frontend/package-lock.json has no root package")
    require(root_package.get("version") == version, "frontend lock root package version is stale")

    pep440 = f"{match.group('base')}b{match.group('number')}"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    require(
        pyproject.get("project", {}).get("version") == pep440, "pyproject.toml version is stale"
    )
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_project = [
        package
        for package in uv_lock.get("package", [])
        if package.get("name") == "dduo-solo-founder" and package.get("source") == {"editable": "."}
    ]
    require(len(locked_project) == 1, "uv.lock must contain one editable dduo package")
    require(locked_project[0].get("version") == pep440, "uv.lock project version is stale")

    backend_init = (ROOT / "backend/dduo_solo_founder/__init__.py").read_text(encoding="utf-8")
    backend_version = re.fullmatch(r'__version__\s*=\s*"([^"]+)"\s*', backend_init)
    require(backend_version is not None, "backend __version__ must be a single literal assignment")
    require(backend_version.group(1) == version, "backend __version__ is stale")
    return version


def validate_agent_plugin() -> None:
    manifest = read_json("plugin.json")
    validate_agent_plugin_document(manifest, schema_name="plugin", label="plugin.json")
    require(
        manifest.get("$schema") == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "plugin.json must target Agent Plugins 1.0.0",
    )
    for field in ("name", "description", "author", "license"):
        require(manifest.get(field), f"plugin.json is missing {field!r}")
    extensions = manifest.get("extensions")
    require(
        isinstance(extensions, dict) and set(extensions) == {CLIENT_SUPPORT_NAMESPACE},
        "plugin.json must declare only the dDuo-owned client-support extension",
    )
    support = extensions.get(CLIENT_SUPPORT_NAMESPACE)
    require(isinstance(support, dict), "plugin.json is missing the client-support extension")
    require(
        support.get("supportedClients") == ["codex", "claude-code"],
        "the Beta support boundary must remain Codex and Claude Code only",
    )
    require(
        support.get("unsupportedClientBehavior") == "disabled",
        "unsupported Agent Plugin clients must remain disabled",
    )
    require(
        support.get("lifecycleAdapters")
        == {
            "codex": CODEX_ADAPTER_MANIFEST,
            "claude-code": CLAUDE_ADAPTER_MANIFEST,
        },
        "client lifecycle adapters must remain inside the dDuo extension namespace",
    )
    extension_root = ROOT / CLIENT_SUPPORT_NAMESPACE
    require(
        extension_root.is_dir() and not extension_root.is_symlink(),
        "the dDuo client-support extension must be a regular top-level directory",
    )
    require(
        {path.name for path in extension_root.iterdir()} == {"codex", "claude-code"},
        "the dDuo client-support extension must contain only Codex and Claude Code",
    )
    for client in ("codex", "claude-code"):
        client_root = extension_root / client
        require(
            client_root.is_dir() and not client_root.is_symlink(),
            f"the {client} adapter must be a regular extension directory",
        )

    mcp = read_json("mcp.json")
    validate_agent_plugin_document(mcp, schema_name="mcp", label="mcp.json")
    require(
        mcp.get("$schema") == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcp.json must target Agent Plugins 1.0.0",
    )
    server = mcp.get("mcpServers", {}).get("dduo-solo-founder")
    require(isinstance(server, dict), "mcp.json is missing the dDuo server")
    require(server.get("type") == "stdio", "the portable MCP server must use stdio")
    require(server.get("command") == "node", "the portable MCP launcher must use Node.js")
    require(
        server.get("args") == ["${PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
        "the portable MCP launcher must resolve from PLUGIN_ROOT",
    )
    require(server.get("cwd") == "${PLUGIN_ROOT}", "the portable MCP cwd must be PLUGIN_ROOT")

    codex = read_json(CODEX_ADAPTER_MANIFEST)
    codex_server = codex.get("mcpServers", {}).get("dduo-solo-founder")
    require(isinstance(codex_server, dict), "the Codex adapter must declare its MCP server")
    require(
        codex_server.get("command") == "dduo-solo-founder-mcp-dispatch",
        "the Codex source manifest must use the stable dispatcher template",
    )
    require(
        codex_server.get("env") == {"DDUO_SOLO_FOUNDER_CLIENT": "codex"},
        "the Codex MCP adapter must declare only its client identity",
    )
    require("skills" not in codex, "Codex Skill discovery must use the native default path")
    codex_json = json.dumps(codex)
    require(
        "${PWD}" not in codex_json and "DDUO_SOLO_FOUNDER_PROJECT_ROOT" not in codex_json,
        "the Codex adapter must resolve workspace_root independently on every MCP call",
    )
    interface = codex.get("interface")
    require(isinstance(interface, dict), "the Codex adapter must include its interface metadata")
    require(interface.get("displayName") == "dDuo Solo Founder", "Codex display name is stale")
    require(bool(interface.get("defaultPrompt")), "Codex default prompts must not be empty")

    claude = read_json(CLAUDE_ADAPTER_MANIFEST)
    claude_server = claude.get("mcpServers", {}).get("dduo-solo-founder")
    require(isinstance(claude_server, dict), "the Claude adapter must declare its MCP server")
    require(
        claude_server.get("args") == ["${CLAUDE_PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
        "the Claude adapter must launch the package-local bridge",
    )
    require(
        claude_server.get("env", {}).get("DDUO_SOLO_FOUNDER_PROJECT_ROOT")
        == "${CLAUDE_PROJECT_DIR}",
        "the Claude adapter must bind MCP calls to the current project",
    )
    require(
        set(claude.get("hooks", {})) == {"SessionStart", "UserPromptSubmit", "Stop"},
        "the Claude lifecycle adapter is incomplete",
    )
    codex_hooks = read_json(CODEX_HOOK_MANIFEST).get("hooks", {})
    events = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "Stop": "stop"}
    require(set(codex_hooks) == set(events), "the Codex lifecycle adapter is incomplete")
    for hooks, prefix, client in (
        (codex_hooks, 'node "$PLUGIN_ROOT/hooks/codex-runtime-hook.mjs"', "Codex"),
        (claude["hooks"], 'node "${CLAUDE_PLUGIN_ROOT}/bin/agent-plugin-hook.mjs"', "Claude"),
    ):
        for event, argument in events.items():
            handlers = hooks[event]
            require(
                len(handlers) == 1
                and len(handlers[0].get("hooks", [])) == 1
                and handlers[0]["hooks"][0].get("command") == f"{prefix} {argument}",
                f"the {client} lifecycle adapter must use the portable Node runner",
            )


def validate_skill(skill_root: Path | None = None) -> None:
    skill_root = skill_root or ROOT / "skills/dduo-solo-founder"
    skill_file = skill_root / "SKILL.md"
    contents = skill_file.read_text(encoding="utf-8")
    problems = validate_agent_skill(skill_root)
    require(
        not problems,
        "Agent Skills reference validator rejected dduo-solo-founder: " + "; ".join(problems),
    )
    match = re.match(r"^---\n.*?\n---(?:\n|$)", contents, re.DOTALL)
    require(match is not None, "skills/dduo-solo-founder/SKILL.md needs YAML frontmatter")
    require("[TODO:" not in contents, "the dDuo skill contains an unfinished TODO marker")
    require(bool(contents[match.end() :].strip()), "the dDuo skill body is empty")


def validate_distribution_surface() -> None:
    required = (
        "plugin.json",
        "mcp.json",
        CODEX_ADAPTER_MANIFEST,
        CLAUDE_ADAPTER_MANIFEST,
        CLAUDE_MARKETPLACE_MANIFEST,
        CODEX_HOOK_MANIFEST,
        CODEX_HOOK_RUNNER,
        "skills/dduo-solo-founder/SKILL.md",
        "bin/agent-plugin-mcp.mjs",
        "bin/install.mjs",
        "bin/native-command.mjs",
        "checksums.sha256",
        "vendor/agent-plugins/1.0.0/PROVENANCE.json",
        "vendor/agent-plugins/1.0.0/schemas/plugin.schema.json",
        "vendor/agent-plugins/1.0.0/schemas/mcp.schema.json",
    )
    for relative_path in required:
        require((ROOT / relative_path).is_file(), f"release file {relative_path!r} is missing")
    for relative_path in (".codex-plugin", ".claude-plugin", "hooks"):
        require(
            not (ROOT / relative_path).exists(),
            f"client-specific root path {relative_path!r} must live under {CLIENT_SUPPORT_NAMESPACE}",
        )
    require(not (ROOT / ".mcp.json").exists(), "legacy root .mcp.json must not ship in Beta")
    retired = (
        ".github/dduo-update-signing-public-key",
        ".github/workflows/client-release.yml",
        "scripts/build_signed_client_release.py",
        "backend/dduo_solo_founder/client_updater.py",
        "backend/dduo_solo_founder/release_contract.py",
    )
    for relative_path in retired:
        require(
            not (ROOT / relative_path).exists(), f"retired updater asset {relative_path!r} remains"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Optional Git tag; must be v<VERSION>.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        version = validate_versions(args.tag)
        validate_agent_plugin()
        validate_skill()
        validate_distribution_surface()
    except (OSError, ReleaseValidationError, tomllib.TOMLDecodeError) as error:
        print(f"Release validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Release validation passed: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
