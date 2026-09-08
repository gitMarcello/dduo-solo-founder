from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from conftest import assert_private_file
from dduo_solo_founder import claude_statusline


STABLE_LAUNCHER = Path("/opt/dDuo Runtime/dduo-solo-founder-claude-statusline")


@pytest.fixture(autouse=True)
def stable_statusline_launcher(monkeypatch, tmp_path):
    real_which = claude_statusline.shutil.which
    monkeypatch.setattr(
        claude_statusline,
        "HOOK_RUNTIME_BIN_PATH",
        tmp_path / "missing-hook-runtime-bin",
    )
    monkeypatch.setattr(
        claude_statusline.shutil,
        "which",
        lambda name: (
            str(STABLE_LAUNCHER)
            if name == claude_statusline.STATUSLINE_COMMAND
            else real_which(name)
        ),
    )


def write_settings(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_install_and_restore_preserve_the_exact_existing_statusline(tmp_path: Path):
    settings = tmp_path / ".claude/settings.json"
    state = tmp_path / "private/claude-statusline.json"
    previous = {
        "type": "command",
        "command": "my-status --compact",
        "padding": 0,
    }
    original = {"theme": "dark", "statusLine": previous, "other": {"keep": True}}
    write_settings(settings, original)
    assert claude_statusline.install_claude_statusline(settings_path=settings, state_path=state)
    installed = json.loads(settings.read_text())
    assert installed["theme"] == "dark" and installed["other"] == {"keep": True}
    assert installed["statusLine"] == {
        **previous,
        "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
    }
    assert claude_statusline.claude_statusline_installed(settings_path=settings)
    assert not claude_statusline.install_claude_statusline(settings_path=settings, state_path=state)
    assert_private_file(settings)
    assert_private_file(state)

    assert claude_statusline.restore_claude_statusline(settings_path=settings, state_path=state)
    assert json.loads(settings.read_text()) == original
    assert not state.exists()
    assert not claude_statusline.restore_claude_statusline(settings_path=settings, state_path=state)


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ({"theme": "dark"}, {"theme": "dark"}),
        (
            {"theme": "dark", "statusLine": None},
            {"theme": "dark", "statusLine": None},
        ),
    ],
)
def test_restore_distinguishes_missing_from_explicit_null_statusline(
    tmp_path: Path, original: dict, expected: dict
):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    write_settings(settings, original)
    claude_statusline.install_claude_statusline(settings_path=settings, state_path=state)
    assert claude_statusline.restore_claude_statusline(settings_path=settings, state_path=state)
    assert json.loads(settings.read_text()) == expected


def test_default_restore_state_path_prefers_current_then_falls_back_to_legacy(
    monkeypatch,
    tmp_path: Path,
):
    current = tmp_path / "client-telemetry/claude-statusline.json"
    legacy = tmp_path / "usage-guard/claude-statusline.json"
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", current)
    monkeypatch.setattr(claude_statusline, "LEGACY_ADAPTER_STATE_PATH", legacy)

    assert claude_statusline._default_adapter_state_path() == current
    write_settings(
        legacy,
        {
            "version": 1,
            "had_previous_status_line": False,
            "previous_status_line": None,
        },
    )
    assert claude_statusline._default_adapter_state_path() == legacy
    write_settings(
        current,
        {
            "version": 1,
            "had_previous_status_line": False,
            "previous_status_line": None,
        },
    )
    assert claude_statusline._default_adapter_state_path() == current


def test_restore_reads_the_legacy_state_location_without_migrating_it(
    monkeypatch,
    tmp_path: Path,
):
    settings = tmp_path / "home/.claude/settings.json"
    current = tmp_path / "client-telemetry/claude-statusline.json"
    legacy = tmp_path / "usage-guard/claude-statusline.json"
    previous = {"type": "command", "command": "founder-status"}
    write_settings(
        settings,
        {
            "statusLine": {
                "type": "command",
                "command": "dduo-solo-founder-claude-statusline",
            }
        },
    )
    write_settings(
        legacy,
        {
            "version": 1,
            "had_previous_status_line": True,
            "previous_status_line": previous,
        },
    )
    monkeypatch.setattr(claude_statusline, "CLAUDE_SETTINGS_PATH", settings)
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", current)
    monkeypatch.setattr(claude_statusline, "LEGACY_ADAPTER_STATE_PATH", legacy)

    assert claude_statusline.restore_claude_statusline()
    assert json.loads(settings.read_text())["statusLine"] == previous
    assert not legacy.exists()
    assert not current.exists()


def test_restore_never_clobbers_a_command_changed_after_install(tmp_path: Path):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    write_settings(
        settings,
        {"statusLine": {"type": "command", "command": "previous-status"}},
    )
    claude_statusline.install_claude_statusline(settings_path=settings, state_path=state)
    changed = {"statusLine": {"type": "command", "command": "user-changed-status"}}
    write_settings(settings, changed)

    assert not claude_statusline.restore_claude_statusline(settings_path=settings, state_path=state)
    assert json.loads(settings.read_text()) == changed
    assert state.exists()


def test_restore_entrypoint_delegates_to_lossless_restore(monkeypatch):
    calls = []
    monkeypatch.setattr(
        claude_statusline,
        "_preflight_restore_entrypoint",
        lambda: calls.append("preflight"),
    )
    monkeypatch.setattr(
        claude_statusline,
        "restore_claude_statusline",
        lambda: calls.append("restore") or True,
    )
    monkeypatch.setattr(claude_statusline, "_any_claude_statusline_installed", lambda: False)
    claude_statusline.restore_main()
    assert calls == ["preflight", "restore"]


def test_restore_entrypoint_fails_if_adapter_would_be_left_without_state(monkeypatch, capsys):
    monkeypatch.setattr(claude_statusline, "_preflight_restore_entrypoint", lambda: None)
    monkeypatch.setattr(claude_statusline, "restore_claude_statusline", lambda: False)
    monkeypatch.setattr(claude_statusline, "_any_claude_statusline_installed", lambda: True)

    with pytest.raises(SystemExit) as raised:
        claude_statusline.restore_main()

    assert raised.value.code == 1
    assert "runtime was not removed" in capsys.readouterr().err


