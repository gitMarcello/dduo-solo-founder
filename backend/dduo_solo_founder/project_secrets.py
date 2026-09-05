"""Project-scoped host secrets used by isolated local and remote stacks.

The repository configuration deliberately contains no credential material.  A
project's runtime secrets live under the user's private dDuo configuration
directory and can therefore be collected by the host agent for a full recovery
bundle without copying unrelated environment files or the operating-system
keychain.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import uuid
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "dduo-solo-founder"
LEGACY_ENV_FILE = CONFIG_DIR / "env"
RETIRED_LEGACY_ENV_FILE = CONFIG_DIR / "env.alpha-retired"
PROJECT_SECRETS_DIR = CONFIG_DIR / "project-secrets"

# Keep this list intentionally small.  Full recovery means all secrets owned by
# dDuo, not a blind export of a host's environment or home directory.
SECRET_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "DDUO_DATABASE_PASSWORD",
        "DDUO_AUTH_SIGNING_SECRET",
        "DDUO_INFRASTRUCTURE_TOKEN",
        "DDUO_SESSION_SECRET",
        "DDUO_NODE_AUTHORITY_SECRET",
    }
)
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _project_slug(project_id: str) -> str:
    """Return a traversal-safe, stable directory name for one project."""
    value = project_id.strip()
    try:
        return str(uuid.UUID(value))
    except ValueError:
        if not value:
            raise ValueError("project id is required")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def project_secret_dir(project_id: str) -> Path:
    return PROJECT_SECRETS_DIR / _project_slug(project_id)


def project_env_file(project_id: str) -> Path:
    return project_secret_dir(project_id) / "dduo.env"


def project_runtime_env_file(project_id: str) -> Path:
    """Return the private Compose profile restored from runtime-settings.json."""
    return project_secret_dir(project_id) / "runtime.env"


def project_codex_home(project_id: str) -> Path:
    return project_secret_dir(project_id) / "codex"


def pending_manager_bootstrap_file(project_id: str) -> Path:
    """Private crash-recovery state for the first manager workstation token."""
    return project_secret_dir(project_id) / "pending-manager-bootstrap.json"


def _current_uid() -> int | None:
    return os.geteuid() if os.name == "posix" and hasattr(os, "geteuid") else None


def _require_owned(metadata: os.stat_result, *, label: str) -> None:
    current_uid = _current_uid()
    if current_uid is not None and metadata.st_uid != current_uid:
        raise PermissionError(f"{label} is not owned by the current user")


def _path_chain(path: Path) -> list[Path]:
    if not path.is_absolute():
        raise ValueError("private project paths must be absolute")
    return [*reversed(path.parents), path]


def _require_plain_directory_chain(path: Path) -> None:
    """Reject every existing symlink or non-directory before path traversal."""
    for candidate in _path_chain(path):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("private project directory chain is unsafe")


def _managed_directory_chain(path: Path) -> list[Path]:
    try:
        relative = path.relative_to(PROJECT_SECRETS_DIR)
    except ValueError as exc:
        raise ValueError("private project path escaped its managed directory") from exc
    chain = [PROJECT_SECRETS_DIR]
    current = PROJECT_SECRETS_DIR
    for part in relative.parts:
        current /= part
        chain.append(current)
    return chain


def _set_private_mode(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    os.chmod(path, mode, follow_symlinks=False)


def _private_directory(path: Path, *, create: bool = True) -> bool:
    """Validate and tighten one managed directory without following symlinks."""
    chain = _managed_directory_chain(path)
    _require_plain_directory_chain(PROJECT_SECRETS_DIR.parent)
    if create:
        PROJECT_SECRETS_DIR.parent.mkdir(parents=True, exist_ok=True)
        _require_plain_directory_chain(PROJECT_SECRETS_DIR.parent)
    for candidate in chain:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if not create:
                return False
            candidate.mkdir(mode=0o700)
            metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("private project directory is not a regular directory")
        _require_owned(metadata, label="private project directory")
        _set_private_mode(candidate, 0o700)
        verified = candidate.lstat()
        if stat.S_ISLNK(verified.st_mode) or not stat.S_ISDIR(verified.st_mode):
            raise ValueError("private project directory changed during validation")
        _require_owned(verified, label="private project directory")
        if os.name == "posix" and stat.S_IMODE(verified.st_mode) != 0o700:
            raise PermissionError("private project directory permissions are unsafe")
    return True


def _private_file_metadata(path: Path, *, missing_ok: bool) -> os.stat_result | None:
    if not _private_directory(path.parent, create=False):
        if missing_ok:
            return None
        raise FileNotFoundError(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("private project file is not a regular file")
    _require_owned(metadata, label="private project file")
    _set_private_mode(path, 0o600)
    verified = path.lstat()
    if stat.S_ISLNK(verified.st_mode) or not stat.S_ISREG(verified.st_mode):
        raise ValueError("private project file changed during validation")
    _require_owned(verified, label="private project file")
    if os.name == "posix" and stat.S_IMODE(verified.st_mode) != 0o600:
        raise PermissionError("private project file permissions are unsafe")
    return verified


def _read_private_text(path: Path) -> str | None:
    metadata = _private_file_metadata(path, missing_ok=True)
    if metadata is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ValueError("private project file changed during read")
        _require_owned(opened, label="private project file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_external_private_text(path: Path, *, repair_permissions: bool = False) -> str:
    """Read an existing user credential without accepting links or public modes."""
    _require_plain_directory_chain(path.parent)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("credential source is not a regular file")
    _require_owned(metadata, label="credential source")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        if not repair_permissions:
            raise PermissionError("credential source permissions are unsafe")
        _set_private_mode(path, 0o600)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("credential source changed during validation")
        _require_owned(metadata, label="credential source")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PermissionError("credential source permissions are unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ValueError("credential source changed during read")
        _require_owned(opened, label="credential source")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_private_write(path: Path, content: str) -> None:
    _private_directory(path.parent)
    _private_file_metadata(path, missing_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _private_file_metadata(path, missing_ok=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def load_pending_manager_bootstrap(project_id: str) -> dict[str, str] | None:
    path = pending_manager_bootstrap_file(project_id)
    try:
        content = _read_private_text(path)
        if content is None:
            return None
        value = json.loads(content)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("pending manager bootstrap state is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("pending manager bootstrap state is invalid")
    device_id = str(value.get("device_id") or "")
    token = str(value.get("device_token") or "")
    if (
        not re.fullmatch(r"owner-bootstrap-[0-9a-f-]{36}", device_id)
        or not re.fullmatch(r"dduo_dev_[A-Za-z0-9_-]{40,100}", token)
    ):
        raise ValueError("pending manager bootstrap state is invalid")
    return {"device_id": device_id, "device_token": token}


def save_pending_manager_bootstrap(
    project_id: str, *, device_id: str, device_token: str
) -> Path:
    if (
        not re.fullmatch(r"owner-bootstrap-[0-9a-f-]{36}", device_id)
        or not re.fullmatch(r"dduo_dev_[A-Za-z0-9_-]{40,100}", device_token)
    ):
        raise ValueError("pending manager bootstrap state is invalid")
    path = pending_manager_bootstrap_file(project_id)
    _atomic_private_write(
        path,
        json.dumps(
            {"version": 1, "device_id": device_id, "device_token": device_token},
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
    )
    return path


def clear_pending_manager_bootstrap(project_id: str) -> None:
    path = pending_manager_bootstrap_file(project_id)
    if _private_file_metadata(path, missing_ok=True) is not None:
        path.unlink()


def parse_secret_environment(content: str) -> dict[str, str]:
    """Parse only dDuo-owned allowlisted secrets from dotenv-compatible text."""
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in SECRET_ENV_KEYS or not _ENV_KEY.fullmatch(key):
            continue
        values[key] = value.strip()
    return values


def load_legacy_secret_environment(path: Path) -> dict[str, str]:
    """Read one explicit Alpha credential source without following links."""
    return parse_secret_environment(
        _read_external_private_text(path, repair_permissions=True)
    )


def serialize_secret_environment(values: dict[str, str]) -> str:
    filtered = {
        key: str(value).strip()
        for key, value in values.items()
        if key in SECRET_ENV_KEYS and _ENV_KEY.fullmatch(key) and str(value).strip()
    }
    return "".join(f"{key}={filtered[key]}\n" for key in sorted(filtered))


def load_project_secrets(project_id: str, *, include_legacy: bool = False) -> dict[str, str]:
    """Load one project's secrets.

    ``include_legacy`` exists only for the explicit Alpha-to-Beta migrator. New
    projects and normal runtime paths never inherit machine-global credentials.
    """
    values: dict[str, str] = {}
    if include_legacy:
        try:
            legacy = load_legacy_secret_environment(LEGACY_ENV_FILE)
        except FileNotFoundError:
            legacy = None
        if legacy is not None:
            values.update(legacy)
    path = project_env_file(project_id)
    content = _read_private_text(path)
    if content is not None:
        values.update(parse_secret_environment(content))
    return values


def save_project_secrets(project_id: str, values: dict[str, str]) -> Path:
    """Merge and atomically persist dDuo-owned project secrets with mode 0600."""
    current = load_project_secrets(project_id, include_legacy=False)
    current.update(values)
    path = project_env_file(project_id)
    _atomic_private_write(path, serialize_secret_environment(current))
    return path


def replace_project_secrets(project_id: str, values: dict[str, str]) -> Path:
    """Atomically replace one project's exact allowlisted secret set.

    Restore must not merge with the destination host: a key removed before the
    backup would otherwise survive as stale authority on the replacement node.
    """
    normalized: dict[str, str] = {}
    for key, raw_value in values.items():
        value = str(raw_value).strip()
        if (
            key not in SECRET_ENV_KEYS
            or not _ENV_KEY.fullmatch(key)
            or not value
            or any(character in value for character in "\x00\r\n")
        ):
            raise ValueError("project secrets contain an unsupported key or value")
        normalized[key] = value
    path = project_env_file(project_id)
    _atomic_private_write(path, serialize_secret_environment(normalized))
    return path


def save_project_runtime_environment(project_id: str, values: dict[str, str]) -> Path:
    """Atomically replace, rather than merge, one project's runtime profile."""
    path = project_runtime_env_file(project_id)
    content = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    _atomic_private_write(path, content)
    return path


