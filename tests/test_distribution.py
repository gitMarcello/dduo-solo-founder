import json
import os
import shutil
import subprocess
import threading
import time
import tomllib
from hashlib import sha256
from pathlib import Path

import pytest


pytestmark = pytest.mark.distribution


@pytest.fixture(autouse=True)
def _clear_ambient_client_scopes(monkeypatch: pytest.MonkeyPatch):
    # Installer fixtures select their own temporary HOME. An inherited vendor
    # profile must not redirect their configuration writes outside that HOME.
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SUPPORT = ROOT / "it.dduo.client-support"
CODEX_ADAPTER = CLIENT_SUPPORT / "codex"
CLAUDE_ADAPTER = CLIENT_SUPPORT / "claude-code"
CLAUDE_PLUGIN_VERSION = json.loads(
    (CLAUDE_ADAPTER / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
)["version"]
RUNTIME_EXECUTABLES = (
    "dduo-solo-founder",
    "dduo-solo-founder-mcp",
    "dduo-solo-founder-bridge",
    "dduo-solo-founder-agent",
    "dduo-solo-founder-claude-statusline",
    "dduo-solo-founder-claude-statusline-restore",
    "dduo-solo-founder-hook-session-start",
    "dduo-solo-founder-hook-prompt",
    "dduo-solo-founder-hook-stop",
    "dduo-solo-founder-hook-dispatch",
    "dduo-solo-founder-mcp-dispatch",
)
CODEX_HOOK_DISCOVERY_JSON = json.dumps(
    {
        "client": "codex",
        "ready": True,
        "authentication": {"ready": True},
        "hooks": {
            "ready": True,
            "reason": "authorized",
            "hook_count": 3,
            "trust_statuses": ["trusted"],
            "events": [
                "sessionStart",
                "stop",
                "userPromptSubmit",
            ],
        },
        "actions": [],
    },
    separators=(",", ":"),
)


def test_remote_host_requirements_are_provider_neutral_and_consistent():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    italian = (ROOT / "README.it.md").read_text(encoding="utf-8")
    remote_guide = (ROOT / "docs/remote-teams.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills/dduo-solo-founder/SKILL.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, italian, remote_guide, skill)).lower()

    for value in ("1 gib", "1 vcpu", "2 gib", "5 gib", "7 gib"):
        assert value in combined
    assert "any vps provider" in combined
    assert "digitalocean" not in combined
    assert "droplet" not in combined


def test_main_ci_uses_one_required_pull_request_gate():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "  pull_request:\n" in workflow
    assert "  workflow_dispatch:\n" in workflow
    assert "  push:\n    branches: [main]\n" in workflow
    assert "    name: CI gate\n" in workflow
    assert "    if: ${{ always() }}\n" in workflow
    for dependency in ("python", "frontend", "browser", "compose"):
        assert f"      - {dependency}\n" in workflow
        assert f"${{{{ needs.{dependency}.result }}}}" in workflow


def _uv_installer_stub(binaries: Path, log: Path) -> str:
    names = " ".join(RUNTIME_EXECUTABLES)
    sources = binaries / ".runtime-source"
    return (
        f'#!/bin/sh\necho "uv $*" >> "{log}"\n'
        'if [ "$1" = "tool" ] && [ "$2" = "dir" ] && [ "$3" = "--bin" ]; then\n'
        f'  printf "%s\\n" "{binaries}"\n'
        "fi\n"
        'if [ "$1" = "sync" ]; then\n'
        '  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
        f'  mkdir -p "{sources}"\n'
        f"  for executable in {names}; do\n"
        f'    if [ ! -f "{sources}/$executable" ]; then cp "{binaries}/$executable" "{sources}/$executable"; fi\n'
        f'    cp "{sources}/$executable" "$UV_PROJECT_ENVIRONMENT/bin/$executable"\n'
        "  done\n"
        "fi\n"
    )


def _write_codex_installer_stubs(
    tmp_path: Path,
    *,
    path_version: str,
    desktop_version: str | None = None,
) -> tuple[dict[str, str], Path, Path | None]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in (
        "uv",
        "dduo-solo-founder",
        "dduo-solo-founder-bridge",
        "dduo-solo-founder-agent",
        "dduo-solo-founder-mcp",
        "dduo-solo-founder-mcp-dispatch",
        "dduo-solo-founder-hook-dispatch",
        "dduo-solo-founder-claude-statusline",
        "dduo-solo-founder-claude-statusline-restore",
        "dduo-solo-founder-hook-session-start",
        "dduo-solo-founder-hook-prompt",
        "dduo-solo-founder-hook-stop",
    ):
        executable = binaries / command
        if command == "dduo-solo-founder":
            executable.write_text(
                f'#!/bin/sh\necho "{command} $*" >> "{log}"\n'
                'if [ "$1" = "client-readiness" ]; then\n'
                f"  printf '%s\\n' '{CODEX_HOOK_DISCOVERY_JSON}'\n"
                "fi\n"
            )
        else:
            executable.write_text(f'#!/bin/sh\necho "{command} $*" >> "{log}"\n')
        executable.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(_uv_installer_stub(binaries, log))
    uv.chmod(0o755)

    path_codex = binaries / "codex"
    path_codex.write_text(
        f'#!/bin/sh\necho "path-codex $*" >> "{log}"\n'
        f'if [ "$1" = "--version" ]; then echo "codex-cli {path_version}"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo \'{"installed":[],"available":[]}\'; fi\n'
    )
    path_codex.chmod(0o755)

    desktop_codex = None
    if desktop_version is not None:
        desktop_codex = tmp_path / "Codex Desktop"
        desktop_codex.write_text(
            f'#!/bin/sh\necho "desktop-codex $*" >> "{log}"\n'
            f'if [ "$1" = "--version" ]; then echo "codex-cli {desktop_version}"; fi\n'
            'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi\n'
            'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo \'{"installed":[],"available":[]}\'; fi\n'
        )
        desktop_codex.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    if desktop_codex is not None:
        env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(desktop_codex)
    else:
        env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(tmp_path / "missing-desktop-codex")
    return env, log, desktop_codex


def _isolated_codex_installer(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    env, log, _desktop = _write_codex_installer_stubs(
        tmp_path,
        path_version="0.150.0",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    return env, log, project


def _installer_bootstrap_copy(tmp_path: Path) -> Path:
    """Exercise the actual installer in a sealed, checksum-valid test package."""
    source = tmp_path / "test-distribution"
    shutil.copytree(ROOT / "bin", source / "bin")
    entries = []
    for path in sorted((source / "bin").rglob("*")):
        if path.is_file():
            entries.append(f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(source).as_posix()}")
    (source / "checksums.sha256").write_text("\n".join(entries) + "\n")
    return source / "bin/install.mjs"


@pytest.mark.parametrize("family", ["codex", "claude"])
@pytest.mark.parametrize("vendor_failure", [False, True])
def test_uninstall_uses_recorded_client_and_scope_without_path_or_only(
    tmp_path: Path, family: str, vendor_failure: bool
):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required")
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    sentinel = runtime / "sentinel"
    sentinel.write_text("keep until native removal succeeds")
    profile = tmp_path / "custom-client-profile"
    profile.mkdir()
    vendor = tmp_path / "vendor-not-on-path"
    vendor.mkdir()
    launcher = vendor / family
    registered = tmp_path / "vendor-registration"
    registered.touch()
    log = tmp_path / "vendor.log"
    listing = (
        '{"installed":[{"name":"dduo-solo-founder","pluginId":"dduo-solo-founder@personal"}]}'
        if family == "codex" else '[{"id":"dduo-solo-founder@dduo-solo-founder"}]'
    )
    empty = '{"installed":[]}' if family == "codex" else '[]'
    launcher.write_text(
        '#!/bin/sh\n'
        f'printf "%s|%s|%s\\n" "$*" "$CODEX_HOME" "$CLAUDE_CONFIG_DIR" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        f'  if [ -f "{registered}" ]; then echo \'{listing}\'; else echo \'{empty}\'; fi\n'
        'fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then echo "[]"; fi\n'
        'if [ "$1" = "plugin" ] && { [ "$2" = "remove" ] || [ "$2" = "uninstall" ]; }; then\n'
        + ('  exit 9\n' if vendor_failure else f'  rm -f "{registered}"\n')
        + 'fi\n'
    )
    launcher.chmod(0o755)
    executable_record = config / "client-executables.json"
    scope_record = config / "client-scopes.json"
    executable_record.write_text(json.dumps({"version": 1, "clients": {family: {"launcher_path": str(launcher)}}}))
    scope_record.write_text(json.dumps({"version": 1, "clients": {family: {"config_dir": str(profile)}}}))
    before = executable_record.read_bytes(), scope_record.read_bytes()
    env = os.environ.copy()
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        env.pop(key, None)
    env.update({"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "DDUO_SOLO_FOUNDER_CODEX_DESKTOP": str(tmp_path / "absent")})
    result = subprocess.run([node, str(ROOT / "bin/install.mjs"), "--uninstall", "--yes"],
                            env=env, text=True, capture_output=True)
    commands = log.read_text()
    assert str(profile) in commands
    assert ("plugin remove" if family == "codex" else "plugin uninstall") in commands
    if vendor_failure:
        assert result.returncode != 0
        assert registered.exists() and sentinel.exists()
        assert (executable_record.read_bytes(), scope_record.read_bytes()) == before
    else:
        assert result.returncode == 0, result.stderr
        assert not registered.exists() and not sentinel.exists()
        assert family not in json.loads(executable_record.read_text())["clients"]
        assert family not in json.loads(scope_record.read_text())["clients"]


def test_codex_preflight_does_not_enable_hooks_or_replace_runtime_when_list_fails(tmp_path: Path):
    env, log, project = _isolated_codex_installer(tmp_path)
    installer = _installer_bootstrap_copy(tmp_path)
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'#!/bin/sh\necho "codex $*" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable false"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo "not-json"; fi\n'
    )
    codex.chmod(0o755)
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    (runtime / "sentinel").write_text("previous")
    result = subprocess.run([shutil.which("node"), str(installer), "--only", "codex", "--yes", "--project-root", str(project)],
                            env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "did not return valid JSON" in result.stderr
    commands = log.read_text()
    assert "features enable" not in commands and "bridge-stop" not in commands
    assert "uv sync" not in commands and "plugin add" not in commands
    assert (runtime / "sentinel").read_text() == "previous"


@pytest.mark.parametrize("surface", ["cli", "vscode"])
@pytest.mark.parametrize("standalone_present", [False, True])
def test_installer_never_falls_back_to_desktop_for_cli_or_vscode(
    tmp_path: Path, surface: str, standalone_present: bool,
):
    env, log, _ = _write_codex_installer_stubs(
        tmp_path, path_version="0.149.0", desktop_version="0.150.0",
    )
    node = shutil.which("node")
    assert node is not None
    if not standalone_present:
        (tmp_path / "bin/codex").unlink()
    # No ambient client can turn a missing standalone CLI into a real probe.
    env["PATH"] = f"{tmp_path / 'bin'}:/usr/bin:/bin"
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    sentinel = runtime / "previous-release"
    sentinel.write_text("preserve")
    result = subprocess.run(
        [node, str(_installer_bootstrap_copy(tmp_path)), "--yes", "--only", "codex", "--surface", surface,
         "--project-root", str(tmp_path)],
        env=env, text=True, capture_output=True,
    )
    assert result.returncode != 0
    if standalone_present:
        assert "found 0.149.0" in result.stderr
    else:
        assert "Codex CLI not found" in result.stderr
    commands = log.read_text() if log.exists() else ""
    assert "desktop-codex" not in commands
    assert "features enable" not in commands
    assert "plugin add" not in commands
    assert "bridge-stop" not in commands
    assert "uv sync" not in commands
    assert sentinel.read_text() == "preserve"


def test_explicit_record_repair_passes_exact_launcher_scope_and_node_to_readiness(tmp_path: Path):
    env, log, project = _isolated_codex_installer(tmp_path)
    node = shutil.which("node")
    assert node is not None
    profile = tmp_path / "custom-profile"
    profile.mkdir()
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    record = config / "client-executables.json"
    record.write_text('{"version":1,"clients":{"codex":[]}}')
    (config / "client-scopes.json").write_text(json.dumps({"version": 1, "clients": {"codex": {"config_dir": str(profile)}}}))
    package = tmp_path / "npm/node_modules/@openai/codex"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"name":"@openai/codex","bin":{"codex":"codex.js"}}')
    (package / "codex.js").write_text(
        'if (process.argv[2] === "--version") console.log("codex-cli 0.150.0");\n'
        'if (process.argv[2] === "features") console.log("hooks stable true");\n'
    )
    launcher = tmp_path / "npm/codex.cmd"
    launcher.write_text("@echo off\nthis batch script must not execute\n")
    launcher.chmod(0o755)
    env.pop("CODEX_HOME", None)
    result = subprocess.run([node, str(ROOT / "bin/install.mjs"), "--verify", "--force", "--only", "codex",
                             "--client-executable", str(launcher), "--client-config-dir", str(profile),
                             "--project-root", str(project)], env=env, text=True, capture_output=True)
    # Deliberately incomplete package: readiness still runs, but verification
    # must not persist a repair before all native adapter checks succeed.
    assert result.returncode != 0
    commands = log.read_text()
    assert f"--client-executable {launcher}" in commands
    assert f"--client-config-dir {profile}" in commands
    runtime_node = subprocess.run([node, "-p", "process.execPath"], check=True, capture_output=True, text=True).stdout.strip()
    assert f"--client-node-executable {runtime_node}" in commands
    assert "--repair-client-record" in commands
    assert json.loads(record.read_text())["clients"]["codex"] == []


@pytest.mark.parametrize("surface", ["vscode", "cli", "desktop", "unknown"])
def test_installer_hook_approval_directions_match_the_selected_surface(tmp_path: Path, surface: str):
    env, log, project = _isolated_codex_installer(tmp_path)
    readiness = json.loads(CODEX_HOOK_DISCOVERY_JSON)
    readiness["ready"] = False
    readiness["hooks"].update(ready=False, reason="authorization_required", trust_statuses=["untrusted"])
    executable = tmp_path / "bin/dduo-solo-founder"
    executable.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        f"  printf '%s\\n' '{json.dumps(readiness)}'\n"
        "  exit 8\nfi\n"
    )
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "codex", "--surface", surface,
         "--project-root", str(project)],
        env=env, text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "automatic memory is not ready" in result.stderr
    assert "PASS  installation verified" not in result.stdout
    if surface == "vscode":
        assert "Settings > Hooks" not in result.stderr
        assert "/hooks" not in result.stderr
        assert "native approval UI offered by the Codex extension" in result.stderr
        assert "stop support validation" in result.stderr
        assert "graphical lifecycle acceptance are still required" in result.stderr
        assert "Installation checks only; graphical lifecycle acceptance remains unverified" in result.stdout
    elif surface == "cli":
        assert "Settings > Hooks" not in result.stderr
        assert "Open /hooks if this Codex CLI offers it" in result.stderr
    else:
        assert "Settings > Hooks" in result.stderr
        assert "Review and Trust all" in result.stderr


def test_claude_marketplace_points_to_repository_plugin():
    marketplace = json.loads((CLAUDE_ADAPTER / ".claude-plugin/marketplace.json").read_text())
    assert marketplace["name"] == "dduo-solo-founder"
    assert marketplace["plugins"][0]["name"] == "dduo-solo-founder"
    assert marketplace["plugins"][0]["source"] == "./"


def test_checksum_manifest_covers_the_repository_distribution():
    manifest_paths = {
        line.split("  ", 1)[1]
        for line in (ROOT / "checksums.sha256").read_text().splitlines()
        if line
    }
    assert ".coverage" not in manifest_paths
    assert not any(path.endswith(".tsbuildinfo") for path in manifest_paths)
    if shutil.which("git"):
        source_paths = set(
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split("\0")
        )
        source_paths.discard("")
        source_paths.discard("checksums.sha256")
        source_paths = {path for path in source_paths if (ROOT / path).exists()}
        assert manifest_paths == source_paths


def test_git_checkout_preserves_distribution_bytes_across_platforms():
    attributes = (ROOT / ".gitattributes").read_text().splitlines()
    assert "* text=auto eol=lf" in attributes


def test_agent_contract_is_concise_open_and_task_preferring():
    skill = (ROOT / "skills/dduo-solo-founder/SKILL.md").read_text()
    normalized_skill = " ".join(skill.split())
    assert "operating cofounder" in skill
    assert "Use the fewest words that preserve accuracy" in skill
    assert "Invite the user to reason together" in skill
    assert "Lifecycle implementation tools are private" in normalized_skill
    assert "propose_task_change" not in skill
    assert "versioned decision or design document" in normalized_skill
    assert "first reuse or create the relevant Plan" in normalized_skill
    assert "first reuse or create the relevant Epic or Task" in normalized_skill
    assert "completion evidence" in skill
    assert "human title, linked to the exact `url` returned by dDuo" in skill
    assert "Prefer best practices" in skill
    assert "Do not be a yes-man" in skill
    assert "update_project_manual" in skill
    assert "authoritative project host owns one automatic sleep executor" in normalized_skill
    assert "Codex or Claude preference" in normalized_skill
    assert "eight pending turns" in normalized_skill
    assert "Codex Desktop requires quitting/reopening the app after installation/update" in normalized_skill
    assert "VS Code requires reloading the window and a new graphical chat" in normalized_skill
    assert "a CLI requires a new session" in normalized_skill
    assert "fresh chat/session is required after installation" in normalized_skill


def test_install_prompts_require_a_new_session_after_install_or_update():
    english = " ".join((ROOT / "docs/installation.md").read_text().split())
    italian = " ".join((ROOT / "docs/installation.it.md").read_text().split())
    assert "open dduo setup" in english.lower()
    assert "new chat" in english
    assert "**Codex Desktop:** fully quit and reopen the app" in english
    assert "**Codex or Claude Code CLI:** start a new CLI session" in english
    assert "VS Code extension:** reload the VS Code window" in english
    assert "OpenAI" in english
    assert "aprire dduo setup" in italian.lower()
    assert "nuova chat" in italian
    assert "**Codex Desktop:** chiudere completamente e riaprire l'app" in italian
    assert "**CLI Codex o Claude Code:** avviare una nuova sessione CLI" in italian
    assert "per VS Code:** ricaricare la finestra VS Code" in italian
    assert "OpenAI" in italian


def test_readme_quick_starts_are_bilingual_concise_and_release_pinned():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    italian = (ROOT / "README.it.md").read_text(encoding="utf-8")
    normalized_english = " ".join(english.split())
    normalized_italian = " ".join(italian.split())

    for landing in (english, italian):
        language_links = landing.splitlines()[0]
        assert "English" in language_links and "](README.md)" in language_links
        assert "Italiano" in language_links and "](README.it.md)" in language_links
    assert len(english.splitlines()) <= 180
    assert len(italian.splitlines()) <= 180
    assert english.count("\n## ") == italian.count("\n## ")
    assert english.count("\n### ") == italian.count("\n### ")
    for landing in (english, italian):
        short_prompt = landing.split("```text\n", 1)[1].split("```", 1)[0].strip()
        assert len(short_prompt) <= 200
        assert f"https://github.com/gitMarcello/dduo-solo-founder/tree/v{version}" in short_prompt
        assert "instructions-for-the-installing-agent" in landing
    assert f"--branch v{version}" in english
    assert f"--branch v{version}" in italian
    assert 'dduo_install_dir="$(mktemp -d)"' in english
    assert 'dduo_install_dir="$(mktemp -d)"' in italian
    assert '--project-root "$PWD" --yes' in english
    assert '--project-root "$PWD" --yes' in italian
    installation = " ".join((ROOT / "docs/installation.md").read_text().split())
    installation_it = " ".join((ROOT / "docs/installation.it.md").read_text().split())
    assert "temporary release checkout outside the user's project" in installation
    assert "checkout temporaneo della release esterno al progetto" in installation_it
    assert f"/blob/v{version}/docs/remote-teams.md" in english
    assert f"/blob/v{version}/docs/remote-teams.it.md" in italian
    assert english.index("## Start here") < english.index("## What you get")
    assert italian.index("## Inizia qui") < italian.index("## Cosa ottieni")
    assert "enable and verify off-record capture" in normalized_english
    assert "attiva e verifica la modalità off-record" in normalized_italian


def test_distribution_contains_the_complete_apache_2_license():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in license_text
    for section in range(1, 10):
        assert f"   {section}. " in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert len(license_text) > 10_000


def test_onboarding_uses_three_context_selected_paths_and_real_optional_starts():
    english = (ROOT / "README.md").read_text()
    italian = (ROOT / "README.it.md").read_text()
    installation = (ROOT / "docs/installation.md").read_text()
    skill = (ROOT / "skills/dduo-solo-founder/SKILL.md").read_text()
    normalized_skill = " ".join(skill.split())
    manifest = json.loads((CODEX_ADAPTER / ".codex-plugin/plugin.json").read_text())

    assert "There is no separate onboarding wizard" in installation
    assert "invitation in place of the local installation prompt" in " ".join(english.split())
    assert "invito al posto del prompt di installazione locale" in " ".join(italian.split())
    for path in (
        "New local project",
        "Existing remote project",
        "Existing project moving to a VPS",
    ):
        assert path in installation
    assert "Never ask a generic local/VPS/invitation question" in normalized_skill
    assert "at most these three optional starts" in normalized_skill
    assert "Never create sample memories, demo Plans or demo Tasks" in normalized_skill

    prompts = manifest["interface"]["defaultPrompt"]
    assert prompts == [
        "Show what dDuo knows about this project and why",
        "Turn my current priority into a Plan or Task",
        "Open Work and summarize status, risks, and next actions",
    ]
    assert all(len(prompt) <= 128 for prompt in prompts)


def test_authority_transfer_runbooks_use_receipts_and_remote_rebind():
    security = " ".join((ROOT / "SECURITY.md").read_text().split())
    recovery = " ".join((ROOT / "docs/backup-and-recovery.md").read_text().split())

    assert "--new-node-verified" not in security
    assert "remote-transfer-retire --activation-receipt '<RECEIPT>' --yes" in security
    assert "first local-to-VPS move" in recovery
    assert "manager token returned after step 5" in recovery
    assert "`remote-bind --replace-existing`" in recovery
    assert "already connected remotely, use `remote-rebind`" in recovery
    assert "issue fresh member invitations when their endpoint changed" not in recovery


def test_host_ports_are_bound_only_to_loopback():
    compose = (ROOT / "compose.yaml").read_text()
    assert "127.0.0.1:${DDUO_SOLO_FOUNDER_API_PORT:-8765}:8000" in compose
    assert "127.0.0.1:${DDUO_SOLO_FOUNDER_WEB_PORT:-4173}:80" in compose


def test_base_api_receives_authority_secret_for_local_to_remote_transfer():
    compose = (ROOT / "compose.yaml").read_text()
    assert "DDUO_NODE_AUTHORITY_SECRET: ${DDUO_NODE_AUTHORITY_SECRET:-}" in compose


def test_task_index_settings_are_forwarded_to_api_and_worker():
    compose = (ROOT / "compose.yaml").read_text()
    expected = {
        "TASK_EMBEDDING_INDEX_VERSION": "v1",
        "TASK_INDEX_MAX_CHARACTERS": "16000",
        "TASK_INDEX_MAX_UTF8_BYTES": "7500",
        "TASK_RETRIEVAL_SIMILARITY_THRESHOLD": "0.35",
    }
    for name, default in expected.items():
        assert compose.count(f"{name}: ${{{name}:-{default}}}") == 2


def test_minimal_lifecycle_hooks_are_packaged_for_codex_and_claude():
    shared = json.loads((CODEX_ADAPTER / "hooks/hooks.json").read_text())["hooks"]
    claude = json.loads((CLAUDE_ADAPTER / ".claude-plugin/plugin.json").read_text())["hooks"]
    codex_events = {
        "SessionStart": "session-start",
        "UserPromptSubmit": "prompt",
        "Stop": "stop",
    }
    claude_expected = {
        event: f'node "${{CLAUDE_PLUGIN_ROOT}}/bin/agent-plugin-hook.mjs" {argument}'
        for event, argument in codex_events.items()
    }
    for event, argument in codex_events.items():
        handler = shared[event][0]["hooks"][0]
        command = handler["command"]
        assert command == f'node "$PLUGIN_ROOT/hooks/codex-runtime-hook.mjs" {argument}'
        if event in {"SessionStart", "UserPromptSubmit"}:
            assert handler["additionalContextLimit"] == 0
        else:
            assert "additionalContextLimit" not in handler
        if event == "UserPromptSubmit":
            assert handler["timeout"] == 60
        if event == "Stop":
            assert handler["timeout"] == 20
    for event, command in claude_expected.items():
        handler = claude[event][0]["hooks"][0]
        assert handler["command"] == command
        assert "additionalContextLimit" not in handler
        if event == "UserPromptSubmit":
            assert handler["timeout"] == 60
        if event == "Stop":
            assert handler["timeout"] == 20
    assert (CODEX_ADAPTER / "hooks/codex-runtime-hook.sh").is_file()
    assert (CODEX_ADAPTER / "hooks/codex-runtime-hook.mjs").is_file()


def test_codex_and_claude_packages_expose_one_mcp_task_contract_per_client():
    standard_mcp = json.loads((ROOT / "mcp.json").read_text())
    standard_server = standard_mcp["mcpServers"]["dduo-solo-founder"]
    assert standard_server == {
        "type": "stdio",
        "command": "node",
        "args": ["${PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
        "cwd": "${PLUGIN_ROOT}",
    }

    claude_manifest = json.loads((CLAUDE_ADAPTER / ".claude-plugin/plugin.json").read_text())
    claude_server = claude_manifest["mcpServers"]["dduo-solo-founder"]
    assert claude_server == {
        "command": "node",
        "args": ["${CLAUDE_PLUGIN_ROOT}/bin/agent-plugin-mcp.mjs"],
        "env": {
            "DDUO_SOLO_FOUNDER_CLIENT": "claude",
            "DDUO_SOLO_FOUNDER_PROJECT_ROOT": "${CLAUDE_PROJECT_DIR}",
        },
    }
    assert not (ROOT / ".mcp.json").exists()

    # The repository keeps the portable MCP contract and two isolated native
    # adapters. Claude must never auto-discover a second root .mcp.json.
    claude_marketplace = json.loads(
        (CLAUDE_ADAPTER / ".claude-plugin/marketplace.json").read_text()
    )
    codex_manifest = json.loads((CODEX_ADAPTER / ".codex-plugin/plugin.json").read_text())
    assert claude_marketplace["plugins"][0]["source"] == "./"
    assert codex_manifest["mcpServers"] == {
        "dduo-solo-founder": {
            "command": "dduo-solo-founder-mcp-dispatch",
            "env": {"DDUO_SOLO_FOUNDER_CLIENT": "codex"},
        }
    }
    assert "skills" not in codex_manifest

    from dduo_solo_founder.mcp_server import TOOL_MODELS

    expected_task_tools = {
        "list_tasks",
        "get_task",
        "search_tasks",
        "activate_task_context",
        "create_task",
        "update_task",
    }
    assert expected_task_tools.issubset(TOOL_MODELS)


def test_release_versions_are_aligned():
    release = (ROOT / "VERSION").read_text().strip()
    standard = json.loads((ROOT / "plugin.json").read_text())["version"]
    codex = json.loads((CODEX_ADAPTER / ".codex-plugin/plugin.json").read_text())["version"]
    claude = json.loads((CLAUDE_ADAPTER / ".claude-plugin/plugin.json").read_text())["version"]
    frontend = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    python = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    backend_namespace: dict[str, str] = {}
    exec(
        (ROOT / "backend/dduo_solo_founder/__init__.py").read_text(),
        backend_namespace,
    )
    assert standard == release
    assert codex == release or codex.startswith(f"{release}+codex.")
    assert release == claude == frontend
    assert backend_namespace["__version__"] == release
    assert python == release.replace("-alpha.", "a").replace("-beta.", "b")


def test_remote_gateway_image_is_pinned_to_the_reviewed_multiarch_digest():
    compose = (ROOT / "compose.remote-gateway.yaml").read_text()
    assert (
        "image: caddy:2.11.4-alpine@"
        "sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
    ) in compose


def test_installer_help_is_available_before_runtime_initialization():
    if not shutil.which("node"):
        return
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stderr == ""
    assert result.stdout.startswith("Usage: node bin/install.mjs ")


def test_installer_dry_run_does_not_write(tmp_path: Path):
    if not shutil.which("node"):
        return
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--dry-run", "--only", "codex", "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "PASS  distribution checksums"
    assert lines[1].startswith("Preview: install the local runtime and Codex plugin.")
    assert len(lines) == 3
    assert not (tmp_path / ".local/share/dduo-solo-founder").exists()
    assert not (tmp_path / ".agents/plugins/marketplace.json").exists()


def test_installer_rejects_codex_older_than_hook_configuration_minimum(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, _, _ = _write_codex_installer_stubs(tmp_path, path_version="0.149.0")
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "Codex 0.150.0 or newer is required" in result.stderr
    assert not (tmp_path / ".local/share/dduo-solo-founder/runtime").exists()


def test_installer_rejects_a_missing_project_before_mutating_the_runtime(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    missing_project = tmp_path / "missing-project"

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(missing_project),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert f"Project folder is unavailable: {missing_project}" in result.stderr
    assert not (tmp_path / ".local/share/dduo-solo-founder/runtime").exists()
    assert "uv sync" not in log.read_text()
    assert "plugin add" not in log.read_text()


def test_installer_defers_setup_without_failing_an_installed_runtime(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    project_root = tmp_path / "project"
    project_root.mkdir()
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'#!/bin/sh\necho "path-codex $*" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo \'{"installed":[],"available":[]}\'; fi\n'
        f'if [ "$1" = "plugin" ] && [ "$2" = "add" ]; then rmdir "{project_root}"; fi\n'
    )
    codex.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "PASS  installation verified (Codex)" in result.stdout
    assert "SETUP_OPEN_DEFERRED" in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" in result.stdout
    assert (
        "Setup was not opened automatically. "
        "Fully quit and reopen Codex, then open Setup from a new chat "
        "in the same project folder."
    ) in result.stdout
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    assert runtime.exists()
    assert "dduo-solo-founder setup --project-root" not in log.read_text()


def test_installer_defers_when_the_project_disappears_during_setup(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    project_root = tmp_path / "project"
    project_root.mkdir()
    dduo = tmp_path / "bin/dduo-solo-founder"
    dduo.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        f"  printf '%s\\n' '{CODEX_HOOK_DISCOVERY_JSON}'\n"
        "fi\n"
        f'if [ "$1" = "setup" ]; then rmdir "{project_root}"; exit 1; fi\n'
    )
    dduo.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project_root),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "PASS  installation verified (Codex)" in result.stdout
    assert "SETUP_OPEN_DEFERRED" in result.stdout
    assert f"dduo-solo-founder setup --project-root {project_root}" in log.read_text()


def test_installer_does_not_mask_a_setup_command_failure(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    dduo = tmp_path / "bin/dduo-solo-founder"
    dduo.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        f"  printf '%s\\n' '{CODEX_HOOK_DISCOVERY_JSON}'\n"
        "fi\n"
        'if [ "$1" = "setup" ]; then exit 1; fi\n'
    )
    dduo.chmod(0o755)

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "SETUP_OPEN_DEFERRED" not in result.stdout
    assert "PASS  installation verified (Codex)" in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" in result.stdout
    assert (tmp_path / ".local/share/dduo-solo-founder/runtime").exists()
    assert f"dduo-solo-founder setup --project-root {tmp_path}" in log.read_text()


def test_installer_selects_compatible_desktop_codex_over_stale_path_cli(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, desktop = _write_codex_installer_stubs(
        tmp_path,
        path_version="0.149.0",
        desktop_version="0.150.0",
    )
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "PASS  installation verified (Codex)" in result.stdout
    commands = log.read_text()
    assert "desktop-codex features list" in commands
    assert "desktop-codex plugin add dduo-solo-founder@personal" in commands
    assert "path-codex features enable hooks" not in commands
    assert desktop is not None


@pytest.mark.parametrize(
    ("reason", "trust_status", "readiness_exit"),
    [
        ("authorization_required", "untrusted", 0),
        ("authorization_required", "untrusted", 8),
        ("reauthorization_required", "modified", 8),
    ],
)
def test_installer_preserves_pending_native_consent_without_passing_verification(
    tmp_path: Path, reason: str, trust_status: str, readiness_exit: int,
):
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    project = tmp_path / "selected project"
    project.mkdir()
    readiness = json.loads(CODEX_HOOK_DISCOVERY_JSON)
    readiness["ready"] = False
    readiness["hooks"].update(ready=False, reason=reason, trust_statuses=[trust_status])
    executable = tmp_path / "bin/dduo-solo-founder"
    executable.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        f"  printf '%s\\n' '{json.dumps(readiness)}'\n"
        f"  exit {readiness_exit}\n"
        "fi\n"
    )
    command = [
        "node", str(ROOT / "bin/install.mjs"), "--only", "codex",
        "--project-root", str(project), "--no-setup",
    ]
    # Installation and an update both retain valid components for the user's
    # subsequent native review; standalone verification must remain a failure.
    for _attempt in range(2):
        installed = subprocess.run(
            [*command, "--yes"], capture_output=True, text=True, env=env,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        assert "INSTALLED  components verified (Codex)" in installed.stdout
        assert "PASS  installation verified" not in installed.stdout
        assert "Settings > Hooks" in installed.stderr
        assert "Review and Trust all" in installed.stderr
        assert "automatic memory is not ready" in installed.stderr
        assert "CODEX_APP_RESTART_REQUIRED" in installed.stdout
        verified = subprocess.run(
            [*command, "--verify"], capture_output=True, text=True, env=env,
        )
        assert verified.returncode == 1
        assert "FAIL  Codex lifecycle hook authorization" in verified.stdout
        assert "PASS  installation verified" not in verified.stdout
        assert "Settings > Hooks" in verified.stderr
        assert (tmp_path / "plugins/dduo-solo-founder/.codex-plugin/plugin.json").is_file()
    commands = log.read_text()
    assert f"client-readiness --client codex --project-root {project}" in commands
    assert "codex hooks trust" not in commands
    assert "codex hooks authorize" not in commands


@pytest.mark.parametrize(
    ("reason", "hook_count", "events"),
    [
        (
            "hooks_incomplete",
            2,
            ["sessionStart", "userPromptSubmit"],
        ),
        (
            "hooks_disabled",
            3,
            ["sessionStart", "stop", "userPromptSubmit"],
        ),
        (
            "authorized",
            3,
            ["sessionStart", "stop", "userPromptSubmit"],
        ),
    ],
)
def test_installer_rolls_back_when_codex_lifecycle_is_not_runnable(
    tmp_path: Path,
    reason: str,
    hook_count: int,
    events: list[str],
):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    old_plugin = tmp_path / "plugins/dduo-solo-founder"
    old_plugin.mkdir(parents=True)
    old_plugin.joinpath("previous-plugin").write_text("preserve")
    dduo = tmp_path / "bin/dduo-solo-founder"
    readiness = json.dumps(
        {
            "client": "codex",
            "ready": False,
            "authentication": {"ready": True},
            "hooks": {
                "ready": False,
                "reason": reason,
                "hook_count": hook_count,
                "trust_statuses": ["trusted"],
                "events": events,
            },
            "actions": [],
        },
        separators=(",", ":"),
    )
    dduo.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        f"  printf '%s\\n' '{readiness}'\n"
        "fi\n"
    )
    dduo.chmod(0o755)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "FAIL  Codex lifecycle hook discovery (3/3)" in result.stdout
    assert "PASS  installation verified (Codex)" not in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" not in result.stdout
    assert old_plugin.joinpath("previous-plugin").read_text() == "preserve"
    assert not old_plugin.joinpath(".codex-plugin/plugin.json").exists()
    assert not (tmp_path / ".local/share/dduo-solo-founder/runtime").exists()


def test_installer_rolls_back_codex_config_if_legacy_plugin_remains_registered(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'#!/bin/sh\necho "path-codex $*" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        '  echo \'{"installed":[{"pluginId":"opendduo@personal"}],"available":[]}\'\n'
        "fi\n"
    )
    codex.chmod(0o755)
    codex_config = tmp_path / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True)
    original_config = (
        'note = "configurazione è intatta"\r\n\r\n'
        '[plugins."opendduo@personal"]\r\n'
        "enabled = true\r\n\r\n"
        '[plugins."dduo-solo-founder@personal"]\r\n'
        "enabled = true\r\n"
    ).encode()
    codex_config.write_bytes(original_config)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "PASS  installation verified (Codex)" not in result.stdout
    assert "Legacy OpenDduo Codex plugin is still installed." in result.stderr
    assert codex_config.read_bytes() == original_config
    assert not (tmp_path / ".local/share/dduo-solo-founder/runtime").exists()
    assert "path-codex plugin remove opendduo@personal" in log.read_text()
    assert "dduo-solo-founder setup --project-root" not in log.read_text()


def test_installer_warns_but_succeeds_when_post_commit_legacy_cleanup_fails(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    cleanup_started = tmp_path / "legacy-cleanup-started"
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'''#!/bin/sh
echo "path-codex $*" >> "{log}"
if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi
if [ "$1" = "plugin" ] && [ "$2" = "remove" ] && [ "$3" = "opendduo@personal" ]; then
  touch "{cleanup_started}"
fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ] && [ "$3" = "--json" ]; then
  if [ -f "{cleanup_started}" ]; then
    echo '{{"installed":[{{"pluginId":"opendduo@personal"}}],"available":[]}}'
  else
    echo '{{"installed":[],"available":[]}}'
  fi
fi
'''
    )
    codex.chmod(0o755)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "PASS  installation verified (Codex)" in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" in result.stdout
    assert result.stderr.count("WARN  ") == 1
    assert (
        "Installed components verified, but legacy OpenDduo cleanup is incomplete. "
        "Re-run the installer to retry cleanup: "
        "Legacy OpenDduo Codex plugin is still installed."
    ) in result.stderr
    assert "ERROR " not in result.stderr
    # Trigger the simulated vendor failure on the post-commit cleanup action,
    # never on a count of legitimate preflight registration reads.
    assert cleanup_started.exists()
    commands = log.read_text()
    assert commands.index("client-readiness") < commands.index("plugin remove opendduo@personal")
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    assert runtime.exists()
    assert (tmp_path / "plugins/dduo-solo-founder/.codex-plugin/plugin.json").exists()
    installed_codex_manifest = json.loads(
        (tmp_path / "plugins/dduo-solo-founder/.codex-plugin/plugin.json").read_text()
    )
    assert installed_codex_manifest["mcpServers"]["dduo-solo-founder"]["command"] == str(
        runtime / ".venv/bin/dduo-solo-founder-mcp-dispatch"
    )
    assert "dduo-solo-founder setup --project-root" in log.read_text()


def test_installer_continues_other_cleanup_when_retired_updater_cleanup_fails(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    retired_launcher = tmp_path / "bin/dduo-solo-founder-client-update"
    retired_launcher.mkdir()
    legacy_plugin = tmp_path / "plugins/opendduo"
    legacy_plugin.mkdir(parents=True)
    legacy_cache = tmp_path / ".codex/plugins/cache/personal/opendduo/alpha"
    legacy_cache.mkdir(parents=True)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "PASS  installation verified (Codex)" in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" in result.stdout
    assert result.stderr.count("WARN  ") == 1
    assert (
        "Installed components verified, but retired Alpha updater cleanup is incomplete. "
        "Re-run the installer to retry cleanup:"
    ) in result.stderr
    assert "legacy OpenDduo cleanup is incomplete" not in result.stderr
    assert retired_launcher.is_dir()
    assert not legacy_plugin.exists()
    assert not legacy_cache.exists()
    assert "dduo-solo-founder setup --project-root" in log.read_text()


def test_installer_preserves_unowned_retired_paths_and_reports_cleanup(
    tmp_path: Path,
):
    if not shutil.which("node") or os.name == "nt":
        return
    env, _, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    unowned_launcher = tmp_path / "bin/dduo-solo-founder-hook-post-tool"
    unowned_launcher.write_text("#!/bin/sh\necho external\n")
    unowned_launcher.chmod(0o755)
    outside = tmp_path / "outside-usage-guard"
    outside.mkdir()
    outside_policy = outside / "usage-guard.json"
    outside_policy.write_text("external\n")
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    config.joinpath("usage-guard").symlink_to(outside, target_is_directory=True)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "PASS  installation verified (Codex)" in result.stdout
    assert "retired runtime launcher cleanup is incomplete" in result.stderr
    assert "retired usage reserve cleanup is incomplete" in result.stderr
    assert unowned_launcher.read_text() == "#!/bin/sh\necho external\n"
    assert outside_policy.read_text() == "external\n"


def test_installer_warns_and_continues_after_post_verify_finalizer_failure(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("requires non-root POSIX directory permissions")
    env, log, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    runtime.joinpath("old-runtime").write_text("preserve")
    plugin = tmp_path / "plugins/dduo-solo-founder"
    plugin.mkdir(parents=True)
    plugin.joinpath("old-plugin").write_text("preserve")
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'''#!/bin/sh
echo "path-codex $*" >> "{log}"
if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then
  for rollback in "$HOME"/plugins/dduo-solo-founder.*.rollback; do
    if [ -d "$rollback" ]; then chmod 000 "$rollback"; fi
  done
  echo "hooks stable true"
fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
  echo '{{"installed":[],"available":[]}}'
fi
'''
    )
    codex.chmod(0o755)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )
    plugin_rollbacks = list(plugin.parent.glob("dduo-solo-founder.*.rollback"))
    for rollback in plugin_rollbacks:
        rollback.chmod(0o700)

    assert result.returncode == 0
    assert "PASS  installation verified (Codex)" in result.stdout
    assert "CODEX_APP_RESTART_REQUIRED" in result.stdout
    assert result.stderr.count("WARN  ") == 1
    assert (
        "Installed components verified, but Codex installation finalization is incomplete. "
        "Re-run the installer to retry cleanup:"
    ) in result.stderr
    assert len(plugin_rollbacks) == 1
    assert plugin_rollbacks[0].joinpath("old-plugin").read_text() == "preserve"
    assert not list(runtime.parent.glob("runtime.*.rollback"))
    assert (plugin / ".codex-plugin/plugin.json").exists()
    assert "dduo-solo-founder setup --project-root" in log.read_text()


def test_installer_preserves_codex_config_symlink_while_cleaning_legacy_sections(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, _, _ = _write_codex_installer_stubs(tmp_path, path_version="0.150.0")
    stored_config = tmp_path / "config-store/config.toml"
    stored_config.parent.mkdir()
    stored_config.write_text(
        '[plugins."opendduo@personal"]\n'
        "enabled = true\n\n"
        '[plugins."dduo-solo-founder@personal"]\n'
        "enabled = true\n"
    )
    codex_config = tmp_path / ".codex/config.toml"
    codex_config.parent.mkdir()
    codex_config.symlink_to(stored_config)

    subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert codex_config.is_symlink()
    assert codex_config.resolve() == stored_config.resolve()
    parsed = tomllib.loads(stored_config.read_text())
    assert "opendduo@personal" not in parsed["plugins"]
    assert parsed["plugins"]["dduo-solo-founder@personal"]["enabled"] is True


def test_verify_rejects_a_tampered_codex_package_file(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, _, project = _isolated_codex_installer(tmp_path)
    subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    installed_hook = tmp_path / "plugins/dduo-solo-founder/hooks/codex-runtime-hook.sh"
    installed_hook.write_text(installed_hook.read_text() + "\n# damaged after installation\n")

    verification = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert verification.returncode != 0
    assert "FAIL  Codex native plugin package" in verification.stdout


def test_verify_rejects_a_tampered_claude_package_file(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _desktop = _write_codex_installer_stubs(
        tmp_path,
        path_version="0.150.0",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    installed = tmp_path / "claude-installed"
    marketplace = tmp_path / "claude-marketplace"
    claude = tmp_path / "bin/claude"
    claude.write_text(
        f'''#!/bin/sh
echo "claude $*" >> "{log}"
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
  if [ -e "{installed}" ]; then
    if [ "$3" = "--json" ]; then echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}"}}]';
    else echo 'dduo-solo-founder@dduo-solo-founder'; fi
  elif [ "$3" = "--json" ]; then echo '[]'; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then touch "{installed}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "uninstall" ]; then rm -f "{installed}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then
  if [ -e "{marketplace}" ]; then echo '[{{"name":"dduo-solo-founder"}}]'; else echo '[]'; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "add" ]; then touch "{marketplace}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "remove" ]; then rm -f "{marketplace}"; fi
'''
    )
    claude.chmod(0o755)

    subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "claude",
            "--project-root",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    installed_launcher = (
        tmp_path / ".local/share/dduo-solo-founder/claude-plugin/bin/agent-plugin-mcp.mjs"
    )
    installed_launcher.write_text(
        installed_launcher.read_text() + "\n// damaged after installation\n"
    )

    verification = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "claude"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert verification.returncode != 0
    assert "FAIL  Claude native plugin package" in verification.stdout


def test_verify_rejects_a_registered_but_unhealthy_claude_plugin(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, _desktop = _write_codex_installer_stubs(
        tmp_path,
        path_version="0.150.0",
    )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    installed = tmp_path / "claude-installed"
    marketplace = tmp_path / "claude-marketplace"
    unhealthy = tmp_path / "claude-unhealthy"
    outdated = tmp_path / "claude-outdated"
    claude = tmp_path / "bin/claude"
    claude.write_text(
        f'''#!/bin/sh
echo "claude $*" >> "{log}"
if [ "$1" = "plugin" ] && [ "$2" = "list" ] && [ "$3" = "--json" ]; then
  if [ -e "{installed}" ]; then
    if [ -e "{unhealthy}" ]; then
      echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}","errors":["Marketplace failed to load: cache-miss"]}}]'
    elif [ -e "{outdated}" ]; then
      echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"0.1.0-obsolete","errors":[]}}]'
    else
      echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}","errors":[]}}]'
    fi
  else
    echo '[]'
  fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then touch "{installed}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "uninstall" ]; then rm -f "{installed}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then
  if [ -e "{marketplace}" ]; then echo '[{{"name":"dduo-solo-founder"}}]'; else echo '[]'; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "add" ]; then touch "{marketplace}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "remove" ]; then rm -f "{marketplace}"; fi
'''
    )
    claude.chmod(0o755)

    subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "claude",
            "--project-root",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    unhealthy.touch()

    verification = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "claude"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert verification.returncode != 0
    assert "FAIL  Claude plugin" in verification.stdout

    unhealthy.unlink()
    outdated.touch()
    outdated_verification = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "claude"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert outdated_verification.returncode != 0
    assert "FAIL  Claude plugin" in outdated_verification.stdout


def test_installer_creates_and_removes_isolated_distribution(tmp_path: Path):
    if not shutil.which("node"):
        return
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in (
        "uv",
        "codex",
        "claude",
        "docker",
        "dduo-solo-founder",
        "dduo-solo-founder-bridge",
        "dduo-solo-founder-agent",
        "dduo-solo-founder-mcp",
        "dduo-solo-founder-mcp-dispatch",
        "dduo-solo-founder-hook-dispatch",
        "dduo-solo-founder-claude-statusline",
        "dduo-solo-founder-claude-statusline-restore",
        "dduo-solo-founder-hook-session-start",
        "dduo-solo-founder-hook-prompt",
        "dduo-solo-founder-hook-stop",
    ):
        path = binaries / command
        path.write_text(f'#!/bin/sh\necho "{command} $*" >> "{log}"\n')
        path.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(_uv_installer_stub(binaries, log))
    uv.chmod(0o755)
    hook_outputs = {
        "dduo-solo-founder-hook-session-start": '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"session"}}',
        "dduo-solo-founder-hook-prompt": '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"prompt"}}',
        "dduo-solo-founder-hook-stop": '{"continue":true}',
    }
    for command, output in hook_outputs.items():
        path = binaries / command
        path.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf \"%s\\n\" '{output}'\n")
        path.chmod(0o755)
    dispatcher = binaries / "dduo-solo-founder-hook-dispatch"
    dispatcher.write_text(
        '#!/bin/sh\ncat >/dev/null\ncase "$1" in\n'
        '  session-start) printf "%s\\n" \'{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"session"}}\' ;;\n'
        '  prompt) printf "%s\\n" \'{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"prompt"}}\' ;;\n'
        '  *) printf "%s\\n" \'{"continue":true}\' ;;\n'
        "esac\n"
    )
    dispatcher.chmod(0o755)
    claude = binaries / "claude"
    claude_registration = tmp_path / "claude-registration"
    claude_marketplace = tmp_path / "claude-marketplace"
    claude.write_text(
        f'#!/bin/sh\necho "claude $*" >> "{log}"\n'
        'if [ "$1" = "plugin" ] && [ "$3" = "dduo-solo-founder@dduo-solo-founder" ]; then\n'
        f'  if [ "$2" = "install" ]; then touch "{claude_registration}"; fi\n'
        f'  if [ "$2" = "uninstall" ]; then rm -f "{claude_registration}"; fi\n'
        "fi\n"
        'if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ]; then\n'
        f'  if [ "$3" = "add" ]; then touch "{claude_marketplace}"; fi\n'
        f'  if [ "$3" = "remove" ] && [ "$4" = "dduo-solo-founder" ]; then rm -f "{claude_marketplace}"; fi\n'
        "fi\n"
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        f'  if [ ! -f "{claude_registration}" ]; then echo "[]"; exit 0; fi\n'
        '  if [ "$3" = "--json" ]; then\n'
        f'    echo \'[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}"}}]\'\n'
        "  else\n"
        '    echo "dduo-solo-founder@dduo-solo-founder"\n'
        "  fi\n"
        "fi\n"
        'if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then\n'
        f'  if [ ! -f "{claude_marketplace}" ]; then echo "[]"; exit 0; fi\n'
        '  echo \'[{"name":"dduo-solo-founder"}]\'\n'
        "fi\n"
    )
    claude.chmod(0o755)
    codex = binaries / "codex"
    codex.write_text(
        f'#!/bin/sh\necho "codex $*" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then\n'
        '  echo "hooks stable true"\n'
        "fi\n"
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then\n'
        '  echo \'{"installed":[],"available":[]}\'\n'
        "fi\n"
    )
    codex.chmod(0o755)
    dduo = binaries / "dduo-solo-founder"
    dduo.write_text(
        f'#!/bin/sh\necho "dduo-solo-founder $*" >> "{log}"\n'
        'if [ "$1" = "client-readiness" ]; then\n'
        '  client="$3"\n'
        '  if [ "$DDUO_TEST_READINESS" = "auth-required" ]; then\n'
        '    printf \'{"client":"%s","ready":false,"authentication":{"ready":false,"reason":"login_required","login_command":"%s login"},"hooks":null,"actions":[]}\\n\' "$client" "$client"\n'
        '  elif [ "$DDUO_TEST_READINESS" = "hooks-required" ]; then\n'
        '    printf \'{"client":"codex","ready":false,"authentication":{"ready":true,"reason":"authenticated","login_command":"codex login"},"hooks":{"ready":false,"reason":"authorization_required","hook_count":3,"events":["sessionStart","stop","userPromptSubmit"]},"actions":[]}\\n\'\n'
        "  else\n"
        '    if [ "$client" = "codex" ]; then\n'
        '      printf \'{"client":"codex","ready":true,"authentication":{"ready":true,"reason":"authenticated","login_command":"codex login"},"hooks":{"ready":true,"reason":"authorized","hook_count":3,"events":["sessionStart","stop","userPromptSubmit"]},"actions":[]}\\n\'\n'
        "    else\n"
        '      printf \'{"client":"%s","ready":true,"authentication":{"ready":true,"reason":"authenticated","login_command":"%s login"},"hooks":null,"actions":[]}\\n\' "$client" "$client"\n'
        "    fi\n"
        "  fi\n"
        "fi\n"
    )
    dduo.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(codex)
    codex_config = tmp_path / ".codex/config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text(
        'model = "test"\n'
        "banner = '''\n"
        '[plugins."opendduo@personal"]\n'
        '[hooks.state."opendduo@personal:hooks/hooks.json:fake:0:0"]\n'
        "'''\n"
        '# [plugins."opendduo@personal"] is documentation, not a table\n\n'
        '[plugins."opendduo@personal"]\n'
        "# preserve this standalone comment\n"
        "enabled = true\n\n"
        '[plugins."opendduo@personal".mcp_servers.opendduo.tools.health]\n'
        'approval_mode = "approve"\n\n'
        '[plugins."opendduo@personal-copy"]\n'
        "enabled = true\n\n"
        '[plugins."dduo-solo-founder@personal"]\n'
        "enabled = true\n\n"
        '[hooks.state."opendduo@personal:hooks/hooks.json:session_start:0:0"]\n'
        'trusted_hash = "legacy"\n\n'
        '[hooks.state."dduo-solo-founder@personal:hooks/hooks.json:session_start:0:0"]\n'
        'trusted_hash = "current"\n'
    )
    legacy_cache = tmp_path / ".codex/plugins/cache/personal/opendduo/0.1.0-alpha.4"
    legacy_cache.mkdir(parents=True)
    legacy_cache.joinpath("plugin.json").write_text("legacy")
    legacy_plugin = tmp_path / "plugins/opendduo"
    legacy_plugin.mkdir(parents=True)
    legacy_plugin.joinpath("plugin.json").write_text("legacy")
    marketplace_path = tmp_path / ".agents/plugins/marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "personal",
                "interface": {"displayName": "Personal"},
                "plugins": [{"name": "opendduo", "source": {"source": "local", "path": "legacy"}}],
            }
        )
    )
    retired_policy = tmp_path / ".config/dduo-solo-founder/usage-guard/usage-guard.json"
    legacy_statusline_state = retired_policy.with_name("claude-statusline.json")
    retired_policy.parent.mkdir(parents=True)
    retired_policy.write_text('{"version": 1, "providers": {}}\n')
    legacy_statusline_state.write_text('{"version": 2, "projects": {}}\n')
    retired_post_tool = tmp_path / "bin/dduo-solo-founder-hook-post-tool"
    retired_post_tool.write_text("#!/bin/sh\n# dduo-runtime-shim-v1\nexit 127\n")
    retired_post_tool.chmod(0o755)

    installation = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert installation.stdout.strip().splitlines() == [
        "Installing dDuo Solo Founder...",
        "PASS  distribution checksums",
        "PASS  installation verified (Claude + Codex)",
        "CODEX_APP_RESTART_REQUIRED",
        "SESSION_RELOAD_REQUIRED",
        "Setup opened. Fully quit and reopen Codex; for Claude, open a new session in the same project folder.",
    ]

    verification = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--verify", "--only", "claude"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert verification.stdout.strip().splitlines() == [
        "PASS  installation verified (Claude)",
    ]

    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    claude_plugin = tmp_path / ".local/share/dduo-solo-founder/claude-plugin"
    marketplace = json.loads(marketplace_path.read_text())
    assert (runtime / "compose.yaml").exists()
    assert sorted(path.name for path in claude_plugin.iterdir()) == [
        ".claude-plugin",
        "LICENSE",
        "bin",
        "skills",
    ]
    assert (claude_plugin / ".claude-plugin/plugin.json").is_file()
    assert (claude_plugin / "skills/dduo-solo-founder/SKILL.md").is_file()
    assert (claude_plugin / "bin/agent-plugin-mcp.mjs").is_file()
    assert not (claude_plugin / "plugin.json").exists()
    assert not (claude_plugin / "mcp.json").exists()
    assert not (claude_plugin / ".codex-plugin").exists()
    assert (tmp_path / ".config/dduo-solo-founder/runtime-path").read_text().strip() == str(runtime)
    assert (tmp_path / ".config/dduo-solo-founder/hook-runtime-bin").read_text().strip() == str(
        runtime / ".venv/bin"
    )
    assert not retired_policy.exists()
    assert not retired_post_tool.exists()
    assert legacy_statusline_state.exists()
    assert marketplace["plugins"][0]["name"] == "dduo-solo-founder"
    assert not legacy_cache.exists()
    assert not legacy_plugin.exists()
    cleaned_codex_config = codex_config.read_text()
    parsed_codex_config = tomllib.loads(cleaned_codex_config)
    assert '[plugins."opendduo@personal"]' in parsed_codex_config["banner"]
    assert '[hooks.state."opendduo@personal:' in parsed_codex_config["banner"]
    assert '# [plugins."opendduo@personal"] is documentation' in cleaned_codex_config
    assert "# preserve this standalone comment" in cleaned_codex_config
    assert "opendduo@personal" not in parsed_codex_config["plugins"]
    assert not any(
        key.startswith("opendduo@personal:") for key in parsed_codex_config["hooks"]["state"]
    )
    assert '[plugins."opendduo@personal-copy"]' in cleaned_codex_config
    assert '[plugins."dduo-solo-founder@personal"]' in cleaned_codex_config
    assert '[hooks.state."dduo-solo-founder@personal:' in cleaned_codex_config
    assert parsed_codex_config["plugins"]["opendduo@personal-copy"]["enabled"] is True
    assert parsed_codex_config["plugins"]["dduo-solo-founder@personal"]["enabled"] is True
    assert (tmp_path / "plugins/dduo-solo-founder/.codex-plugin/plugin.json").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/plugin.json").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/mcp.json").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/.claude-plugin").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/frontend").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/compose.yaml").exists()
    assert not (tmp_path / "plugins/dduo-solo-founder/bin").exists()
    assert not any(
        path.name == "build" or path.name.endswith(".egg-info") for path in runtime.rglob("*")
    )
    plugin_root = tmp_path / "plugins/dduo-solo-founder"
    for event, expected in (
        (
            "session-start",
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "session",
                }
            },
        ),
        (
            "prompt",
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "prompt",
                }
            },
        ),
        ("stop", {"continue": True}),
    ):
        hook = subprocess.run(
            ["/bin/sh", str(plugin_root / "hooks/codex-runtime-hook.sh"), event],
            input="{}",
            check=True,
            capture_output=True,
            text=True,
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "PLUGIN_ROOT": str(plugin_root)},
        )
        payload = json.loads(hook.stdout)
        assert payload == expected
        assert "additional_context" not in payload
        assert hook.stderr == ""
    commands = log.read_text()
    assert "codex --version" in commands
    assert f"claude plugin marketplace add {claude_plugin}" in commands
    assert f"claude plugin marketplace add {runtime}" not in commands
    assert "claude plugin marketplace add example-owner/dduo-solo-founder" not in commands
    assert "claude plugin install dduo-solo-founder@dduo-solo-founder" in commands
    assert "codex features list" in commands
    assert "codex features list" in commands
    assert "codex plugin add dduo-solo-founder@personal" in commands
    assert "codex plugin remove opendduo@personal" in commands
    assert "codex plugin list --json" in commands
    assert "claude plugin uninstall opendduo@opendduo" in commands
    assert "claude plugin marketplace remove opendduo" in commands
    assert "claude plugin list --json" in commands
    assert "claude plugin marketplace list --json" in commands
    assert f"dduo-solo-founder client-readiness --client codex --project-root {ROOT}" in commands
    assert "dduo-solo-founder setup --project-root" in commands

    subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--uninstall", "--yes"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert not runtime.exists()
    assert not claude_plugin.exists()
    assert not (tmp_path / "plugins/dduo-solo-founder").exists()
    assert "dduo-solo-founder bridge-stop" in log.read_text()
    assert "dduo-solo-founder backup all" not in log.read_text()
    assert "dduo-solo-founder-claude-statusline-restore " in log.read_text()

    log.write_text("")
    targeted = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "claude"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "PASS  installation verified (Claude)" in targeted.stdout
    assert not (tmp_path / "plugins/dduo-solo-founder").exists()
    assert "codex plugin add" not in log.read_text()
    assert "codex plugin remove opendduo@personal" not in log.read_text()