def test_restore_entrypoint_is_a_noop_when_adapter_is_not_configured(monkeypatch):
    monkeypatch.setattr(claude_statusline, "_preflight_restore_entrypoint", lambda: None)
    monkeypatch.setattr(claude_statusline, "restore_claude_statusline", lambda: False)
    monkeypatch.setattr(claude_statusline, "_any_claude_statusline_installed", lambda: False)

    claude_statusline.restore_main()


def test_restore_entrypoint_fails_when_any_adapter_remains_after_partial_restore(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(claude_statusline, "_preflight_restore_entrypoint", lambda: None)
    monkeypatch.setattr(claude_statusline, "restore_claude_statusline", lambda: True)
    monkeypatch.setattr(claude_statusline, "_any_claude_statusline_installed", lambda: True)

    with pytest.raises(SystemExit) as raised:
        claude_statusline.restore_main()

    assert raised.value.code == 1
    assert "runtime was not removed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        "dduo-solo-founder-claude-statusline",
        r"C:\\Users\\founder\\bin\\dduo-solo-founder-claude-statusline.cmd",
        r"C:\\runtime\\dduo-solo-founder-claude-statusline.EXE",
    ],
)
def test_adapter_identity_accepts_only_the_exact_cross_platform_launcher(command: str):
    assert claude_statusline._is_adapter({"type": "command", "command": command})
    assert not claude_statusline._is_adapter({"type": "command", "command": f"{command}-other"})


def test_posix_launcher_is_absolute_shell_safe_and_does_not_need_path(tmp_path: Path):
    launcher = tmp_path / "Runtime With Spaces" / "dduo-solo-founder-claude-statusline"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nprintf ready")
    launcher.chmod(0o700)
    command = claude_statusline._render_statusline_command(launcher)

    result = claude_statusline.subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        env={"PATH": ""},
        check=False,
    )

    assert result.returncode == 0 and result.stdout == "ready"
    assert claude_statusline._is_adapter({"type": "command", "command": command})


def test_launcher_resolves_from_private_runtime_pointer_when_path_is_empty(
    monkeypatch,
    tmp_path: Path,
):
    runtime_bin = tmp_path / "Versioned Runtime With Spaces/bin"
    runtime_bin.mkdir(parents=True)
    launcher = runtime_bin / "dduo-solo-founder-claude-statusline"
    launcher.write_text("#!/bin/sh\nexit 0")
    launcher.chmod(0o700)
    pointer = tmp_path / "private/hook-runtime-bin"
    pointer.parent.mkdir()
    pointer.write_text(f"{runtime_bin}\n")
    pointer.chmod(0o600)
    monkeypatch.setattr(claude_statusline, "HOOK_RUNTIME_BIN_PATH", pointer)
    monkeypatch.setattr(claude_statusline.shutil, "which", lambda _: None)

    assert claude_statusline._statusline_command() == (
        claude_statusline._render_statusline_command(launcher)
    )


def test_stored_stable_launcher_follows_runtime_pointer_across_updates(
    monkeypatch,
    tmp_path: Path,
):
    pointer = tmp_path / "runtime-pointer"
    executable_name = "dduo-solo-founder-claude-statusline"
    runtimes = []
    for version in ("A", "B"):
        runtime = tmp_path / f"Runtime {version}"
        runtime.mkdir()
        executable = runtime / executable_name
        executable.write_text(f"#!/bin/sh\nprintf {version}")
        executable.chmod(0o700)
        runtimes.append(runtime)
    stable = tmp_path / "Stable Bin" / executable_name
    stable.parent.mkdir()
    stable.write_text(
        "#!/bin/sh\n"
        f'runtime_bin=$(/bin/cat "{pointer}")\n'
        f'exec "$runtime_bin/{executable_name}"\n'
    )
    stable.chmod(0o700)
    pointer.write_text(str(runtimes[0]))
    monkeypatch.setattr(
        claude_statusline.shutil,
        "which",
        lambda name: str(stable) if name == executable_name else None,
    )

    stored_command = claude_statusline._statusline_command()
    first = claude_statusline.subprocess.run(
        stored_command,
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    )
    pointer.write_text(str(runtimes[1]))
    second = claude_statusline.subprocess.run(
        stored_command,
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    )

    assert first.stdout == "A" and second.stdout == "B"


def test_windows_launcher_encoding_handles_spaces_and_shell_metacharacters():
    path = PureWindowsPath(
        r"C:\Program Files\Founder O'Brien & Team\dduo-solo-founder-claude-statusline.cmd"
    )
    command = claude_statusline._render_statusline_command(path, windows=True)

    assert command.startswith('powershell -NoProfile -Command "& ')
    assert "Program Files" not in command and "O'Brien" not in command
    assert claude_statusline._is_adapter({"type": "command", "command": command})


@pytest.mark.parametrize(
    "legacy_command",
    [
        "/Users/Founder Name/dDuo Runtime/dduo-solo-founder-claude-statusline",
        r"C:\\Program Files\\dDuo\\dduo-solo-founder-claude-statusline.cmd",
        r"C:\\Program Files\\dDuo\\dduo-solo-founder-claude-statusline.exe",
    ],
)
def test_existing_absolute_adapter_is_normalized_to_the_stable_path_launcher(
    tmp_path: Path,
    legacy_command: str,
):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    previous = {"type": "command", "command": "founder-status --compact"}
    write_settings(
        settings,
        {"statusLine": {"type": "command", "command": legacy_command, "padding": 1}},
    )
    write_settings(
        state,
        {
            "version": 1,
            "had_previous_status_line": True,
            "previous_status_line": previous,
        },
    )

    assert claude_statusline.install_claude_statusline(
        settings_path=settings,
        state_path=state,
        repair=True,
    )
    assert json.loads(settings.read_text())["statusLine"] == {
        "type": "command",
        "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
        "padding": 1,
    }
    assert not claude_statusline.install_claude_statusline(
        settings_path=settings,
        state_path=state,
        repair=True,
    )
    assert claude_statusline.restore_claude_statusline(
        settings_path=settings,
        state_path=state,
    )
    assert json.loads(settings.read_text())["statusLine"] == previous


