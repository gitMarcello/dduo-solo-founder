"""Optional Personal Memory adapter. Private credentials are never project data.

No configuration means no network calls and unchanged Solo Founder behavior.
"""

from __future__ import annotations
import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5
import httpx

TIMEOUT = 1.5
FRACTION = 0.12
MAX_FRACTION = 0.20
MIN_SIMILARITY = 0.75
MAX_RESPONSE_BYTES = 65536


def config_path(role="client"):
    return Path(
        os.environ.get(
            "DDUO_PERSONAL_" + role.upper() + "_CONFIG",
            str(Path.home() / ".config/dduo-personal" / f"{role}.json"),
        )
    )


def private_config(role="client"):
    path = config_path(role)
    if not path.exists():
        return {}
    stat = path.lstat()
    if (
        path.is_symlink()
        or stat.st_mode & 0o077
        or (hasattr(os, "getuid") and stat.st_uid != os.getuid())
    ):
        raise ValueError("unsafe_personal_configuration")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("unsupported_personal_configuration")
    if any(not isinstance(data.get(key, {}), dict) for key in ["projects", "bindings"]):
        raise ValueError("invalid_personal_configuration")
    return data


def store_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(temp, path)


def https_url(url):
    if not isinstance(url, str):
        return False
    p = urlsplit(url)
    return (
        p.scheme == "https"
        and p.hostname
        and not (p.username or p.password or p.query or p.fragment)
    )


def publisher(project_id):
    config = private_config("publisher").get("projects", {}).get(project_id, {})
    if not isinstance(config, dict):
        raise ValueError("invalid_personal_publisher_configuration")
    if not config.get("enabled"):
        return None
    if (
        not https_url(config.get("url", ""))
        or not config.get("connection_id")
        or not config.get("publisher_key")
    ):
        raise ValueError("invalid_personal_publisher_configuration")
    return config


def publishing_enabled(project_id):
    try:
        return publisher(project_id) is not None
    except (ValueError, OSError):
        return False


def bridge_claim_key(node_type, node_key):
    identity = f"{node_type}:{node_key}"
    # Short identities retain their existing wire representation. The hash prefix
    # is outside the canonical node types, so it cannot collide with a short key.
    return (
        identity
        if len(identity) <= 300
        else "sha256:" + hashlib.sha256(identity.encode()).hexdigest()
    )


async def enqueue_personal_event(db, project_id, job_id, memory, proposal):
    """Same SQL transaction as the source consolidation, no remote side effect."""
    from sqlalchemy import select
    from .models import OutboxEvent

    config = publisher(project_id)
    if not config or memory is None:
        return
    claim = bridge_claim_key(memory.node_type, memory.node_key)
    previous = list(
        (
            await db.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.project_id == project_id,
                    OutboxEvent.event_type == "personal.publish",
                )
            )
        ).all()
    )
    versions = [
        e.payload.get("claim_version", 0)
        for e in previous
        if e.payload.get("claim_key") == claim
        and e.payload.get("connection_id") == config["connection_id"]
    ]
    event_id = str(uuid5(NAMESPACE_URL, f"{project_id}:{job_id}:{memory.id}:{proposal.operation}"))
    existing = next((e for e in previous if e.payload.get("event_id") == event_id), None)
    payload = dict(
        schema_version=1,
        event_id=event_id,
        connection_id=config["connection_id"],
        claim_key=claim,
        claim_version=existing.payload["claim_version"]
        if existing
        else max(versions, default=0) + 1,
        operation=proposal.operation,
        text=proposal.text,
        epistemic_status=proposal.epistemic_status,
        source_ref=dict(
            system="dduo-solo-founder",
            project_id=project_id,
            memory_id=memory.id,
            revision=memory.revision,
            sleep_job_id=job_id,
        ),
        origin_chain=[],
    )
    if existing:
        if existing.payload != payload:
            raise ValueError("personal_event_idempotency_conflict")
        return
    db.add(
        OutboxEvent(
            project_id=project_id,
            event_type="personal.publish",
            aggregate_id=memory.id,
            payload=payload,
            delivery_priority=50,
        )
    )


