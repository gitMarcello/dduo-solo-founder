"""Small project-scoped authentication and team primitives.

The local product remains zero-configuration on a trusted loopback/private
Compose network. A remote node opts into mandatory bearer authentication with
``DDUO_AUTH_REQUIRED=true``. Tokens are opaque device secrets and only their
hashes cross the persistence boundary.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dduo_solo_founder.db import get_session
from dduo_solo_founder.client_protocol import CLIENT_PROTOCOL_VERSION
from dduo_solo_founder.models import BrowserAuthCredential, Project, TeamAccessToken, TeamMember
from dduo_solo_founder import __version__


INFRASTRUCTURE_MANAGER = "infrastructure_manager"
PROJECT_MEMBER = "project_member"
CAPABILITY_LABELS = {
    INFRASTRUCTURE_MANAGER: "Gestore dell’infrastruttura",
    PROJECT_MEMBER: "Membro del progetto",
}
AUTH_EXEMPT_SUFFIXES = ("/auth/exchange", "/auth/browser-session")
BOOTSTRAP_SUFFIX = "/team/bootstrap"
AUTHORITY_SECRET_SUFFIXES = (
    BOOTSTRAP_SUFFIX,
    "/authority/status",
    "/authority/initialize",
    "/authority/activate",
    "/authority/complete",
    "/authority/recover",
)
CSRF_HEADER = "X-DDUO-CSRF"
_TRUE = frozenset({"1", "true", "yes", "on"})
CLIENT_COMPONENTS = frozenset({"hook", "mcp", "launcher"})
TOKEN_USAGE_TOUCH_INTERVAL = timedelta(minutes=15)
STATEFUL_READ_SUFFIXES = ("/memories/search",)
_RELEASE_VERSION = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?(?:\+[A-Za-z0-9._-]+)?$"
)
_PRERELEASE_ORDER = {"alpha": 0, "beta": 1, "rc": 2}


@dataclass(frozen=True, slots=True)
class TeamPrincipal:
    project_id: str | None
    member_id: str | None
    access_token_id: str | None
    browser_session_id: str | None
    display_name: str
    capability: str
    trusted_local: bool = False
    anonymous: bool = False

    @property
    def can_manage_infrastructure(self) -> bool:
        return self.capability == INFRASTRUCTURE_MANAGER


_request_principal: ContextVar[TeamPrincipal | None] = ContextVar(
    "dduo_team_principal", default=None
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def invitation_code() -> str:
    return f"dduo_inv_{secrets.token_urlsafe(32)}"


def browser_secret() -> str:
    return f"dduo_web_{secrets.token_urlsafe(32)}"


def browser_csrf_secret() -> str:
    """Return a per-session secret that JavaScript keeps in this port's origin."""
    return f"dduo_csrf_{secrets.token_urlsafe(32)}"


def browser_cookie_name(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]
    return f"dduo_session_{digest}"


def _release_order(value: str) -> tuple[int, int, int, int, int] | None:
    match = _RELEASE_VERSION.fullmatch(value.strip())
    if match is None:
        return None
    major, minor, patch, stage, number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        _PRERELEASE_ORDER[stage] if stage else 3,
        int(number or 0),
    )


def enforce_client_compatibility(request: Request, response: Response) -> None:
    """Negotiate only authenticated dDuo hook/MCP transports, never browser traffic."""
    component = request.headers.get("X-DDUO-Client-Component", "").strip().lower()
    if component not in CLIENT_COMPONENTS:
        return
    protocol = request.headers.get("X-DDUO-Client-Protocol", "").strip()
    headers = {
        "X-DDUO-Client-Status": "blocked",
        "X-DDUO-Target-Release": __version__,
    }
    try:
        supported = int(protocol) == int(CLIENT_PROTOCOL_VERSION)
    except ValueError:
        supported = False
    if not supported:
        raise HTTPException(
            426,
            "dDuo client protocol is incompatible; update the plugin, reload the active "
            "client as instructed, and open a new chat or session",
            headers=headers,
        )

    client_version = request.headers.get("X-DDUO-Client-Version", "").strip()
    client_order = _release_order(client_version)
    server_order = _release_order(__version__)
    if client_order is not None and server_order is not None and client_order < server_order:
        response.headers["X-DDUO-Client-Status"] = "grace"
        response.headers["X-DDUO-Target-Release"] = __version__
    else:
        # Unknown or newer versions stay usable. Protocol compatibility is the
        # hard boundary; the server never asks a client to downgrade.
        response.headers["X-DDUO-Client-Status"] = "compatible"


