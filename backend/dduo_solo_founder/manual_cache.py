"""Authenticated offline cache for a remote project's operating manual.

Only the remote bearer already scoped to this project can authenticate the
cache. A copied, edited, or cross-project file is therefore ignored instead of
being injected into an agent while the authoritative service is unavailable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import uuid
from pathlib import Path

from dduo_solo_founder.client_binding import ProjectBinding


MANUAL_CACHE_DIR = Path.home() / ".config" / "dduo-solo-founder" / "manual-cache"
MANUAL_CACHE_VERSION = 1


def _cache_path(binding: ProjectBinding, directory: Path | None = None) -> Path:
    scope = hashlib.sha256(
        f"{binding.project_id}\0{binding.binding_id}".encode("utf-8")
    ).hexdigest()
    return (directory or MANUAL_CACHE_DIR) / f"{scope}.json"


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _key(binding: ProjectBinding) -> bytes | None:
    token = binding.bearer_token if binding.remote else None
    return token.encode("utf-8") if token else None


def _normalized_manual(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    content = value.get("content")
    version = value.get("version")
    if not isinstance(content, str) or len(content) > 100_000:
        return None
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        return None
    return {
        "content": content,
        "version": version,
        "updated_by_member_id": (
            str(value["updated_by_member_id"])
            if value.get("updated_by_member_id") is not None
            else None
        ),
        "updated_at": (
            str(value["updated_at"]) if value.get("updated_at") is not None else None
        ),
        "characters": len(content),
        "soft_limit_characters": int(value.get("soft_limit_characters") or 4_000),
        "hard_limit_characters": 100_000,
        "warnings": [
            str(item)
            for item in value.get("warnings", [])
            if isinstance(item, str)
        ][:20],
    }


def store_verified_manual(
    binding: ProjectBinding,
    manual: object,
    *,
    directory: Path | None = None,
) -> Path | None:
    """Persist a manual only after an authenticated remote API response succeeded."""
    key = _key(binding)
    normalized = _normalized_manual(manual)
    if key is None or normalized is None:
        return None
    payload = {
        "version": MANUAL_CACHE_VERSION,
        "project_id": binding.project_id,
        "binding_id": binding.binding_id,
        "manual": normalized,
    }
    envelope = {
        **payload,
        "authentication": hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest(),
    }
    path = _cache_path(binding, directory)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_verified_manual(
    binding: ProjectBinding,
    *,
    directory: Path | None = None,
) -> dict | None:
    """Return only an intact cache bound to this exact project endpoint and token."""
    key = _key(binding)
    if key is None:
        return None
    path = _cache_path(binding, directory)
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            return None
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            return None
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            return None
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict):
        return None
    supplied = str(envelope.pop("authentication", ""))
    expected = hmac.new(key, _canonical(envelope), hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        return None
    if (
        envelope.get("version") != MANUAL_CACHE_VERSION
        or envelope.get("project_id") != binding.project_id
        or envelope.get("binding_id") != binding.binding_id
    ):
        return None
    return _normalized_manual(envelope.get("manual"))