def test_installer_backs_up_each_configured_project_with_its_exact_root(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, project = _isolated_codex_installer(tmp_path)
    command = [
        "node",
        str(ROOT / "bin/install.mjs"),
        "--yes",
        "--only",
        "codex",
        "--project-root",
        str(project),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, env=env)

    project_id = "55555555-5555-5555-5555-555555555555"
    config = tmp_path / ".config/dduo-solo-founder"
    (config / "projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {project_id: {"root_path": str(project)}},
            }
        )
    )
    (config / "backups.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {project_id: {"destination": str(tmp_path / "backups")}},
            }
        )
    )
    docker = tmp_path / "bin/docker"
    docker.write_text(
        f'#!/bin/sh\necho "docker $*" >> "{log}"\n'
        'if [ "$1" = "volume" ] && [ "$2" = "inspect" ]; then exit 1; fi\n'
    )
    docker.chmod(0o755)
    log.write_text("")

    subprocess.run(command, check=True, capture_output=True, text=True, env=env)

    commands = log.read_text()
    assert (
        f"dduo-solo-founder backup create --trigger update --project-root {project}"
    ) in commands
    assert "dduo-solo-founder backup all" not in commands


def test_beta_installer_migrates_alpha_secrets_once_per_project_and_retires_updater_state(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    env, _log, project = _isolated_codex_installer(tmp_path)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    project_ids = [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    roots = []
    for index, project_id in enumerate(project_ids):
        root = tmp_path / f"registered-{index}"
        root.mkdir()
        roots.append(root)
    (config / "projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    project_id: {"root_path": str(root)}
                    for project_id, root in zip(project_ids, roots, strict=True)
                },
            }
        )
    )
    legacy = b"OPENAI_API_KEY=legacy-key\nDDUO_AUTH_SIGNING_SECRET=legacy-auth\nFOREIGN=ignored\n"
    (config / "env").write_bytes(legacy)
    existing = config / "project-secrets" / project_ids[1] / "dduo.env"
    existing.parent.mkdir(parents=True)
    existing.write_text("OPENAI_API_KEY=project-wins\nDDUO_SESSION_SECRET=existing-session\n")
    existing.chmod(0o600)

    obsolete = [
        config / "update-queue/pending.json",
        config / "update-trust.json",
        config / "session-pins/pin.json",
        config / "client-update-state.json",
        tmp_path / ".local/share/dduo-solo-founder/client-releases/old/release.json",
        tmp_path / ".local/share/dduo-solo-founder/client-current.json",
        tmp_path / ".local/share/dduo-solo-founder/client-previous.json",
        tmp_path / "bin/dduo-solo-founder-client-update",
    ]
    for path in obsolete:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("obsolete")

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "PASS  installation verified (Codex)" in result.stdout
    assert not (config / "env").exists()
    assert (config / "env.alpha-retired").read_bytes() == legacy
    assert not (config / "env.alpha-migrating").exists()
    first = config / "project-secrets" / project_ids[0] / "dduo.env"
    assert first.read_text() == (
        "DDUO_AUTH_SIGNING_SECRET=legacy-auth\nOPENAI_API_KEY=legacy-key\n"
    )
    assert existing.read_text() == (
        "DDUO_AUTH_SIGNING_SECRET=legacy-auth\n"
        "DDUO_SESSION_SECRET=existing-session\n"
        "OPENAI_API_KEY=project-wins\n"
    )
    marker = json.loads((config / "legacy-secrets-migrated-v1.json").read_text())
    assert marker["projects"] == sorted(project_ids)
    assert marker["source_sha256"] == sha256(legacy).hexdigest()
    if os.name != "nt":
        assert first.stat().st_mode & 0o777 == 0o600
        assert existing.stat().st_mode & 0o777 == 0o600
    assert all(not path.exists() for path in obsolete)