def request_principal() -> TeamPrincipal | None:
    return _request_principal.get()


def capability_flags(principal: TeamPrincipal) -> dict[str, bool]:
    return {
        "manage_infrastructure": principal.can_manage_infrastructure,
        "participate_in_project": not principal.anonymous,
    }


def serialize_member(member: TeamMember) -> dict:
    return {
        "id": member.id,
        "project_id": member.project_id,
        "display_name": member.display_name,
        "capability": member.capability,
        "capability_label": CAPABILITY_LABELS[member.capability],
        "status": member.status,
        "created_at": member.created_at,
        "updated_at": member.updated_at,
    }


def serialize_current_member(principal: TeamPrincipal) -> dict | None:
    if principal.anonymous:
        return None
    return {
        "id": principal.member_id or "trusted-local-owner",
        "project_id": principal.project_id,
        "display_name": principal.display_name,
        "capability": principal.capability,
        "capability_label": CAPABILITY_LABELS[principal.capability],
        "trusted_local": principal.trusted_local,
        "access_token_id": principal.access_token_id,
    }


def team_envelope(principal: TeamPrincipal, **payload) -> dict:
    return {
        **payload,
        "current_member": serialize_current_member(principal),
        "capabilities": capability_flags(principal),
    }


def require_infrastructure_manager(principal: TeamPrincipal) -> None:
    if not principal.can_manage_infrastructure:
        raise HTTPException(403, "infrastructure manager capability required")


def require_node_authority(request: Request) -> None:
    expected = os.getenv("DDUO_NODE_AUTHORITY_SECRET", "")
    supplied = request.headers.get("X-DDUO-Authority", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, "node authority credential required")


def require_project(principal: TeamPrincipal, project_id: str) -> None:
    if principal.trusted_local:
        return
    if principal.anonymous or principal.project_id != project_id:
        raise HTTPException(404, "project not found")


def project_authority_writable(project: Project) -> bool:
    """Fence restored databases to the host identity that owns their generation."""
    if project.authority_state != "active":
        return False
    owner = (project.authority_node_id or "").strip()
    if not owner:
        return True
    return secrets.compare_digest(owner, os.getenv("DDUO_NODE_ID", "").strip())


def project_authority_predicate():
    """SQL equivalent of :func:`project_authority_writable` for worker claims."""
    node_id = os.getenv("DDUO_NODE_ID", "").strip()
    return and_(
        Project.authority_state == "active",
        or_(Project.authority_node_id.is_(None), Project.authority_node_id == node_id),
    )


async def lock_writable_project(db: AsyncSession, project_id: str) -> Project:
    """Re-enter the authority fence after a route deliberately released its transaction.

    Provider and vector-store calls should not keep a PostgreSQL row locked for
    their whole duration.  Any route that commits before such a call must use
    this second fence before persisting its result, otherwise a transfer could
    freeze the project while that request was in flight and the old node could
    commit afterwards.
    """
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise HTTPException(404, "project not found")
    if not project_authority_writable(project):
        raise HTTPException(409, "project authority is read-only on this memory node")
    return project


def require_member_session(principal: TeamPrincipal, member_id: str | None) -> None:
    """Prevent a remote token from attributing writes to another member's session."""
    if principal.trusted_local:
        return
    if principal.anonymous or member_id != principal.member_id:
        raise HTTPException(404, "session not found")


def _auth_required() -> bool:
    return os.getenv("DDUO_AUTH_REQUIRED", "").strip().lower() in _TRUE


def _trusted_local_request(request: Request) -> bool:
    if _auth_required() or request.client is None:
        return False
    try:
        address = ipaddress.ip_address(request.client.host)
    except ValueError:
        return request.client.host in {"localhost", "testclient"}
    # The web container reaches the API over a private Compose network. Remote
    # deployments must set DDUO_AUTH_REQUIRED=true, disabling this compatibility path.
    return address.is_loopback or address.is_private


def _exempt_request(request: Request) -> bool:
    return request.url.path == "/health" or (
        request.method == "POST" and request.url.path.endswith(AUTH_EXEMPT_SUFFIXES)
    )


def _authority_bootstrap_request(request: Request) -> bool:
    if request.method != "POST" or not request.url.path.endswith(AUTHORITY_SECRET_SUFFIXES):
        return False
    expected = os.getenv("DDUO_NODE_AUTHORITY_SECRET", "")
    supplied = request.headers.get("X-DDUO-Authority", "")
    return bool(expected and supplied) and secrets.compare_digest(expected, supplied)


