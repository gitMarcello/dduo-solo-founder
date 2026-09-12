"""Read-only container-to-host bridge check; no generation or configuration."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from dduo_solo_founder.bridge_auth import BRIDGE_PROTOCOL_VERSION

MAX_RESPONSE_BYTES = 8192
PROBE_TIMEOUT_SECONDS = 5.0


def _result(reason: str) -> dict[str, bool | str]:
    return {"ready": reason == "ready", "reason": reason}


def probe(project_id: str, *, environ: Mapping[str, str] | None = None) -> dict[str, bool | str]:
    """Use the container's real URL/token, returning only content-free status."""
    environment = os.environ if environ is None else environ
    url = environment.get("CLI_BRIDGE_URL", "")
    token = environment.get("CLI_BRIDGE_TOKEN", "")
    if (
        not project_id
        or len(project_id) > 160
        or any(character.isspace() or ord(character) < 32 for character in project_id)
        or not url
        or not token
        or any(character.isspace() or ord(character) < 32 for character in url + token)
    ):
        return _result("configuration")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or parsed.port == 0
        ):
            return _result("configuration")
    except ValueError:
        return _result("configuration")

    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    try:
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            verify=True,
            timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS),
        ) as client:
            with client.stream(
                "GET",
                f"{url.rstrip('/')}/health",
                params={"project_id": project_id},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            ) as response:
                if response.status_code in {401, 403}:
                    return _result("unauthorized")
                if response.status_code != 200:
                    return _result("invalid_response")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=1024):
                    if time.monotonic() > deadline:
                        return _result("unreachable")
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        return _result("invalid_response")
    except httpx.HTTPError:
        return _result("unreachable")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError):
        return _result("invalid_response")
    if not isinstance(payload, dict) or payload.get("status") != "ok" or payload.get("project_id") != project_id:
        return _result("invalid_response")
    if type(payload.get("bridge_protocol_version")) is not int or payload["bridge_protocol_version"] != BRIDGE_PROTOCOL_VERSION:
        return _result("protocol_mismatch")
    return _result("ready")


def main(argv: Sequence[str] | None = None) -> int:
    """One non-secret project ID in argv; never print request or exception data."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        result = probe(arguments[0]) if len(arguments) == 1 else _result("configuration")
    except Exception:
        result = _result("invalid_response")
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ready"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