def test_restore_requires_valid_preserved_state_and_installed_handles_bad_json(tmp_path: Path):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    write_settings(
        settings,
        {
            "statusLine": {
                "type": "command",
                "command": "dduo-solo-founder-claude-statusline",
            }
        },
    )
    write_settings(state, {"version": 2, "previous_status_line": None})
    assert not claude_statusline.restore_claude_statusline(settings_path=settings, state_path=state)

    settings.write_text("not json")
    settings.chmod(0o600)
    assert not claude_statusline.claude_statusline_installed(settings_path=settings)


def test_explicit_repair_rebuilds_a_safe_restore_boundary_for_an_orphaned_adapter(
    tmp_path: Path,
):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    write_settings(
        settings,
        {
            "theme": "dark",
            "statusLine": {
                "type": "command",
                "command": "dduo-solo-founder-claude-statusline",
            },
        },
    )
    with pytest.raises(RuntimeError, match="repair it from local Setup"):
        claude_statusline.install_claude_statusline(settings_path=settings, state_path=state)

    assert claude_statusline.install_claude_statusline(
        settings_path=settings,
        state_path=state,
        repair=True,
    )
    assert claude_statusline.restore_claude_statusline(
        settings_path=settings,
        state_path=state,
    )
    assert json.loads(settings.read_text()) == {"theme": "dark"}


def test_explicit_repair_recovers_an_unreadable_private_adapter_state(tmp_path: Path):
    settings = tmp_path / "settings.json"
    state = tmp_path / "state.json"
    write_settings(
        settings,
        {
            "statusLine": {
                "type": "command",
                "command": "dduo-solo-founder-claude-statusline",
            }
        },
    )
    state.write_text("not json")
    state.chmod(0o600)

    assert claude_statusline.install_claude_statusline(
        settings_path=settings,
        state_path=state,
        repair=True,
    )
    assert claude_statusline.restore_claude_statusline(
        settings_path=settings,
        state_path=state,
    )
    assert json.loads(settings.read_text()) == {}


def test_project_scopes_preserve_precedence_forward_dynamically_and_restore_losslessly(
    monkeypatch,
    tmp_path: Path,
):
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "private/claude-statusline.json"
    root_a = tmp_path / "Project A"
    root_b = tmp_path / "Project B"
    root_a.mkdir()
    root_b.mkdir()
    user_previous = {"type": "command", "command": "user-status --compact"}
    project_a_previous = {"type": "command", "command": "project-a-status"}
    local_b_previous = {"type": "command", "command": "project-b-local-status"}
    write_settings(user_settings, {"theme": "dark", "statusLine": user_previous})
    write_settings(
        root_a / ".claude/settings.json",
        {"statusLine": project_a_previous},
    )
    write_settings(root_a / ".claude/settings.local.json", {"permissions": {"allow": []}})
    write_settings(
        root_b / ".claude/settings.local.json",
        {"statusLine": local_b_previous, "language": "it"},
    )

    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root_a,
        managed_settings_paths=(),
    )
    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root_b,
        managed_settings_paths=(),
    )
    assert claude_statusline.claude_statusline_status(
        root_a,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )["ready"]
    assert claude_statusline.claude_statusline_status(
        root_b,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )["ready"]

    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", state)
    forwarded = []

    def run(command, **kwargs):
        forwarded.append(command)
        return SimpleNamespace(stdout=f"{command}\n".encode())

    monkeypatch.setattr(claude_statusline.subprocess, "run", run)
    raw = b'{}\n'
    assert claude_statusline._forward_previous(raw, {"cwd": str(root_a)})
    assert forwarded[-1] == "project-a-status"
    assert claude_statusline._forward_previous(raw, {"cwd": str(root_b)})
    assert forwarded[-1] == "project-b-local-status"

    # A lower-precedence project command may evolve while the local adapter is
    # active; forwarding resolves it on every tick rather than freezing it.
    write_settings(
        root_a / ".claude/settings.json",
        {"statusLine": {"type": "command", "command": "project-a-status-v2"}},
    )
    assert claude_statusline._forward_previous(raw, {"cwd": str(root_a)})
    assert forwarded[-1] == "project-a-status-v2"

    write_settings(
        root_a / ".claude/settings.json",
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    assert claude_statusline._forward_previous(raw, {"cwd": str(root_a)})
    assert forwarded[-1] == "user-status --compact"
    write_settings(
        root_a / ".claude/settings.json",
        {"statusLine": {"type": "command", "command": "project-a-status-v2"}},
    )

    assert claude_statusline.restore_claude_statusline(state_path=state)
    assert json.loads(user_settings.read_text()) == {
        "theme": "dark",
        "statusLine": user_previous,
    }
    assert json.loads((root_a / ".claude/settings.local.json").read_text()) == {
        "permissions": {"allow": []}
    }
    assert json.loads((root_b / ".claude/settings.local.json").read_text()) == {
        "statusLine": local_b_previous,
        "language": "it",
    }
    assert not state.exists()


def test_project_scope_migrates_v1_user_state_and_repairs_legacy_absolute_launcher(
    tmp_path: Path,
):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    previous = {"type": "command", "command": "original-user-status"}
    write_settings(
        user_settings,
        {
            "statusLine": {
                "type": "command",
                "command": "/Old Runtime With Spaces/dduo-solo-founder-claude-statusline",
            }
        },
    )
    write_settings(
        state,
        {
            "version": 1,
            "had_previous_status_line": True,
            "previous_status_line": previous,
        },
    )

    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
        repair=True,
    )
    migrated = json.loads(state.read_text())
    assert migrated["version"] == 2 and len(migrated["projects"]) == 1
    assert json.loads(user_settings.read_text())["statusLine"]["command"] == (
        claude_statusline._render_statusline_command(STABLE_LAUNCHER)
    )
    assert claude_statusline.restore_claude_statusline(state_path=state)
    assert json.loads(user_settings.read_text())["statusLine"] == previous
    assert not (root / ".claude/settings.local.json").exists()


