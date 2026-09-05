from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from dduo_solo_founder import project_config
from dduo_solo_founder.project_config import (
    canonical_project_root,
    compose_name,
    dashboard_item_url,
    find_project_root,
    find_workspace_root,
    load_project,
    move_project_registration,
    new_project_config,
    port_available,
    project_dashboard_url,
    project_root_fingerprint,
    rebind_project_registration,
    register_project_config,
    reserve_project_ports,
    restore_project_config,
    set_local_deployment_mode,
    unregister_project_config,
    write_project_config,
)
from conftest import assert_private_file


WINDOWS_REJECTS_OPEN_FILE_REPLACEMENT = pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "Windows already forbids unlinking or replacing a file while this security "
        "test holds it open"
    ),
)


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(project_config, "REGISTRY_PATH", tmp_path / "config/projects.json")


def init_git_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def test_project_identity_and_ports_are_stable(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    path, first = new_project_config(root, "Example")
    repeated_path, repeated = new_project_config(root, "Ignored")
    loaded = load_project(root)
    assert path == repeated_path == root / ".dduo-solo-founder/project.toml"
    assert loaded["id"] == first == repeated
    assert loaded["name"] == "Example"
    assert 18000 <= loaded["api_port"] < 19000
    assert 20000 <= loaded["web_port"] < 21000
    assert compose_name(first).startswith("dduo-solo-founder-")
    registry = json.loads(project_config.REGISTRY_PATH.read_text())
    assert registry["projects"][first]["root_path"] == str(root.resolve())
    assert registry["projects"][first]["root_fingerprint"] == project_root_fingerprint(root)
    assert_private_file(project_config.REGISTRY_PATH)


def test_project_dashboard_url_auto_connects_to_requested_tab():
    project = {"id": "p1", "web_port": 20004}
    assert project_dashboard_url(project, "tasks") == (
        "http://127.0.0.1:20004/?project=p1&tab=tasks"
    )
    assert project_dashboard_url(project, "tasks", "plans") == (
        "http://127.0.0.1:20004/?project=p1&tab=tasks&view=plans"
    )
    assert project_dashboard_url(project, work_id="task / one") == (
        "http://127.0.0.1:20004/?project=p1&tab=tasks&work=task+%2F+one"
    )
    assert project_dashboard_url(project, plan_id="plan/one") == (
        "http://127.0.0.1:20004/?project=p1&tab=tasks&view=plans&plan=plan%2Fone"
    )
    assert dashboard_item_url(
        project_dashboard_url(project, "tasks", "plans"), work_id="task-one"
    ) == "http://127.0.0.1:20004/?project=p1&tab=tasks&work=task-one"
    assert dashboard_item_url(
        "https://memory.example.test/dduo/?project=p1&tab=tasks&view=plans",
        work_id="task-one",
    ) == "https://memory.example.test/dduo/?project=p1&tab=tasks&work=task-one"
    assert project_dashboard_url(project) == ("http://127.0.0.1:20004/?project=p1&tab=tasks")
    with pytest.raises(ValueError, match="exactly one"):
        dashboard_item_url(project_dashboard_url(project))
    with pytest.raises(ValueError, match="unsupported dashboard tab"):
        project_dashboard_url(project, "unknown")
    with pytest.raises(ValueError, match="unsupported work view"):
        project_dashboard_url(project, "tasks", "timeline")
    with pytest.raises(ValueError, match="only supported for the Work tab"):
        project_dashboard_url(project, "memory", "plans")
    with pytest.raises(ValueError, match="exactly one"):
        dashboard_item_url(project_dashboard_url(project), work_id="t1", plan_id="p1")
    with pytest.raises(ValueError, match="only be linked from the Work tab"):
        dashboard_item_url(project_dashboard_url(project, "memory"), work_id="t1")


def test_project_root_and_toml_boundaries_fail_closed(tmp_path: Path, monkeypatch):
    broken = tmp_path / "broken"
    real_resolve = project_config.Path.resolve

    def fail_selected_resolve(path, *args, **kwargs):
        if path == broken:
            raise OSError("unresolvable")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(project_config.Path, "resolve", fail_selected_resolve)
    with pytest.raises(ValueError, match="could not canonicalize"):
        canonical_project_root(broken)

    real_loads = project_config.tomllib.loads
    with monkeypatch.context() as context:
        context.setattr(
            project_config.tomllib,
            "loads",
            lambda _value: real_loads('value = "unterminated'),
        )
        with pytest.raises(ValueError, match="invalid string"):
            project_config.toml_string("value")

    with monkeypatch.context() as context:
        context.setattr(project_config.tomllib, "loads", lambda _value: {"value": "other"})
        with pytest.raises(ValueError, match="did not round-trip"):
            project_config.toml_string("value")


def test_unconfigured_project_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="dduo-solo-founder init"):
        load_project(tmp_path)


def test_nested_initialization_uses_git_root_and_stays_out_of_git_status(tmp_path: Path):
    root = tmp_path / "project"
    nested = root / "apps/mobile"
    init_git_repository(root)
    nested.mkdir(parents=True)

    path, project_id = new_project_config(nested, "ExampleApp")

    assert path == root / project_config.CONFIG_PATH
    assert find_workspace_root(nested) == root
    assert find_project_root(nested) == root
    assert load_project(nested)["id"] == project_id
    assert not (nested / project_config.CONFIG_PATH).exists()
    assert "/.dduo-solo-founder/" in (root / ".git/info/exclude").read_text().splitlines()

    new_project_config(nested, "Ignored")
    assert (root / ".git/info/exclude").read_text().splitlines().count("/.dduo-solo-founder/") == 1


def test_nested_git_repository_never_inherits_parent_memory(tmp_path: Path):
    outer = tmp_path / "portfolio"
    inner = outer / "ExampleApp"
    init_git_repository(outer)
    init_git_repository(inner)
    new_project_config(outer, "Portfolio")

    assert find_project_root(inner) is None
    assert find_workspace_root(inner) == inner
    with pytest.raises(FileNotFoundError):
        load_project(inner)

    path, _ = new_project_config(inner, "ExampleApp")
    assert path == inner / project_config.CONFIG_PATH


def test_git_detection_and_exclusion_errors_fail_closed(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()

    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("git unavailable")),
        )
        assert project_config.is_git_worktree(root) is False

    real_resolve = project_config.Path.resolve
    resolve_calls = 0

    def fail_result_resolve(path, *args, **kwargs):
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            raise OSError("invalid Git top level")
        return real_resolve(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=f"{root}\ntrue\n",
            ),
        )
        context.setattr(project_config.Path, "resolve", fail_result_resolve)
        assert project_config.is_git_worktree(root) is False

    monkeypatch.setattr(project_config, "is_git_worktree", lambda _root: True)
    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("git unavailable")),
        )
        project_config.exclude_local_config(root)

    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
        )
        project_config.exclude_local_config(root)

    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="missing-git-dir\n"),
        )
        project_config.exclude_local_config(root)

    common = tmp_path / "common"
    common.mkdir()
    with monkeypatch.context() as context:
        context.setattr(
            project_config.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=f"{common}\n",
            ),
        )
        project_config.exclude_local_config(root)
    assert not (common / "info/exclude").exists()


