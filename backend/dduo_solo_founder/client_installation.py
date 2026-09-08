"""Resolve one explicit native client installation for dDuo.

The adapter that a person uses to chat, the process that verifies that adapter
and the process that performs memory sleep must never silently pick different
copies of Codex or Claude.  This module keeps that decision small, local and
serialisable without persisting authentication or IDE-specific state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .native_process import npm_client_entrypoint


ClientFamily = Literal["codex", "claude"]
ClientSurface = Literal["cli", "vscode", "desktop", "unknown"]

CLIENT_CONFIG_ROOT = Path.home() / ".config" / "dduo-solo-founder"
EXECUTABLE_RECORD = CLIENT_CONFIG_ROOT / "client-executables.json"
SCOPE_RECORD = CLIENT_CONFIG_ROOT / "client-scopes.json"
DEFAULT_CODEX_CONFIG = Path.home() / ".codex"
DEFAULT_CLAUDE_CONFIG = Path.home() / ".claude"
DEFAULT_CODEX_DESKTOP_EXECUTABLE = Path(
    "/Applications/ChatGPT.app/Contents/Resources/codex"
)
CODEX_DESKTOP_EXECUTABLE_ENV = "DDUO_SOLO_FOUNDER_CODEX_DESKTOP"


class ClientInstallationError(RuntimeError):
    """One selected client installation cannot safely be used."""


@dataclass(frozen=True)
class ClientCommand:
    executable: Path
    prefix_args: tuple[str, ...] = ()
    node_executable: Path | None = None


@dataclass(frozen=True)
class ClientInstallation:
    family: ClientFamily
    surface_hint: ClientSurface
    config_dir: Path
    management_command: ClientCommand
    probe_command: ClientCommand | None = None

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        environment = dict(os.environ if base is None else base)
        if self.family == "codex":
            environment["CODEX_HOME"] = str(self.config_dir)
        else:
            environment["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        return environment


def _validate_family(value: str) -> ClientFamily:
    family = value.strip().lower()
    if family not in {"codex", "claude"}:
        raise ClientInstallationError("client must be codex or claude")
    return family  # type: ignore[return-value]


def _validate_surface(value: str) -> ClientSurface:
    surface = value.strip().lower()
    if surface not in {"cli", "vscode", "desktop", "unknown"}:
        raise ClientInstallationError("surface must be cli, vscode, desktop, or unknown")
    return surface  # type: ignore[return-value]


def _normal_path(value: str | Path, *, label: str, require_exists: bool) -> Path:
    raw = str(value).strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ClientInstallationError(f"{label} must be one non-empty path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ClientInstallationError(f"{label} must be an absolute path")
    # absolute() intentionally does not dereference a vendor-maintained launcher
    # symlink.  Updates may change its destination without changing the stable
    # launcher path recorded by dDuo.
    path = Path(os.path.abspath(path))
    if require_exists and (not path.is_file() or not os.access(path, os.X_OK)):
        raise ClientInstallationError(f"{label} is not an executable file")
    return path


def _read_json(
    path: Path, *, repair_family: ClientFamily | None = None, required_field: str | None = None
) -> dict:
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe record")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ClientInstallationError(
            f"dDuo client record {path} is invalid; run setup repair"
        ) from error
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
        or not isinstance(data.get("clients"), dict)
    ):
        raise ClientInstallationError(
            f"dDuo client record {path} is invalid; run setup repair"
        )
    clients = dict(data.get("clients", {}))
    for family, entry in list(clients.items()):
        try:
            if family not in {"codex", "claude"} or not isinstance(entry, dict) or not entry:
                raise ClientInstallationError("invalid client entry")
            if required_field is not None:
                if not isinstance(entry.get(required_field), str):
                    raise ClientInstallationError("invalid client path")
                _normal_path(entry[required_field], label=required_field, require_exists=False)
            if required_field == "launcher_path" and "node_executable" in entry:
                if not isinstance(entry["node_executable"], str):
                    raise ClientInstallationError("invalid Node path")
                _normal_path(entry["node_executable"], label="node executable", require_exists=False)
        except ClientInstallationError as error:
            if family == repair_family:
                del clients[family]
            else:
                raise ClientInstallationError(
                    f"dDuo client record {path} is invalid; run setup repair"
                ) from error
    return {**data, "clients": clients}


def _record_client(path: Path, family: ClientFamily, *, required_field: str, repair: bool = False) -> dict:
    clients = _read_json(
        path, repair_family=family if repair else None, required_field=required_field
    ).get("clients", {})
    if family not in clients:
        return {}
    entry = clients[family]
    if not isinstance(entry, dict) or not isinstance(entry.get(required_field), str) or not entry[required_field]:
        if repair:
            return {}
        raise ClientInstallationError(f"dDuo client record {path} is invalid; run setup repair")
    return dict(entry)


def _record_path(entry: dict, field: str, *, label: str) -> Path | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ClientInstallationError(f"saved {label} is invalid; run setup repair")
    try:
        return _normal_path(value, label=label, require_exists=True)
    except ClientInstallationError as error:
        raise ClientInstallationError(f"saved {label} is invalid; run setup repair") from error


def default_config_dir(family: ClientFamily, environment: dict[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    key = "CODEX_HOME" if family == "codex" else "CLAUDE_CONFIG_DIR"
    value = environment.get(key)
    if value:
        path = _normal_path(value, label=key, require_exists=False)
        if path.exists() and (not path.is_dir() or not os.access(path, os.R_OK | os.W_OK)):
            raise ClientInstallationError(f"{key} is not an accessible configuration directory")
        return path
    return DEFAULT_CODEX_CONFIG if family == "codex" else DEFAULT_CLAUDE_CONFIG


def _desktop_candidate(surface: ClientSurface) -> Path | None:
    if surface not in {"desktop", "unknown"}:
        return None
    configured = os.getenv(CODEX_DESKTOP_EXECUTABLE_ENV)
    candidate = Path(configured).expanduser() if configured else DEFAULT_CODEX_DESKTOP_EXECUTABLE
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.absolute()
    return None


def _path_candidate(family: ClientFamily) -> Path | None:
    found = shutil.which(family)
    return Path(found).absolute() if found else None


def _node_for_launcher(family: ClientFamily, launcher: Path) -> Path | None:
    """Persist Node only when a launcher is clearly a Node shim.

    Native executables deliberately keep ``None``: they must not depend on an
    ambient PATH.  Node itself remains an explicit, validated dependency for
    supported npm launchers.
    """
    if not _launcher_requires_node(launcher, family):
        return None
    node = shutil.which("node")
    if not node:
        raise ClientInstallationError(
            f"{family.title()} uses a Node launcher but Node is not available; repair its official installation"
        )
    return _normal_path(node, label="node executable", require_exists=True)


def _launcher_requires_node(launcher: Path, family: ClientFamily) -> bool:
    """Recognise the supported Node launcher shapes without resolving symlinks."""
    if launcher.suffix.lower() in {".cmd", ".bat", ".js", ".mjs", ".cjs"}:
        return True
    try:
        with launcher.open("rb") as stream:
            header = stream.read(256)
    except OSError:
        return False
    if re.match(rb"^#![^\r\n]*\bnode(?:\s|$)", header):
        return True
    return bool(
        re.match(rb"^#![^\r\n]*\b(?:sh|bash|zsh|dash|ksh)(?:\s|$)", header)
        and npm_client_entrypoint(launcher, family) is not None
    )


def resolve_client_installation(
    family_value: str,
    *,
    surface_value: str = "unknown",
    config_dir: str | Path | None = None,
    executable: str | Path | None = None,
    node_executable: str | Path | None = None,
    probe_executable: str | Path | None = None,
    environment: dict[str, str] | None = None,
    record_path: Path = EXECUTABLE_RECORD,
    scope_path: Path = SCOPE_RECORD,
    repair: bool = False,
) -> ClientInstallation:
    """Resolve one management command with explicit, non-fallback precedence."""
    family = _validate_family(family_value)
    surface = _validate_surface(surface_value)
    if family == "claude" and surface == "desktop":
        raise ClientInstallationError("desktop is only a supported Codex surface")
    if repair and (config_dir is None or executable is None):
        raise ClientInstallationError("client record repair requires an explicit executable and configuration directory")
    if node_executable is not None and executable is None:
        raise ClientInstallationError("an explicit Node executable requires an explicit client executable")
    saved_scope = _record_client(scope_path, family, required_field="config_dir", repair=repair).get("config_dir")
    saved_config = None
    if saved_scope is not None:
        try:
            saved_config = _normal_path(
                saved_scope,
                label="saved client configuration directory",
                require_exists=False,
            )
        except ClientInstallationError:
            if not repair:
                raise
    selected_environment = os.environ if environment is None else environment
    config_key = "CODEX_HOME" if family == "codex" else "CLAUDE_CONFIG_DIR"
    explicit_scope = config_dir is not None or bool(selected_environment.get(config_key))
    selected_config = (
        _normal_path(config_dir, label="client config directory", require_exists=False)
        if config_dir is not None
        else default_config_dir(family, selected_environment)
        if explicit_scope or saved_config is None
        else saved_config
    )
    if saved_config is not None:
        if saved_config != selected_config:
            raise ClientInstallationError(
                f"dDuo manages {family.title()} in {saved_config}; "
                "repair or remove that profile before selecting another configuration directory"
            )

    entry = _record_client(record_path, family, required_field="launcher_path", repair=repair)
    if executable is not None:
        launcher = _normal_path(executable, label="client executable", require_exists=True)
        if node_executable is not None:
            node = _normal_path(node_executable, label="node executable", require_exists=True)
        elif entry and str(launcher) == entry.get("launcher_path"):
            node = _record_path(entry, "node_executable", label="node executable")
            if _launcher_requires_node(launcher, family) and node is None:
                raise ClientInstallationError(
                    "saved client executable requires its recorded Node executable; run setup repair"
                )
        else:
            node = _node_for_launcher(family, launcher)
    elif entry:
        launcher = _record_path(entry, "launcher_path", label="client executable")
        if launcher is None:  # defensive: record entry is invalid rather than absent
            raise ClientInstallationError("saved client executable is invalid; run setup repair")
        node = _record_path(entry, "node_executable", label="node executable")
        if _launcher_requires_node(launcher, family) and node is None:
            raise ClientInstallationError(
                "saved client executable requires its recorded Node executable; run setup repair"
            )
    else:
        launcher = _path_candidate(family)
        if launcher is None and family == "codex":
            launcher = _desktop_candidate(surface)
        if launcher is None:
            raise ClientInstallationError(
                f"{family.title()} is not installed. Install its official CLI, then run dDuo setup again."
            )
        node = _node_for_launcher(family, launcher)

    probe = None
    if probe_executable is not None:
        probe_path = _normal_path(probe_executable, label="client probe executable", require_exists=True)
        probe = ClientCommand(probe_path, node_executable=_node_for_launcher(family, probe_path))
    return ClientInstallation(
        family=family,
        surface_hint=surface,
        config_dir=selected_config,
        management_command=ClientCommand(launcher, node_executable=node),
        probe_command=probe,
    )


def persist_client_installation(
    installation: ClientInstallation,
    *,
    record_path: Path = EXECUTABLE_RECORD,
    scope_path: Path = SCOPE_RECORD,
) -> None:
    """Atomically persist one verified launcher and one managed profile per family."""
    scope_entry = _record_client(scope_path, installation.family, required_field="config_dir")
    existing_scope = scope_entry.get("config_dir")
    if existing_scope and str(existing_scope) != str(installation.config_dir):
        raise ClientInstallationError(
            f"dDuo already manages another {installation.family.title()} profile on this host; "
            "remove it explicitly before selecting a second profile"
        )
    record = _read_json(record_path, required_field="launcher_path")
    record["version"] = 1
    clients = record.setdefault("clients", {})
    clients[installation.family] = {"launcher_path": str(installation.management_command.executable)}
    if installation.management_command.node_executable is not None:
        clients[installation.family]["node_executable"] = str(installation.management_command.node_executable)
    scopes = _read_json(scope_path, required_field="config_dir")
    scopes["version"] = 1
    scopes.setdefault("clients", {})[installation.family] = {"config_dir": str(installation.config_dir)}
    snapshots = {
        path: path.read_bytes() if path.exists() else None
        for path in (record_path, scope_path)
    }
    try:
        for path, value in ((record_path, record), (scope_path, scopes)):
            _write_private_json(path, value)
    except OSError as error:
        # The two records are a single registration. Do not leave a partial
        # scope that makes a later install look like a conflicting profile.
        restore_errors: list[OSError] = []
        for path, original in snapshots.items():
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _write_private_bytes(path, original)
            except OSError as restore_error:
                restore_errors.append(restore_error)
        if restore_errors:
            raise ClientInstallationError(
                "client registration failed and rollback was incomplete; run setup repair"
            ) from restore_errors[0]
        raise ClientInstallationError("could not persist the selected client installation") from error


def _write_private_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(contents)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _write_private_json(path: Path, value: dict) -> None:
    _write_private_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def remove_client_installation(
    family_value: str,
    *,
    record_path: Path = EXECUTABLE_RECORD,
    scope_path: Path = SCOPE_RECORD,
) -> None:
    """Remove dDuo ownership records after native deregistration succeeds."""
    family = _validate_family(family_value)
    for path, required_field in ((record_path, "launcher_path"), (scope_path, "config_dir")):
        value = _read_json(path, required_field=required_field)
        clients = value.get("clients")
        if not isinstance(clients, dict) or family not in clients:
            continue
        del clients[family]
        _write_private_json(path, value)
