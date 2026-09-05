from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import stat
import subprocess
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CONFIG_PATH = Path(".dduo-solo-founder/project.toml")
REGISTRY_PATH = Path.home() / ".config" / "dduo-solo-founder" / "projects.json"
PORT_SLOTS = 1000
API_PORT_BASE = 18000
WEB_PORT_BASE = 20000
DASHBOARD_TABS = {
    "project",
    "memory",
    "tasks",
    "observability",
    "team",
    "activity",
    "backup",
    "setup",
}
WORK_VIEWS = {"board", "list", "epics", "plans"}
ROOT_FINGERPRINT_PREFIX = "sha256:"
REGISTRY_MAX_BYTES = 4 * 1024 * 1024
REGISTRY_LOCK_MAX_BYTES = 16 * 1024


def canonical_project_root(root: Path) -> Path:
    """Return the filesystem identity used by the machine-local registry.

    ``resolve`` deliberately follows symlinks. A checkout opened through a
    symlink and the same checkout opened through its real path must not acquire
    two identities. The path need not still exist: explicit move/recovery
    operations must also be able to name the old registered location.
    """
    try:
        return root.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"could not canonicalize dDuo project root: {root}") from exc


def project_root_fingerprint(root: Path) -> str:
    """Return a stable, non-secret fingerprint for one canonical root."""
    canonical = canonical_project_root(root)
    normalized = os.path.normcase(os.path.normpath(str(canonical)))
    digest = hashlib.sha256(os.fsencode(normalized)).hexdigest()
    return f"{ROOT_FINGERPRINT_PREFIX}{digest}"


def toml_string(value: object) -> str:
    """Serialize one value as a TOML basic string without allowing key injection."""
    text = str(value)
    if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise ValueError("project configuration contains invalid Unicode")
    encoded = json.dumps(text, ensure_ascii=False)
    try:
        # JSON strings are a compatible subset of TOML basic strings. Keep the
        # parser assertion beside the serializer so future changes fail closed.
        parsed = tomllib.loads(f"value = {encoded}\n")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("project configuration contains an invalid string") from exc
    if parsed.get("value") != text:
        raise ValueError("project configuration string did not round-trip")
    return encoded


def dashboard_item_url(
    dashboard_url: str,
    *,
    work_id: str | None = None,
    plan_id: str | None = None,
) -> str:
    """Open one Work item without exposing its internal identifier in prose."""
    if bool(work_id) == bool(plan_id):
        raise ValueError("exactly one work or plan identifier is required")
    parsed = urlsplit(dashboard_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query.get("tab") != "tasks":
        raise ValueError("a work item can only be linked from the Work tab")
    query.pop("work", None)
    query.pop("plan", None)
    if work_id:
        query.pop("view", None)
        query["work"] = work_id
    else:
        query["view"] = "plans"
        query["plan"] = str(plan_id)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def project_dashboard_url(
    project: dict,
    tab: str = "tasks",
    view: str | None = None,
    *,
    work_id: str | None = None,
    plan_id: str | None = None,
) -> str:
    """Build an auto-connecting deep link for a view or one exact Work item."""
    if tab not in DASHBOARD_TABS:
        raise ValueError(f"unsupported dashboard tab: {tab}")
    if view is not None:
        if tab != "tasks":
            raise ValueError("a work view is only supported for the Work tab")
        if view not in WORK_VIEWS:
            raise ValueError(f"unsupported work view: {view}")
    query_values = {"project": str(project["id"]), "tab": tab}
    if view:
        query_values["view"] = view
    query = urlencode(query_values)
    dashboard_url = f"http://127.0.0.1:{int(project['web_port'])}/?{query}"
    if work_id or plan_id:
        return dashboard_item_url(dashboard_url, work_id=work_id, plan_id=plan_id)
    return dashboard_url


def find_project_root(start: Path) -> Path | None:
    """Return the nearest configured root without crossing a Git boundary."""
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_PATH).is_file():
            return candidate
        if (candidate / ".git").exists():
            return None
    return None