def deliver(project_id, payload, *, transport=None):
    """Synchronous worker entry point; async callers must use a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError("personal_delivery_requires_sync_worker")
    config = publisher(project_id)
    if not config or config["connection_id"] != payload.get("connection_id"):
        raise ValueError("personal_connection_disabled")
    token = config["connection_id"] + "." + config["publisher_key"]

    async def send():
        async with (
            asyncio.timeout(TIMEOUT),
            httpx.AsyncClient(
                timeout=TIMEOUT, transport=transport, follow_redirects=False
            ) as client,
        ):
            async with client.stream(
                "POST",
                config["url"].rstrip("/") + "/bridge/v1/events",
                json=payload,
                headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
            ) as response:
                if response.status_code not in {200, 201, 409}:
                    # No exception URL, response body, token or source text in shared logs.
                    raise ValueError("personal_delivery_failed")
                content = bytearray()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("personal_delivery_failed")
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > 4096:
                        raise ValueError("personal_delivery_failed")
                    content.extend(chunk)
                try:
                    receipt = json.loads(content)
                except (ValueError, UnicodeError):
                    receipt = None
                if response.status_code == 409:
                    if isinstance(receipt, dict) and receipt.get("error") == "stale_claim_version":
                        return  # A newer version is already authoritative; retries cannot help.
                    raise ValueError("personal_delivery_failed")
                if (
                    not isinstance(receipt, dict)
                    or not isinstance(receipt.get("source_id"), str)
                    or not receipt["source_id"].strip()
                ):
                    raise ValueError("personal_delivery_failed")

    asyncio.run(send())


def recall(workspace_root, project_id, query, *, fraction=FRACTION, budget=9000, transport=None):
    """Serialize rotating refresh credentials across chats; a busy store adds no context."""
    if not config_path().exists() or os.name != "posix":
        return []
    deadline = time.monotonic() + TIMEOUT
    try:
        import fcntl

        lock_path = str(config_path()) + ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as guard:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _recall_locked(
                workspace_root,
                project_id,
                query,
                fraction=fraction,
                budget=budget,
                transport=transport,
                deadline=deadline,
            )
    except (OSError, ValueError):
        return []


def _recall_locked(
    workspace_root,
    project_id,
    query,
    *,
    fraction=FRACTION,
    budget=9000,
    transport=None,
    deadline=None,
):
    """Synchronous hook adapter with a cancellable total network deadline."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return []  # A synchronous hook must not block an existing async caller.

    async def run():
        left = (deadline or time.monotonic() + TIMEOUT) - time.monotonic()
        if left <= 0:
            return []
        async with asyncio.timeout(left):
            return await _recall_locked_async(
                workspace_root,
                project_id,
                query,
                fraction=fraction,
                budget=budget,
                transport=transport,
                deadline=deadline,
            )

    try:
        return asyncio.run(run())
    except TimeoutError:
        return []