def test_managed_statusline_is_unavailable_and_never_reported_ready(tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    managed = tmp_path / "managed-settings.json"
    write_settings(managed, {"statusLine": {"type": "command", "command": "company-status"}})

    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(managed,),
    )
    assert not status["ready"] and status["reason"] == "managed_override"
    with pytest.raises(RuntimeError, match="managed settings own statusLine"):
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(managed,),
        )
    assert not user_settings.exists()
    assert not (root / ".claude/settings.local.json").exists()


def test_hook_only_policies_do_not_gate_statusline_telemetry(tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    managed = tmp_path / "managed-settings.json"
    write_settings(managed, {"allowManagedHooksOnly": True})
    write_settings(user_settings, {"disableAllHooks": True})
    write_settings(
        root / ".claude/settings.local.json",
        {"disableAllHooks": True, "language": "it"},
    )

    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(managed,),
    )
    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(managed,),
    )
    assert status["ready"] and status["reason"] == "ready"
    assert json.loads(user_settings.read_text())["disableAllHooks"] is True
    local = json.loads((root / ".claude/settings.local.json").read_text())
    assert local["disableAllHooks"] is True and local["language"] == "it"


def test_multiscope_restore_preflights_every_record_before_mutating_any_file(tmp_path: Path):
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    root_a.mkdir()
    root_b.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    write_settings(user_settings, {"statusLine": {"type": "command", "command": "user"}})
    for root in (root_a, root_b):
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(),
        )
    before_a = (root_a / ".claude/settings.local.json").read_bytes()
    document = json.loads(state.read_text())
    key_b = claude_statusline._project_scope_key(root_b.resolve())
    document["projects"][key_b].pop("previous_status_line")
    write_settings(state, document)

    with pytest.raises(RuntimeError, match="restore state"):
        claude_statusline.restore_claude_statusline(state_path=state)

    assert (root_a / ".claude/settings.local.json").read_bytes() == before_a
    assert claude_statusline._is_adapter(
        json.loads(user_settings.read_text())["statusLine"]
    )


def test_uninstall_preflight_rejects_an_orphan_adapter_before_any_restore(
    monkeypatch,
    tmp_path: Path,
):
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    root_a.mkdir()
    root_b.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    registry = tmp_path / "projects.json"
    write_settings(user_settings, {"statusLine": {"type": "command", "command": "user"}})
    claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root_a,
        managed_settings_paths=(),
    )
    orphan = {
        "statusLine": {
            "type": "command",
            "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
        }
    }
    write_settings(root_b / ".claude/settings.local.json", orphan)
    write_settings(
        registry,
        {
            "version": 1,
            "projects": {
                "a": {"root_path": str(root_a), "api_port": 1, "web_port": 2},
                "b": {"root_path": str(root_b), "api_port": 3, "web_port": 4},
            },
        },
    )
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", state)
    monkeypatch.setattr(claude_statusline, "REGISTRY_PATH", registry)
    before_a = (root_a / ".claude/settings.local.json").read_bytes()
    before_user = user_settings.read_bytes()

    with pytest.raises(RuntimeError, match="no valid restore state"):
        claude_statusline.restore_claude_statusline(state_path=state)

    assert (root_a / ".claude/settings.local.json").read_bytes() == before_a
    assert user_settings.read_bytes() == before_user


def test_project_local_adapter_is_excluded_from_git_status(tmp_path: Path):
    root = tmp_path / "Project"
    claude_statusline.subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
    )
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"

    claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
    )

    exclude = root / ".git/info/exclude"
    assert "/.claude/settings.local.json" in exclude.read_text().splitlines()
    assert "/.claude/settings.local.json.lock" in exclude.read_text().splitlines()
    status = claude_statusline.subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == ""


def test_install_never_modifies_a_tracked_project_local_settings_file(tmp_path: Path):
    root = tmp_path / "Project"
    claude_statusline.subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
    )
    local = root / ".claude/settings.local.json"
    shared = {"statusLine": {"type": "command", "command": "team-status"}}
    write_settings(local, shared)
    claude_statusline.subprocess.run(
        ["git", "-C", str(root), "add", "-f", ".claude/settings.local.json"],
        check=True,
    )
    before = local.read_bytes()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"

    with pytest.raises(RuntimeError, match="tracked"):
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(),
        )

    assert local.read_bytes() == before
    assert not user_settings.exists() and not state.exists()


