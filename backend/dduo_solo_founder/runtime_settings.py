"""Portable, project-scoped runtime settings for full-recovery backups.

Only model/index/retrieval/sleep/backup tuning belongs here.  Endpoints, bind
mounts, credentials and host identity are deliberately reconstructed by the
destination host instead of being copied from the old machine.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from dduo_solo_founder.backup import BackupError
from dduo_solo_founder.config import Settings
from dduo_solo_founder.project_secrets import (
    load_project_runtime_environment_text,
    save_project_runtime_environment,
)

RUNTIME_SETTINGS_SCHEMA_VERSION = 1

# Keep the order stable: snapshots and environment files remain reviewable.
PORTABLE_RUNTIME_ENV: dict[str, str] = {
    "embedding_provider": "EMBEDDING_PROVIDER",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_index_version": "EMBEDDING_INDEX_VERSION",
    "task_embedding_index_version": "TASK_EMBEDDING_INDEX_VERSION",
    "task_index_max_characters": "TASK_INDEX_MAX_CHARACTERS",
    "task_index_max_utf8_bytes": "TASK_INDEX_MAX_UTF8_BYTES",
    "task_retrieval_similarity_threshold": "TASK_RETRIEVAL_SIMILARITY_THRESHOLD",
    "retrieval_similarity_threshold": "RETRIEVAL_SIMILARITY_THRESHOLD",
    "retrieval_episode_limit": "RETRIEVAL_EPISODE_LIMIT",
    "retrieval_fact_limit": "RETRIEVAL_FACT_LIMIT",
    "retrieval_heuristic_limit": "RETRIEVAL_HEURISTIC_LIMIT",
    "sleep_idle_seconds": "SLEEP_IDLE_SECONDS",
    "sleep_turn_threshold": "SLEEP_TURN_THRESHOLD",
    "sleep_batch_size": "SLEEP_BATCH_SIZE",
    "sleep_batch_char_limit": "SLEEP_BATCH_CHAR_LIMIT",
    "sleep_poll_seconds": "SLEEP_POLL_SECONDS",
    "sleep_cli_timeout_seconds": "SLEEP_CLI_TIMEOUT_SECONDS",
    "artifact_max_bytes": "ARTIFACT_MAX_BYTES",
    "collection_prefix": "COLLECTION_PREFIX",
    "backup_include_qdrant": "DDUO_SOLO_FOUNDER_BACKUP_INCLUDE_QDRANT",
    "backup_retention_daily": "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_DAILY",
    "backup_retention_weekly": "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_WEEKLY",
    "backup_retention_monthly": "DDUO_SOLO_FOUNDER_BACKUP_RETENTION_MONTHLY",
    "backup_auto_seconds": "BACKUP_AUTO_SECONDS",
}
PORTABLE_RUNTIME_KEYS = tuple(PORTABLE_RUNTIME_ENV)

# alpha.47 already emitted these fields.  Requiring that original contract
# catches truncated or unrelated JSON while the four later additions remain
# backwards-compatible with already-created v2 archives.
_V1_REQUIRED_KEYS = frozenset(
    {
        "embedding_provider",
        "embedding_model",
        "embedding_index_version",
        "task_embedding_index_version",
        "task_index_max_characters",
        "task_index_max_utf8_bytes",
        "task_retrieval_similarity_threshold",
        "retrieval_similarity_threshold",
        "retrieval_episode_limit",
        "retrieval_fact_limit",
        "retrieval_heuristic_limit",
        "sleep_idle_seconds",
        "sleep_turn_threshold",
        "sleep_batch_size",
        "sleep_batch_char_limit",
        "collection_prefix",
        "backup_include_qdrant",
        "backup_retention_daily",
        "backup_retention_weekly",
        "backup_retention_monthly",
    }
)


@dataclass(frozen=True)
class RestoredRuntimeSettings:
    """Validated restore result and the backup registry options it carries."""

    path: Path
    values: dict[str, object]
    environment: dict[str, str]

    @property
    def backup_options(self) -> dict[str, object]:
        return {
            "include_qdrant": bool(self.values["backup_include_qdrant"]),
            "daily": int(self.values["backup_retention_daily"]),
            "weekly": int(self.values["backup_retention_weekly"]),
            "monthly": int(self.values["backup_retention_monthly"]),
            "auto_seconds": int(self.values["backup_auto_seconds"]),
        }


def _primitive_is_valid(value: object, default: object) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if isinstance(default, str):
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= 500
            and not any(character in value for character in "\x00\r\n")
        )
    return False


def validate_runtime_settings_payload(payload: object) -> tuple[str, dict[str, object]]:
    """Validate an authenticated runtime-settings.json without trusting its shape."""
    if not isinstance(payload, dict):
        raise BackupError("runtime settings are malformed")
    app_version = payload.get("app_version")
    raw = payload.get("settings")
    if (
        payload.get("schema_version") != RUNTIME_SETTINGS_SCHEMA_VERSION
        or not isinstance(app_version, str)
        or not app_version.strip()
        or len(app_version) > 100
        or not isinstance(raw, dict)
    ):
        raise BackupError("runtime settings are malformed")
    keys = set(raw)
    allowed = set(PORTABLE_RUNTIME_KEYS)
    if not _V1_REQUIRED_KEYS.issubset(keys) or not keys.issubset(allowed):
        raise BackupError("runtime settings contain missing or unsupported fields")

    defaults = Settings.model_validate({}).model_dump()
    for key, value in raw.items():
        if not _primitive_is_valid(value, defaults[key]):
            raise BackupError(f"runtime setting {key} has an invalid value")
    try:
        # model_validate does not consult the destination host environment. It
        # applies only Settings defaults and field constraints to archive data.
        validated = Settings.model_validate(raw).model_dump()
    except (TypeError, ValueError) as exc:
        raise BackupError("runtime settings failed validation") from exc
    normalized = {key: validated[key] for key in PORTABLE_RUNTIME_KEYS}
    positive = (
        "sleep_idle_seconds",
        "sleep_turn_threshold",
        "sleep_batch_size",
        "sleep_batch_char_limit",
        "sleep_poll_seconds",
        "sleep_cli_timeout_seconds",
        "artifact_max_bytes",
        "backup_auto_seconds",
    )
    non_negative = (
        "retrieval_episode_limit",
        "retrieval_fact_limit",
        "retrieval_heuristic_limit",
        "backup_retention_daily",
        "backup_retention_weekly",
        "backup_retention_monthly",
    )
    if any(float(normalized[key]) <= 0 for key in positive) or any(
        int(normalized[key]) < 0 for key in non_negative
    ):
        raise BackupError("runtime settings contain an out-of-range value")
    for key in ("task_retrieval_similarity_threshold", "retrieval_similarity_threshold"):
        if not -1.0 <= float(normalized[key]) <= 1.0:
            raise BackupError(f"runtime setting {key} has an invalid value")
    return app_version.strip(), normalized


def read_runtime_settings(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("runtime settings are unreadable or malformed") from exc
    _, values = validate_runtime_settings_payload(payload)
    return values


def runtime_environment(values: dict[str, object]) -> dict[str, str]:
    """Serialize validated values into the exact variables consumed by Compose."""
    environment: dict[str, str] = {}
    for key, env_key in PORTABLE_RUNTIME_ENV.items():
        value = values[key]
        if isinstance(value, bool):
            environment[env_key] = str(value).lower()
        else:
            environment[env_key] = str(value)
    return environment


def _values_from_environment(environment: dict[str, str]) -> dict[str, object]:
    defaults = Settings.model_validate({}).model_dump()
    keys_by_environment = {value: key for key, value in PORTABLE_RUNTIME_ENV.items()}
    values: dict[str, object] = {}
    try:
        for env_key, raw in environment.items():
            key = keys_by_environment[env_key]
            default = defaults[key]
            if isinstance(default, bool):
                if raw not in {"true", "false"}:
                    raise ValueError("invalid boolean")
                values[key] = raw == "true"
            elif isinstance(default, int):
                values[key] = int(raw)
            elif isinstance(default, float):
                values[key] = float(raw)
            else:
                values[key] = raw
    except (KeyError, ValueError) as exc:
        raise RuntimeError("project runtime settings are malformed") from exc
    return values


def write_runtime_settings(
    output: Path,
    settings: Settings,
    *,
    app_version: str,
) -> None:
    """Write a canonical portable snapshot for one encrypted backup."""
    values = settings.model_dump()
    payload = {
        "schema_version": RUNTIME_SETTINGS_SCHEMA_VERSION,
        "app_version": app_version,
        "settings": {key: values[key] for key in PORTABLE_RUNTIME_KEYS},
    }
    # Run the same validator used by restore so an invalid local override can
    # never produce a backup that only fails when it is urgently needed.
    validate_runtime_settings_payload(payload)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o600)


def restore_project_runtime_settings(
    source: Path,
    project_id: str,
) -> RestoredRuntimeSettings:
    """Validate and atomically install one project's portable runtime profile."""
    values = read_runtime_settings(source)
    environment = runtime_environment(values)
    path = save_project_runtime_environment(project_id, environment)
    return RestoredRuntimeSettings(
        path=path,
        values=values,
        environment=environment,
    )


def load_project_runtime_environment(project_id: str) -> dict[str, str]:
    """Load a previously restored profile, rejecting tampering or foreign keys."""
    try:
        content = load_project_runtime_environment_text(project_id)
        if content is None:
            return {}
        lines = content.splitlines()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("project runtime settings are unreadable") from exc
    allowed = set(PORTABLE_RUNTIME_ENV.values())
    environment: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            raise RuntimeError("project runtime settings are malformed")
        key, value = line.split("=", 1)
        if key not in allowed or key in environment or any(c in value for c in "\x00\r\n"):
            raise RuntimeError("project runtime settings are malformed")
        environment[key] = value
    if set(environment) != allowed:
        raise RuntimeError("project runtime settings are incomplete")
    try:
        _, values = validate_runtime_settings_payload(
            {
                "schema_version": RUNTIME_SETTINGS_SCHEMA_VERSION,
                "app_version": "restored-profile",
                "settings": _values_from_environment(environment),
            }
        )
    except BackupError as exc:
        raise RuntimeError("project runtime settings are malformed") from exc
    return runtime_environment(values)