def find_workspace_root(start: Path) -> Path:
    """Prefer an existing dDuo root, then the nearest Git root, then ``start``."""
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    configured = find_project_root(current)
    if configured:
        return configured
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def load_project(root: Path) -> dict:
    project_root = find_project_root(root)
    path = (project_root or root.expanduser().resolve()) / CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run `dduo-solo-founder init`")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def is_git_worktree(root: Path) -> bool:
    """Return whether ``root`` is the top level of a real Git worktree."""
    root = root.expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    lines = result.stdout.splitlines()
    if result.returncode or len(lines) != 2 or lines[1].strip() != "true":
        return False
    try:
        return Path(lines[0].strip()).resolve() == root
    except (OSError, RuntimeError):
        return False


def exclude_local_config(root: Path) -> None:
    """Keep machine-specific ports and identity out of ordinary Git status."""
    root = root.expanduser().resolve()
    git_marker = root / ".git"
    if git_marker.is_symlink() or not is_git_worktree(root):
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if result.returncode or not result.stdout.strip():
        return
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    try:
        common = common.resolve(strict=True)
    except (OSError, RuntimeError):
        return
    info = common / "info"
    exclude = info / "exclude"
    if not info.is_dir() or info.is_symlink() or exclude.is_symlink():
        return
    entry = "/.dduo-solo-founder/"
    lines = exclude.read_text().splitlines() if exclude.exists() else []
    if entry in lines:
        return
    exclude.write_text("\n".join([*lines, entry]) + "\n")