def test_uninstall_rejects_a_legacy_adapter_in_shared_project_settings(
    monkeypatch,
    tmp_path: Path,
):
    root = tmp_path / "Project"
    root.mkdir()
    shared = root / ".claude/settings.json"
    write_settings(
        shared,
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    registry = tmp_path / "projects.json"
    write_settings(
        registry,
        {
            "version": 1,
            "projects": {
                "p1": {"root_path": str(root), "api_port": 1, "web_port": 2}
            },
        },
    )
    state = tmp_path / "missing-state.json"
    user_settings = tmp_path / "missing-user-settings.json"
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", state)
    monkeypatch.setattr(claude_statusline, "CLAUDE_SETTINGS_PATH", user_settings)
    monkeypatch.setattr(claude_statusline, "REGISTRY_PATH", registry)

    with pytest.raises(RuntimeError, match="no project-aware restore state"):
        claude_statusline._preflight_restore_entrypoint()


def test_uninstall_preserves_a_project_local_statusline_changed_by_the_user(tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "state.json"
    write_settings(user_settings, {})
    claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
    )
    changed = {
        "statusLine": {"type": "command", "command": "user-replacement"},
        "language": "it",
    }
    write_settings(root / ".claude/settings.local.json", changed)

    assert claude_statusline.restore_claude_statusline(state_path=state)
    assert json.loads((root / ".claude/settings.local.json").read_text()) == changed
    assert json.loads(user_settings.read_text()) == {}
    assert not state.exists()


def test_previous_statusline_receives_the_original_payload_and_output_is_lossless(
    monkeypatch, tmp_path: Path
):
    state = tmp_path / "state.json"
    write_settings(
        state,
        {
            "version": 1,
            "previous_status_line": {
                "type": "command",
                "command": "existing-status --flag",
            },
        },
    )
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", state)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="original output\n".encode())

    monkeypatch.setattr(claude_statusline.subprocess, "run", run)
    raw = b'{"session_id":"s1"}\n'
    assert claude_statusline._forward_previous(raw) == "original output\n"
    assert calls[0][0] == "existing-status --flag"
    assert calls[0][1]["input"] == raw
    assert calls[0][1]["shell"] is True


def test_claude_cost_uses_only_positive_cumulative_deltas_and_never_context_snapshots(
    monkeypatch, tmp_path: Path
):
    ledger = tmp_path / "private/claude-sessions.json"
    monkeypatch.setattr(claude_statusline, "CLAUDE_USAGE_LEDGER_PATH", ledger)
    payload = {
        "session_id": "session-1",
        "model": {"id": "claude-sonnet-4-6"},
        "context_window": {
            "current_usage": {
                "input_tokens": 10_000,
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": 30_000,
                "output_tokens": 500,
            }
        },
        "cost": {"total_cost_usd": "0.100000"},
    }

    first = claude_statusline._claude_usage_event(payload, "p1")
    assert first is not None
    assert first["client_cost_usd"] == "0.100000"
    assert first["cost_source"] == "claude_code_client_estimate"
    assert first["event_id"].endswith(".0.1")
    assert "model" not in first
    assert not {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reported_total_tokens",
    } & set(first)

    # The context snapshot changes while cumulative cost does not. Treating it
    # as consumption would create a false second usage event.
    payload["context_window"]["current_usage"]["input_tokens"] = 99_000
    assert claude_statusline._claude_usage_event(payload, "p1") is None

    payload["cost"]["total_cost_usd"] = "0.125000"
    second = claude_statusline._claude_usage_event(payload, "p1")
    assert second is not None
    assert second["client_cost_usd"] == "0.025000"
    assert second["event_id"].endswith(".0.2")

    # A lower cumulative counter starts a new local epoch (for /clear or a
    # client reset) instead of producing a negative delta.
    payload["cost"]["total_cost_usd"] = "0.010000"
    reset = claude_statusline._claude_usage_event(payload, "p1")
    assert reset is not None
    assert reset["client_cost_usd"] == "0.010000"
    assert reset["event_id"].endswith(".1.1")
    assert_private_file(ledger)

    payload["cost"]["total_cost_usd"] = "0"
    assert claude_statusline._claude_usage_event(payload, "p1") is None

    without_cost = {**payload, "session_id": "session-2", "cost": {}}
    assert claude_statusline._claude_usage_event(without_cost, "p1") is None


def test_claude_cost_baseline_follows_the_session_across_projects(monkeypatch, tmp_path: Path):
    ledger = tmp_path / "private/claude-sessions.json"
    monkeypatch.setattr(claude_statusline, "CLAUDE_USAGE_LEDGER_PATH", ledger)
    payload = {"session_id": "moving-session", "cost": {"total_cost_usd": "0.10"}}

    first = claude_statusline._claude_usage_event(payload, "project-a")
    payload["cost"]["total_cost_usd"] = "0.15"
    resumed = claude_statusline._claude_usage_event(payload, "project-b")

    assert first is not None and first["client_cost_usd"] == "0.10"
    assert resumed is not None and resumed["client_cost_usd"] == "0.05"
    assert resumed["event_id"].endswith(".0.2")
    assert len(json.loads(ledger.read_text())["sessions"]) == 1


def test_usage_ledger_recovers_shape_omits_invalid_model_and_keeps_resumable_baselines(
    monkeypatch, tmp_path: Path
):
    ledger = tmp_path / "claude-sessions.json"
    write_settings(ledger, {"version": 9, "sessions": []})
    monkeypatch.setattr(claude_statusline, "CLAUDE_USAGE_LEDGER_PATH", ledger)
    invalid_model = {
        "session_id": "s1",
        "model": {"id": "invalid model name"},
        "cost": {"total_cost_usd": "0.01"},
    }
    event = claude_statusline._claude_usage_event(invalid_model, "p1")
    assert event is not None and "model" not in event

    resumed_key = hashlib.sha256(b"resumed-session").hexdigest()
    sessions = {
        resumed_key: {
            "cumulative_cost": "0.10",
            "epoch": 0,
            "sequence": 1,
            "updated_at": "2025-01-01T00:00:00+00:00",
        },
        **{
            f"old-{index}": {
                "cumulative_cost": "1",
                "epoch": 0,
                "sequence": 1,
                "updated_at": f"2026-01-01T00:{index // 60:02}:{index % 60:02}+00:00",
            }
            for index in range(499)
        },
    }
    write_settings(ledger, {"version": 1, "sessions": sessions})

    # Crossing the legacy 500-session boundary must not evict the oldest
    # cumulative baseline.
    event = claude_statusline._claude_usage_event(
        {"session_id": "new", "cost": {"total_cost_usd": "0.02"}}, "p1"
    )
    assert event is not None and "model" not in event
    resumed = claude_statusline._claude_usage_event(
        {
            "session_id": "resumed-session",
            "cost": {"total_cost_usd": "0.15"},
        },
        "p1",
    )
    assert resumed is not None
    assert resumed["client_cost_usd"] == "0.05"
    assert resumed["event_id"].endswith(".0.2")
    assert len(json.loads(ledger.read_text())["sessions"]) == 501


def test_next_usage_sample_removes_legacy_reserve_metadata_from_the_ledger(
    monkeypatch,
    tmp_path: Path,
):
    ledger = tmp_path / "private/claude-sessions.json"
    monkeypatch.setattr(claude_statusline, "CLAUDE_USAGE_LEDGER_PATH", ledger)
    write_settings(
        ledger,
        {
            "version": 1,
            "sessions": {},
            "guard_state": {"blocked": True},
            "guard_namespace": "legacy",
            "guard_sequence": 4,
            "pending_guard_transitions": [{"legacy": True}],
        },
    )

    event = claude_statusline._claude_usage_event(
        {"session_id": "s1", "cost": {"total_cost_usd": "0.01"}},
        "p1",
    )

    assert event is not None
    stored = json.loads(ledger.read_text())
    assert set(stored) == {"version", "sessions"}


def test_statusline_event_rejects_missing_session_and_invalid_cost(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        claude_statusline,
        "CLAUDE_USAGE_LEDGER_PATH",
        tmp_path / "claude-sessions.json",
    )
    assert claude_statusline._claude_usage_event({"cost": {"total_cost_usd": 1}}, "p1") is None
    assert (
        claude_statusline._claude_usage_event(
            {"session_id": "s", "cost": {"total_cost_usd": "not-money"}}, "p1"
        )
        is None
    )


def test_usage_delta_is_spooled_before_the_ledger_advances(monkeypatch, tmp_path: Path):
    ledger = tmp_path / "private/claude-sessions.json"
    monkeypatch.setattr(claude_statusline, "CLAUDE_USAGE_LEDGER_PATH", ledger)
    payload = {"session_id": "s1", "cost": {"total_cost_usd": "0.25"}}
    attempted = []

    class CrashAfterDurableWrite:
        def enqueue(self, project_id, event):
            attempted.append((project_id, dict(event)))
            raise RuntimeError("simulated interruption after spool write")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        claude_statusline._claude_usage_event(
            payload,
            "p1",
            spool=CrashAfterDurableWrite(),
        )
    assert not ledger.exists()

    class AlreadyQueued:
        def enqueue(self, project_id, event):
            assert project_id == "p1"
            assert event["event_id"] == attempted[0][1]["event_id"]
            return False

    retried = claude_statusline._claude_usage_event(
        payload,
        "p1",
        spool=AlreadyQueued(),
    )
    assert retried is not None
    assert json.loads(ledger.read_text())["sessions"]


def test_main_forwards_existing_statusline_without_adding_telemetry_output(monkeypatch):
    raw = b'{"session_id":"s1"}'
    monkeypatch.setattr(claude_statusline.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    output = io.StringIO()
    monkeypatch.setattr(claude_statusline.sys, "stdout", output)
    monkeypatch.setattr(claude_statusline, "capture_statusline", lambda payload: object())
    monkeypatch.setattr(
        claude_statusline,
        "_forward_previous",
        lambda raw_payload, payload: "kept\n",
    )

    claude_statusline.main()

    assert output.getvalue() == "kept\n"


def test_main_is_silent_when_there_is_no_previous_statusline(monkeypatch):
    raw = b'{"session_id":"s1","cost":{"total_cost_usd":"0.01"}}'
    monkeypatch.setattr(claude_statusline.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    output = io.StringIO()
    monkeypatch.setattr(claude_statusline.sys, "stdout", output)
    captured = []
    monkeypatch.setattr(
        claude_statusline,
        "capture_statusline",
        lambda payload: captured.append(payload),
    )
    monkeypatch.setattr(claude_statusline, "_forward_previous", lambda *args: None)

    claude_statusline.main()

    assert captured == [
        {"session_id": "s1", "cost": {"total_cost_usd": "0.01"}}
    ]
    assert output.getvalue() == ""


def test_capture_statusline_enqueues_usage_only_when_project_exists(monkeypatch):
    captured = []

    class Spool:
        pass

    monkeypatch.setattr(claude_statusline, "ClientTelemetrySpool", Spool)
    monkeypatch.setattr(claude_statusline, "_statusline_project", lambda payload: None)
    assert claude_statusline.capture_statusline({"session_id": "s1"}) is None
    assert captured == []

    monkeypatch.setattr(claude_statusline, "_statusline_project", lambda payload: {"id": "p1"})

    def usage_event(payload, project_id, *, spool):
        captured.append((payload, project_id, spool))

    monkeypatch.setattr(claude_statusline, "_claude_usage_event", usage_event)
    payload = {"session_id": "s2", "prompt_id": "prompt-2"}
    assert claude_statusline.capture_statusline(payload) is None
    assert len(captured) == 1
    assert captured[0][:2] == (payload, "p1")
    assert isinstance(captured[0][2], Spool)


def test_statusline_project_resolution_and_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(claude_statusline, "find_workspace_root", lambda path: path.resolve())
    monkeypatch.setattr(
        claude_statusline,
        "load_project",
        lambda root: {"id": "p1", "root": str(root)},
    )
    assert claude_statusline._statusline_project({}) is None
    project = claude_statusline._statusline_project(
        {"workspace": {"project_dir": str(tmp_path)}, "cwd": "/ignored"}
    )
    assert project == {"id": "p1", "root": str(tmp_path.resolve())}

    monkeypatch.setattr(
        claude_statusline,
        "load_project",
        lambda root: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert claude_statusline._statusline_project({"cwd": str(tmp_path)}) is None


def test_statusline_private_json_helpers_reject_unsafe_or_invalid_files(tmp_path: Path):
    missing = tmp_path / "missing.json"
    assert claude_statusline._read_object(missing, missing={"ok": True}) == {"ok": True}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json")
    invalid.chmod(0o600)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        claude_statusline._read_object(invalid, missing={})
    invalid.write_text("[]")
    with pytest.raises(RuntimeError, match="JSON object"):
        claude_statusline._read_object(invalid, missing={})

    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match="unsafe configuration file"):
        claude_statusline._read_object(link, missing={})

    if os.name != "nt":
        invalid.write_text("{}")
        invalid.chmod(0o666)
        with pytest.raises(RuntimeError, match="writable configuration"):
            claude_statusline._read_object(invalid, missing={})

    directory = tmp_path / "directory"
    directory.mkdir()
    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(RuntimeError, match="unsafe configuration directory"):
        claude_statusline._write_private_json(directory_link / "state.json", {})


def test_forward_previous_and_money_parsing_fail_safely(monkeypatch, tmp_path: Path):
    state = tmp_path / "state.json"
    monkeypatch.setattr(claude_statusline, "ADAPTER_STATE_PATH", state)
    assert claude_statusline._forward_previous(b"{}") is None
    write_settings(state, {"previous_status_line": {"command": "status"}})
    monkeypatch.setattr(
        claude_statusline.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    assert claude_statusline._forward_previous(b"{}") is None

    assert claude_statusline._money(None) is None
    assert claude_statusline._money(True) is None
    assert claude_statusline._money("bad") is None
    assert claude_statusline._money("NaN") is None
    assert claude_statusline._money("-1") is None
    assert str(claude_statusline._money("1.25")) == "1.25"


def test_project_status_distinguishes_each_repair_boundary(monkeypatch, tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "private/state.json"
    monkeypatch.setattr(
        claude_statusline.shutil,
        "which",
        lambda name: str(STABLE_LAUNCHER)
        if name == claude_statusline.STATUSLINE_COMMAND
        else None,
    )

    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
    )
    assert claude_statusline.claude_statusline_installed(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
    )

    user = json.loads(user_settings.read_text())
    user["statusLine"] = {"type": "command", "command": "founder-status"}
    write_settings(user_settings, user)
    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )
    assert status["reason"] == "user_fallback_missing"
    assert status["project_adapter_installed"] is True

    local_path = root / ".claude/settings.local.json"
    local = json.loads(local_path.read_text())
    local.pop("statusLine")
    write_settings(local_path, local)
    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )
    assert status["reason"] == "project_adapter_missing"
    assert status["installed"] is False

    state.unlink()
    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )
    assert status["reason"] == "restore_state_missing"


def test_project_status_rejects_shared_or_unreadable_configuration(tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "private/state.json"
    shared = root / ".claude/settings.json"
    write_settings(
        shared,
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )

    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )
    assert status["reason"] == "shared_adapter_unmanaged"

    shared.unlink()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not-json")
    state.chmod(0o600)
    status = claude_statusline.claude_statusline_status(
        root,
        user_settings_path=user_settings,
        state_path=state,
        managed_settings_paths=(),
    )
    assert status["reason"] == "settings_unavailable"


def test_corrupt_managed_settings_are_treated_as_an_authoritative_override(tmp_path: Path):
    managed = tmp_path / "managed.json"
    managed.write_text("not-json")
    managed.chmod(0o600)

    assert claude_statusline._managed_statusline_override((managed,)) == managed


def test_project_install_rejects_missing_root_invalid_state_and_orphan_scope(
    monkeypatch,
    tmp_path: Path,
):
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="project root is unavailable"):
        claude_statusline.install_claude_statusline(
            project_root=missing,
            managed_settings_paths=(),
        )

    root = tmp_path / "Project"
    root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "private/state.json"
    monkeypatch.setattr(
        claude_statusline.shutil,
        "which",
        lambda name: str(STABLE_LAUNCHER)
        if name == claude_statusline.STATUSLINE_COMMAND
        else None,
    )
    write_settings(state, {"version": 99, "projects": {}})
    with pytest.raises(RuntimeError, match="restore state is invalid"):
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(),
        )

    write_settings(
        user_settings,
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    write_settings(state, {"version": 2, "user": None, "projects": {}})
    with pytest.raises(RuntimeError, match="restore state is invalid"):
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(),
        )

    assert claude_statusline.install_claude_statusline(
        settings_path=user_settings,
        state_path=state,
        project_root=root,
        managed_settings_paths=(),
        repair=True,
    )
    repaired = json.loads(state.read_text())
    assert repaired["user"]["had_previous_status_line"] is False