def test_beta_installer_rolls_secret_migration_back_byte_for_byte(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, log, project = _isolated_codex_installer(tmp_path)
    codex = tmp_path / "bin/codex"
    activated = tmp_path / "codex-activated"
    codex.write_text(
        f'''#!/bin/sh
echo "codex $*" >> "{log}"
if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then
  if [ -e "{activated}" ]; then echo "hooks stable false"; else echo "hooks stable true"; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "add" ]; then touch "{activated}"; fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo '{{"installed":[],"available":[]}}'; fi
'''
    )
    codex.chmod(0o755)
    env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(codex)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    project_id = "33333333-3333-3333-3333-333333333333"
    (config / "projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {project_id: {"root_path": str(project)}},
            }
        )
    )
    legacy = b"# exact alpha bytes\nOPENAI_API_KEY=legacy\n"
    (config / "env").write_bytes(legacy)
    destination = config / "project-secrets" / project_id / "dduo.env"
    destination.parent.mkdir(parents=True)
    previous = b"OPENAI_API_KEY=existing\n"
    destination.write_bytes(previous)
    destination.chmod(0o600)

    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert (config / "env").read_bytes() == legacy
    assert destination.read_bytes() == previous
    assert destination.stat().st_mode & 0o777 == 0o600
    assert not (config / "env.alpha-retired").exists()
    assert not (config / "env.alpha-migrating").exists()
    assert not (config / "legacy-secrets-migrated-v1.json").exists()