def port_available(port: int) -> bool:
    """Return whether a TCP port can currently be bound on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _project_slot(project_id: str) -> int:
    return int(hashlib.sha256(project_id.encode()).hexdigest()[:6], 16) % PORT_SLOTS


def _effective_user_id() -> int | None:
    getter = getattr(os, "geteuid", None)
    return int(getter()) if getter is not None else None


def _validate_owned_directory(metadata: os.stat_result, path: Path, label: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"unsafe dDuo Solo Founder {label}: {path} is not a directory")
    expected_uid = _effective_user_id()
    if expected_uid is not None and metadata.st_uid != expected_uid:
        raise RuntimeError(
            f"unsafe dDuo Solo Founder {label}: {path} is not owned by the current user"
        )
    if os.name != "nt" and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(
            f"unsafe dDuo Solo Founder {label}: {path} is writable by other users"
        )


def _registry_parent(path: Path, *, create: bool) -> os.stat_result | None:
    parent = path.parent
    if create:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = parent.lstat()
    except FileNotFoundError:
        if not create:
            return None
        raise
    _validate_owned_directory(metadata, parent, "registry directory")
    return metadata


def _validate_owned_regular_file(metadata: os.stat_result, path: Path, label: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"unsafe dDuo Solo Founder {label}: {path} is not a regular file")
    expected_uid = _effective_user_id()
    if expected_uid is not None and metadata.st_uid != expected_uid:
        raise RuntimeError(
            f"unsafe dDuo Solo Founder {label}: {path} is not owned by the current user"
        )
    if os.name != "nt" and metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(
            f"unsafe dDuo Solo Founder {label}: {path} is writable by other users"
        )


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_owned_regular_file(
    path: Path,
    label: str,
    *,
    missing_ok: bool = False,
    writable: bool = False,
) -> tuple[int, os.stat_result] | None:
    """Open one user-owned regular file without following a swapped symlink."""
    parent_before = _registry_parent(path, create=False)
    if parent_before is None:
        if missing_ok:
            return None
        raise FileNotFoundError(path.parent)
    try:
        before = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    _validate_owned_regular_file(before, path, label)
    flags = (
        (os.O_RDWR if writable else os.O_RDONLY)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"unsafe dDuo Solo Founder {label}: {path} changed while it was opened"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"unsafe dDuo Solo Founder {label}: {path} could not be opened") from exc
    try:
        opened = os.fstat(descriptor)
        _validate_owned_regular_file(opened, path, label)
        current = path.lstat()
        _validate_owned_regular_file(current, path, label)
        parent_after = _registry_parent(path, create=False)
        if (
            parent_after is None
            or not _same_file_identity(parent_before, parent_after)
            or not _same_file_identity(before, opened)
            or not _same_file_identity(opened, current)
        ):
            raise RuntimeError(
                f"unsafe dDuo Solo Founder {label}: {path} changed while it was opened"
            )
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def _read_descriptor(descriptor: int, *, max_bytes: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"unsafe dDuo Solo Founder {label}: file exceeds size limit")
        chunks.append(chunk)


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("dDuo Solo Founder file write made no progress")
        remaining = remaining[written:]


def _path_matches_file(
    path: Path,
    expected: os.stat_result,
    label: str,
    *,
    strict: bool,
) -> bool:
    try:
        current = path.lstat()
        _validate_owned_regular_file(current, path, label)
    except FileNotFoundError:
        return False
    except RuntimeError:
        if strict:
            raise
        return False
    return _same_file_identity(current, expected)


def _exclusive_owned_file(
    path: Path,
    label: str,
    *,
    read_write: bool = False,
) -> tuple[int, os.stat_result]:
    parent_before = _registry_parent(path, create=False)
    if parent_before is None:
        raise FileNotFoundError(path.parent)
    flags = (
        os.O_CREAT
        | os.O_EXCL
        | (os.O_RDWR if read_write else os.O_WRONLY)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        _validate_owned_regular_file(metadata, path, label)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        current = path.lstat()
        _validate_owned_regular_file(current, path, label)
        parent_after = _registry_parent(path, create=False)
        if (
            parent_after is None
            or not _same_file_identity(parent_before, parent_after)
            or not _same_file_identity(metadata, current)
        ):
            raise RuntimeError(
                f"unsafe dDuo Solo Founder {label}: {path} changed while it was created"
            )
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _try_lock_descriptor(descriptor: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise
    return True


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _is_windows_sharing_violation(error: BaseException) -> bool:
    cause = error.__cause__ if isinstance(error, RuntimeError) else error
    return isinstance(cause, PermissionError) and getattr(cause, "winerror", None) in {32, 33}


@contextmanager
def portable_file_lock(registry_path: Path, timeout: float = 5) -> Iterator[None]:
    """Acquire a persistent, owner-validated kernel lock for one registry."""
    _registry_parent(registry_path, create=True)
    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    owner = (
        json.dumps(
            {
                "pid": os.getpid(),
                "created_at": time.time(),
                "nonce": uuid.uuid4().hex,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    while descriptor is None:
        candidate: int | None = None
        candidate_locked = False
        try:
            try:
                opened_lock = _open_owned_regular_file(
                    lock_path,
                    "registry lock",
                    missing_ok=True,
                    writable=True,
                )
            except (RuntimeError, PermissionError) as exc:
                if not _is_windows_sharing_violation(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out waiting for dDuo Solo Founder registry: {lock_path}"
                    ) from exc
                time.sleep(0.05)
                continue
            if opened_lock is None:
                try:
                    candidate, candidate_stat = _exclusive_owned_file(
                        lock_path,
                        "registry lock",
                        read_write=True,
                    )
                except FileExistsError:
                    continue
                except PermissionError as exc:
                    if not _is_windows_sharing_violation(exc):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out waiting for dDuo Solo Founder registry: {lock_path}"
                        ) from exc
                    time.sleep(0.05)
                    continue
            else:
                candidate, candidate_stat = opened_lock
            if candidate_stat.st_size > REGISTRY_LOCK_MAX_BYTES:
                raise RuntimeError(
                    "unsafe dDuo Solo Founder registry lock: file exceeds size limit"
                )
            # ``msvcrt.locking`` locks a byte range from the current position.
            # Keep byte zero allocated before and throughout the lock lifetime.
            if candidate_stat.st_size == 0:
                os.ftruncate(candidate, 1)
                candidate_stat = os.fstat(candidate)
            candidate_locked = _try_lock_descriptor(candidate)
            if candidate_locked:
                if not _path_matches_file(
                    lock_path,
                    candidate_stat,
                    "registry lock",
                    strict=True,
                ):
                    raise RuntimeError(
                        f"unsafe dDuo Solo Founder registry lock: {lock_path} changed "
                        "during acquisition"
                    )
                os.lseek(candidate, 0, os.SEEK_SET)
                _write_descriptor(candidate, owner)
                os.ftruncate(candidate, len(owner))
                os.fsync(candidate)
                descriptor = candidate
                candidate = None
        finally:
            if candidate is not None:
                try:
                    if candidate_locked:
                        _unlock_descriptor(candidate)
                finally:
                    os.close(candidate)
        if descriptor is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for dDuo Solo Founder registry: {lock_path}"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


# Compatibility alias retained for alpha callers and focused lock tests.
_registry_lock = portable_file_lock


def _read_registry(path: Path) -> dict:
    opened = _open_owned_regular_file(path, "port registry", missing_ok=True)
    if opened is None:
        return {"version": 1, "projects": {}}
    descriptor, _ = opened
    try:
        value = json.loads(
            _read_descriptor(
                descriptor,
                max_bytes=REGISTRY_MAX_BYTES,
                label="port registry",
            ).decode("utf-8")
        )
        if value.get("version") != 1 or not isinstance(value.get("projects"), dict):
            raise ValueError("unsupported registry structure")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        raise RuntimeError(f"invalid dDuo Solo Founder port registry: {path}") from exc
    finally:
        os.close(descriptor)


def _atomic_owned_file_write(
    path: Path,
    payload: bytes,
    *,
    temporary_label: str,
    existing_label: str,
) -> None:
    """Commit bytes without following a pre-created or swapped temporary symlink."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    temporary_stat: os.stat_result | None = None
    try:
        descriptor, temporary_stat = _exclusive_owned_file(temporary, temporary_label)
        _write_descriptor(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None

        existing = _open_owned_regular_file(path, existing_label, missing_ok=True)
        if existing is not None:
            existing_descriptor, _ = existing
            os.close(existing_descriptor)
        if not _path_matches_file(
            temporary,
            temporary_stat,
            temporary_label,
            strict=True,
        ):
            raise RuntimeError(
                f"unsafe dDuo Solo Founder {temporary_label}: {temporary} changed before commit"
            )
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_stat is not None and _path_matches_file(
            temporary,
            temporary_stat,
            temporary_label,
            strict=False,
        ):
            temporary.unlink()


def _write_registry(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > REGISTRY_MAX_BYTES:
        raise RuntimeError("unsafe dDuo Solo Founder port registry: payload exceeds size limit")
    _atomic_owned_file_write(
        path,
        payload,
        temporary_label="registry temporary file",
        existing_label="port registry",
    )


def _root_claim(root: Path) -> dict[str, str]:
    canonical = canonical_project_root(root)
    return {
        "root_path": str(canonical),
        "root_fingerprint": project_root_fingerprint(canonical),
    }


def _registered_root_claim(project_id: str, entry: object) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise RuntimeError(
            f"the dDuo Solo Founder registration for {project_id} is malformed"
        )
    try:
        raw_root = entry["root_path"]
        if (
            not isinstance(raw_root, str)
            or not raw_root.strip()
            or "\x00" in raw_root
            or not Path(raw_root).expanduser().is_absolute()
        ):
            raise ValueError("registered root must be a non-empty absolute path")
        claim = _root_claim(Path(raw_root))
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"the dDuo Solo Founder registration for {project_id} is malformed"
        ) from exc
    stored_fingerprint = entry.get("root_fingerprint")
    if stored_fingerprint is not None and stored_fingerprint != claim["root_fingerprint"]:
        raise RuntimeError(
            f"the dDuo Solo Founder registration for {project_id} has an invalid root fingerprint"
        )
    return claim


def registered_project_root(
    project_id: str,
    *,
    registry_path: Path | None = None,
) -> Path | None:
    """Resolve one registered root through the hardened machine-local boundary."""
    registry = _read_registry(registry_path or REGISTRY_PATH)
    entry = registry["projects"].get(str(project_id))
    if entry is None:
        return None
    return Path(_registered_root_claim(str(project_id), entry)["root_path"])


def _claim_conflict(
    projects: dict,
    project_id: str,
    claim: dict[str, str],
    *,
    ignored_project_ids: set[str] | None = None,
) -> tuple[str, dict] | None:
    ignored = ignored_project_ids or set()
    for other_id, other in projects.items():
        if str(other_id) == project_id or str(other_id) in ignored:
            continue
        other_claim = _registered_root_claim(str(other_id), other)
        if other_claim["root_fingerprint"] == claim["root_fingerprint"]:
            return str(other_id), other_claim
    return None


def _assert_matching_claim(project_id: str, entry: object, claim: dict[str, str]) -> None:
    registered = _registered_root_claim(project_id, entry)
    if registered["root_fingerprint"] != claim["root_fingerprint"]:
        raise RuntimeError(
            "this dDuo Solo Founder project identity is already claimed by another "
            "checkout; use an explicit move, restore, or rebind operation"
        )


def _with_current_claim(entry: dict, claim: dict[str, str]) -> dict:
    return {**entry, **claim}


def _reserved_ports(projects: dict, *, ignored_project_ids: set[str] | None = None) -> set[int]:
    ignored = ignored_project_ids or set()
    reserved: set[int] = set()
    for project_id, project in projects.items():
        if str(project_id) in ignored or not isinstance(project, dict):
            continue
        for key in ("api_port", "web_port"):
            if key in project:
                reserved.add(int(project[key]))
    return reserved


def _available_port_pair(
    project_id: str,
    projects: dict,
    checker: Callable[[int], bool],
    *,
    ignored_project_ids: set[str] | None = None,
) -> tuple[int, int]:
    reserved = _reserved_ports(projects, ignored_project_ids=ignored_project_ids)
    start = _project_slot(project_id)
    for offset in range(PORT_SLOTS):
        slot = (start + offset) % PORT_SLOTS
        api_port = API_PORT_BASE + slot
        web_port = WEB_PORT_BASE + slot
        if {api_port, web_port} & reserved:
            continue
        if checker(api_port) and checker(web_port):
            return api_port, web_port
    raise RuntimeError("no free dDuo Solo Founder port pair is available")


def reserve_project_ports(
    project_id: str,
    root: Path,
    *,
    registry_path: Path | None = None,
    checker: Callable[[int], bool] | None = None,
    refresh: bool = False,
) -> tuple[int, int]:
    """Reserve one available API/web pair without colliding with other projects."""
    registry_path = registry_path or REGISTRY_PATH
    checker = checker or port_available
    project_id = str(project_id)
    claim = _root_claim(root)
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        projects = registry["projects"]
        existing = projects.get(project_id)
        if existing and not refresh:
            _assert_matching_claim(project_id, existing, claim)
            migrated = _with_current_claim(existing, claim)
            if migrated != existing:
                projects[project_id] = migrated
                _write_registry(registry_path, registry)
            return int(existing["api_port"]), int(existing["web_port"])
        if existing and refresh:
            _assert_matching_claim(project_id, existing, claim)
        conflict = _claim_conflict(projects, project_id, claim)
        if conflict:
            raise RuntimeError(
                "this checkout is already claimed by another dDuo Solo Founder project"
            )
        api_port, web_port = _available_port_pair(
            project_id,
            projects,
            checker,
            ignored_project_ids={project_id} if refresh else None,
        )
        projects[project_id] = {
            **claim,
            "api_port": api_port,
            "web_port": web_port,
        }
        _write_registry(registry_path, registry)
        return api_port, web_port


def register_project_config(
    root: Path,
    project: dict,
    *,
    registry_path: Path | None = None,
) -> None:
    """Claim ``project_id`` for one canonical root without implicit reassignment."""
    registry_path = registry_path or REGISTRY_PATH
    project_id = str(project["id"])
    claim = _root_claim(root)
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        projects = registry["projects"]
        expected = {
            **claim,
            "api_port": int(project["api_port"]),
            "web_port": int(project["web_port"]),
        }
        existing = projects.get(project_id)
        if existing == expected:
            return
        if existing is not None:
            _assert_matching_claim(project_id, existing, claim)
        conflict = _claim_conflict(projects, project_id, claim)
        if conflict:
            raise RuntimeError(
                "this checkout is already claimed by another dDuo Solo Founder project"
            )
        requested = {expected["api_port"], expected["web_port"]}
        for other_id, other in projects.items():
            other_ports = {
                int(other[key]) for key in ("api_port", "web_port") if key in other
            }
            if other_id != project_id and requested & other_ports:
                raise RuntimeError(
                    "dDuo Solo Founder port collision with another registered project; "
                    "keep the existing project config and choose new ports explicitly"
                )
        projects[project_id] = expected
        _write_registry(registry_path, registry)


def validate_project_registration(
    project_id: str,
    root: Path,
    *,
    registry_path: Path | None = None,
) -> dict:
    """Validate one existing machine-local project claim without reassigning it.

    Runtime adapters use this read-only compare-and-check boundary before they
    contact a local stack. A copied ``project.toml`` therefore cannot silently
    route another checkout into the original project's memory.
    """
    registry_path = registry_path or REGISTRY_PATH
    project_id = str(project_id)
    claim = _root_claim(root)
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        projects = registry["projects"]
        existing = projects.get(project_id)
        if existing is None:
            raise RuntimeError(
                "this dDuo Solo Founder project is not activated on this computer; "
                "open Setup for this project"
            )
        _assert_matching_claim(project_id, existing, claim)
        conflict = _claim_conflict(projects, project_id, claim)
        if conflict:
            raise RuntimeError(
                "this checkout is already claimed by another dDuo Solo Founder project"
            )
        return dict(existing)


def move_project_registration(
    project_id: str,
    source_root: Path,
    destination_root: Path,
    *,
    registry_path: Path | None = None,
) -> dict:
    """Atomically move an existing project claim to a new canonical root."""
    registry_path = registry_path or REGISTRY_PATH
    project_id = str(project_id)
    source_claim = _root_claim(source_root)
    destination_claim = _root_claim(destination_root)
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        projects = registry["projects"]
        existing = projects.get(project_id)
        if existing is None:
            raise RuntimeError("the dDuo Solo Founder project is not registered on this computer")
        _assert_matching_claim(project_id, existing, source_claim)
        conflict = _claim_conflict(projects, project_id, destination_claim)
        if conflict:
            raise RuntimeError(
                "the destination checkout is already claimed by another dDuo Solo Founder project"
            )
        moved = _with_current_claim(existing, destination_claim)
        projects[project_id] = moved
        _write_registry(registry_path, registry)
        return dict(moved)


def rebind_project_registration(
    project_id: str,
    root: Path,
    *,
    previous_root: Path | None = None,
    registry_path: Path | None = None,
) -> dict:
    """Verify a same-root rebind, or explicitly transfer its local claim.

    Endpoint changes do not alter filesystem identity. ``previous_root`` is
    required only when the checkout itself was deliberately moved.
    """
    registry_path = registry_path or REGISTRY_PATH
    if previous_root is not None:
        return move_project_registration(
            project_id,
            previous_root,
            root,
            registry_path=registry_path,
        )
    project_id = str(project_id)
    claim = _root_claim(root)
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        existing = registry["projects"].get(project_id)
        if existing is None:
            raise RuntimeError("the dDuo Solo Founder project is not registered on this computer")
        _assert_matching_claim(project_id, existing, claim)
        current = _with_current_claim(existing, claim)
        if current != existing:
            registry["projects"][project_id] = current
            _write_registry(registry_path, registry)
        return dict(current)


def unregister_project_config(
    project_id: str,
    *,
    registry_path: Path | None = None,
) -> bool:
    """Remove exactly one project from the local port registry."""
    registry_path = registry_path or REGISTRY_PATH
    with portable_file_lock(registry_path):
        registry = _read_registry(registry_path)
        removed = registry["projects"].pop(str(project_id), None) is not None
        if removed:
            _write_registry(registry_path, registry)
        return removed


def new_project_config(root: Path, name: str | None = None) -> tuple[Path, str]:
    root = find_workspace_root(root)
    path = root / CONFIG_PATH
    if path.exists():
        existing = load_project(root)
        register_project_config(root, existing)
        return path, existing["id"]
    project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve())))
    display_name = name or root.name
    api_port, web_port = reserve_project_ports(project_id, root)
    write_project_config(path, project_id, display_name, api_port, web_port)
    exclude_local_config(root)
    return path, project_id