def test_project_specific_restore_leaves_other_projects_and_user_fallback_active(
    monkeypatch,
    tmp_path: Path,
):
    roots = [tmp_path / "A", tmp_path / "B"]
    for root in roots:
        root.mkdir()
    user_settings = tmp_path / "home/.claude/settings.json"
    state = tmp_path / "private/state.json"
    write_settings(user_settings, {"statusLine": {"type": "command", "command": "user"}})
    monkeypatch.setattr(
        claude_statusline.shutil,
        "which",
        lambda name: str(STABLE_LAUNCHER)
        if name == claude_statusline.STATUSLINE_COMMAND
        else None,
    )
    for root in roots:
        claude_statusline.install_claude_statusline(
            settings_path=user_settings,
            state_path=state,
            project_root=root,
            managed_settings_paths=(),
        )

    assert claude_statusline.restore_claude_statusline(
        state_path=state,
        project_root=roots[0],
    )
    remaining = json.loads(state.read_text())
    assert set(remaining["projects"]) == {
        claude_statusline._project_scope_key(roots[1].resolve())
    }
    assert remaining["user"] is not None
    assert not (roots[0] / ".claude/settings.local.json").exists()
    assert claude_statusline._is_adapter(
        json.loads((roots[1] / ".claude/settings.local.json").read_text())["statusLine"]
    )
    assert claude_statusline._is_adapter(json.loads(user_settings.read_text())["statusLine"])

    unknown = tmp_path / "Unknown"
    unknown.mkdir()
    before = state.read_bytes()
    assert not claude_statusline.restore_claude_statusline(
        state_path=state,
        project_root=unknown,
    )
    assert json.loads(state.read_bytes()) == json.loads(before)

    assert claude_statusline.restore_claude_statusline(state_path=state)
    assert not state.exists()
    assert json.loads(user_settings.read_text())["statusLine"]["command"] == "user"