@pytest.mark.parametrize("case", ["registry", "registry-symlink", "slug-collision", "symlink"])
def test_beta_installer_rejects_unsafe_alpha_secret_migrations_without_mutation(
    tmp_path: Path,
    case: str,
):
    if not shutil.which("node"):
        return
    home = tmp_path / case
    home.mkdir()
    env, _log, project = _isolated_codex_installer(home)
    config = home / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    legacy = config / "env"
    registry_target = None
    if case == "registry":
        (config / "projects.json").write_text("not-json")
        legacy.write_bytes(b"OPENAI_API_KEY=keep\n")
    elif case == "registry-symlink":
        registry_target = home / "outside-projects.json"
        registry_target.write_text(
            json.dumps(
                {
                    "version": 1,
                    "projects": {"p1": {"root_path": str(project)}},
                }
            )
        )
        (config / "projects.json").symlink_to(registry_target)
        legacy.write_bytes(b"OPENAI_API_KEY=keep\n")
    elif case == "slug-collision":
        roots = [home / "one", home / "two"]
        for root in roots:
            root.mkdir()
        canonical = "44444444-4444-4444-4444-444444444444"
        compact = canonical.replace("-", "")
        (config / "projects.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "projects": {
                        canonical: {"root_path": str(roots[0])},
                        compact: {"root_path": str(roots[1])},
                    },
                }
            )
        )
        legacy.write_bytes(b"OPENAI_API_KEY=keep\n")
    else:
        (config / "projects.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "projects": {"p1": {"root_path": str(project)}},
                }
            )
        )
        target = home / "outside-env"
        target.write_bytes(b"OPENAI_API_KEY=keep\n")
        legacy.symlink_to(target)

    original = legacy.read_bytes()
    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert legacy.read_bytes() == original
    assert not (config / "env.alpha-retired").exists()
    assert not (config / "legacy-secrets-migrated-v1.json").exists()
    assert not (config / "project-secrets").exists()
    if registry_target is not None:
        assert json.loads(registry_target.read_text())["projects"]["p1"]["root_path"] == str(
            project
        )


