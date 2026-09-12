"""Keep dashboard access-link credentials out of automatically captured content.

This deliberately recognizes only dDuo's access-link token, not arbitrary
secrets. It does not inspect image/binary formats or the vendor's chat history.
"""

from __future__ import annotations

import re
from typing import Any


def _literal_or_percent_encoded(character: str) -> str:
    hexadecimal = f"{ord(character):02x}"
    encoded = "".join(
        f"[{digit.lower()}{digit.upper()}]" if digit.isalpha() else digit
        for digit in hexadecimal
    )
    # Once- and twice-percent-encoded links are common in copied URLs. Never
    # decode the whole text: unrelated user content must remain byte-identical.
    return f"(?:{re.escape(character)}|%(?:25)?{encoded})"


_PREFIX = "".join(_literal_or_percent_encoded(character) for character in "dduo_link_")
_SECRET_CHARACTER = (
    r"(?:[A-Za-z0-9_-]|%(?:25)?(?:2[dD]|3[0-9]|[46][1-9A-Fa-f]|[57][0-9Aa]|5[fF]))"
)
_ACCESS_TOKEN = re.compile(f"(?P<prefix>{_PREFIX})(?P<secret>{_SECRET_CHARACTER}{{43,}})")
_REDACTED_RENDER_SUFFIX = "+access-link-redacted-v1"


def redacted_render_version(version: str) -> str:
    """Label a redacted snapshot without exceeding its existing schema bound."""
    if version.endswith(_REDACTED_RENDER_SUFFIX):
        return version
    return version[: 80 - len(_REDACTED_RENDER_SUFFIX)] + _REDACTED_RENDER_SUFFIX


def redact_dashboard_access_tokens(value: Any) -> Any:
    """Return a redacted copy, preserving character/UTF-8 measurement lengths.

    The credential suffix is replaced by ASCII asterisks. Existing content
    hashes still need to be recomputed by the observation producer.
    """
    if isinstance(value, str):
        return _ACCESS_TOKEN.sub(
            lambda match: match.group("prefix") + "*" * len(match.group("secret")), value
        )
    if isinstance(value, dict):
        return {
            redact_dashboard_access_tokens(key): redact_dashboard_access_tokens(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_dashboard_access_tokens(item) for item in value]
    return value


def contains_dashboard_access_tokens(value: Any) -> bool:
    """Recognize access-link credentials in textual artifact fields."""
    if isinstance(value, str):
        return _ACCESS_TOKEN.search(value) is not None
    if isinstance(value, dict):
        return any(
            contains_dashboard_access_tokens(key) or contains_dashboard_access_tokens(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_dashboard_access_tokens(item) for item in value)
    return False