@pytest.mark.parametrize(
    "broken_record",
    [
        None,
        {},
        {
            "project_root": "/wrong",
            "settings_path": "/wrong/.claude/settings.local.json",
            "file_existed": False,
            "had_previous_status_line": False,
            "previous_status_line": None,
        },
    ],
)
def test_project_restore_rejects_malformed_selected_record_before_mutation(
    tmp_path: Path,
    broken_record,
):
    root = (tmp_path / "Project").resolve()
    root.mkdir()
    key = claude_statusline._project_scope_key(root)
    state_path = tmp_path / "state.json"
    document = {
        "version": 2,
        "user": None,
        "projects": {key: broken_record},
    }
    write_settings(state_path, document)
    before = state_path.read_bytes()

    with pytest.raises(RuntimeError, match="project telemetry restore state is invalid"):
        claude_statusline.restore_claude_statusline(
            state_path=state_path,
            project_root=root,
        )

    assert state_path.read_bytes() == before


def test_restore_entrypoint_converts_preflight_errors_to_a_safe_uninstall_failure(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        claude_statusline,
        "_preflight_restore_entrypoint",
        lambda: (_ for _ in ()).throw(RuntimeError("damaged state")),
    )

    with pytest.raises(SystemExit) as raised:
        claude_statusline.restore_main()

    assert raised.value.code == 1
    assert "runtime was not removed" in capsys.readouterr().err