def _stateful_project_request(request: Request) -> bool:
    """Identify durable writes that happen behind an otherwise safe HTTP verb."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return True
    return request.method in {"GET", "HEAD"} and request.url.path.endswith(
        STATEFUL_READ_SUFFIXES
    )


async def _require_browser_csrf(
    db: AsyncSession,
    principal: TeamPrincipal,
    request: Request,
) -> None:
    """Protect host-wide cookies from another project served on a different port.

    Browser cookie scope ignores ports and SameSite treats two HTTPS ports on the
    same IP as the same site. A second project origin can therefore make the
    browser attach this project's HttpOnly cookie. The independent token lives
    in browser storage, which is isolated by origin, including the port.
    """
    if not _stateful_project_request(request):
        return
    if principal.browser_session_id is None:
        raise HTTPException(403, "browser CSRF protection is unavailable")
    browser_session = await db.get(BrowserAuthCredential, principal.browser_session_id)
    supplied = request.headers.get(CSRF_HEADER, "").strip()
    expected = browser_session.csrf_secret_hash if browser_session is not None else None
    if (
        not supplied
        or not expected
        or not secrets.compare_digest(hash_secret(supplied), expected)
    ):
        raise HTTPException(403, "browser CSRF token required")


async def _bearer_principal(db: AsyncSession, supplied: str) -> TeamPrincipal | None:
    token_hash = hash_secret(supplied)
    row = (
        await db.execute(
            select(TeamAccessToken, TeamMember)
            .join(TeamMember, TeamMember.id == TeamAccessToken.member_id)
            .where(TeamAccessToken.token_hash == token_hash)
        )
    ).first()
    if row is None:
        return None
    token, member = row
    if (
        token.revoked_at is not None
        or member.status != "active"
        or token.project_id != member.project_id
    ):
        return None
    return TeamPrincipal(
        project_id=member.project_id,
        member_id=member.id,
        access_token_id=token.id,
        browser_session_id=None,
        display_name=member.display_name,
        capability=member.capability,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _persist_access_token_usage(
    db: AsyncSession,
    token_id: str,
    used_at: datetime,
) -> None:
    """Commit usage telemetry independently from the route transaction."""
    bind = getattr(db, "bind", None)
    if bind is None:
        return
    factory = async_sessionmaker(bind, expire_on_commit=False)
    async with factory() as telemetry_db:
        await telemetry_db.execute(
            update(TeamAccessToken)
            .where(
                TeamAccessToken.id == token_id,
                TeamAccessToken.revoked_at.is_(None),
                TeamAccessToken.project_id.in_(
                    select(Project.id).where(project_authority_predicate())
                ),
                or_(
                    TeamAccessToken.last_used_at.is_(None),
                    TeamAccessToken.last_used_at
                    < used_at - TOKEN_USAGE_TOUCH_INTERVAL,
                ),
            )
            .values(last_used_at=used_at)
        )
        await telemetry_db.commit()


async def _touch_access_token_usage(
    db: AsyncSession,
    token_id: str | None,
) -> None:
    """Best-effort, rate-limited access telemetry for bearer and browser auth."""
    if token_id is None:
        return
    try:
        await _persist_access_token_usage(db, token_id, utcnow())
    except Exception:
        # Authentication already succeeded. Telemetry must never make the
        # request unavailable or roll back work performed by its route.
        return


async def _browser_principal(
    db: AsyncSession, project_id: str, supplied: str
) -> TeamPrincipal | None:
    row = (
        await db.execute(
            select(BrowserAuthCredential, TeamAccessToken, TeamMember)
            .join(TeamAccessToken, TeamAccessToken.id == BrowserAuthCredential.access_token_id)
            .join(TeamMember, TeamMember.id == BrowserAuthCredential.member_id)
            .where(
                BrowserAuthCredential.project_id == project_id,
                BrowserAuthCredential.kind == "session",
                BrowserAuthCredential.secret_hash == hash_secret(supplied),
            )
        )
    ).first()
    if row is None:
        return None
    browser_session, token, member = row
    if (
        browser_session.revoked_at is not None
        or browser_session.consumed_at is not None
        or _aware(browser_session.expires_at) <= utcnow()
        or token.revoked_at is not None
        or member.status != "active"
        or browser_session.project_id != token.project_id
        or token.project_id != member.project_id
    ):
        return None
    return TeamPrincipal(
        project_id=project_id,
        member_id=member.id,
        access_token_id=token.id,
        browser_session_id=browser_session.id,
        display_name=member.display_name,
        capability=member.capability,
    )


async def authenticate_team_request(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> AsyncIterator[TeamPrincipal]:
    """Authenticate once for every API route and expose a request-local actor."""
    enforce_client_compatibility(request, response)
    if _authority_bootstrap_request(request):
        principal = TeamPrincipal(
            project_id=request.path_params.get("project_id"),
            member_id=None,
            access_token_id=None,
            browser_session_id=None,
            display_name="Node authority",
            capability=INFRASTRUCTURE_MANAGER,
            trusted_local=True,
        )
    elif _exempt_request(request):
        principal = TeamPrincipal(
            project_id=request.path_params.get("project_id"),
            member_id=None,
            access_token_id=None,
            browser_session_id=None,
            display_name="Anonymous invitation",
            capability=PROJECT_MEMBER,
            anonymous=True,
        )
    else:
        browser_cookie_authenticated = False
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ").strip()
            principal = await _bearer_principal(db, supplied) if supplied else None
            if principal is None:
                raise HTTPException(401, "invalid or revoked project token")
        elif (path_project_id := request.path_params.get("project_id")) and (
            supplied_cookie := request.cookies.get(browser_cookie_name(str(path_project_id)))
        ):
            principal = await _browser_principal(db, str(path_project_id), supplied_cookie)
            if principal is None:
                raise HTTPException(401, "invalid or expired browser session")
            browser_cookie_authenticated = True
        elif _trusted_local_request(request):
            principal = TeamPrincipal(
                project_id=request.path_params.get("project_id"),
                member_id=None,
                access_token_id=None,
                browser_session_id=None,
                display_name="Local owner",
                capability=INFRASTRUCTURE_MANAGER,
                trusted_local=True,
            )
        else:
            raise HTTPException(401, "project bearer token required")

        path_project_id = request.path_params.get("project_id")
        if path_project_id:
            require_project(principal, str(path_project_id))
        elif (
            request.method == "POST"
            and request.url.path == "/projects"
            and not principal.trusted_local
        ):
            raise HTTPException(403, "remote project tokens cannot create projects")
        if browser_cookie_authenticated:
            await _require_browser_csrf(db, principal, request)

    path_project_id = request.path_params.get("project_id")
    project: Project | None = None
    stateful = bool(path_project_id) and _stateful_project_request(request)
    if path_project_id:
        statement = select(Project).where(Project.id == str(path_project_id))
        if stateful:
            statement = statement.with_for_update()
        project = await db.scalar(statement)

    if path_project_id and stateful:
        # Serialize the transfer boundary with project-scoped mutations. Routes
        # may commit early, so the transfer command additionally drains the API
        # before its final archive; this lock closes the normal request race.
        if project is not None and not project_authority_writable(project):
            path = request.url.path
            authority_control = path.endswith(
                (
                    "/authority/status",
                    "/authority/prepare",
                    "/authority/activate",
                    "/authority/complete",
                    "/authority/recover",
                    "/authority/cancel",
                    "/authority/finalize",
                )
            )
            pending_control = (
                authority_control
                or path.endswith("/backups")
                or path.endswith("/backups/register-restore")
                or path.endswith(
                    ("/auth/browser-ticket", "/auth/browser-session", "/auth/logout")
                )
            )
            if not pending_control:
                raise HTTPException(
                    409,
                    "project authority is read-only on this memory node",
                )

    # Frozen nodes keep canonical project/memory state immutable.  The narrow
    # control-plane allowlist above may still append final-backup provenance or
    # create/revoke an ephemeral browser session so an authorized operator can
    # inspect a clone.  Token-usage telemetry stays fully frozen: the isolated
    # UPDATE repeats the authority predicate so a concurrent transition cannot
    # race that best-effort timestamp.
    if project is None or project_authority_writable(project):
        await _touch_access_token_usage(db, principal.access_token_id)

    context_token = _request_principal.set(principal)
    request.state.team_principal = principal
    try:
        yield principal
    finally:
        _request_principal.reset(context_token)


def principal_from_request(request: Request) -> TeamPrincipal:
    principal = getattr(request.state, "team_principal", None)
    if not isinstance(principal, TeamPrincipal):
        raise HTTPException(401, "project authentication unavailable")
    return principal