async def _recall_locked_async(
    workspace_root,
    project_id,
    query,
    *,
    fraction=FRACTION,
    budget=9000,
    transport=None,
    deadline=None,
):
    """Project-approved recall on the member's device. Fail open."""
    if not query.strip():
        return []
    try:
        path = config_path()
        data = private_config()
        key = hashlib.sha256(
            (str(Path(workspace_root).resolve()) + "\n" + project_id).encode()
        ).hexdigest()
        binding = data.get("bindings", {}).get(key, {})
        if not isinstance(binding, dict):
            return []
        if not binding.get("enabled") or not https_url(binding.get("url", "")):
            return []
        quota = max(0, int(budget * min(max(fraction, 0), MAX_FRACTION)))
        if not quota:
            return []
        deadline = deadline or time.monotonic() + TIMEOUT
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:

            async def request(url, payload, token=None, *, form=False):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError()
                headers = {"Accept-Encoding": "identity"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                async with client.stream(
                    "POST",
                    url,
                    **({"data": payload} if form else {"json": payload}),
                    headers=headers,
                    timeout=left,
                ) as response:
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise ValueError("unexpected_personal_response_encoding")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ValueError("personal_response_too_large")
                        content.extend(chunk)
                    return json.loads(content)

            token = binding.get("access_token", "")
            if binding.get("expires_at", 0) < time.time() + 30:
                if binding.get("token_endpoint", "") != binding["url"].rstrip("/") + "/token":
                    return []
                fresh = await request(
                    binding["token_endpoint"],
                    dict(
                        grant_type="refresh_token",
                        refresh_token=binding["refresh_token"],
                        client_id=binding["client_id"],
                        resource=binding.get("audience", binding["url"].rstrip("/") + "/mcp"),
                    ),
                    form=True,
                )
                token = fresh["access_token"]
                binding.update(
                    access_token=token,
                    refresh_token=fresh.get("refresh_token", binding["refresh_token"]),
                    expires_at=time.time() + fresh.get("expires_in", 300),
                )
                store_private(path, data)
            ctx = await request(
                binding["url"].rstrip("/") + "/api/contexts",
                {"connection_id": binding["connection_id"], "strict": True},
                token,
            )
            if not isinstance(ctx, dict) or not isinstance(ctx.get("context_id"), str):
                return []
            result = await request(
                binding["url"].rstrip("/") + "/api/search",
                dict(
                    context_id=ctx["context_id"],
                    query=query[:12000],
                    threshold=MIN_SIMILARITY,
                    limit=8,
                    character_budget=quota,
                ),
                token,
            )
        if not isinstance(result, dict) or not isinstance(result.get("items", []), list):
            return []
        selected = []
        seen = set()
        used = 0
        for memory in result.get("items", []):
            if not isinstance(memory, dict):
                continue
            score = memory.get("score", 0)
            if (
                type(score) not in (int, float)
                or not math.isfinite(score)
                or not MIN_SIMILARITY <= score <= 1
            ):
                continue
            if not isinstance(memory.get("id"), str) or not isinstance(memory.get("text"), str):
                continue
            origin = memory.get("origin", {})
            if not isinstance(origin, dict) or not isinstance(origin.get("source_ref", {}), dict):
                continue
            provenance = memory.get("origins") or ([origin] if origin else [])
            if not isinstance(provenance, list) or any(
                not isinstance(value, dict)
                or not isinstance(value.get("source_ref", {}), dict)
                or ("claim_identity" in value and not isinstance(value["claim_identity"], str))
                for value in provenance
            ):
                continue
            if origin and origin not in provenance:
                provenance = [origin, *provenance]
            lineages = {
                value["claim_identity"] for value in provenance if value.get("claim_identity")
            } or {memory["id"]}
            if not memory["id"] or lineages & seen:
                continue
            # A mirrored memory originating in this same project is already known there.
            if any(
                value.get("source_ref", {}).get("project_id") == project_id for value in provenance
            ):
                continue
            content = dict(
                id=memory["id"],
                text=memory["text"],
                revision=memory.get("revision"),
                epistemic_status=memory.get("epistemic_status"),
                origin=memory.get("origin", {}),
                origins=provenance,
                source="personal-memory",
                untrusted_evidence=True,
            )
            encoded = json.dumps(content, ensure_ascii=False)
            size = max(len(encoded), len(encoded.encode("utf-16-le")) // 2) + 180
            if used + size > quota:
                continue
            used += size
            seen.update(lineages)
            selected.append(content)
        return selected
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RecursionError,
        httpx.HTTPError,
        TimeoutError,
    ):
        return []


def login(args):
    """Native PKCE login. OAuth tokens never pass through a model or prompt."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse
    import webbrowser

    args.issuer = getattr(args, "issuer", None) or args.url.rstrip("/")
    if not all(https_url(v) for v in [args.url, args.issuer]):
        raise ValueError("https_required")
    if args.issuer != args.url.rstrip("/") or args.audience != args.url.rstrip("/") + "/mcp":
        raise ValueError("local_instance_identity_required")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    metadata = httpx.get(
        args.issuer.rstrip("/") + "/.well-known/oauth-authorization-server", timeout=5
    ).json()
    if metadata.get("issuer") != args.issuer or not all(
        metadata.get(k, "").startswith(args.issuer + "/")
        for k in ["authorization_endpoint", "token_endpoint"]
    ):
        raise ValueError("invalid_issuer_metadata")
    callback = "http://127.0.0.1:18741/callback"
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != "/callback" or params.get("state", [""])[0] != state:
                self.send_error(400)
                return
            returned_issuer = params.get("iss", [None])[0]
            if ((metadata.get("authorization_response_iss_parameter_supported") and returned_issuer != args.issuer)
                or (returned_issuer is not None and returned_issuer != args.issuer)):
                self.send_error(400)
                return
            result["code"] = params.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Login received. Return to the terminal.")

        def log_message(self, *values):
            pass

    server = HTTPServer(("127.0.0.1", 18741), Callback)
    server.timeout = 180
    url = (
        metadata["authorization_endpoint"]
        + "?"
        + urlencode(
            dict(
                client_id=args.client_id,
                response_type="code",
                redirect_uri=callback,
                code_challenge=challenge,
                code_challenge_method="S256",
                state=state,
                resource=args.audience,
                scope="memory:read",
            )
        )
    )
    webbrowser.open(url)
    server.handle_request()
    server.server_close()
    if not result.get("code"):
        raise ValueError("login_not_completed")
    response = httpx.post(
        metadata["token_endpoint"],
        data=dict(
            grant_type="authorization_code",
            code=result["code"],
            client_id=args.client_id,
            code_verifier=verifier,
            redirect_uri=callback,
            resource=args.audience,
        ),
        timeout=10,
    )
    response.raise_for_status()
    tokens = response.json()
    me = httpx.get(
        args.url.rstrip("/") + "/api/me",
        headers={"Authorization": "Bearer " + tokens["access_token"]},
        timeout=5,
    )
    me.raise_for_status()
    if me.json().get("project_connection") != args.connection:
        raise ValueError("oauth_client_not_bound_to_requested_project")
    data = private_config() or {"version": 1, "bindings": {}}
    key = hashlib.sha256(
        (str(Path(args.workspace_root).resolve()) + "\n" + args.project).encode()
    ).hexdigest()
    data["bindings"][key] = dict(
        enabled=True,
        url=args.url,
        connection_id=args.connection,
        client_id=args.client_id,
        audience=args.audience,
        token_endpoint=metadata["token_endpoint"],
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        expires_at=time.time() + tokens.get("expires_in", 300),
    )
    store_private(config_path(), data)
    print("Project-bound personal recall configured in the private device store.")


def main():
    p = argparse.ArgumentParser(description="Optional Personal Memory connector")
    sub = p.add_subparsers(dest="action", required=True)
    login_parser = sub.add_parser("login")
    for name in [
        "url",
        "audience",
        "client-id",
        "connection",
        "workspace-root",
        "project",
    ]:
        login_parser.add_argument("--" + name, required=True)
    login_parser.add_argument("--issuer", help="Optional; must match the instance URL")
    publisher_parser = sub.add_parser("configure-publisher")
    for name in ["url", "project", "connection"]:
        publisher_parser.add_argument("--" + name, required=True)
    args = p.parse_args()
    if args.action == "login":
        login(args)
    else:
        import getpass

        if not https_url(args.url):
            raise ValueError("https_required")
        key = getpass.getpass("Publisher credential from Dashboard (private): ")
        if "." in key:
            connection, key = key.split(".", 1)
            if connection != args.connection:
                raise ValueError("connection_mismatch")
        if len(key) < 32:
            raise ValueError("invalid_publisher_credential")
        data = private_config("publisher") or {"version": 1, "projects": {}}
        data["projects"][args.project] = dict(
            enabled=True, url=args.url, connection_id=args.connection, publisher_key=key
        )
        store_private(config_path("publisher"), data)
        print("Publisher configured in the private host store; no read permission granted.")


if __name__ == "__main__":
    main()
