"""Reusable browser links, attached only to this binding's dashboard URLs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dduo_solo_founder.project_config import DASHBOARD_TABS

ACCESS_TOKEN_PARAMETER = "access_token"
ACCESS_TOKEN_PATTERN = re.compile(r"dduo_link_[A-Za-z0-9_-]{43}")
LINK_FIELDS = {"url", "item_url", "dashboard_url", "plans_dashboard_url"}


def validate_browser_link(payload: dict) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("the server returned an invalid dashboard access link")
    token = payload.get("token")
    expiry = payload.get("expires_at")
    if (
        not isinstance(token, str)
        or not ACCESS_TOKEN_PATTERN.fullmatch(token)
        or payload.get("reusable") is not True
        or not isinstance(expiry, str)
    ):
        raise ValueError("the server returned an invalid dashboard access link")
    try:
        expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        valid = expires_at.tzinfo is not None and expires_at > datetime.now(timezone.utc)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("the server returned an expired dashboard access link")
    return token, expiry


def is_project_dashboard_url(value: object, dashboard_url: str, project_id: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed, expected = urlsplit(value), urlsplit(dashboard_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        projects = [item for key, item in query if key == "project"]
        tabs = [item for key, item in query if key == "tab"]
        return (
            parsed.scheme == expected.scheme == "https"
            and parsed.netloc.lower() == expected.netloc.lower()
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") == expected.path.rstrip("/")
            and projects == [project_id]
            and len(tabs) == 1
            and tabs[0] in DASHBOARD_TABS
        )
    except ValueError:
        return False


def with_dashboard_access_token(url: str, token: str) -> str:
    """Append a browser-only credential; never accept the plugin's bearer here."""
    if not ACCESS_TOKEN_PATTERN.fullmatch(token):
        raise ValueError("invalid dashboard link credential")
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key not in {ACCESS_TOKEN_PARAMETER, "ticket"}]
    query.append((ACCESS_TOKEN_PARAMETER, token))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def map_dashboard_links(value: object, dashboard_url: str, project_id: str, transform) -> object:
    """Only presentation URL fields, never Markdown, arbitrary URLs or stored text."""
    if isinstance(value, dict):
        return {
            key: transform(item)
            if key in LINK_FIELDS and is_project_dashboard_url(item, dashboard_url, project_id)
            else map_dashboard_links(item, dashboard_url, project_id, transform)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [map_dashboard_links(item, dashboard_url, project_id, transform) for item in value]
    return value
