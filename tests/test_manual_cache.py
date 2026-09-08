from __future__ import annotations

import json
import os
import shutil

import pytest

from dduo_solo_founder import manual_cache
from dduo_solo_founder.client_binding import ProjectBinding
from dduo_solo_founder.manual_cache import (
    _cache_path,
    load_verified_manual,
    store_verified_manual,
)


def remote_binding(tmp_path, *, token: str = "project-token", suffix: str = "a"):
    return ProjectBinding(
        project_id=f"project-{suffix}",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url="https://203.0.113.10",
        binding_id=suffix * 64,
        bearer_token=token,
    )


def test_verified_manual_cache_round_trip_is_private_and_normalized(tmp_path):
    binding = remote_binding(tmp_path)
    path = store_verified_manual(
        binding,
        {
            "version": 4,
            "content": "Always deploy from the authoritative branch.",
            "updated_at": "2026-08-28T12:00:00Z",
            "warnings": ["manual_above_soft_limit", 7],
        },
        directory=tmp_path / "cache",
    )

    assert path is not None
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    cached = load_verified_manual(binding, directory=tmp_path / "cache")
    assert cached == {
        "content": "Always deploy from the authoritative branch.",
        "version": 4,
        "updated_by_member_id": None,
        "updated_at": "2026-08-28T12:00:00Z",
        "characters": 44,
        "soft_limit_characters": 4_000,
        "hard_limit_characters": 100_000,
        "warnings": ["manual_above_soft_limit"],
    }


def test_cache_rejects_tampering_wrong_token_and_cross_binding_copy(tmp_path):
    directory = tmp_path / "cache"
    binding = remote_binding(tmp_path)
    path = store_verified_manual(
        binding,
        {"version": 1, "content": "trusted"},
        directory=directory,
    )
    assert path is not None

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["manual"]["content"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert load_verified_manual(binding, directory=directory) is None

    path = store_verified_manual(
        binding,
        {"version": 1, "content": "trusted"},
        directory=directory,
    )
    wrong_token = remote_binding(tmp_path, token="different-token")
    assert load_verified_manual(wrong_token, directory=directory) is None

    other = remote_binding(tmp_path, suffix="b")
    other_path = _cache_path(other, directory)
    shutil.copyfile(path, other_path)
    other_path.chmod(0o600)
    assert load_verified_manual(other, directory=directory) is None


def test_cache_rejects_unsafe_or_invalid_state_and_never_caches_local(tmp_path):
    directory = tmp_path / "cache"
    binding = remote_binding(tmp_path)
    assert store_verified_manual(
        binding,
        {"version": 1, "content": "x" * 100_001},
        directory=directory,
    ) is None
    assert store_verified_manual(
        binding,
        {"version": True, "content": "invalid"},
        directory=directory,
    ) is None

    local = ProjectBinding(
        project_id="local",
        name="Local",
        root_path=tmp_path,
        kind="local",
        api_url="http://127.0.0.1:8765",
        dashboard_url="http://127.0.0.1:4173",
        binding_id="c" * 64,
        api_port=8765,
        web_port=4173,
    )
    assert store_verified_manual(
        local, {"version": 1, "content": "local"}, directory=directory
    ) is None
    assert load_verified_manual(local, directory=directory) is None

    path = store_verified_manual(
        binding, {"version": 1, "content": "trusted"}, directory=directory
    )
    assert path is not None
    if os.name != "nt":
        path.chmod(0o644)
        assert load_verified_manual(binding, directory=directory) is None


def test_cache_write_failure_is_visible_to_the_optional_caller(monkeypatch, tmp_path):
    binding = remote_binding(tmp_path)
    # Atomic persistence uses os.replace; this assertion protects the public
    # contract that callers may safely catch filesystem failures.
    monkeypatch.setattr(
        "dduo_solo_founder.manual_cache.os.replace",
        lambda *args: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(OSError, match="disk unavailable"):
        store_verified_manual(
            binding,
            {"version": 1, "content": "manual"},
            directory=tmp_path / "cache",
        )


def test_cache_rejects_non_object_unreadable_and_foreign_owner_state(
    monkeypatch, tmp_path
):
    directory = tmp_path / "cache"
    binding = remote_binding(tmp_path)
    assert store_verified_manual(binding, [], directory=directory) is None

    path = _cache_path(binding, directory)
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    path.chmod(0o600)
    assert load_verified_manual(binding, directory=directory) is None

    path.write_text("[]", encoding="utf-8")
    assert load_verified_manual(binding, directory=directory) is None

    assert store_verified_manual(
        binding, {"version": 1, "content": "trusted"}, directory=directory
    ) == path
    if hasattr(os, "getuid"):
        monkeypatch.setattr(manual_cache.os, "getuid", lambda: path.stat().st_uid + 1)
        assert load_verified_manual(binding, directory=directory) is None


def test_cache_rejects_symlinks(tmp_path):
    directory = tmp_path / "cache"
    binding = remote_binding(tmp_path)
    target = store_verified_manual(
        binding, {"version": 1, "content": "trusted"}, directory=directory
    )
    assert target is not None
    copied = tmp_path / "copied-cache"
    copied.mkdir()
    _cache_path(binding, copied).symlink_to(target)
    assert load_verified_manual(binding, directory=copied) is None