def test_preflight_ownership_rejects_shared_adapter_and_invalid_user_state(
    monkeypatch,
    tmp_path: Path,
):
    root = tmp_path / "Project"
    root.mkdir()
    state_path = tmp_path / "state.json"
    user_settings = tmp_path / "home/.claude/settings.json"
    user_record = claude_statusline._scope_record(
        user_settings,
        file_existed=False,
        had_previous=False,
        previous=None,
        project_root=None,
    )
    state = {"version": 2, "user": user_record, "projects": {}}
    monkeypatch.setattr(claude_statusline, "_known_project_roots", lambda _: {root})
    write_settings(
        root / ".claude/settings.json",
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    with pytest.raises(RuntimeError, match="shared settings"):
        claude_statusline._preflight_adapter_ownership(state, state_path=state_path)

    (root / ".claude/settings.json").unlink()
    state["user"] = None
    with pytest.raises(RuntimeError, match="user telemetry restore state is invalid"):
        claude_statusline._preflight_adapter_ownership(state, state_path=state_path)


def test_any_installed_checks_user_project_and_unreadable_scopes(monkeypatch, tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    state = tmp_path / "state.json"
    user = tmp_path / "user.json"
    monkeypatch.setattr(claude_statusline, "_known_project_roots", lambda _: {root})
    assert not claude_statusline._any_claude_statusline_installed(
        user_settings_path=user,
        state_path=state,
    )

    write_settings(
        root / ".claude/settings.local.json",
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    assert claude_statusline._any_claude_statusline_installed(
        user_settings_path=user,
        state_path=state,
    )

    (root / ".claude/settings.local.json").write_text("not-json")
    (root / ".claude/settings.local.json").chmod(0o600)
    assert claude_statusline._any_claude_statusline_installed(
        user_settings_path=user,
        state_path=state,
    )


def test_previous_statusline_resolution_fails_safe_at_each_scope(monkeypatch, tmp_path: Path):
    root = tmp_path / "Project"
    root.mkdir()
    monkeypatch.setattr(claude_statusline, "find_workspace_root", lambda _: root)
    assert claude_statusline._statusline_root({}) is None
    monkeypatch.setattr(
        claude_statusline,
        "find_workspace_root",
        lambda _: (_ for _ in ()).throw(RuntimeError("untrusted")),
    )
    assert claude_statusline._statusline_root({"cwd": str(root)}) is None

    (root / ".claude").mkdir()
    shared = root / ".claude/settings.json"
    shared.write_text("not-json")
    shared.chmod(0o600)
    assert claude_statusline._lower_statusline(root, {}) == (False, None)

    shared.unlink()
    user = tmp_path / "user.json"
    record = claude_statusline._scope_record(
        user,
        file_existed=True,
        had_previous=True,
        previous={"type": "command", "command": "previous"},
        project_root=None,
    )
    state = {"version": 2, "user": record, "projects": {}}
    assert claude_statusline._effective_user_statusline(state) == (False, None)
    write_settings(user, {"theme": "dark"})
    assert claude_statusline._effective_user_statusline(state) == (False, None)
    write_settings(user, {"statusLine": {"type": "command", "command": "current"}})
    assert claude_statusline._effective_user_statusline(state) == (
        True,
        {"type": "command", "command": "current"},
    )
    write_settings(
        user,
        {
            "statusLine": {
                "type": "command",
                "command": claude_statusline._render_statusline_command(STABLE_LAUNCHER),
            }
        },
    )
    assert claude_statusline._effective_user_statusline(state) == (
        True,
        {"type": "command", "command": "previous"},
    )


@pytest.mark.parametrize(
    "pointer_value",
    ["", "relative/runtime", "/definitely/missing/runtime"],
)
def test_private_runtime_pointer_rejects_unusable_targets(
    monkeypatch,
    tmp_path: Path,
    pointer_value: str,
):
    pointer = tmp_path / "runtime-pointer"
    pointer.write_text(pointer_value)
    pointer.chmod(0o600)
    monkeypatch.setattr(claude_statusline, "HOOK_RUNTIME_BIN_PATH", pointer)
    assert claude_statusline._runtime_pointer_launcher() is None


def test_launcher_and_adapter_parsers_reject_ambiguous_commands(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(claude_statusline.shutil, "which", lambda _: None)
    monkeypatch.setattr(claude_statusline, "HOOK_RUNTIME_BIN_PATH", tmp_path / "missing")
    with pytest.raises(RuntimeError, match="stable Claude telemetry launcher is unavailable"):
        claude_statusline._statusline_command()
    with pytest.raises(ValueError, match="path must be absolute"):
        claude_statusline._render_statusline_command(Path("relative/launcher"))

    assert not claude_statusline._is_adapter({"command": 42})
    assert not claude_statusline._is_adapter({"command": "'unterminated"})
    invalid_encoded = (
        'powershell -NoProfile -Command "& '
        "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%%%')))\""
    )
    assert not claude_statusline._is_adapter({"command": invalid_encoded})
