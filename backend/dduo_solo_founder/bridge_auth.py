"""Derive one unforgeable host-bridge credential per isolated project stack."""

from __future__ import annotations

import hashlib
import hmac


BRIDGE_PROTOCOL_VERSION = 3
_PROJECT_SCOPE = b"dduo-host-bridge-project-v1\0"


def project_bridge_token(master_token: str, project_id: str) -> str:
    """Bind a Docker-visible token to exactly one project without storing a map."""
    master = str(master_token).strip()
    project = str(project_id).strip()
    if not master or not project or len(project) > 160:
        return ""
    digest = hmac.new(
        master.encode("utf-8"),
        _PROJECT_SCOPE + project.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"dduo_bridge_{digest}"


def bridge_token_authorized(
    master_token: str,
    supplied_token: str,
    *,
    project_id: str | None = None,
) -> bool:
    """Accept host control or the derived credential for the exact project only."""
    master = str(master_token).strip()
    supplied = str(supplied_token).strip()
    if not master or not supplied:
        return False
    if hmac.compare_digest(master, supplied):
        return True
    scoped = project_bridge_token(master, project_id or "")
    return bool(scoped) and hmac.compare_digest(scoped, supplied)