def load_project_runtime_environment_text(project_id: str) -> str | None:
    """Read one project runtime profile through the private-file boundary."""
    return _read_private_text(project_runtime_env_file(project_id))


def ensure_project_secret_environment(project_id: str) -> Path:
    """Create one isolated project store without inheriting global secrets."""
    path = project_env_file(project_id)
    if _private_file_metadata(path, missing_ok=True) is not None:
        return path
    _atomic_private_write(path, "")
    return path


def migrate_legacy_project_secrets(project_id: str) -> Path:
    """Materialize the legacy host file once for a known Alpha project.

    The explicit installer calls the equivalent transactional migration for
    every registered project before retiring the global file. Keeping this
    helper makes the policy independently testable and supports repair tools.
    """
    path = project_env_file(project_id)
    try:
        legacy = load_legacy_secret_environment(LEGACY_ENV_FILE)
    except FileNotFoundError:
        legacy = {}
    existing = load_project_secrets(project_id, include_legacy=False)
    _atomic_private_write(
        path,
        serialize_secret_environment({**legacy, **existing}),
    )
    return path


def ensure_remote_runtime_secrets(project_id: str) -> Path:
    """Create the project-owned secrets required by a new remote stack.

    This is deliberately separate from local initialization: existing local
    PostgreSQL volumes were created with the historical default password and
    must never be silently locked out by a launcher upgrade.
    """
    values = load_project_secrets(project_id, include_legacy=False)
    for key in (
        "DDUO_DATABASE_PASSWORD",
        "DDUO_AUTH_SIGNING_SECRET",
        "DDUO_SESSION_SECRET",
        "DDUO_NODE_AUTHORITY_SECRET",
    ):
        if not values.get(key):
            values[key] = secrets.token_urlsafe(48)
    if not values.get("DDUO_INFRASTRUCTURE_TOKEN"):
        values["DDUO_INFRASTRUCTURE_TOKEN"] = f"dduo_dev_{secrets.token_urlsafe(48)}"
    return save_project_secrets(project_id, values)


def ensure_project_codex_home(project_id: str, *, import_global_auth: bool = True) -> Path:
    """Create a portable file-backed CODEX_HOME for the project's sleep executor.

    OpenAI documents copying ``auth.json`` to trusted headless machines and
    containers.  Importing the existing file is a one-time convenience; OS
    keyring contents are deliberately never scraped.
    """
    home = project_codex_home(project_id)
    _private_directory(home)
    config = home / "config.toml"
    if _private_file_metadata(config, missing_ok=True) is None:
        _atomic_private_write(config, 'cli_auth_credentials_store = "file"\n')
    auth = home / "auth.json"
    global_auth = Path.home() / ".codex" / "auth.json"
    existing_auth = _private_file_metadata(auth, missing_ok=True)
    if import_global_auth and existing_auth is None:
        try:
            global_auth_content = _read_external_private_text(global_auth)
        except FileNotFoundError:
            global_auth_content = None
        if global_auth_content is not None:
            _atomic_private_write(auth, global_auth_content)
    return home


def codex_environment(project_id: str, base: dict[str, str]) -> dict[str, str]:
    """Return an environment pinned to the project's portable Codex login."""
    environment = dict(base)
    environment["CODEX_HOME"] = str(ensure_project_codex_home(project_id))
    return environment
