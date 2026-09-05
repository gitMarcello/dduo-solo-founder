from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from dduo_solo_founder import restore_index


def test_restore_snapshot_uploads_a_verified_file_and_quotes_collection(monkeypatch, tmp_path):
    snapshot = tmp_path / "index.snapshot"
    snapshot.write_bytes(b"qdrant")
    captured = {}
    monkeypatch.setattr(
        restore_index,
        "get_settings",
        lambda: SimpleNamespace(qdrant_url="http://qdrant:6333"),
    )
    monkeypatch.setattr(
        restore_index.httpx,
        "post",
        lambda url, **kwargs: captured.update(url=url, **kwargs)
        or SimpleNamespace(raise_for_status=lambda: None),
    )
    restore_index.restore_snapshot(snapshot, "project/name")
    assert captured["url"].endswith("/project%2Fname/snapshots/upload?priority=snapshot")
    assert captured["files"]["snapshot"][0] == "index.snapshot"
    with pytest.raises(FileNotFoundError):
        restore_index.restore_snapshot(tmp_path / "missing.snapshot", "project")


def test_restore_index_cli_delegates_to_upload(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sys, "argv", ["restore", str(tmp_path / "x"), "collection"])
    monkeypatch.setattr(restore_index, "restore_snapshot", lambda snapshot, collection: calls.append((snapshot, collection)))
    restore_index.main()
    assert calls == [(tmp_path / "x", "collection")]