def write_project_config(
    path: Path,
    project_id: str,
    name: str,
    api_port: int,
    web_port: int,
) -> None:
    """Atomically write the non-secret identity shared by agent clients."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "version = 2\n"
        f"id = {toml_string(project_id)}\n"
        f"name = {toml_string(name)}\n"
        'binding = "local"\n'
        f"api_port = {api_port}\n"
        f"web_port = {web_port}\n"
    )
    _atomic_owned_file_write(
        path,
        content.encode("utf-8"),
        temporary_label="project configuration temporary file",
        existing_label="project configuration",
    )


def project_binding_kind(project: dict) -> str:
    """Return the explicit binding discriminator, defaulting legacy configs to local."""
    kind = str(project.get("binding") or "local").strip().lower()
    if kind not in {"local", "remote"}:
        raise ValueError("project binding must be local or remote")
    return kind


def set_local_deployment_mode(root: Path, mode: str) -> dict:
    """Persist whether a local stack is loopback-only or VPS-hosted.

    A VPS host still has a local binding to its own containers; collaborators
    receive a separate remote binding.  Keeping these concepts orthogonal
    prevents a remote client from accidentally starting a second Docker stack.
    """
    if mode not in {"local", "remote"}:
        raise ValueError("deployment mode must be local or remote")
    root = find_workspace_root(root)
    project = load_project(root)
    if project_binding_kind(project) != "local":
        raise ValueError("only a local stack can be exposed as a remote deployment")
    validate_project_registration(str(project.get("id") or ""), root)
    path = root / CONFIG_PATH
    content = (
        "version = 2\n"
        f"id = {toml_string(project['id'])}\n"
        f"name = {toml_string(project.get('name') or root.name)}\n"
        'binding = "local"\n'
        f'api_port = {int(project["api_port"])}\n'
        f'web_port = {int(project["web_port"])}\n'
        f'deployment = "{mode}"\n'
    )
    _atomic_owned_file_write(
        path,
        content.encode("utf-8"),
        temporary_label="project configuration temporary file",
        existing_label="project configuration",
    )
    updated = load_project(root)
    register_project_config(root, updated)
    return updated


def restore_project_config(root: Path, archived: dict, *, force: bool = False) -> dict:
    """Restore project identity while reserving ports that are safe on this computer."""
    root = find_workspace_root(root)
    path = root / CONFIG_PATH
    if path.exists():
        current = load_project(root)
        if current["id"] != archived["id"] and not force:
            raise FileExistsError(
                "current repository belongs to a different dDuo Solo Founder project"
            )
    project_id = str(archived["id"])
    claim = _root_claim(root)
    with portable_file_lock(REGISTRY_PATH):
        registry = _read_registry(REGISTRY_PATH)
        projects = registry["projects"]
        conflicting_ids: set[str] = set()
        existing = projects.get(project_id)
        if existing is not None:
            registered = _registered_root_claim(project_id, existing)
            if registered["root_fingerprint"] != claim["root_fingerprint"]:
                if not force:
                    raise RuntimeError(
                        "this dDuo Solo Founder project identity is already claimed by "
                        "another checkout; rerun the verified restore with force only when "
                        "that registration is intentionally being replaced"
                    )
        root_conflict = _claim_conflict(projects, project_id, claim)
        if root_conflict:
            if not force:
                raise RuntimeError(
                    "this restore target is already claimed by another dDuo Solo Founder project"
                )
            conflicting_ids.add(root_conflict[0])
        ignored = {project_id, *conflicting_ids}
        api_port, web_port = _available_port_pair(
            project_id,
            projects,
            port_available,
            ignored_project_ids=ignored,
        )
        previous_registry = json.loads(json.dumps(registry))
        for conflicting_id in conflicting_ids:
            projects.pop(conflicting_id, None)
        projects[project_id] = {
            **claim,
            "api_port": api_port,
            "web_port": web_port,
        }
        _write_registry(REGISTRY_PATH, registry)
        try:
            write_project_config(
                path,
                project_id,
                str(archived.get("name") or root.name),
                api_port,
                web_port,
            )
        except BaseException:
            _write_registry(REGISTRY_PATH, previous_registry)
            raise
    exclude_local_config(root)
    restored = load_project(root)
    return restored


def compose_name(project_id: str) -> str:
    return "dduo-solo-founder-" + hashlib.sha256(project_id.encode()).hexdigest()[:12]
