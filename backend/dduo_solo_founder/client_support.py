from __future__ import annotations

import re


SUPPORTED_CLIENT_FAMILIES = frozenset({"codex", "claude"})


class UnsupportedClientError(ValueError):
    """Raised when a client is outside the deliberately supported Beta surface."""


def _normalized_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower()).strip()


def client_family(client_name: str | None) -> str | None:
    """Map an MCP client name to the small, supported Beta client surface."""

    normalized = _normalized_name(client_name)
    if normalized in {
        "codex",
        "openai codex",
        "codex cli",
        "codex desktop",
        "codex mcp client",
    }:
        return "codex"
    if normalized in {
        "claude",
        "claude ai",
        "claude code",
        "anthropic claude",
        "anthropic claude code",
    }:
        return "claude"
    return None


def require_supported_client(
    client_name: str | None,
    *,
    declared_client: str | None = None,
) -> str:
    """Return the client family or reject unsupported/mismatched adapters.

    This is a product-support boundary, not DRM. ``client_name`` comes from the
    MCP initialize request. Native adapters may additionally declare their
    client; when present it must agree with the MCP client identity.
    """

    family = client_family(client_name)
    if family not in SUPPORTED_CLIENT_FAMILIES:
        raise UnsupportedClientError(
            "dDuo Solo Founder Beta supports only Codex and Claude Code"
        )
    if declared_client is not None and client_family(declared_client) != family:
        raise UnsupportedClientError("dDuo client adapter does not match the MCP client")
    return family
