"""Lossless Claude status-line multiplexer for interactive usage telemetry."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from dduo_solo_founder.client_telemetry import (
    CLIENT_TELEMETRY_DIR,
    ClientTelemetrySpool,
)
from dduo_solo_founder.project_config import (
    REGISTRY_PATH,
    find_workspace_root,
    load_project,
    portable_file_lock,
)
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
ADAPTER_STATE_PATH = CLIENT_TELEMETRY_DIR / "claude-statusline.json"
LEGACY_ADAPTER_STATE_PATH = (
    Path.home() / ".config" / "dduo-solo-founder" / "usage-guard" / "claude-statusline.json"
)
HOOK_RUNTIME_BIN_PATH = (
    Path.home() / ".config" / "dduo-solo-founder" / "hook-runtime-bin"
)
CLAUDE_USAGE_LEDGER_PATH = CLIENT_TELEMETRY_DIR / "claude-sessions.json"
STATUSLINE_COMMAND = "dduo-solo-founder-claude-statusline"
MAX_STATUSLINE_BYTES = 4 * 1024 * 1024
PROJECT_SETTINGS_RELATIVE = Path(".claude") / "settings.json"
PROJECT_LOCAL_SETTINGS_RELATIVE = Path(".claude") / "settings.local.json"


def _default_adapter_state_path() -> Path:
    """Keep beta.5 restore ownership readable without writing new guard state."""
    if ADAPTER_STATE_PATH.exists():
        return ADAPTER_STATE_PATH
    if LEGACY_ADAPTER_STATE_PATH.exists():
        return LEGACY_ADAPTER_STATE_PATH
    return ADAPTER_STATE_PATH


def install_claude_statusline(
    *,
    settings_path: Path | None = None,
    state_path: Path | None = None,
    project_root: Path | None = None,
    managed_settings_paths: tuple[Path, ...] | None = None,
    repair: bool = False,
) -> bool:
    """Install the adapter without losing the effective prior status line.

    A project-aware install owns both the user fallback and the project's
    highest writable settings scope. Direct ``settings_path`` calls without a
    project root retain the v1 single-file contract for recovery compatibility.
    """
    if project_root is not None:
        return _install_project_scopes(
            project_root,
            user_settings_path=settings_path or CLAUDE_SETTINGS_PATH,
            state_path=state_path or _default_adapter_state_path(),
            managed_settings_paths=managed_settings_paths,
            repair=repair,
        )
    return _install_single_statusline(
        settings_path=settings_path or CLAUDE_SETTINGS_PATH,
        state_path=state_path or _default_adapter_state_path(),
        repair=repair,
    )


def _install_single_statusline(
    *,
    settings_path: Path,
    state_path: Path,
    repair: bool,
) -> bool:
    """Preserve the legacy single-user-scope lifecycle contract."""
    settings_file = settings_path
    adapter_state = state_path
    with portable_file_lock(settings_file):
        settings = _read_object(settings_file, missing={})
        current = settings.get("statusLine")
        if _is_adapter(current):
            try:
                state = _read_object(adapter_state, missing={})
            except RuntimeError:
                if not repair:
                    raise
                state = {}
            if _valid_adapter_state(state):
                return _normalize_adapter_command(settings_file, settings, current)
            if not repair:
                raise RuntimeError(
                    "Claude usage telemetry is installed but its restore state is invalid; "
                    "repair it from local Setup"
                )
            # The prior command cannot be reconstructed after its private state is
            # lost. Repair establishes a safe future uninstall boundary: remove
            # only dDuo's adapter rather than leaving a dead command behind.
            _write_private_json(
                adapter_state,
                {
                    "version": 1,
                    "had_previous_status_line": False,
                    "previous_status_line": None,
                },
            )
            _normalize_adapter_command(settings_file, settings, current)
            return True
        _write_private_json(
            adapter_state,
            {
                "version": 1,
                "had_previous_status_line": "statusLine" in settings,
                "previous_status_line": current,
            },
        )
        replacement = dict(current) if isinstance(current, Mapping) else {}
        replacement.update(
            {
                "type": "command",
                # Point at the release-independent shim. Claude may start with
                # a fresh PATH, so resolving only at install time is deliberate.
                "command": _statusline_command(),
            }
        )
        settings["statusLine"] = replacement
        _write_private_json(settings_file, settings)
    return True


def restore_claude_statusline(
    *,
    settings_path: Path | None = None,
    state_path: Path | None = None,
    project_root: Path | None = None,
) -> bool:
    """Restore only settings scopes still owned by dDuo."""
    settings_file = settings_path or CLAUDE_SETTINGS_PATH
    adapter_state = state_path or _default_adapter_state_path()
    try:
        state = _read_object(adapter_state, missing={})
    except RuntimeError:
        state = {}
    if state.get("version") == 2 and isinstance(state.get("projects"), dict):
        return _restore_project_scopes(
            state,
            state_path=adapter_state,
            project_root=project_root,
        )
    with portable_file_lock(settings_file):
        settings = _read_object(settings_file, missing={})
        if not _is_adapter(settings.get("statusLine")):
            return False
        state = _read_object(adapter_state, missing={})
        if not _valid_adapter_state(state):
            return False
        previous = state.get("previous_status_line")
        had_previous = state.get("had_previous_status_line")
        if had_previous is False or (had_previous is None and previous is None):
            settings.pop("statusLine", None)
        else:
            settings["statusLine"] = previous
        _write_private_json(settings_file, settings)
        adapter_state.unlink(missing_ok=True)
    return True


def claude_statusline_installed(
    *,
    settings_path: Path | None = None,
    state_path: Path | None = None,
    project_root: Path | None = None,
    managed_settings_paths: tuple[Path, ...] | None = None,
) -> bool:
    if project_root is not None:
        return bool(
            claude_statusline_status(
                project_root,
                user_settings_path=settings_path or CLAUDE_SETTINGS_PATH,
                state_path=state_path or _default_adapter_state_path(),
                managed_settings_paths=managed_settings_paths,
            )["ready"]
        )
    try:
        settings = _read_object(settings_path or CLAUDE_SETTINGS_PATH, missing={})
    except RuntimeError:
        return False
    return _is_adapter(settings.get("statusLine"))


def claude_statusline_status(
    project_root: Path,
    *,
    user_settings_path: Path | None = None,
    state_path: Path | None = None,
    managed_settings_paths: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    """Report the effective writable Claude status-line configuration."""
    root = project_root.expanduser().resolve()
    user_settings = user_settings_path or CLAUDE_SETTINGS_PATH
    adapter_state = state_path or _default_adapter_state_path()
    managed = _managed_statusline_override(managed_settings_paths)
    if managed is not None:
        return _statusline_status(
            ready=False,
            reason="managed_override",
            detail=f"Claude managed settings own statusLine at {managed}.",
        )
    try:
        state = _read_object(adapter_state, missing={})
        local_settings = _read_object(root / PROJECT_LOCAL_SETTINGS_RELATIVE, missing={})
        shared_settings = _read_object(root / PROJECT_SETTINGS_RELATIVE, missing={})
        user = _read_object(user_settings, missing={})
    except RuntimeError:
        return _statusline_status(
            ready=False,
            reason="settings_unavailable",
            detail="Claude status-line settings or private restore state are unavailable.",
        )
    local_adapter = _is_adapter(local_settings.get("statusLine"))
    user_adapter = _is_adapter(user.get("statusLine"))
    if _is_adapter(shared_settings.get("statusLine")):
        return _statusline_status(
            ready=False,
            reason="shared_adapter_unmanaged",
            detail=(
                "A legacy dDuo adapter is stored in shared project settings; "
                "remove it before repair or uninstall."
            ),
            local_adapter=local_adapter,
            user_adapter=user_adapter,
        )
    if state.get("version") != 2 or not isinstance(state.get("projects"), Mapping):
        return _statusline_status(
            ready=False,
            reason="restore_state_missing",
            detail="Project-aware Claude telemetry needs installation or repair.",
            local_adapter=local_adapter,
            user_adapter=user_adapter,
        )
    project_record = state["projects"].get(_project_scope_key(root))
    user_record = state.get("user")
    local_state_ready = _valid_scope_record(
        project_record,
        settings_path=root / PROJECT_LOCAL_SETTINGS_RELATIVE,
        project_root=root,
    )
    user_state_ready = _valid_scope_record(user_record, settings_path=user_settings)
    ready = local_adapter and user_adapter and local_state_ready and user_state_ready
    if ready:
        reason = "ready"
        detail = None
    elif not local_adapter or not local_state_ready:
        reason = "project_adapter_missing"
        detail = "The effective project-local Claude telemetry adapter needs repair."
    else:
        reason = "user_fallback_missing"
        detail = "The Claude telemetry fallback for other projects needs repair."
    return _statusline_status(
        ready=ready,
        reason=reason,
        detail=detail,
        local_adapter=local_adapter,
        user_adapter=user_adapter,
    )


def _statusline_status(
    *,
    ready: bool,
    reason: str,
    detail: str | None,
    local_adapter: bool = False,
    user_adapter: bool = False,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "installed": local_adapter or user_adapter,
        "reason": reason,
        "detail": detail,
        "project_adapter_installed": local_adapter,
        "user_fallback_installed": user_adapter,
        "scope": "project_default_and_user_fallback",
        # A one-off Claude ``--settings`` argument is process-local and cannot
        # be proven from Setup. If such an override is used, telemetry is not a
        # hard guarantee for that invocation.
        "cli_override_detection": "unavailable",
    }


def _install_project_scopes(
    project_root: Path,
    *,
    user_settings_path: Path,
    state_path: Path,
    managed_settings_paths: tuple[Path, ...] | None,
    repair: bool,
) -> bool:
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Claude telemetry project root is unavailable")
    managed = _managed_statusline_override(managed_settings_paths)
    if managed is not None:
        raise RuntimeError(
            f"Claude managed settings own statusLine at {managed}; "
            "project telemetry is unavailable"
        )
    _prepare_project_local_settings(root)
    with portable_file_lock(state_path):
        try:
            raw_state = _read_object(state_path, missing={})
        except RuntimeError:
            if not repair:
                raise
            raw_state = {}
        state, migrated = _multilevel_state(
            raw_state,
            user_settings_path=user_settings_path,
            repair=repair,
        )
        if migrated:
            _write_private_json(state_path, state)
        changed = migrated
        changed |= _install_scope(
            user_settings_path,
            state=state,
            state_path=state_path,
            project_root=None,
            repair=repair,
        )
        changed |= _install_scope(
            root / PROJECT_LOCAL_SETTINGS_RELATIVE,
            state=state,
            state_path=state_path,
            project_root=root,
            repair=repair,
        )
        return changed


def _multilevel_state(
    value: Mapping[str, Any],
    *,
    user_settings_path: Path,
    repair: bool,
) -> tuple[dict[str, Any], bool]:
    if value.get("version") == 2 and isinstance(value.get("projects"), dict):
        state = dict(value)
        state["projects"] = dict(value["projects"])
        return state, False
    if _valid_adapter_state(value):
        return (
            {
                "version": 2,
                "user": {
                    "settings_path": str(user_settings_path),
                    "file_existed": True,
                    "had_previous_status_line": value["had_previous_status_line"],
                    "previous_status_line": value["previous_status_line"],
                },
                "projects": {},
            },
            True,
        )
    if value and not repair:
        raise RuntimeError(
            "Claude usage telemetry restore state is invalid; repair it from local Setup"
        )
    return {"version": 2, "user": None, "projects": {}}, True


def _install_scope(
    settings_path: Path,
    *,
    state: dict[str, Any],
    state_path: Path,
    project_root: Path | None,
    repair: bool,
) -> bool:
    with portable_file_lock(settings_path):
        file_existed = settings_path.exists()
        settings = _read_object(settings_path, missing={})
        current = settings.get("statusLine")
        if project_root is None:
            record = state.get("user")
        else:
            record = state["projects"].get(_project_scope_key(project_root))
        record_valid = _valid_scope_record(
            record,
            settings_path=settings_path,
            project_root=project_root,
        )
        changed = False
        if _is_adapter(current):
            if not record_valid:
                if not repair:
                    raise RuntimeError(
                        "Claude usage telemetry is installed but its restore state is invalid; "
                        "repair it from local Setup"
                    )
                record = _scope_record(
                    settings_path,
                    file_existed=True,
                    had_previous=False,
                    previous=None,
                    project_root=project_root,
                )
                _store_scope_record(state, record, project_root=project_root)
                _write_private_json(state_path, state)
                changed = True
            return _normalize_adapter_command(settings_path, settings, current) or changed

        record = _scope_record(
            settings_path,
            file_existed=file_existed,
            had_previous="statusLine" in settings,
            previous=current,
            project_root=project_root,
        )
        _store_scope_record(state, record, project_root=project_root)
        # The restoration boundary must exist before the adapter becomes
        # effective. A crash can then only leave harmless extra state.
        _write_private_json(state_path, state)
        replacement = dict(current) if isinstance(current, Mapping) else {}
        replacement.update({"type": "command", "command": _statusline_command()})
        settings["statusLine"] = replacement
        _write_private_json(settings_path, settings)
        return True


def _scope_record(
    settings_path: Path,
    *,
    file_existed: bool,
    had_previous: bool,
    previous: Any,
    project_root: Path | None,
) -> dict[str, Any]:
    record = {
        "settings_path": str(settings_path),
        "file_existed": file_existed,
        "had_previous_status_line": had_previous,
        "previous_status_line": previous,
    }
    if project_root is not None:
        record["project_root"] = str(project_root)
    return record


def _store_scope_record(
    state: dict[str, Any],
    record: dict[str, Any],
    *,
    project_root: Path | None,
) -> None:
    if project_root is None:
        state["user"] = record
    else:
        state["projects"][_project_scope_key(project_root)] = record


def _valid_scope_record(
    value: Any,
    *,
    settings_path: Path,
    project_root: Path | None = None,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    if (
        value.get("settings_path") != str(settings_path)
        or not isinstance(value.get("file_existed"), bool)
        or not isinstance(value.get("had_previous_status_line"), bool)
        or "previous_status_line" not in value
    ):
        return False
    return project_root is None or value.get("project_root") == str(project_root)


def _project_scope_key(project_root: Path) -> str:
    return hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()


def _managed_statusline_override(paths: tuple[Path, ...] | None) -> Path | None:
    for path in paths if paths is not None else _default_managed_settings_paths():
        if not path.exists():
            continue
        try:
            settings = _read_object(path, missing={})
        except RuntimeError:
            return path
        if "statusLine" in settings:
            return path
    return None


def _default_managed_settings_paths() -> tuple[Path, ...]:
    if os.name == "nt":
        program_data = Path(os.environ.get("PROGRAMDATA", r"C:\\ProgramData"))
        return (program_data / "ClaudeCode" / "managed-settings.json",)
    if sys.platform == "darwin":
        return (Path("/Library/Application Support/ClaudeCode/managed-settings.json"),)
    return (Path("/etc/claude-code/managed-settings.json"),)


def _prepare_project_local_settings(project_root: Path) -> None:
    """Keep Claude's machine-local override local and reject tracked files."""
    shared_path = project_root / PROJECT_SETTINGS_RELATIVE
    shared = _read_object(shared_path, missing={})
    if _is_adapter(shared.get("statusLine")):
        raise RuntimeError(
            f"A legacy dDuo adapter is stored in shared settings at {shared_path}; "
            "remove it before installing project-local telemetry"
        )
    git = shutil.which("git")
    if not git:
        return
    try:
        top = subprocess.run(
            [git, "-C", str(project_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if top.returncode != 0:
        return
    try:
        if Path(top.stdout.strip()).resolve() != project_root:
            raise RuntimeError("Claude telemetry project root must be the Git worktree root")
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("Claude telemetry Git worktree is unavailable") from exc
    relative = PROJECT_LOCAL_SETTINGS_RELATIVE.as_posix()
    tracked = subprocess.run(
        [git, "-C", str(project_root), "ls-files", "--error-unmatch", "--", relative],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if tracked.returncode == 0:
        raise RuntimeError(
            ".claude/settings.local.json is tracked; dDuo will not modify a shared file"
        )
    if tracked.returncode != 1:
        raise RuntimeError("Claude telemetry could not verify the local settings Git state")
    resolved = subprocess.run(
        [git, "-C", str(project_root), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise RuntimeError("Claude telemetry could not resolve the local Git exclude file")
    exclude = Path(resolved.stdout.strip())
    if not exclude.is_absolute():
        exclude = project_root / exclude
    exclude = exclude.absolute()
    entries = (
        "/.claude/settings.local.json",
        "/.claude/settings.local.json.lock",
    )
    with portable_file_lock(exclude):
        if exclude.is_symlink() or (exclude.exists() and not exclude.is_file()):
            raise RuntimeError("Claude telemetry Git exclude file is unsafe")
        existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        missing = [entry for entry in entries if entry not in existing]
        if not missing:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        temporary = exclude.with_name(f"{exclude.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text("\n".join([*existing, *missing]) + "\n", encoding="utf-8")
            os.replace(temporary, exclude)
        finally:
            temporary.unlink(missing_ok=True)


def _restore_project_scopes(
    original_state: Mapping[str, Any],
    *,
    state_path: Path,
    project_root: Path | None,
) -> bool:
    del original_state  # Re-read under the lifecycle lock.
    restored = False
    with portable_file_lock(state_path):
        state = _read_object(state_path, missing={})
        if state.get("version") != 2 or not isinstance(state.get("projects"), dict):
            raise RuntimeError("Claude telemetry restore state is invalid")
        projects = dict(state["projects"])
        if project_root is None:
            _preflight_adapter_ownership(state, state_path=state_path)
        if project_root is None:
            selected = list(projects.items())
        else:
            root = project_root.expanduser().resolve()
            key = _project_scope_key(root)
            selected = [(key, projects[key])] if key in projects else []
        project_plans: list[tuple[str, Path, Mapping[str, Any]]] = []
        for key, record in selected:
            if not isinstance(record, Mapping):
                raise RuntimeError("Claude project telemetry restore state is invalid")
            try:
                root = Path(str(record["project_root"]))
                settings_path = Path(str(record["settings_path"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Claude project telemetry restore state is invalid") from exc
            if not _valid_scope_record(
                record,
                settings_path=settings_path,
                project_root=root,
            ) or key != _project_scope_key(root) or settings_path != (
                root / PROJECT_LOCAL_SETTINGS_RELATIVE
            ):
                raise RuntimeError("Claude project telemetry restore state is invalid")
            project_plans.append((key, settings_path, record))

        user_plan: tuple[Path, Mapping[str, Any]] | None = None
        if project_root is None:
            user_record = state.get("user")
            if not isinstance(user_record, Mapping):
                raise RuntimeError("Claude user telemetry restore state is invalid")
            try:
                user_settings = Path(str(user_record["settings_path"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Claude user telemetry restore state is invalid") from exc
            if not _valid_scope_record(user_record, settings_path=user_settings):
                raise RuntimeError("Claude user telemetry restore state is invalid")
            user_plan = (user_settings, user_record)

        # Validate every selected settings file before the first mutation. A
        # damaged later record must never leave an earlier project restored and
        # the runtime still installed only partially.
        for _, settings_path, record in project_plans:
            _preflight_restore_scope(settings_path, record)
        if user_plan is not None:
            _preflight_restore_scope(*user_plan)

        for key, settings_path, record in project_plans:
            restored |= _restore_scope(settings_path, record)
            projects.pop(key, None)
        state["projects"] = projects
        if user_plan is not None:
            restored |= _restore_scope(*user_plan)
            state["user"] = None

        if not state["projects"] and state.get("user") is None:
            state_path.unlink(missing_ok=True)
        else:
            _write_private_json(state_path, state)
    return restored


def _preflight_restore_scope(settings_path: Path, record: Mapping[str, Any]) -> None:
    if not _valid_scope_record(record, settings_path=settings_path):
        raise RuntimeError("Claude telemetry restore state is invalid")
    _read_object(settings_path, missing={})


def _preflight_adapter_ownership(
    state: Mapping[str, Any],
    *,
    state_path: Path,
) -> None:
    projects = state.get("projects")
    if not isinstance(projects, Mapping):
        raise RuntimeError("Claude telemetry restore state is invalid")
    for root in _known_project_roots(state_path):
        shared_settings_path = root / PROJECT_SETTINGS_RELATIVE
        shared = _read_object(shared_settings_path, missing={})
        if _is_adapter(shared.get("statusLine")):
            raise RuntimeError(
                f"Claude telemetry adapter in shared settings at {shared_settings_path} "
                "must be removed explicitly before uninstall"
            )
        settings_path = root / PROJECT_LOCAL_SETTINGS_RELATIVE
        settings = _read_object(settings_path, missing={})
        if not _is_adapter(settings.get("statusLine")):
            continue
        record = projects.get(_project_scope_key(root))
        if not _valid_scope_record(
            record,
            settings_path=settings_path,
            project_root=root,
        ):
            raise RuntimeError(
                f"Claude telemetry adapter at {settings_path} has no valid restore state"
            )
    user_record = state.get("user")
    if not isinstance(user_record, Mapping):
        raise RuntimeError("Claude user telemetry restore state is invalid")
    try:
        user_settings = Path(str(user_record["settings_path"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Claude user telemetry restore state is invalid") from exc
    user = _read_object(user_settings, missing={})
    if _is_adapter(user.get("statusLine")) and not _valid_scope_record(
        user_record,
        settings_path=user_settings,
    ):
        raise RuntimeError(
            f"Claude telemetry adapter at {user_settings} has no valid restore state"
        )


def _restore_scope(settings_path: Path, record: Mapping[str, Any]) -> bool:
    with portable_file_lock(settings_path):
        settings = _read_object(settings_path, missing={})
        if not _is_adapter(settings.get("statusLine")):
            return False
        if record["had_previous_status_line"]:
            settings["statusLine"] = record["previous_status_line"]
        else:
            settings.pop("statusLine", None)
        if not settings and record["file_existed"] is False:
            settings_path.unlink(missing_ok=True)
        else:
            _write_private_json(settings_path, settings)
    return True


def _known_project_roots(state_path: Path) -> set[Path]:
    roots: set[Path] = set()
    try:
        state = _read_object(state_path, missing={})
    except RuntimeError:
        state = {}
    projects = state.get("projects")
    if isinstance(projects, Mapping):
        for record in projects.values():
            if not isinstance(record, Mapping):
                continue
            try:
                roots.add(Path(str(record["project_root"])).expanduser().resolve())
            except (KeyError, OSError, RuntimeError, ValueError):
                continue
    if state_path not in {ADAPTER_STATE_PATH, LEGACY_ADAPTER_STATE_PATH}:
        return roots
    registry = _read_object(REGISTRY_PATH, missing={"version": 1, "projects": {}})
    registered = registry.get("projects")
    if isinstance(registered, Mapping):
        for project in registered.values():
            if not isinstance(project, Mapping):
                continue
            try:
                roots.add(Path(str(project["root_path"])).expanduser().resolve())
            except (KeyError, OSError, RuntimeError, ValueError):
                continue
    return roots


def _any_claude_statusline_installed(
    *,
    user_settings_path: Path | None = None,
    state_path: Path | None = None,
) -> bool:
    user_settings_path = user_settings_path or CLAUDE_SETTINGS_PATH
    state_path = state_path or _default_adapter_state_path()
    if user_settings_path == CLAUDE_SETTINGS_PATH:
        user_installed = claude_statusline_installed()
    else:
        user_installed = claude_statusline_installed(settings_path=user_settings_path)
    if user_installed:
        return True
    for root in _known_project_roots(state_path):
        for relative in (PROJECT_LOCAL_SETTINGS_RELATIVE, PROJECT_SETTINGS_RELATIVE):
            try:
                settings = _read_object(root / relative, missing={})
            except RuntimeError:
                return True
            if _is_adapter(settings.get("statusLine")):
                return True
    return False


def capture_statusline(payload: Mapping[str, Any]) -> None:
    """Enqueue one deduplicated project usage sample when Claude reports it."""
    project = _statusline_project(payload)
    if project is None:
        return
    project_id = str(project["id"])
    _claude_usage_event(payload, project_id, spool=ClientTelemetrySpool())


def main() -> None:  # pragma: no cover - exercised through the installed command
    raw = sys.stdin.buffer.read(MAX_STATUSLINE_BYTES + 1)
    if len(raw) > MAX_STATUSLINE_BYTES:
        return
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        capture_statusline(payload)
    except Exception:
        pass
    forwarded = _forward_previous(raw, payload)
    if forwarded is not None:
        sys.stdout.write(forwarded)
    return


def restore_main() -> None:
    """Restore the prior Claude status line during client uninstall."""
    try:
        _preflight_restore_entrypoint()
        restore_claude_statusline()
        adapter_remains = _any_claude_statusline_installed()
    except RuntimeError:
        adapter_remains = True
    if adapter_remains:
        print(
            "dDuo cannot restore Claude's previous status line because its preserved "
            "adapter state is missing or invalid. The runtime was not removed; open "
            "local Setup and repair Claude usage telemetry before retrying uninstall.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _preflight_restore_entrypoint() -> None:
    state_path = _default_adapter_state_path()
    state = _read_object(state_path, missing={})
    if state.get("version") == 2 and isinstance(state.get("projects"), dict):
        _preflight_adapter_ownership(state, state_path=state_path)
        return
    for root in _known_project_roots(state_path):
        for relative in (PROJECT_LOCAL_SETTINGS_RELATIVE, PROJECT_SETTINGS_RELATIVE):
            settings = _read_object(root / relative, missing={})
            if _is_adapter(settings.get("statusLine")):
                raise RuntimeError(
                    "Claude project telemetry adapter has no project-aware restore state"
                )
    user = _read_object(CLAUDE_SETTINGS_PATH, missing={})
    if _is_adapter(user.get("statusLine")) and not _valid_adapter_state(state):
        raise RuntimeError("Claude user telemetry adapter has no valid restore state")


def _claude_usage_event(
    payload: Mapping[str, Any],
    project_id: str,
    *,
    spool: ClientTelemetrySpool | None = None,
) -> dict[str, Any] | None:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id or len(session_id) > 256:
        return None
    cost = payload.get("cost")
    cumulative_cost = _money(cost.get("total_cost_usd")) if isinstance(cost, Mapping) else None
    if cumulative_cost is None:
        return None

    # Claude's cumulative session cost follows the session when its cwd moves.
    # Keep one content-free baseline per provider session; project_id scopes
    # delivery only and must not reset the cumulative counter.
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    with portable_file_lock(CLAUDE_USAGE_LEDGER_PATH):
        ledger = _read_usage_ledger()
        previous = ledger["sessions"].get(key)
        previous = previous if isinstance(previous, dict) else {}
        previous_cost = _money(previous.get("cumulative_cost")) or Decimal(0)
        epoch = int(previous.get("epoch") or 0)
        sequence = int(previous.get("sequence") or 0)
        if cumulative_cost < previous_cost:
            epoch += 1
            sequence = 0
            previous_cost = Decimal(0)
        delta_cost = max(cumulative_cost - previous_cost, Decimal(0))
        if cumulative_cost == previous_cost:
            return None
        event: dict[str, Any] | None = None
        if delta_cost > 0:
            sequence += 1
            event = {
                "kind": "agent_usage",
                "event_id": f"agent.claude.{key[:24]}.{epoch}.{sequence}",
                "provider": "claude",
                "measurement_source": "local_estimate",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                # Claude's status-line cost is cumulative for the session while
                # ``model.id`` is only the model active at this instant. The
                # delta can span models, so assigning it to one would be false.
                "client_cost_usd": format(delta_cost, "f"),
                "cost_source": "claude_code_client_estimate",
            }
            # Persist the durable outbox entry before advancing the ledger. If
            # the spool write fails, the next status update retries the exact
            # deterministic event id. If it already exists after a crash, the
            # spool deduplicates it and the ledger can safely advance.
            if spool is not None:
                spool.enqueue(project_id, event)
        ledger["sessions"][key] = {
            "cumulative_cost": str(cumulative_cost),
            "epoch": epoch,
            "sequence": sequence,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # A session may resume long after it was last seen. Its hashed baseline
        # is intentionally retained: evicting it would make the next cumulative
        # provider value look like a brand-new delta and double-count cost. The
        # ledger contains no prompts, paths, model output, or session id text.
        _write_private_json(CLAUDE_USAGE_LEDGER_PATH, ledger)
    # ``context_window.current_usage`` is a live context snapshot, not an
    # incremental consumption counter. Summing it would overstate usage, so
    # interactive tokens remain unavailable until Claude exposes a delta source.
    return event


def _read_usage_ledger() -> dict[str, Any]:
    ledger = _read_object(
        CLAUDE_USAGE_LEDGER_PATH,
        missing={"version": 1, "sessions": {}},
    )
    if ledger.get("version") != 1 or not isinstance(ledger.get("sessions"), dict):
        return {"version": 1, "sessions": {}}
    # Old guard-only keys are intentionally dropped on the next write.
    return {"version": 1, "sessions": dict(ledger["sessions"])}


def _statusline_project(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    workspace = payload.get("workspace")
    root_value = (
        workspace.get("project_dir") if isinstance(workspace, Mapping) else None
    ) or payload.get("cwd")
    if not isinstance(root_value, str) or not root_value.strip():
        return None
    try:
        root = find_workspace_root(Path(root_value).expanduser())
        return load_project(root)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _forward_previous(raw: bytes, payload: Mapping[str, Any] | None = None) -> str | None:
    try:
        state = _read_object(_default_adapter_state_path(), missing={})
        previous = _previous_statusline(state, payload or {})
        command = previous.get("command") if isinstance(previous, Mapping) else None
        if not isinstance(command, str) or not command.strip() or _is_adapter(previous):
            return None
        result = subprocess.run(
            command,
            shell=True,
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return result.stdout.decode("utf-8", errors="replace")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None


def _previous_statusline(
    state: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> Any:
    if state.get("version") == 1:
        return state.get("previous_status_line")
    if state.get("version") != 2 or not isinstance(state.get("projects"), Mapping):
        return None
    root = _statusline_root(payload)
    if root is not None:
        record = state["projects"].get(_project_scope_key(root))
        if _valid_scope_record(
            record,
            settings_path=root / PROJECT_LOCAL_SETTINGS_RELATIVE,
            project_root=root,
        ):
            if record["had_previous_status_line"]:
                return record["previous_status_line"]
            found, lower = _lower_statusline(root, state)
            return lower if found else None
    found, user = _effective_user_statusline(state)
    return user if found else None


def _statusline_root(payload: Mapping[str, Any]) -> Path | None:
    workspace = payload.get("workspace")
    value = (
        workspace.get("project_dir") if isinstance(workspace, Mapping) else None
    ) or payload.get("cwd")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return find_workspace_root(Path(value).expanduser())
    except (OSError, RuntimeError, ValueError):
        return None


def _lower_statusline(root: Path, state: Mapping[str, Any]) -> tuple[bool, Any]:
    try:
        project = _read_object(root / PROJECT_SETTINGS_RELATIVE, missing={})
    except RuntimeError:
        return False, None
    if "statusLine" in project:
        candidate = project["statusLine"]
        if not _is_adapter(candidate):
            return True, candidate
    return _effective_user_statusline(state)


def _effective_user_statusline(state: Mapping[str, Any]) -> tuple[bool, Any]:
    record = state.get("user")
    if not isinstance(record, Mapping):
        return False, None
    try:
        settings_path = Path(str(record["settings_path"]))
    except (KeyError, TypeError, ValueError):
        return False, None
    if not _valid_scope_record(record, settings_path=settings_path):
        return False, None
    try:
        settings = _read_object(settings_path, missing={})
    except RuntimeError:
        return False, None
    if "statusLine" not in settings:
        return False, None
    current = settings["statusLine"]
    if not _is_adapter(current):
        return True, current
    if record["had_previous_status_line"]:
        previous = record["previous_status_line"]
        return (False, None) if _is_adapter(previous) else (True, previous)
    return False, None


def _read_object(path: Path, *, missing: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(missing)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"unsafe configuration file: {path}")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o022:
        raise RuntimeError(f"writable configuration file is unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON configuration: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"configuration must be a JSON object: {path}")
    return value


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError(f"unsafe configuration directory: {path.parent}")
    if os.name != "nt":
        path.parent.chmod(0o700)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _is_adapter(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    command = value.get("command")
    if not isinstance(command, str):
        return False
    candidate = command.strip()
    powershell_prefix = (
        'powershell -NoProfile -Command "& '
        "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
    )
    powershell_suffix = "')))\""
    if candidate.startswith(powershell_prefix) and candidate.endswith(powershell_suffix):
        encoded = candidate[len(powershell_prefix) : -len(powershell_suffix)]
        try:
            candidate = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return False
    else:
        try:
            parsed = shlex.split(candidate)
        except ValueError:
            parsed = []
        if len(parsed) == 1:
            candidate = parsed[0]
    # Recognize old raw absolute paths too, including paths containing spaces.
    basename = candidate.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    expected = STATUSLINE_COMMAND.casefold()
    return basename in {expected, f"{expected}.cmd", f"{expected}.exe"}


def _statusline_command() -> str:
    # Prefer the release-independent shim: it follows hook-runtime-bin across
    # explicit transactional reinstalls while Claude keeps this absolute
    # command unchanged.
    # The pointer target is only a migration fallback when Setup cannot see the
    # stable tool-bin directory on its PATH.
    resolved = shutil.which(STATUSLINE_COMMAND) or _runtime_pointer_launcher()
    if not resolved:
        raise RuntimeError(
            "The stable Claude telemetry launcher is unavailable; repair the dDuo runtime"
        )
    return _render_statusline_command(Path(resolved).expanduser().absolute())


def _runtime_pointer_launcher() -> str | None:
    pointer = HOOK_RUNTIME_BIN_PATH
    try:
        if pointer.is_symlink() or not pointer.is_file():
            return None
        if os.name != "nt" and stat.S_IMODE(pointer.stat().st_mode) & 0o077:
            return None
        value = pointer.read_text(encoding="utf-8")
        if len(value) > 4096 or not value.strip():
            return None
        runtime_bin = Path(value.strip())
        if not runtime_bin.is_absolute():
            return None
        executable = runtime_bin / (
            f"{STATUSLINE_COMMAND}.exe" if os.name == "nt" else STATUSLINE_COMMAND
        )
        if executable.is_file() and os.access(executable, os.X_OK):
            return str(executable)
    except (OSError, UnicodeError):
        return None
    return None


def _render_statusline_command(
    path: Path | PureWindowsPath,
    *,
    windows: bool | None = None,
) -> str:
    use_windows = os.name == "nt" if windows is None else windows
    value = str(path)
    is_absolute = PureWindowsPath(value).is_absolute() if use_windows else path.is_absolute()
    if not is_absolute:
        raise ValueError("Claude telemetry launcher path must be absolute")
    if not use_windows:
        return shlex.quote(value)
    # Claude can invoke statusLine through Git Bash or PowerShell on Windows.
    # The outer command is valid in either; Base64 keeps the executable path
    # free of shell metacharacters, whitespace, and quote ambiguity.
    portable = value.replace("\\", "/")
    encoded = base64.b64encode(portable.encode("utf-8")).decode("ascii")
    return (
        'powershell -NoProfile -Command "& '
        "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        f"{encoded}')))\""
    )


def _normalize_adapter_command(
    settings_path: Path,
    settings: dict[str, Any],
    current: Any,
) -> bool:
    replacement = dict(current) if isinstance(current, Mapping) else {}
    replacement.update({"type": "command", "command": _statusline_command()})
    if current == replacement:
        return False
    settings["statusLine"] = replacement
    _write_private_json(settings_path, settings)
    return True


def _valid_adapter_state(value: Mapping[str, Any]) -> bool:
    return (
        value.get("version") == 1
        and isinstance(value.get("had_previous_status_line"), bool)
        and "previous_status_line" in value
    )


def _money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None
