"""Per-folder onboarding consent, separate from project data and client installs."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from dduo_solo_founder.project_config import CONFIG_PATH, project_root_fingerprint

ACTIVATION_DIR = Path.home() / ".config" / "dduo-solo-founder" / "activation"


def _marker(root: Path) -> Path:
    return ACTIVATION_DIR / (project_root_fingerprint(root).split(":", 1)[1] + ".declined")


def setup_declined(root: Path) -> bool:
    """An unreadable preference never authorizes initializing any memory."""
    return _marker(root).exists()


def _has_regular_marker(marker: Path) -> bool:
    try:
        mode = marker.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise RuntimeError("activation preference must be a regular file")
    return True


def decline_setup(root: Path) -> None:
    """Remember a refusal without creating a project or disabling an existing one."""
    if (root / CONFIG_PATH).exists():
        raise ValueError("project memory is already configured; declining setup cannot disable it")
    ACTIVATION_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ACTIVATION_DIR.is_symlink():
        raise RuntimeError("activation preferences directory must not be a symlink")
    marker = _marker(root)
    if _has_regular_marker(marker):
        return
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except (FileExistsError, PermissionError) as error:
        # A directory created after the precheck yields EACCES on Windows.
        # Only a regular-file EEXIST race is an idempotent successful decline.
        exists = _has_regular_marker(marker)
        if isinstance(error, FileExistsError) and exists:
            return
        raise
    os.close(descriptor)


def allow_setup(root: Path) -> None:
    """Explicit activation is the only operation that clears a previous refusal."""
    if ACTIVATION_DIR.is_symlink():
        raise RuntimeError("activation preferences directory must not be a symlink")
    _marker(root).unlink(missing_ok=True)