def test_beta_installer_lock_is_owner_aware_and_released_on_early_exit(tmp_path: Path):
    if not shutil.which("node"):
        return
    env, _log, project = _isolated_codex_installer(tmp_path)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    lock = config / "installer.lock"
    lock.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": os.getpid(),
                "nonce": "live-owner",
                "started_at": "2026-08-30T00:00:00.000Z",
            }
        )
    )
    live = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--project-root", str(project)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert live.returncode != 0 and "installation is running" in live.stderr
    assert lock.exists()

    lock.write_text("{}")
    fresh = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--project-root", str(project)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert fresh.returncode != 0 and "acquiring the installer lock" in fresh.stderr
    assert lock.exists()

    old = time.time() - 10
    os.utime(lock, (old, old))
    no_confirmation = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--project-root", str(project)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert no_confirmation.returncode == 3
    assert not lock.exists()


def test_beta_installer_preserves_both_alpha_files_when_a_writer_races_migration(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    env, _log, project = _isolated_codex_installer(tmp_path)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    projects = {}
    for index in range(400):
        root = tmp_path / f"registered-{index}"
        root.mkdir()
        projects[f"project-{index}"] = {"root_path": str(root)}
    (config / "projects.json").write_text(json.dumps({"version": 1, "projects": projects}))
    old_bytes = b"OPENAI_API_KEY=old-alpha\n"
    new_bytes = b"OPENAI_API_KEY=new-alpha\n"
    active = config / "env"
    staging = config / "env.alpha-migrating"
    active.write_bytes(old_bytes)
    writer_done = threading.Event()

    def alpha_writer():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if staging.exists():
                active.write_bytes(new_bytes)
                writer_done.set()
                return
            time.sleep(0.001)

    writer = threading.Thread(target=alpha_writer, daemon=True)
    writer.start()
    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    writer.join(timeout=2)
    assert writer_done.is_set()
    assert result.returncode != 0
    assert active.read_bytes() == new_bytes
    frozen = staging if staging.exists() else config / "env.alpha-retired"
    assert frozen.read_bytes() == old_bytes
    assert not (config / "legacy-secrets-migrated-v1.json").exists()
    assert not list((config / "project-secrets").rglob("dduo.env"))


def test_beta_installer_rejects_alpha_writer_after_retirement_before_commit(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    env, log, project = _isolated_codex_installer(tmp_path)
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    project_id = "55555555-5555-5555-5555-555555555555"
    (config / "projects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {project_id: {"root_path": str(project)}},
            }
        )
    )
    old_bytes = b"OPENAI_API_KEY=old-alpha\n"
    new_bytes = b"OPENAI_API_KEY=late-alpha\n"
    active = config / "env"
    retired = config / "env.alpha-retired"
    active.write_bytes(old_bytes)

    # Keep verification open after retirement so the uncooperative Alpha
    # writer deterministically reaches the final pre-commit check.
    codex = tmp_path / "bin/codex"
    codex.write_text(
        f'''#!/bin/sh
echo "codex $*" >> "{log}"
if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then sleep 1; echo '{{"installed":[],"available":[]}}'; fi
'''
    )
    codex.chmod(0o755)
    writer_done = threading.Event()

    def late_alpha_writer():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if retired.exists():
                active.write_bytes(new_bytes)
                writer_done.set()
                return
            time.sleep(0.001)

    writer = threading.Thread(target=late_alpha_writer, daemon=True)
    writer.start()
    result = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--yes",
            "--only",
            "codex",
            "--project-root",
            str(project),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    writer.join(timeout=2)

    assert writer_done.is_set()
    assert result.returncode != 0
    assert active.read_bytes() == new_bytes
    assert retired.read_bytes() == old_bytes
    assert not (config / "legacy-secrets-migrated-v1.json").exists()
    assert not list((config / "project-secrets").rglob("dduo.env"))