def test_reservation_skips_reserved_and_occupied_slots(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    monkeypatch.setattr(project_config, "_project_slot", lambda _: 0)
    first = reserve_project_ports(
        "p1", tmp_path / "one", registry_path=registry, checker=lambda _: True
    )
    checked = []

    def checker(port: int) -> bool:
        checked.append(port)
        return port != project_config.API_PORT_BASE + 1

    second = reserve_project_ports("p2", tmp_path / "two", registry_path=registry, checker=checker)
    assert first == (18000, 20000)
    assert second == (18002, 20002)
    assert 18001 in checked
    assert reserve_project_ports(
        "p1", tmp_path / "one", registry_path=registry, checker=lambda _: False
    ) == first

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        reserve_project_ports(
            "p1", tmp_path, registry_path=registry, checker=lambda _: False
        )

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        reserve_project_ports(
            "p1",
            tmp_path / "other-checkout",
            registry_path=registry,
            checker=lambda _: True,
            refresh=True,
        )


def test_reserved_ports_ignore_malformed_partial_and_ignored_entries():
    assert project_config._reserved_ports(
        {
            "ignored": {"api_port": 18000, "web_port": 20000},
            "partial": {"api_port": 18001},
            "malformed": [],
        },
        ignored_project_ids={"ignored"},
    ) == {18001}


def test_reservations_are_atomic_across_concurrent_projects(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    monkeypatch.setattr(project_config, "_project_slot", lambda _: 0)

    def reserve(index: int):
        return reserve_project_ports(
            f"p{index}",
            tmp_path / str(index),
            registry_path=registry,
            checker=lambda _: True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        pairs = list(pool.map(reserve, range(20)))
    assert len(set(pairs)) == 20
    assert len(json.loads(registry.read_text())["projects"]) == 20


def test_registry_lock_retries_windows_sharing_violations(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")
    lock.write_text("held")
    real_open = os.open
    attempts = 0

    def open_with_sharing_violation(path, flags, mode=0o777):
        nonlocal attempts
        if Path(path) == lock and not flags & os.O_CREAT:
            attempts += 1
            if attempts == 1:
                error = PermissionError("simulated Windows sharing violation")
                error.winerror = 32
                raise error
        return real_open(path, flags, mode)

    monkeypatch.setattr(project_config.os, "open", open_with_sharing_violation)
    monkeypatch.setattr(project_config.time, "sleep", lambda _: None)

    with project_config._registry_lock(registry):
        assert lock.exists()
    assert attempts == 2
    assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_registry_lock_retries_a_direct_lstat_sharing_violation(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")
    real_lstat = Path.lstat
    attempts = 0

    def lstat_with_one_sharing_violation(path):
        nonlocal attempts
        if path == lock and attempts == 0:
            attempts += 1
            error = PermissionError("simulated Windows lstat sharing violation")
            error.winerror = 32
            raise error
        return real_lstat(path)

    monkeypatch.setattr(project_config.Path, "lstat", lstat_with_one_sharing_violation)
    monkeypatch.setattr(project_config.time, "sleep", lambda _seconds: None)

    with project_config.portable_file_lock(registry):
        assert lock.exists()

    assert attempts == 1
    assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_windows_lock_helpers_cover_success_contention_and_errors(
    tmp_path: Path,
    monkeypatch,
):
    lock = tmp_path / "windows.lock"
    lock.write_bytes(b"x")
    descriptor = os.open(lock, os.O_RDWR)
    calls = []
    failure = {"errno": None}

    def locking(candidate, mode, size):
        calls.append((candidate, mode, size))
        if failure["errno"] is not None:
            raise OSError(failure["errno"], "simulated lock error")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    windows_os = SimpleNamespace(name="nt", SEEK_SET=os.SEEK_SET, lseek=os.lseek)
    try:
        with monkeypatch.context() as context:
            context.setitem(sys.modules, "msvcrt", fake_msvcrt)
            context.setattr(project_config, "os", windows_os)

            assert project_config._try_lock_descriptor(descriptor) is True
            failure["errno"] = errno.EACCES
            assert project_config._try_lock_descriptor(descriptor) is False
            failure["errno"] = errno.EBADF
            with pytest.raises(OSError, match="simulated lock error"):
                project_config._try_lock_descriptor(descriptor)
            failure["errno"] = None
            project_config._unlock_descriptor(descriptor)
    finally:
        os.close(descriptor)

    assert [mode for _, mode, _ in calls] == [1, 1, 1, 2]
    assert all(size == 1 for _, _, size in calls)


def test_posix_lock_helper_propagates_unexpected_errors(
    tmp_path: Path,
    monkeypatch,
):
    if os.name == "nt":
        pytest.skip("fcntl is unavailable on Windows")
    import fcntl

    lock = tmp_path / "posix.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)

    def fail_flock(*_args):
        raise OSError(errno.EBADF, "simulated flock error")

    try:
        monkeypatch.setattr(fcntl, "flock", fail_flock)
        with pytest.raises(OSError, match="simulated flock error"):
            project_config._try_lock_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_registry_lock_sharing_and_creation_races_are_bounded(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")
    lock.write_text("held")
    real_open = os.open

    def sharing_violation(path, flags, mode=0o777):
        if Path(path) == lock and not flags & os.O_CREAT:
            error = PermissionError("simulated Windows sharing violation")
            error.winerror = 32
            raise error
        return real_open(path, flags, mode)

    with monkeypatch.context() as context:
        context.setattr(project_config.os, "open", sharing_violation)
        with pytest.raises(TimeoutError, match="timed out"):
            with project_config.portable_file_lock(registry, timeout=0):
                pass

    lock.unlink()
    real_exclusive = project_config._exclusive_owned_file
    attempts = 0

    def race_then_create(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FileExistsError("simulated creation race")
        return real_exclusive(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(project_config, "_exclusive_owned_file", race_then_create)
        with project_config.portable_file_lock(registry):
            pass
    assert attempts == 2

    lock.unlink()
    error = PermissionError("simulated Windows sharing violation")
    error.winerror = 33
    with monkeypatch.context() as context:
        context.setattr(
            project_config,
            "_exclusive_owned_file",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
        )
        with pytest.raises(TimeoutError, match="timed out"):
            with project_config.portable_file_lock(registry, timeout=0):
                pass

    attempts = 0

    def sharing_then_create(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = PermissionError("simulated Windows sharing violation")
            error.winerror = 33
            raise error
        return real_exclusive(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(project_config, "_exclusive_owned_file", sharing_then_create)
        context.setattr(project_config.time, "sleep", lambda _seconds: None)
        with project_config.portable_file_lock(registry):
            pass
    assert attempts == 2


def test_registry_lock_keeps_an_allocated_byte_and_reuses_its_inode(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")
    observed_sizes = []
    real_try_lock = project_config._try_lock_descriptor

    def observe_lock_size(descriptor):
        observed_sizes.append(os.fstat(descriptor).st_size)
        return real_try_lock(descriptor)

    monkeypatch.setattr(project_config, "_try_lock_descriptor", observe_lock_size)
    with project_config.portable_file_lock(registry):
        first_identity = (lock.stat().st_dev, lock.stat().st_ino)
    with project_config.portable_file_lock(registry):
        second_identity = (lock.stat().st_dev, lock.stat().st_ino)

    assert observed_sizes and min(observed_sizes) >= 1
    assert first_identity == second_identity
    assert lock.stat().st_size >= 1
    assert_private_file(lock)


def test_registry_read_and_write_reject_symlinks_and_non_regular_files(tmp_path: Path):
    registry = tmp_path / "projects.json"
    victim = tmp_path / "victim.json"
    original = '{"version":1,"projects":{}}\n'
    victim.write_text(original)
    registry.symlink_to(victim)

    with pytest.raises(RuntimeError, match="port registry.*not a regular file"):
        project_config._read_registry(registry)
    with pytest.raises(RuntimeError, match="port registry.*not a regular file"):
        project_config._write_registry(registry, {"version": 1, "projects": {}})
    assert registry.is_symlink()
    assert victim.read_text() == original

    registry.unlink()
    registry.mkdir()
    with pytest.raises(RuntimeError, match="port registry.*not a regular file"):
        project_config._read_registry(registry)
    with pytest.raises(RuntimeError, match="port registry.*not a regular file"):
        project_config._write_registry(registry, {"version": 1, "projects": {}})
    assert not list(tmp_path.glob("projects.json.*.tmp"))


def test_registry_and_lock_reject_files_owned_by_another_user(
    tmp_path: Path,
    monkeypatch,
):
    if project_config._effective_user_id() is None:
        pytest.skip("file ownership is unavailable on this platform")
    registry = tmp_path / "projects.json"
    registry.write_text('{"version":1,"projects":{}}\n')
    lock = registry.with_suffix(".json.lock")
    lock.write_text("stale")
    actual_uid = registry.stat().st_uid
    real_lstat = project_config.Path.lstat

    def lstat_with_wrong_file_owner(path):
        metadata = real_lstat(path)
        if Path(path) in {registry, lock}:
            fields = list(metadata)
            fields[4] = actual_uid + 1
            return os.stat_result(fields)
        return metadata

    monkeypatch.setattr(project_config.Path, "lstat", lstat_with_wrong_file_owner)

    with pytest.raises(RuntimeError, match="port registry.*not owned"):
        project_config._read_registry(registry)
    with pytest.raises(RuntimeError, match="registry lock.*not owned"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass
    assert lock.read_text() == "stale"


def test_registry_boundaries_reject_unsafe_parent_paths_and_permissions(
    tmp_path: Path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)
    linked_registry = linked_parent / "projects.json"

    with pytest.raises(RuntimeError, match="registry directory.*not a directory"):
        project_config._read_registry(linked_registry)
    with pytest.raises(RuntimeError, match="registry directory.*not a directory"):
        with project_config.portable_file_lock(linked_registry, timeout=0):
            pass
    assert not list(outside.iterdir())

    if os.name == "nt":
        return
    writable_parent = tmp_path / "writable-parent"
    writable_parent.mkdir(mode=0o777)
    writable_parent.chmod(0o777)
    writable_registry = writable_parent / "projects.json"
    with pytest.raises(RuntimeError, match="registry directory.*writable by other users"):
        project_config._read_registry(writable_registry)
    with pytest.raises(RuntimeError, match="registry directory.*writable by other users"):
        with project_config.portable_file_lock(writable_registry, timeout=0):
            pass
    assert not list(writable_parent.iterdir())


def test_registry_parent_and_missing_file_boundaries(tmp_path: Path, monkeypatch):
    missing_registry = tmp_path / "missing" / "projects.json"
    assert project_config._read_registry(missing_registry) == {"version": 1, "projects": {}}
    assert (
        project_config._open_owned_regular_file(
            missing_registry,
            "port registry",
            missing_ok=True,
        )
        is None
    )
    with pytest.raises(FileNotFoundError):
        project_config._open_owned_regular_file(missing_registry, "port registry")
    with pytest.raises(FileNotFoundError):
        project_config._exclusive_owned_file(missing_registry, "registry temporary file")

    existing_parent = tmp_path / "existing"
    existing_parent.mkdir()
    missing_file = existing_parent / "projects.json"
    with pytest.raises(FileNotFoundError):
        project_config._open_owned_regular_file(missing_file, "port registry")

    with monkeypatch.context() as context:
        context.setattr(project_config.Path, "mkdir", lambda *_args, **_kwargs: None)
        context.setattr(
            project_config.Path,
            "lstat",
            lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing parent")),
        )
        with pytest.raises(FileNotFoundError, match="missing parent"):
            project_config._registry_parent(missing_registry, create=True)

    exclusive = existing_parent / "exclusive.tmp"
    with monkeypatch.context() as context:
        context.delattr(project_config.os, "fchmod", raising=False)
        descriptor, _ = project_config._exclusive_owned_file(exclusive, "test file")
        os.close(descriptor)
    exclusive.unlink()

    if project_config._effective_user_id() is None:
        return
    registry_parent = tmp_path / "owned-parent"
    registry_parent.mkdir()
    registry = registry_parent / "projects.json"
    actual_uid = registry_parent.stat().st_uid
    monkeypatch.setattr(project_config, "_effective_user_id", lambda: actual_uid + 1)
    with pytest.raises(RuntimeError, match="registry directory.*not owned"):
        project_config._read_registry(registry)
    with pytest.raises(RuntimeError, match="registry directory.*not owned"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass


def test_registry_and_lock_reject_files_writable_by_other_users(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("POSIX mode bits are unavailable on Windows")
    registry = tmp_path / "projects.json"
    registry.write_text('{"version":1,"projects":{}}\n')
    registry.chmod(0o666)
    lock = registry.with_suffix(".json.lock")
    lock.write_text("stale")
    lock.chmod(0o666)

    with pytest.raises(RuntimeError, match="port registry.*writable by other users"):
        project_config._read_registry(registry)
    with pytest.raises(RuntimeError, match="registry lock.*writable by other users"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass


def test_registry_open_and_descriptor_errors_fail_closed(tmp_path: Path, monkeypatch):
    registry = tmp_path / "projects.json"
    registry.write_text('{"version":1,"projects":{}}\n')
    real_open = os.open

    with monkeypatch.context() as context:
        context.setattr(
            project_config.os,
            "open",
            lambda path, flags, mode=0o777: (
                (_ for _ in ()).throw(FileNotFoundError(path))
                if Path(path) == registry
                else real_open(path, flags, mode)
            ),
        )
        with pytest.raises(RuntimeError, match="changed while it was opened"):
            project_config._read_registry(registry)

    with monkeypatch.context() as context:
        context.setattr(
            project_config.os,
            "open",
            lambda path, flags, mode=0o777: (
                (_ for _ in ()).throw(OSError("open failed"))
                if Path(path) == registry
                else real_open(path, flags, mode)
            ),
        )
        with pytest.raises(RuntimeError, match="could not be opened"):
            project_config._read_registry(registry)

    registry.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="invalid.*port registry"):
        project_config._read_registry(registry)

    registry.unlink()
    with monkeypatch.context() as context:
        context.setattr(project_config.os, "write", lambda _descriptor, _payload: 0)
        with pytest.raises(OSError, match="made no progress"):
            project_config._write_registry(registry, {"version": 1, "projects": {}})
    assert not list(tmp_path.glob("projects.json.*.tmp"))


def test_path_identity_checks_reject_unsafe_or_replaced_files(tmp_path: Path):
    expected_file = tmp_path / "expected"
    expected_file.write_text("expected")
    expected = expected_file.stat()
    unsafe = tmp_path / "unsafe"
    unsafe.symlink_to(expected_file)

    assert project_config._path_matches_file(
        unsafe,
        expected,
        "test file",
        strict=False,
    ) is False
    with pytest.raises(RuntimeError, match="not a regular file"):
        project_config._path_matches_file(
            unsafe,
            expected,
            "test file",
            strict=True,
        )


def test_registry_read_detects_a_file_swap_between_lstat_and_open(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    replacement = tmp_path / "replacement.json"
    registry.write_text('{"version":1,"projects":{"first":{}}}\n')
    replacement.write_text('{"version":1,"projects":{"second":{}}}\n')
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, mode=0o777):
        nonlocal swapped
        if Path(path) == registry and not flags & os.O_CREAT and not swapped:
            os.replace(replacement, registry)
            swapped = True
        return real_open(path, flags, mode)

    monkeypatch.setattr(project_config.os, "open", swap_before_open)
    with pytest.raises(RuntimeError, match="changed while it was opened"):
        project_config._read_registry(registry)
    assert json.loads(registry.read_text())["projects"] == {"second": {}}


@WINDOWS_REJECTS_OPEN_FILE_REPLACEMENT
def test_registry_read_detects_a_symlink_swap_after_open(tmp_path: Path, monkeypatch):
    registry = tmp_path / "projects.json"
    victim = tmp_path / "victim.json"
    registry.write_text('{"version":1,"projects":{}}\n')
    victim.write_text("preserve")
    real_open = os.open
    swapped = False

    def swap_after_open(path, flags, mode=0o777):
        nonlocal swapped
        descriptor = real_open(path, flags, mode)
        if Path(path) == registry and not flags & os.O_CREAT and not swapped:
            registry.unlink()
            registry.symlink_to(victim)
            swapped = True
        return descriptor

    monkeypatch.setattr(project_config.os, "open", swap_after_open)
    with pytest.raises(RuntimeError, match="port registry.*not a regular file"):
        project_config._read_registry(registry)
    assert registry.is_symlink()
    assert victim.read_text() == "preserve"


def test_registry_temporary_write_is_exclusive_and_does_not_follow_symlinks(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    temporary = tmp_path / f"projects.json.{os.getpid()}.fixed.tmp"
    victim = tmp_path / "victim.json"
    victim.write_text("preserve")
    temporary.symlink_to(victim)
    observed_flags = 0
    real_open = os.open

    class FixedUUID:
        hex = "fixed"

    def capture_open(path, flags, mode=0o777):
        nonlocal observed_flags
        if Path(path) == temporary:
            observed_flags = flags
        return real_open(path, flags, mode)

    monkeypatch.setattr(project_config.uuid, "uuid4", lambda: FixedUUID())
    monkeypatch.setattr(project_config.os, "open", capture_open)
    with pytest.raises(FileExistsError):
        project_config._write_registry(registry, {"version": 1, "projects": {}})

    assert observed_flags & os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert observed_flags & os.O_NOFOLLOW
    assert temporary.is_symlink()
    assert victim.read_text() == "preserve"
    assert not registry.exists()


@WINDOWS_REJECTS_OPEN_FILE_REPLACEMENT
def test_registry_temporary_write_detects_a_symlink_swap_after_create(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    temporary = tmp_path / f"projects.json.{os.getpid()}.fixed.tmp"
    victim = tmp_path / "victim.json"
    victim.write_text("preserve")
    real_open = os.open

    class FixedUUID:
        hex = "fixed"

    def swap_after_create(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        if Path(path) == temporary and flags & os.O_CREAT:
            temporary.unlink()
            temporary.symlink_to(victim)
        return descriptor

    monkeypatch.setattr(project_config.uuid, "uuid4", lambda: FixedUUID())
    monkeypatch.setattr(project_config.os, "open", swap_after_create)
    with pytest.raises(RuntimeError, match="registry temporary file.*not a regular file"):
        project_config._write_registry(registry, {"version": 1, "projects": {}})

    assert temporary.is_symlink()
    assert victim.read_text() == "preserve"
    assert not registry.exists()


@WINDOWS_REJECTS_OPEN_FILE_REPLACEMENT
def test_registry_temporary_write_detects_a_regular_file_swap_after_create(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    temporary = tmp_path / f"projects.json.{os.getpid()}.fixed.tmp"
    replacement = tmp_path / "replacement.tmp"
    replacement.write_text("replacement")
    real_open = os.open

    class FixedUUID:
        hex = "fixed"

    def swap_after_create(path, flags, mode=0o777):
        descriptor = real_open(path, flags, mode)
        if Path(path) == temporary and flags & os.O_CREAT:
            os.replace(replacement, temporary)
        return descriptor

    monkeypatch.setattr(project_config.uuid, "uuid4", lambda: FixedUUID())
    monkeypatch.setattr(project_config.os, "open", swap_after_create)
    with pytest.raises(RuntimeError, match="changed while it was created"):
        project_config._write_registry(registry, {"version": 1, "projects": {}})

    assert temporary.read_text() == "replacement"
    assert not registry.exists()


def test_registry_and_lock_reads_are_bounded(tmp_path: Path, monkeypatch):
    registry = tmp_path / "projects.json"
    registry.write_bytes(b"x" * 9)
    monkeypatch.setattr(project_config, "REGISTRY_MAX_BYTES", 8)
    with pytest.raises(RuntimeError, match="port registry.*size limit"):
        project_config._read_registry(registry)

    registry.unlink()
    lock = registry.with_suffix(".json.lock")
    lock.write_bytes(b"x" * 9)
    monkeypatch.setattr(project_config, "REGISTRY_LOCK_MAX_BYTES", 8)
    with pytest.raises(RuntimeError, match="registry lock.*size limit"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass
    assert lock.read_bytes() == b"x" * 9

    with pytest.raises(RuntimeError, match="port registry.*payload.*size limit"):
        project_config._write_registry(
            registry,
            {"version": 1, "projects": {"large": {"value": "x" * 9}}},
        )
    assert not registry.exists()


def test_registry_writer_handles_short_writes_and_cleans_failed_temporary_files(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    value = {"version": 1, "projects": {"p1": {"api_port": 18001}}}
    real_write = os.write

    def short_write(descriptor, payload):
        return real_write(descriptor, payload[:2])

    monkeypatch.setattr(project_config.os, "write", short_write)
    project_config._write_registry(registry, value)
    assert json.loads(registry.read_text()) == value

    def fail_write(*_args):
        raise OSError("simulated registry write failure")

    monkeypatch.setattr(project_config.os, "write", fail_write)
    with pytest.raises(OSError, match="simulated registry write failure"):
        project_config._write_registry(registry, value)
    assert json.loads(registry.read_text()) == value
    assert not list(tmp_path.glob("projects.json.*.tmp"))


def test_registry_writer_rejects_identity_loss_before_commit(tmp_path: Path, monkeypatch):
    registry = tmp_path / "projects.json"
    real_matches = project_config._path_matches_file

    def lose_identity(path, expected, label, *, strict):
        if label == "registry temporary file" and strict:
            return False
        return real_matches(path, expected, label, strict=strict)

    monkeypatch.setattr(project_config, "_path_matches_file", lose_identity)
    with pytest.raises(RuntimeError, match="changed before commit"):
        project_config._write_registry(registry, {"version": 1, "projects": {}})

    assert not registry.exists()
    assert not list(tmp_path.glob("projects.json.*.tmp"))


def test_registry_lock_rejects_symlinks_and_detects_recovery_swaps(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    lock = registry.with_suffix(".json.lock")
    victim = tmp_path / "victim.lock"
    victim.write_text("stale")
    lock.symlink_to(victim)

    with pytest.raises(RuntimeError, match="registry lock.*not a regular file"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass
    assert lock.is_symlink()
    assert victim.read_text() == "stale"

    lock.unlink()
    lock.write_text("stale")
    replacement = tmp_path / "replacement.lock"
    replacement.write_text("stale")
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, mode=0o777):
        nonlocal swapped
        if Path(path) == lock and not flags & os.O_CREAT and not swapped:
            os.replace(replacement, lock)
            swapped = True
        return real_open(path, flags, mode)

    monkeypatch.setattr(project_config.os, "open", swap_before_open)
    with pytest.raises(RuntimeError, match="changed while it was opened"):
        with project_config.portable_file_lock(registry, timeout=0):
            pass
    assert lock.read_text() == "stale"


@WINDOWS_REJECTS_OPEN_FILE_REPLACEMENT
def test_registry_lock_cleanup_does_not_follow_a_replacement_symlink(tmp_path: Path):
    registry = tmp_path / "projects.json"
    lock = registry.with_suffix(".json.lock")
    victim = tmp_path / "victim.lock"
    victim.write_text("preserve")

    with project_config.portable_file_lock(registry):
        lock.unlink()
        lock.symlink_to(victim)

    assert lock.is_symlink()
    assert victim.read_text() == "preserve"


def test_registry_lock_releases_when_its_path_identity_changes(
    tmp_path: Path,
    monkeypatch,
):
    registry = tmp_path / "projects.json"
    real_matches = project_config._path_matches_file

    def reject_lock_identity(path, expected, label, *, strict):
        if label == "registry lock" and strict:
            return False
        return real_matches(path, expected, label, strict=strict)

    with monkeypatch.context() as context:
        context.setattr(project_config, "_path_matches_file", reject_lock_identity)
        with pytest.raises(RuntimeError, match="changed during acquisition"):
            with project_config.portable_file_lock(registry, timeout=0):
                pass

    with project_config.portable_file_lock(registry, timeout=0):
        pass


def test_registry_lock_propagates_real_permission_errors(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"

    def deny_open(*_):
        raise PermissionError("directory is not writable")

    monkeypatch.setattr(project_config.os, "open", deny_open)
    with pytest.raises(PermissionError, match="not writable"):
        with project_config._registry_lock(registry):
            pass


def test_registry_lock_releases_after_a_failed_owner_write(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")

    def fail_write(*_):
        raise OSError("disk write failed")

    with monkeypatch.context() as context:
        context.setattr(project_config.os, "write", fail_write)
        with pytest.raises(OSError, match="disk write failed"):
            with project_config._registry_lock(registry):
                pass

    with project_config._registry_lock(registry, timeout=0):
        pass
    assert json.loads(lock.read_text())["pid"] == os.getpid()


def test_stale_lock_is_recovered_and_exhaustion_is_explicit(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    lock = registry.with_suffix(".json.lock")
    lock.write_text("stale")
    old = time.time() - 60
    os.utime(lock, (old, old))
    assert reserve_project_ports("p1", tmp_path, registry_path=registry, checker=lambda _: True)
    monkeypatch.setattr(project_config, "PORT_SLOTS", 2)
    with pytest.raises(RuntimeError, match="no free"):
        reserve_project_ports(
            "p2", tmp_path, registry_path=tmp_path / "full.json", checker=lambda _: False
        )


def test_invalid_registry_and_legacy_collision_are_rejected(tmp_path: Path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(RuntimeError, match="invalid dDuo Solo Founder"):
        reserve_project_ports("p", tmp_path, registry_path=invalid)

    registry = tmp_path / "legacy.json"
    first = {"id": "p1", "api_port": 18001, "web_port": 20001}
    register_project_config(tmp_path / "one", first, registry_path=registry)
    register_project_config(tmp_path / "one", first, registry_path=registry)
    with pytest.raises(RuntimeError, match="collision"):
        register_project_config(
            tmp_path / "two",
            {"id": "p2", "api_port": 18001, "web_port": 20002},
            registry_path=registry,
        )


def test_registered_project_root_uses_the_hardened_registry_boundary(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"
    register_project_config(
        root,
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )

    assert not root.exists()
    assert project_config.registered_project_root("p1", registry_path=registry) == root.resolve()
    assert project_config.registered_project_root("missing", registry_path=registry) is None


def test_registered_project_root_rejects_malformed_claims(tmp_path: Path):
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"version": 1, "projects": {"p1": []}}))
    with pytest.raises(RuntimeError, match="registration for p1 is malformed"):
        project_config.registered_project_root("p1", registry_path=registry)

    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": {
                        "root_path": str(tmp_path / "project"),
                        "root_fingerprint": "sha256:not-the-root",
                    }
                },
            }
        )
    )
    with pytest.raises(RuntimeError, match="invalid root fingerprint"):
        project_config.registered_project_root("p1", registry_path=registry)


def test_unregister_project_config_removes_only_the_exact_project(tmp_path: Path):
    registry = tmp_path / "projects.json"
    register_project_config(
        tmp_path / "one",
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )
    register_project_config(
        tmp_path / "two",
        {"id": "p2", "api_port": 18002, "web_port": 20002},
        registry_path=registry,
    )

    assert unregister_project_config("p1", registry_path=registry) is True
    assert unregister_project_config("p1", registry_path=registry) is False
    assert set(json.loads(registry.read_text())["projects"]) == {"p2"}


def test_port_probe_detects_bind_success_and_failure(monkeypatch):
    class Probe:
        def __init__(self, error=None):
            self.error = error

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def bind(self, address):
            assert address == ("127.0.0.1", 18000)
            if self.error:
                raise self.error

    monkeypatch.setattr(project_config.socket, "socket", lambda *args: Probe(OSError()))
    assert port_available(18000) is False
    monkeypatch.setattr(project_config.socket, "socket", lambda *args: Probe())
    assert port_available(18000) is True


def test_restore_preserves_identity_but_refreshes_ports(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    init_git_repository(root)
    path = root / project_config.CONFIG_PATH
    write_project_config(path, "p1", "Old", 18000, 20000)
    register_project_config(root, load_project(root))
    monkeypatch.setattr(project_config, "_project_slot", lambda _: 0)
    monkeypatch.setattr(
        project_config,
        "port_available",
        lambda port: port not in {18000, 20000},
    )
    restored = restore_project_config(root, {"id": "p1", "name": "Restored"})
    assert restored["id"] == "p1" and restored["name"] == "Restored"
    assert (restored["api_port"], restored["web_port"]) == (18001, 20001)
    assert "/.dduo-solo-founder/" in (root / ".git/info/exclude").read_text().splitlines()

    with pytest.raises(FileExistsError, match="different"):
        restore_project_config(root, {"id": "p2", "name": "Other"})
    replaced = restore_project_config(root, {"id": "p2", "name": "Other"}, force=True)
    assert replaced["id"] == "p2"


def test_root_discovery_accepts_files_and_exclusion_without_git_metadata(tmp_path: Path):
    root = tmp_path / "project"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    source = nested / "module.py"
    source.write_text("pass\n")
    assert find_workspace_root(source) == root
    assert find_project_root(source) is None

    project_config.exclude_local_config(tmp_path / "not-a-repository")


def test_local_config_exclusion_supports_real_worktrees_and_rejects_git_symlinks(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    init_git_repository(repository)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=dDuo test",
            "-c",
            "user.email=dduo@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
            "-q",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "-q",
            "-b",
            "linked",
            str(worktree),
        ],
        check=True,
    )

    assert (worktree / ".git").is_file()
    assert project_config.is_git_worktree(worktree) is True
    project_config.exclude_local_config(worktree)
    common_exclude = repository / ".git/info/exclude"
    assert "/.dduo-solo-founder/" in common_exclude.read_text().splitlines()

    before = common_exclude.read_text()
    untrusted = tmp_path / "untrusted"
    untrusted.mkdir()
    (untrusted / ".git").symlink_to(repository / ".git", target_is_directory=True)
    project_config.exclude_local_config(untrusted)
    assert common_exclude.read_text() == before


def test_registry_lock_timeout_and_unsupported_shapes_are_explicit(tmp_path: Path):
    registry = tmp_path / "registry.json"
    lock = registry.with_suffix(".json.lock")
    with project_config.portable_file_lock(registry):
        with pytest.raises(TimeoutError, match="timed out"):
            with project_config.portable_file_lock(registry, timeout=0):
                pass
        assert lock.exists()

    for value in ("[]", '{"version":2,"projects":{}}'):
        invalid = tmp_path / f"invalid-{len(value)}.json"
        invalid.write_text(value)
        with pytest.raises(RuntimeError, match="invalid dDuo Solo Founder"):
            reserve_project_ports("p", tmp_path, registry_path=invalid)


def test_same_project_requires_an_explicit_move_before_ports_change(tmp_path: Path):
    registry = tmp_path / "registry.json"
    first_root = tmp_path / "first"
    moved_root = tmp_path / "moved"
    register_project_config(
        first_root,
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )
    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        register_project_config(
            moved_root,
            {"id": "p1", "api_port": 18002, "web_port": 20002},
            registry_path=registry,
        )

    move_project_registration("p1", first_root, moved_root, registry_path=registry)
    register_project_config(
        moved_root,
        {"id": "p1", "api_port": 18002, "web_port": 20002},
        registry_path=registry,
    )
    registered = json.loads(registry.read_text())["projects"]["p1"]
    assert registered["api_port"] == 18002
    assert registered["root_path"] == str(moved_root.resolve())


def test_local_deployment_mode_is_validated_and_persisted(tmp_path: Path):
    root = tmp_path / "project"
    (root / ".git/info").mkdir(parents=True)
    write_project_config(root / project_config.CONFIG_PATH, "p1", 'Project "One"', 18001, 20001)
    register_project_config(root, load_project(root))
    updated = set_local_deployment_mode(root, "remote")
    assert updated["deployment"] == "remote"
    assert updated["name"] == 'Project "One"'
    assert set_local_deployment_mode(root, "local")["deployment"] == "local"

    with pytest.raises(ValueError, match="deployment mode"):
        set_local_deployment_mode(root, "shared")
    with pytest.raises(ValueError, match="binding must be local or remote"):
        project_config.project_binding_kind({"binding": "sideways"})

    path = root / project_config.CONFIG_PATH
    path.write_text(
        'version = 2\nid = "p1"\nname = "Remote"\nbinding = "remote"\n'
        'api_url = "https://example.test"\n'
    )
    with pytest.raises(ValueError, match="only a local stack"):
        set_local_deployment_mode(root, "remote")


def test_copied_local_config_cannot_change_deployment_or_mutate_the_copy(tmp_path: Path):
    owner = tmp_path / "owner"
    copied = tmp_path / "copied"
    for root in (owner, copied):
        (root / ".git/info").mkdir(parents=True)
        write_project_config(
            root / project_config.CONFIG_PATH,
            "p1",
            "Project One",
            18001,
            20001,
        )
    register_project_config(owner, load_project(owner))
    copied_config = copied / project_config.CONFIG_PATH
    before = copied_config.read_bytes()

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        set_local_deployment_mode(copied, "remote")

    assert copied_config.read_bytes() == before


def test_project_config_strings_round_trip_without_toml_injection(tmp_path: Path):
    root = tmp_path / "project"
    (root / ".git/info").mkdir(parents=True)
    name = 'TeamApp "alpha"\\server\napi_port = 1\t✓'
    path = root / project_config.CONFIG_PATH

    write_project_config(path, "p1", name, 18001, 20001)

    loaded = project_config.load_project(root)
    assert loaded["name"] == name
    assert loaded["api_port"] == 18001
    assert loaded["web_port"] == 20001


def test_project_config_rejects_invalid_unicode(tmp_path: Path):
    path = tmp_path / project_config.CONFIG_PATH
    with pytest.raises(ValueError, match="invalid Unicode"):
        write_project_config(path, "p1", "broken\ud800", 18001, 20001)


def test_project_config_write_rejects_a_precreated_temporary_symlink(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / project_config.CONFIG_PATH
    write_project_config(path, "p1", "Original", 18001, 20001)
    original = path.read_bytes()
    victim = tmp_path / "victim.toml"
    victim.write_text("preserve")
    temporary = path.with_name(f"{path.name}.{os.getpid()}.fixed.tmp")
    temporary.symlink_to(victim)

    class FixedUUID:
        hex = "fixed"

    monkeypatch.setattr(project_config.uuid, "uuid4", lambda: FixedUUID())
    with pytest.raises(FileExistsError):
        write_project_config(path, "p1", "Changed", 18002, 20002)

    assert path.read_bytes() == original
    assert temporary.is_symlink()
    assert victim.read_text() == "preserve"


def test_deployment_write_rejects_a_precreated_temporary_symlink(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "project"
    (root / ".git/info").mkdir(parents=True)
    path = root / project_config.CONFIG_PATH
    write_project_config(path, "p1", "Original", 18001, 20001)
    register_project_config(root, load_project(root))
    original_config = path.read_bytes()
    original_registry = project_config.REGISTRY_PATH.read_bytes()
    victim = tmp_path / "victim.toml"
    victim.write_text("preserve")
    temporary = path.with_name(f"{path.name}.{os.getpid()}.fixed.tmp")
    temporary.symlink_to(victim)

    class FixedUUID:
        hex = "fixed"

    monkeypatch.setattr(project_config.uuid, "uuid4", lambda: FixedUUID())
    with pytest.raises(FileExistsError):
        set_local_deployment_mode(root, "remote")

    assert path.read_bytes() == original_config
    assert project_config.REGISTRY_PATH.read_bytes() == original_registry
    assert temporary.is_symlink()
    assert victim.read_text() == "preserve"


def test_restore_into_unconfigured_root_uses_archive_name_fallback(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "restored"
    (root / ".git/info").mkdir(parents=True)
    monkeypatch.setattr(project_config, "_project_slot", lambda _: 7)
    restored = restore_project_config(root, {"id": "restored-id"})
    assert restored["name"] == "restored"
    assert (restored["api_port"], restored["web_port"]) == (18007, 20007)


def test_legacy_registration_is_upgraded_only_from_its_canonical_root(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": {
                        "root_path": str(root),
                        "api_port": 18001,
                        "web_port": 20001,
                    }
                },
            }
        )
    )

    assert reserve_project_ports(
        "p1", root, registry_path=registry, checker=lambda _: False
    ) == (18001, 20001)
    upgraded = json.loads(registry.read_text())["projects"]["p1"]
    assert upgraded["root_path"] == str(root.resolve())
    assert upgraded["root_fingerprint"] == project_root_fingerprint(root)

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        register_project_config(
            tmp_path / "copy",
            {"id": "p1", "api_port": 18001, "web_port": 20001},
            registry_path=registry,
        )


def test_symlink_and_real_path_are_one_project_identity(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    alias = tmp_path / "project-alias"
    alias.symlink_to(root, target_is_directory=True)
    registry = tmp_path / "projects.json"
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}

    register_project_config(alias, project, registry_path=registry)
    register_project_config(root, project, registry_path=registry)

    registered = json.loads(registry.read_text())["projects"]["p1"]
    assert canonical_project_root(alias) == root.resolve()
    assert registered["root_path"] == str(root.resolve())
    assert registered["root_fingerprint"] == project_root_fingerprint(root)


def test_one_canonical_root_cannot_belong_to_two_project_ids(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"
    register_project_config(
        root,
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )
    with pytest.raises(RuntimeError, match="already claimed by another"):
        register_project_config(
            root,
            {"id": "p2", "api_port": 18002, "web_port": 20002},
            registry_path=registry,
        )
    assert set(json.loads(registry.read_text())["projects"]) == {"p1"}


def test_reservation_rejects_a_root_claimed_by_another_project(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"
    register_project_config(
        root,
        {"id": "owner", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )

    with pytest.raises(RuntimeError, match="already claimed by another"):
        reserve_project_ports(
            "new-project",
            root,
            registry_path=registry,
            checker=lambda _port: True,
        )


@pytest.mark.parametrize("registered_root", [None, 42, "", "relative/path", "/tmp/x\0y"])
def test_runtime_validation_rejects_malformed_registered_roots(
    tmp_path: Path,
    registered_root: object,
):
    registry = tmp_path / "projects.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": {
                        "root_path": registered_root,
                        "api_port": 18001,
                        "web_port": 20001,
                    }
                },
            }
        )
    )

    with pytest.raises(RuntimeError, match="registration for p1 is malformed"):
        project_config.validate_project_registration(
            "p1",
            tmp_path / "project",
            registry_path=registry,
        )


def test_move_is_compare_and_swap_and_rebind_is_same_root_only(tmp_path: Path):
    registry = tmp_path / "projects.json"
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    register_project_config(
        source,
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        move_project_registration(
            "p1", tmp_path / "wrong-source", destination, registry_path=registry
        )
    assert rebind_project_registration("p1", source, registry_path=registry)[
        "root_path"
    ] == str(source.resolve())

    rebound = rebind_project_registration(
        "p1", destination, previous_root=source, registry_path=registry
    )
    assert rebound["root_path"] == str(destination.resolve())
    assert rebound["root_fingerprint"] == project_root_fingerprint(destination)
    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        rebind_project_registration("p1", source, registry_path=registry)


def test_registration_operations_reject_missing_or_conflicting_claims(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"

    with pytest.raises(RuntimeError, match="not activated"):
        project_config.validate_project_registration("missing", root, registry_path=registry)
    with pytest.raises(RuntimeError, match="not registered"):
        move_project_registration("missing", root, tmp_path / "moved", registry_path=registry)
    with pytest.raises(RuntimeError, match="not registered"):
        rebind_project_registration("missing", root, registry_path=registry)

    claim = {
        "root_path": str(root.resolve()),
        "root_fingerprint": project_root_fingerprint(root),
        "api_port": 18001,
        "web_port": 20001,
    }
    project_config._write_registry(
        registry,
        {"version": 1, "projects": {"p1": claim, "p2": {**claim, "api_port": 18002}}},
    )
    with pytest.raises(RuntimeError, match="already claimed by another"):
        project_config.validate_project_registration("p1", root, registry_path=registry)


def test_rebind_upgrades_a_legacy_root_claim(tmp_path: Path):
    registry = tmp_path / "projects.json"
    root = tmp_path / "project"
    project_config._write_registry(
        registry,
        {
            "version": 1,
            "projects": {
                "p1": {
                    "root_path": str(root.resolve()),
                    "api_port": 18001,
                    "web_port": 20001,
                }
            },
        },
    )

    rebound = rebind_project_registration("p1", root, registry_path=registry)
    assert rebound["root_fingerprint"] == project_root_fingerprint(root)
    assert json.loads(registry.read_text())["projects"]["p1"] == rebound


def test_move_rejects_a_destination_claimed_by_another_project(tmp_path: Path):
    registry = tmp_path / "projects.json"
    first = tmp_path / "first"
    second = tmp_path / "second"
    register_project_config(
        first,
        {"id": "p1", "api_port": 18001, "web_port": 20001},
        registry_path=registry,
    )
    register_project_config(
        second,
        {"id": "p2", "api_port": 18002, "web_port": 20002},
        registry_path=registry,
    )

    with pytest.raises(RuntimeError, match="destination checkout is already claimed"):
        move_project_registration("p1", first, second, registry_path=registry)
    projects = json.loads(registry.read_text())["projects"]
    assert projects["p1"]["root_path"] == str(first.resolve())
    assert projects["p2"]["root_path"] == str(second.resolve())


def test_restore_never_steals_a_claim_without_force(tmp_path: Path, monkeypatch):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    (old_root / ".git/info").mkdir(parents=True)
    (new_root / ".git/info").mkdir(parents=True)
    write_project_config(old_root / project_config.CONFIG_PATH, "p1", "Old", 18001, 20001)
    register_project_config(old_root, load_project(old_root))
    monkeypatch.setattr(project_config, "port_available", lambda _: True)

    with pytest.raises(RuntimeError, match="already claimed by another checkout"):
        restore_project_config(new_root, {"id": "p1", "name": "Restored"})
    assert not (new_root / project_config.CONFIG_PATH).exists()
    assert json.loads(project_config.REGISTRY_PATH.read_text())["projects"]["p1"][
        "root_path"
    ] == str(old_root.resolve())

    restored = restore_project_config(
        new_root, {"id": "p1", "name": "Restored"}, force=True
    )
    assert restored["id"] == "p1"
    assert json.loads(project_config.REGISTRY_PATH.read_text())["projects"]["p1"][
        "root_path"
    ] == str(new_root.resolve())


def test_restore_rejects_a_target_root_claimed_by_another_project(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "target"
    register_project_config(
        root,
        {"id": "owner", "api_port": 18001, "web_port": 20001},
    )
    monkeypatch.setattr(project_config, "port_available", lambda _port: True)

    with pytest.raises(RuntimeError, match="restore target is already claimed"):
        restore_project_config(root, {"id": "restored", "name": "Restored"})

    projects = json.loads(project_config.REGISTRY_PATH.read_text())["projects"]
    assert set(projects) == {"owner"}


def test_restore_rolls_back_the_registry_when_config_write_fails(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "restored"
    monkeypatch.setattr(project_config, "port_available", lambda _port: True)

    def fail_config_write(*_args, **_kwargs):
        raise OSError("simulated config write failure")

    monkeypatch.setattr(project_config, "write_project_config", fail_config_write)
    with pytest.raises(OSError, match="simulated config write failure"):
        restore_project_config(root, {"id": "p1", "name": "Restored"})

    registry = json.loads(project_config.REGISTRY_PATH.read_text())
    assert registry == {"version": 1, "projects": {}}
    assert not (root / project_config.CONFIG_PATH).exists()


def test_force_restore_atomically_replaces_the_target_root_claim(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "target"
    (root / ".git/info").mkdir(parents=True)
    write_project_config(root / project_config.CONFIG_PATH, "old", "Old", 18001, 20001)
    register_project_config(root, load_project(root))
    monkeypatch.setattr(project_config, "port_available", lambda _: True)

    restored = restore_project_config(root, {"id": "new", "name": "New"}, force=True)
    assert restored["id"] == "new"
    registry = json.loads(project_config.REGISTRY_PATH.read_text())
    assert set(registry["projects"]) == {"new"}
    assert registry["projects"]["new"]["root_path"] == str(root.resolve())


def test_concurrent_claims_for_one_id_choose_exactly_one_root(
    tmp_path: Path, monkeypatch
):
    registry = tmp_path / "projects.json"
    monkeypatch.setattr(project_config, "_project_slot", lambda _: 0)

    def claim(index: int):
        try:
            return reserve_project_ports(
                "shared-id",
                tmp_path / f"root-{index}",
                registry_path=registry,
                checker=lambda _: True,
            )
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum("already claimed by another checkout" in str(result) for result in results) == 1
    registered = json.loads(registry.read_text())["projects"]["shared-id"]
    assert registered["root_path"] in {
        str((tmp_path / "root-0").resolve()),
        str((tmp_path / "root-1").resolve()),
    }


def test_live_long_running_lock_is_never_reclaimed(tmp_path: Path):
    registry = tmp_path / "projects.json"
    lock = registry.with_suffix(".json.lock")
    with project_config.portable_file_lock(registry):
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        old = time.time() - 3_600
        os.utime(lock, (old, old))
        with pytest.raises(TimeoutError, match="timed out"):
            with project_config.portable_file_lock(registry, timeout=0):
                pass
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity
    assert lock.exists()

    with project_config.portable_file_lock(registry, timeout=0):
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity
