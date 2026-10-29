"""Explicit repair of Codex's recorded launcher after a client update.

Only the selected executable's version is probed. Hook trust, authentication,
capture and runtime installation remain separate operations.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import client_installation
from .client_installation import ClientInstallationError
from .client_readiness import resolve_codex_executable


def repair_codex_client(
    *,
    executable: Path,
    surface: str,
    config_dir: Path,
    node_executable: Path | None = None,
    dry_run: bool = False,
    record_path: Path | None = None,
    scope_path: Path | None = None,
) -> dict:
    """Verify an explicit replacement, then persist with the existing rollback."""
    if surface.strip().lower() not in {"cli", "vscode", "desktop"}:
        raise ClientInstallationError("repair requires --surface cli, vscode, or desktop")
    records = record_path if record_path is not None else client_installation.EXECUTABLE_RECORD
    scopes = scope_path if scope_path is not None else client_installation.SCOPE_RECORD
    installation = client_installation.resolve_client_installation(
        "codex",
        surface_value=surface,
        config_dir=config_dir,
        executable=executable,
        node_executable=node_executable,
        record_path=records,
        scope_path=scopes,
    )
    if not installation.config_dir.is_dir() or not os.access(
        installation.config_dir, os.R_OK | os.W_OK
    ):
        raise ClientInstallationError(
            "repair requires an existing, accessible --client-config-dir"
        )
    # Providing installation is essential: the version check must never choose
    # another client from PATH or the default Desktop location.
    resolve_codex_executable(required=True, installation=installation)
    if not dry_run:
        # Persistence rechecks the managed profile after the external command
        # returns and restores both registration files if either write fails.
        client_installation.persist_client_installation(
            installation, record_path=records, scope_path=scopes
        )
    return {
        "client": "codex",
        "surface": installation.surface_hint,
        "client_executable": str(installation.management_command.executable),
        "client_config_dir": str(installation.config_dir),
        "client_node_executable": (
            str(installation.management_command.node_executable)
            if installation.management_command.node_executable is not None else None
        ),
        "dry_run": dry_run,
        "registration_updated": not dry_run,
        "compatibility_verified": True,
        "hook_authorization": "not_checked",
    }