@pytest.mark.parametrize("state_directory", ["client-telemetry", "usage-guard"])
def test_uninstall_restores_claude_statusline_through_runtime_pointer_without_path(
    tmp_path: Path,
    state_directory: str,
):
    node = shutil.which("node")
    if not node:
        return
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    runtime.joinpath("sentinel").write_text("installed")
    config = tmp_path / ".config/dduo-solo-founder"
    runtime_bin = runtime / ".venv/bin"
    runtime_bin.mkdir(parents=True)
    config.mkdir(parents=True)
    config.joinpath("runtime-path").write_text(f"{runtime}\n")
    hook_pointer = config / "hook-runtime-bin"
    hook_pointer.write_text(f"{runtime_bin}\n")
    hook_pointer.chmod(0o600)
    state = config / state_directory / "claude-statusline.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"version": 2, "projects": {}}\n')
    retired_policy = config / "usage-guard/usage-guard.json"
    retired_policy.parent.mkdir(parents=True, exist_ok=True)
    retired_policy.write_text('{"version": 1, "providers": {}}\n')
    restored = tmp_path / "statusline-restored"
    restore = runtime_bin / "dduo-solo-founder-claude-statusline-restore"
    restore.write_text(f'#!/bin/sh\ntouch "{restored}"\nrm -f "{state}"\n')
    restore.chmod(0o755)
    launcher_bin = tmp_path / "bin"
    launcher_bin.mkdir()
    uv = launcher_bin / "uv"
    uv.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "tool" ] && [ "$2" = "dir" ] && [ "$3" = "--bin" ]; then\n'
        f'  printf "%s\\n" "{launcher_bin}"\n'
        "fi\n"
    )
    uv.chmod(0o755)
    retired_post_tool = launcher_bin / "dduo-solo-founder-hook-post-tool"
    retired_post_tool.write_text("#!/bin/sh\n# dduo-runtime-shim-v1\nexit 127\n")
    retired_post_tool.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "PATH": f"{launcher_bin}:/usr/bin:/bin",
            "DDUO_SOLO_FOUNDER_CODEX_DESKTOP": str(tmp_path / "missing-codex"),
        }
    )

    result = subprocess.run(
        [node, str(ROOT / "bin/install.mjs"), "--uninstall", "--yes"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert restored.exists()
    assert not retired_policy.exists()
    assert not retired_post_tool.exists()
    assert not runtime.exists()


@pytest.mark.parametrize("state_directory", ["client-telemetry", "usage-guard"])
def test_uninstall_aborts_before_runtime_removal_when_claude_restore_is_unavailable(
    tmp_path: Path,
    state_directory: str,
):
    node = shutil.which("node")
    if not node:
        return
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    runtime.joinpath("sentinel").write_text("installed")
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    config.joinpath("runtime-path").write_text(f"{runtime}\n")
    hook_pointer = config / "hook-runtime-bin"
    hook_pointer.write_text(f"{runtime / '.venv/bin'}\n")
    hook_pointer.chmod(0o600)
    state = config / state_directory / "claude-statusline.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"version": 2, "projects": {}}\n')
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "DDUO_SOLO_FOUNDER_CODEX_DESKTOP": str(tmp_path / "missing-codex"),
        }
    )

    result = subprocess.run(
        [node, str(ROOT / "bin/install.mjs"), "--uninstall", "--yes"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "restore command is unavailable" in result.stderr
    assert runtime.joinpath("sentinel").read_text() == "installed"
    assert config.joinpath("runtime-path").exists()
    assert config.joinpath("hook-runtime-bin").exists()


def test_installer_rolls_back_runtime_and_claude_as_one_transaction(tmp_path: Path):
    if not shutil.which("node"):
        return
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in ("uv", *RUNTIME_EXECUTABLES):
        executable = binaries / command
        executable.write_text(f'#!/bin/sh\necho "{command} $*" >> "{log}"\n')
        executable.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(_uv_installer_stub(binaries, log))
    uv.chmod(0o755)
    attempts = tmp_path / "claude-install-attempts"
    claude = binaries / "claude"
    claude.write_text(
        f'''#!/bin/sh
echo "claude $*" >> "{log}"
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
  if [ "$3" = "--json" ]; then echo '[{{"id":"dduo-solo-founder@dduo-solo-founder","enabled":true,"version":"{CLAUDE_PLUGIN_VERSION}"}}]';
  else echo 'dduo-solo-founder@dduo-solo-founder'; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ] && [ "$3" = "list" ]; then
  echo '[{{"name":"dduo-solo-founder"}}]'
fi
if [ "$1" = "plugin" ] && [ "$2" = "install" ]; then
  if [ ! -e "{attempts}" ]; then touch "{attempts}"; exit 9; fi
fi
'''
    )
    claude.chmod(0o755)
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    runtime.joinpath("old-release").write_text("preserve")
    claude_plugin = tmp_path / ".local/share/dduo-solo-founder/claude-plugin"
    claude_plugin.mkdir(parents=True)
    claude_plugin.joinpath("old-projection").write_text("preserve")
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    runtime_pointer = config / "runtime-path"
    hook_pointer = config / "hook-runtime-bin"
    runtime_pointer.write_text(f"{runtime}\n")
    hook_pointer.write_text("/old/hooks\n")
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "claude"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "rollback was incomplete" not in result.stderr
    assert runtime.joinpath("old-release").read_text() == "preserve"
    assert claude_plugin.joinpath("old-projection").read_text() == "preserve"
    assert runtime_pointer.read_text() == f"{runtime}\n"
    assert hook_pointer.read_text() == "/old/hooks\n"
    assert not list(runtime.parent.glob("runtime.*.rollback"))
    assert not list(runtime.parent.glob("claude-plugin.*.rollback"))
    commands = log.read_text()
    assert commands.count("claude plugin install") == 2


def test_installer_restores_codex_and_legacy_plugin_after_verification_failure(
    tmp_path: Path,
):
    if not shutil.which("node"):
        return
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in ("uv", *RUNTIME_EXECUTABLES):
        executable = binaries / command
        executable.write_text(f'#!/bin/sh\necho "{command} $*" >> "{log}"\n')
        executable.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(_uv_installer_stub(binaries, log))
    uv.chmod(0o755)
    activated = tmp_path / "codex-new-plugin-activated"
    legacy_active = tmp_path / "opendduo-active"
    legacy_active.touch()
    codex = binaries / "codex"
    codex.write_text(
        f'''#!/bin/sh
echo "codex $*" >> "{log}"
if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi
if [ "$1" = "features" ] && [ "$2" = "list" ]; then
  if [ -e "{activated}" ]; then echo "hooks stable false";
  else echo "hooks stable true"; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "add" ]; then
  if [ "$3" = "opendduo@personal" ]; then touch "{legacy_active}";
  else touch "{activated}"; fi
fi
if [ "$1" = "plugin" ] && [ "$2" = "remove" ] && [ "$3" = "opendduo@personal" ]; then
  rm -f "{legacy_active}"
fi
if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then
  if [ -e "{legacy_active}" ]; then
    echo '{{"installed":[{{"pluginId":"opendduo@personal"}},{{"name":"dduo-solo-founder"}}],"available":[]}}'
  else echo '{{"installed":[{{"name":"dduo-solo-founder"}}],"available":[]}}'; fi
fi
'''
    )
    codex.chmod(0o755)
    runtime = tmp_path / ".local/share/dduo-solo-founder/runtime"
    runtime.mkdir(parents=True)
    runtime.joinpath("old-release").write_text("preserve")
    config = tmp_path / ".config/dduo-solo-founder"
    config.mkdir(parents=True)
    runtime_pointer = config / "runtime-path"
    hook_pointer = config / "hook-runtime-bin"
    runtime_pointer.write_text(f"{runtime}\n")
    hook_pointer.write_text("/old/hooks\n")
    plugin = tmp_path / "plugins/dduo-solo-founder"
    plugin.mkdir(parents=True)
    plugin.joinpath("old-plugin").write_text("preserve")
    marketplace = tmp_path / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace_before = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": "dduo-solo-founder",
                "source": {"source": "local", "path": "./plugins/dduo-solo-founder"},
            }
        ],
    }
    marketplace.write_text(json.dumps(marketplace_before))
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(codex)

    result = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "rollback was incomplete" not in result.stderr
    assert runtime.joinpath("old-release").read_text() == "preserve"
    assert runtime_pointer.read_text() == f"{runtime}\n"
    assert hook_pointer.read_text() == "/old/hooks\n"
    assert plugin.joinpath("old-plugin").read_text() == "preserve"
    assert json.loads(marketplace.read_text()) == marketplace_before
    assert legacy_active.exists()
    assert not list(plugin.parent.glob("dduo-solo-founder.*.rollback"))
    assert not list(runtime.parent.glob("runtime.*.rollback"))
    commands = log.read_text()
    assert "codex plugin remove opendduo@personal" in commands
    assert "codex plugin add opendduo@personal" in commands
    # The registration snapshot says the former package source was not
    # natively registered. Rollback restores only registrations it observed;
    # source files alone must not induce a new plugin activation.
    assert commands.count("codex plugin add") == 2


