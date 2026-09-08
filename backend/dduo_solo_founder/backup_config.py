from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dduo_solo_founder.backup import decode_recovery_key, generate_recovery_key
from dduo_solo_founder.project_config import portable_file_lock
from dduo_solo_founder.runtime_settings import load_project_runtime_environment

CONFIG_DIR = Path.home() / ".config" / "dduo-solo-founder"
BACKUP_REGISTRY_PATH = CONFIG_DIR / "backups.json"
KEY_DIR = CONFIG_DIR / "backup-keys"


def _empty_registry() -> dict:
    return {"version": 1, "projects": {}}


def load_backup_registry(path: Path | None = None) -> dict:
    """Read and validate the user-level per-project backup registry."""
    path = path or BACKUP_REGISTRY_PATH
    if not path.exists():
        return _empty_registry()
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid dDuo Solo Founder backup registry: {path}") from exc
    if value.get("version") != 1 or not isinstance(value.get("projects"), dict):
        raise RuntimeError(f"unsupported dDuo Solo Founder backup registry: {path}")
    return value


def _write_registry(path: Path, value: dict) -> None:
    _write_private_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_private_text(path: Path, value: str) -> None:
    """Atomically replace one user-only configuration or key file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(value)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup_key_path(project_id: str, key_dir: Path | None = None) -> Path:
    """Return a traversal-safe path for one project's local recovery key."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
        raise ValueError("invalid project identifier")
    return (key_dir or KEY_DIR) / f"{project_id}.key"


def load_backup_settings(project_id: str, path: Path | None = None) -> dict | None:
    """Return one project's backup settings, or None when it is unconfigured."""
    return load_backup_registry(path)["projects"].get(project_id)


def read_recovery_key(project_id: str, key_dir: Path | None = None) -> str:
    """Read and validate one locally stored recovery key."""
    path = backup_key_path(project_id, key_dir)
    if not path.is_file():
        raise FileNotFoundError(f"backup key not found for project {project_id}")
    key = path.read_text().strip()
    decode_recovery_key(key)
    return key


def configure_project_backup(
    project: dict,
    destination: Path,
    *,
    include_qdrant: bool = True,
    daily: int = 7,
    weekly: int = 4,
    monthly: int = 6,
    auto_seconds: int = 5 * 60,
    recovery_key: str | None = None,
    replace_key: bool = False,
    exact_destination: bool = False,
    registry_path: Path | None = None,
    key_dir: Path | None = None,
) -> tuple[dict, str, bool]:
    """Persist one isolated backup target and return settings, key, and key-newness."""
    if min(daily, weekly, monthly) < 0:
        raise ValueError("retention values cannot be negative")
    if auto_seconds < 1:
        raise ValueError("automatic backup interval must be positive")
    project_id = str(project["id"])
    root = destination.expanduser().resolve()
    if exact_destination:
        archive_dir = root
    else:
        slug = (
            re.sub(r"[^a-z0-9]+", "-", str(project.get("name", "project")).lower()).strip("-")[:48]
            or "project"
        )
        archive_dir = root / "dduo-solo-founder" / f"{slug}-{project_id[:8]}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "destination": str(archive_dir),
        "include_qdrant": include_qdrant,
        "retention": {"daily": daily, "weekly": weekly, "monthly": monthly},
        "auto_seconds": auto_seconds,
        "configured_at": datetime.now(timezone.utc).isoformat(),
    }
    registry_path = registry_path or BACKUP_REGISTRY_PATH
    with portable_file_lock(registry_path):
        key_path = backup_key_path(project_id, key_dir)
        is_new = not key_path.exists()
        if is_new:
            key = recovery_key or generate_recovery_key()
            decode_recovery_key(key)
            _write_private_text(key_path, key + "\n")
        else:
            key = read_recovery_key(project_id, key_path.parent)
            if recovery_key and recovery_key != key:
                if not replace_key:
                    raise ValueError(
                        "a different recovery key is already configured for this project"
                    )
                decode_recovery_key(recovery_key)
                key = recovery_key
                _write_private_text(key_path, key + "\n")
        registry = load_backup_registry(registry_path)
        registry["projects"][project_id] = settings
        _write_registry(registry_path, registry)
    return settings, key, is_new


def compose_backup_environment(
    project: dict,
    project_root: Path,
    *,
    registry_path: Path | None = None,
    key_dir: Path | None = None,
    fallback_dir: Path | None = None,
) -> dict[str, str]:
    """Resolve safe bind mounts without exposing another project's key to a container."""
    project_id = str(project["id"])
    settings = load_backup_settings(project_id, registry_path)
    key_path = backup_key_path(project_id, key_dir)
    error = ""
    configured = bool(settings)
    destination = Path(settings["destination"]) if settings else Path()
    if settings and not destination.is_dir():
        configured = False
        error = "The configured backup destination is unavailable."
    if settings and not key_path.is_file():
        configured = False
        error = "The project backup key is missing."
    if settings and key_path.is_file():
        try:
            read_recovery_key(project_id, key_path.parent)
        except (FileNotFoundError, ValueError, RuntimeError):
            configured = False
            error = "The project backup key is invalid."

    project_config = (project_root / ".dduo-solo-founder" / "project.toml").resolve()
    if fallback_dir:
        fallback = fallback_dir / project_id
        fallback.mkdir(parents=True, exist_ok=True)
        placeholder_key = fallback / "unconfigured.key"
        if not placeholder_key.exists():
            placeholder_key.write_text("unconfigured\n")
            placeholder_key.chmod(0o600)
    else:
        # A real initialized project always has this file. It is mounted only as an
        # inert placeholder while BACKUP_CONFIGURED=false, avoiding host writes on start.
        fallback = project_root.resolve()
        placeholder_key = project_config

    retention = (settings or {}).get("retention", {})
    environment = load_project_runtime_environment(project_id)
    environment.update(
        {
            "DDUO_SOLO_FOUNDER_PROJECT_ID": project_id,
            "DDUO_SOLO_FOUNDER_BACKUP_CONFIGURED": str(configured).lower(),
            "DDUO_SOLO_FOUNDER_BACKUP_SOURCE": str(destination if configured else fallback),
            "DDUO_SOLO_FOUNDER_BACKUP_KEY_SOURCE": str(key_path if configured else placeholder_key),
            "DDUO_SOLO_FOUNDER_PROJECT_CONFIG_SOURCE": str(project_config),
            "DDUO_SOLO_FOUNDER_BACKUP_INCLUDE_QDRANT": str(
                bool((settings or {}).get("include_qdrant", True))
            ).lower(),
            "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_DAILY": str(retention.get("daily", 7)),
            "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_WEEKLY": str(retention.get("weekly", 4)),
            "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_MONTHLY": str(retention.get("monthly", 6)),
            "BACKUP_AUTO_SECONDS": str((settings or {}).get("auto_seconds", 5 * 60)),
            "DDUO_SOLO_FOUNDER_BACKUP_CONFIGURATION_ERROR": error,
        }
    )
    return environment