def test_installer_rebuilds_every_project_stopped_for_an_upgrade_snapshot(tmp_path: Path):
    if not shutil.which("node"):
        return
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in (
        "uv",
        "codex",
        "dduo-solo-founder",
        "dduo-solo-founder-bridge",
        "dduo-solo-founder-agent",
        "dduo-solo-founder-mcp",
        "dduo-solo-founder-mcp-dispatch",
        "dduo-solo-founder-hook-dispatch",
        "dduo-solo-founder-claude-statusline",
        "dduo-solo-founder-claude-statusline-restore",
        "dduo-solo-founder-hook-session-start",
        "dduo-solo-founder-hook-prompt",
        "dduo-solo-founder-hook-stop",
    ):
        executable = binaries / command
        executable.write_text(
            f'#!/bin/sh\necho "{command} $*" >> "{log}"\n'
            + (
                'if [ "$1" = "client-readiness" ]; then\n'
                f"  printf '%s\\n' '{CODEX_HOOK_DISCOVERY_JSON}'\n"
                "fi\n"
                if command == "dduo-solo-founder"
                else ""
            )
        )
        executable.chmod(0o755)
    uv = binaries / "uv"
    uv.write_text(_uv_installer_stub(binaries, log))
    uv.chmod(0o755)
    codex = binaries / "codex"
    fail_adapter = tmp_path / "fail-adapter-after-restart"
    codex.write_text(
        f'#!/bin/sh\necho "codex $*" >> "{log}"\n'
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.150.0"; fi\n'
        'if [ "$1" = "features" ] && [ "$2" = "list" ]; then echo "hooks stable true"; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then echo \'{"installed":[],"available":[]}\'; fi\n'
        f'if [ "$1 $2" = "plugin add" ] && [ -e "{fail_adapter}" ]; then exit 27; fi\n'
    )
    codex.chmod(0o755)
    docker = binaries / "docker"
    fail_snapshot = tmp_path / "fail-upgrade-snapshot"
    docker.write_text(
        f'''#!/bin/sh
echo "docker $*" >> "{log}"
if [ "$1" = "run" ]; then
  if [ -e "{fail_snapshot}" ]; then exit 19; fi
  for argument in "$@"; do
    case "$argument" in
      *:/snapshot) snapshot="${{argument%:/snapshot}}" ;;
    esac
  done
  case "$*" in
    *postgres-data.tar.gz*) printf x > "$snapshot/postgres-data.tar.gz" ;;
    *qdrant-data.tar.gz*) printf x > "$snapshot/qdrant-data.tar.gz" ;;
  esac
fi
'''
    )
    docker.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    env["DDUO_SOLO_FOUNDER_CODEX_DESKTOP"] = str(codex)
    subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    project_root = tmp_path / "OtherApp"
    project_id = "other-app-project"
    project_root.joinpath(".dduo-solo-founder").mkdir(parents=True)
    project_root.joinpath(".dduo-solo-founder/project.toml").write_text(
        f'id = "{project_id}"\nname = "OtherApp"\napi_port = 18002\nweb_port = 20002\n'
    )
    registry = tmp_path / ".config/dduo-solo-founder/projects.json"
    registry.write_text(
        json.dumps({"version": 1, "projects": {project_id: {"root_path": str(project_root)}}})
    )

    subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    commands = log.read_text()
    assert f"dduo-solo-founder stop --project-root {project_root}" in commands
    assert f"dduo-solo-founder start --project-root {project_root} --build" in commands

    fail_adapter.touch()
    starts_before = commands.count(f"dduo-solo-founder start --project-root {project_root} --build")
    failed_adapter = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert failed_adapter.returncode != 0
    commands = log.read_text()
    assert commands.count(
        f"dduo-solo-founder start --project-root {project_root} --build"
    ) == starts_before + 2  # upgraded stack, then the previous runtime during rollback
    for previous, current in zip(commands.splitlines(), commands.splitlines()[1:]):
        if current == f"dduo-solo-founder start --project-root {project_root} --build":
            assert previous == f"dduo-solo-founder stop --project-root {project_root}"
    fail_adapter.unlink()

    starts_before = commands.count(f"dduo-solo-founder start --project-root {project_root} --build")
    fail_snapshot.touch()
    failed = subprocess.run(
        ["node", str(ROOT / "bin/install.mjs"), "--yes", "--only", "codex"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert failed.returncode != 0
    commands_after_failure = log.read_text()
    assert (
        commands_after_failure.count(
            f"dduo-solo-founder start --project-root {project_root} --build"
        )
        == starts_before + 1
    )


@pytest.mark.parametrize("failure", [None, "remove", "foreign", "running", "checksum"])
def test_installer_restores_a_verified_upgrade_snapshot_only_after_confirmation(
    tmp_path: Path, failure: str | None
):
    if not shutil.which("node"):
        return
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "commands.log"
    for command in ("docker", "dduo-solo-founder"):
        executable = binaries / command
        executable.write_text(f'#!/bin/sh\necho "{command} $*" >> "{log}"\n')
        executable.chmod(0o755)

    project_root = tmp_path / "Project"
    config = project_root / ".dduo-solo-founder"
    config.mkdir(parents=True)
    project_id = "restore-project"
    config.joinpath("project.toml").write_text(
        f'id = "{project_id}"\nname = "Restore"\napi_port = 18001\nweb_port = 20001\n'
    )
    snapshot = tmp_path / "snapshot"
    project_snapshot = snapshot / project_id
    project_snapshot.mkdir(parents=True)
    archives = []
    for suffix in ("postgres-data", "qdrant-data"):
        archive = f"{suffix}.tar.gz"
        payload = f"{suffix}-snapshot".encode()
        project_snapshot.joinpath(archive).write_bytes(payload)
        volume = "dduo-solo-founder-" + sha256(project_id.encode()).hexdigest()[:12]
        archives.append(
            {
                "volume": f"{volume}_{suffix}",
                "suffix": suffix,
                "archive": archive,
                "sha256": sha256(payload).hexdigest(),
            }
        )
    container_id = "a" * 64
    container_present = tmp_path / "container-present"
    container_present.write_text("stopped container still references the project volumes")
    container_metadata = json.dumps([
        {
            "Id": container_id,
            "Config": {"Labels": {"com.docker.compose.project": (
                "unrelated-project" if failure == "foreign" else volume
            )}},
            "State": {"Running": failure == "running", "Paused": False},
        }
    ])
    docker = binaries / "docker"
    docker.write_text(
        f'#!/bin/sh\necho "docker $*" >> "{log}"\n'
        'if [ "$1 $2" = "container ls" ]; then\n'
        f'  echo "{container_id}"\n'
        'elif [ "$1 $2" = "container inspect" ]; then\n'
        f"  printf '%s\\n' '{container_metadata}'\n"
        'elif [ "$1 $2" = "container rm" ]; then\n'
        + ('  exit 19\n' if failure == "remove" else f'  rm "{container_present}"\n')
        + 'elif [ "$1 $2" = "volume rm" ] && '
        f'[ -f "{container_present}" ]; then\n'
        '  echo "volume is in use by stopped container" >&2\n'
        '  exit 18\n'
        'fi\n'
    )
    if failure == "checksum":
        project_snapshot.joinpath("postgres-data.tar.gz").write_bytes(b"corrupted")
    project_snapshot.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "format": "dduo-solo-founder-upgrade-snapshot",
                "version": 1,
                "project_id": project_id,
                "archives": archives,
            }
        )
    )
    snapshot.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "format": "dduo-solo-founder-upgrade-snapshot-set",
                "version": 1,
                "projects": [project_id],
            }
        )
    )

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{binaries}:{env['PATH']}"
    refused = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--restore-upgrade-snapshot",
            str(snapshot),
            "--project-root",
            str(project_root),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert refused.returncode == 3
    assert "explicit founder confirmation" in refused.stdout

    restored = subprocess.run(
        [
            "node",
            str(ROOT / "bin/install.mjs"),
            "--restore-upgrade-snapshot",
            str(snapshot),
            "--project-root",
            str(project_root),
            "--yes",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    commands = log.read_text() if log.exists() else ""
    if failure is not None:
        assert restored.returncode != 0
        assert "docker volume rm" not in commands
        assert "docker volume create" not in commands
        assert container_present.is_file()
        if failure == "checksum":
            assert "checksum failed" in restored.stderr
            assert commands == ""
        elif failure in {"foreign", "running"}:
            assert "docker container rm" not in commands
        return
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout.strip().splitlines() == [
        "Restoring verified local upgrade snapshot...",
        "Snapshot restored. Reinstall the intended dDuo release, then open a new project chat.",
    ]
    assert f"dduo-solo-founder stop --project-root {project_root}" in commands
    assert f"--filter label=com.docker.compose.project={volume}" in commands
    assert commands.index("dduo-solo-founder stop") < commands.index("docker container inspect")
    assert commands.index(f"docker container rm {container_id}") < commands.index("docker volume rm")
    assert not container_present.exists()
    assert "--force" not in commands
    assert "--volumes" not in commands
    assert "docker volume rm dduo-solo-founder-" in commands
    assert "docker volume create dduo-solo-founder-" in commands
    assert "tar -tzf /snapshot/postgres-data.tar.gz" in commands
