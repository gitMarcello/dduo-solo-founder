from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from conftest import project_payload
from dduo_solo_founder import __version__
from dduo_solo_founder import main as main_module
from dduo_solo_founder import team as team_auth
from dduo_solo_founder.authority_receipts import AuthorityReceipt, issue_authority_receipt
from dduo_solo_founder.db import get_session
from dduo_solo_founder.invitations import decode_invite_payload, invitation_setup_prompt
from dduo_solo_founder.main import app, enqueue_task_reindex_events
from dduo_solo_founder.models import (
    Activity,
    BackupRecord,
    BrowserAuthCredential,
    ObservabilityEvent,
    OperationalManual,
    OperationalManualRevision,
    OutboxEvent,
    Project,
    RawEvent,
    Session,
    Task,
    TaskRevision,
    TeamAccessToken,
    TeamInvitation,
    TeamMember,
    Turn,
)
from dduo_solo_founder.team import browser_cookie_name, hash_secret
from dduo_solo_founder.team_manual import (
    MANUAL_DRAFT_MAX_CHARACTERS,
    MANUAL_SOFT_WARNING_CHARACTERS,
    compact_manual_draft,
    deterministic_manual_draft,
    manual_warnings,
    serialize_manual,
)
from dduo_solo_founder.sleep_engine import (
    SleepGeneration,
    SleepGenerationError,
    SleepProviderError,
)


pytestmark = pytest.mark.asyncio


def _request(
    path: str = "/projects/project-1",
    *,
    method: str = "GET",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = ("testclient", 50_000),
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers or [],
            "client": client,
            "server": ("memory.test", 443),
            "root_path": "",
        }
    )


def _device_token(marker: str) -> str:
    return f"dduo_dev_{marker * 43}"


async def test_manual_compaction_falls_back_only_when_bridge_is_unavailable():
    project = Project(
        id="manual-project",
        name="Manual",
        root_path="/tmp/manual",
        cause="Ship safely",
    )

    class Offline:
        async def generate(self, **_kwargs):
            raise SleepProviderError("bridge_unavailable", "offline")

    fallback = await compact_manual_draft(project, None, provider_factory=lambda: Offline())
    assert fallback["source"] == "deterministic_fallback"
    assert fallback["persisted"] is False

    class Invalid:
        async def generate(self, **_kwargs):
            raise SleepProviderError("invalid_model_output", "invalid")

    with pytest.raises(SleepProviderError, match="invalid"):
        await compact_manual_draft(project, None, provider_factory=lambda: Invalid())

    class Observed:
        async def generate_observed(self, **_kwargs):
            return SleepGeneration(
                output={"content": "# Manuale compatto"},
                provider="codex",
                model="gpt-5.6-terra",
                duration_ms=120,
                measurement_source="provider_reported",
                input_tokens=90,
                cached_input_tokens=40,
                cache_write_input_tokens=None,
                output_tokens=12,
                reasoning_tokens=3,
                reported_total_tokens=105,
            )

    observed = await compact_manual_draft(
        project,
        None,
        provider_factory=lambda: Observed(),
    )
    assert observed["draft"] == "# Manuale compatto"
    assert observed["_telemetry"] == {
        "status": "success",
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "duration_ms": 120,
        "measurement_source": "provider_reported",
        "input_tokens": 90,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": None,
        "output_tokens": 12,
        "reasoning_tokens": 3,
        "reported_total_tokens": 105,
        "client_cost_usd": None,
        "cost_source": None,
    }


async def test_authority_concurrency_helpers_fail_closed_at_every_identity_boundary(
    monkeypatch,
):
    local = team_auth.TeamPrincipal(
        project_id="project-one",
        member_id=None,
        access_token_id=None,
        browser_session_id=None,
        display_name="Local owner",
        capability=team_auth.INFRASTRUCTURE_MANAGER,
        trusted_local=True,
    )
    remote = team_auth.TeamPrincipal(
        project_id="project-one",
        member_id="member-one",
        access_token_id="token-one",
        browser_session_id=None,
        display_name="Member",
        capability=team_auth.PROJECT_MEMBER,
    )
    main_module.require_remote_expected_version(local, None)
    main_module.require_remote_expected_version(remote, 2)
    with pytest.raises(HTTPException) as missing_version:
        main_module.require_remote_expected_version(remote, None)
    assert missing_version.value.status_code == 428

    assert main_module.task_search_statuses(SimpleNamespace(status="done", scope="all")) == ["done"]
    assert main_module.task_search_statuses(SimpleNamespace(status=None, scope="active")) == list(
        main_module.ACTIVE_TASK_STATUSES
    )
    assert main_module.task_search_statuses(
        SimpleNamespace(status=None, scope="completed")
    ) == list(main_module.COMPLETED_TASK_STATUSES)
    assert main_module.task_search_statuses(SimpleNamespace(status=None, scope="all")) is None

    monkeypatch.delenv("DDUO_NODE_AUTHORITY_SECRET", raising=False)
    with pytest.raises(HTTPException) as missing_secret:
        main_module._authority_secret()
    assert missing_secret.value.status_code == 503
    monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", "authority-secret")
    assert main_module._authority_secret() == "authority-secret"

    project = Project(
        id="project-one",
        name="Authority",
        root_path="/tmp/authority",
        authority_generation=3,
        authority_state="transfer_pending",
    )
    with pytest.raises(HTTPException) as missing_proof:
        main_module._project_transfer_receipt(project, "destination_ready")
    assert missing_proof.value.status_code == 409
    project.authority_node_id = "node-old"
    project.authority_target_node_id = "node-new"
    project.authority_transfer_nonce = "a" * 64
    expected = AuthorityReceipt(
        kind="destination_ready",
        project_id=project.id,
        source_node_id="node-old",
        target_node_id="node-new",
        source_generation=3,
        nonce="a" * 64,
    )
    token = issue_authority_receipt("authority-secret", expected)
    assert (
        main_module._verified_transfer_receipt(project, token, expected_kind="destination_ready")
        == expected
    )

    monkeypatch.delenv("DDUO_NODE_ID", raising=False)
    with pytest.raises(HTTPException) as missing_node:
        main_module._require_current_authority_node("node-old")
    assert missing_node.value.status_code == 503
    monkeypatch.setenv("DDUO_NODE_ID", "node-other")
    with pytest.raises(HTTPException) as wrong_node:
        main_module._require_current_authority_node("node-old")
    assert wrong_node.value.status_code == 409
    monkeypatch.setenv("DDUO_NODE_ID", "node-old")
    main_module._require_current_authority_node("node-old")

    class TurnDB:
        def __init__(self, values):
            self.values = iter(values)

        async def scalar(self, _statement):
            return next(self.values)

    async def missing_project(_db, _project_id):
        raise HTTPException(404, "project not found")

    monkeypatch.setattr(main_module, "lock_writable_project", missing_project)
    with pytest.raises(HTTPException) as hidden_turn:
        await main_module.lock_writable_turn(TurnDB(["project-one"]), "turn-one", local)
    assert hidden_turn.value.status_code == 404

    async def writable(_db, _project_id):
        return project

    monkeypatch.setattr(main_module, "lock_writable_project", writable)
    with pytest.raises(HTTPException) as vanished_turn:
        await main_module.lock_writable_turn(TurnDB(["project-one", None]), "turn-one", local)
    assert vanished_turn.value.status_code == 404
    assert await main_module.plan_work_item_ids(SimpleNamespace(), []) == {}


async def test_every_authority_route_hides_an_unknown_project(
    api_client,
    monkeypatch,
):
    client, _ = api_client
    monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", "authority-secret")
    monkeypatch.setenv("DDUO_NODE_ID", "node-one")
    authority_headers = {"X-DDUO-Authority": "authority-secret"}
    missing = str(uuid.uuid4())
    receipt = "dduo_authority_v1." + "a" * 43 + "." + "b" * 43
    requests = [
        ("GET", f"/projects/{missing}/authority", {}, None),
        (
            "POST",
            f"/projects/{missing}/authority/initialize",
            authority_headers,
            {"node_id": "node-one", "expected_generation": 1},
        ),
        (
            "POST",
            f"/projects/{missing}/authority/prepare?expected_generation=1&target_node_id=node-two",
            {},
            None,
        ),
        (
            "POST",
            f"/projects/{missing}/authority/cancel?expected_generation=1",
            {},
            None,
        ),
        (
            "POST",
            f"/projects/{missing}/authority/activate",
            authority_headers,
            {"node_id": "node-one", "expected_generation": 1},
        ),
        (
            "POST",
            f"/projects/{missing}/authority/complete",
            authority_headers,
            {
                "node_id": "node-one",
                "expected_generation": 1,
                "finalization_receipt": receipt,
            },
        ),
        (
            "POST",
            f"/projects/{missing}/authority/recover",
            authority_headers,
            {
                "node_id": "node-one",
                "expected_generation": 1,
                "old_node_unreachable": True,
            },
        ),
        (
            "POST",
            f"/projects/{missing}/authority/finalize?expected_generation=1",
            {},
            {"activation_receipt": receipt},
        ),
    ]
    for method, path, headers, payload in requests:
        response = await client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 404, (path, response.text)


async def test_manual_compaction_target_warns_before_it_can_dominate_the_founder_brief():
    assert MANUAL_DRAFT_MAX_CHARACTERS == 4_000
    assert MANUAL_SOFT_WARNING_CHARACTERS == 4_000
    assert manual_warnings("x" * MANUAL_SOFT_WARNING_CHARACTERS) == []
    assert manual_warnings("x" * (MANUAL_SOFT_WARNING_CHARACTERS + 1)) == [
        "manual_above_soft_limit"
    ]

    project = Project(
        id="manual-budget-project",
        name="Manual budget",
        root_path="/tmp/manual-budget",
    )

    class Offline:
        async def generate(self, **_kwargs):
            raise SleepProviderError("bridge_unavailable", "offline")

    draft = await compact_manual_draft(
        project,
        OperationalManual(
            project_id=project.id,
            content="A" * (MANUAL_DRAFT_MAX_CHARACTERS + 500),
            version=1,
        ),
        provider_factory=lambda: Offline(),
    )
    assert len(draft["draft"]) <= MANUAL_DRAFT_MAX_CHARACTERS


async def test_manual_helpers_preserve_profile_sections_and_serialization():
    project = Project(
        id="manual-profile-project",
        name="Manual profile",
        root_path="/tmp/manual-profile",
        cause="  Ship safely  ",
        objectives=["First", "", "Second"],
        principles=["KISS", "  "],
        context="  Deploy from develop  ",
    )
    draft = deterministic_manual_draft(project, "")
    assert draft == (
        "# Manual profile\n\n## Scopo\nShip safely\n\n## Obiettivi\n- First\n- Second"
        "\n\n## Principi\n- KISS\n\n## Contesto operativo\nDeploy from develop"
    )
    minimal = Project(
        id="manual-minimal-project",
        name="Minimal manual",
        root_path="/tmp/manual-minimal",
    )
    assert deterministic_manual_draft(minimal, "") == "# Minimal manual"
    assert manual_warnings("   ") == ["manual_empty"]
    assert serialize_manual(None) == {
        "content": "",
        "version": 0,
        "updated_by_member_id": None,
        "updated_at": None,
        "characters": 0,
        "soft_limit_characters": MANUAL_SOFT_WARNING_CHARACTERS,
        "hard_limit_characters": 100_000,
        "warnings": ["manual_empty"],
    }

    manual = OperationalManual(
        project_id=project.id,
        content="# Rules",
        version=3,
        updated_by_member_id="member-1",
    )
    serialized = serialize_manual(manual)
    assert serialized["content"] == "# Rules"
    assert serialized["version"] == 3
    assert serialized["updated_by_member_id"] == "member-1"
    assert serialized["characters"] == 7
    assert serialized["warnings"] == []


async def test_manual_compaction_supports_legacy_provider_and_truncates_draft():
    project = Project(
        id="manual-legacy-project",
        name="Manual legacy",
        root_path="/tmp/manual-legacy",
    )

    class LegacyProvider:
        async def generate(self, **_kwargs):
            return {"content": "Rule. " * 1_000}

    result = await compact_manual_draft(
        project,
        None,
        provider_factory=lambda: LegacyProvider(),
    )
    assert result["source"] == "provider"
    assert result["characters"] <= MANUAL_DRAFT_MAX_CHARACTERS
    assert result["draft"].endswith("…")
    assert "draft_truncated" in result["warnings"]
    assert "_telemetry" not in result

    class EmptyLegacyProvider:
        async def generate(self, **_kwargs):
            return {"content": ""}

    with pytest.raises(SleepProviderError, match="empty draft"):
        await compact_manual_draft(
            project,
            None,
            provider_factory=lambda: EmptyLegacyProvider(),
        )


async def test_manual_compaction_preserves_failed_observed_generation():
    project = Project(
        id="manual-empty-project",
        name="Manual empty",
        root_path="/tmp/manual-empty",
    )
    generation = SleepGeneration(
        output={},
        provider="codex",
        model="gpt-5.6-terra",
        duration_ms=25,
        measurement_source="provider_reported",
        input_tokens=4,
        cached_input_tokens=0,
        cache_write_input_tokens=None,
        output_tokens=0,
        reasoning_tokens=0,
        reported_total_tokens=4,
    )

    class EmptyObservedProvider:
        async def generate_observed(self, **_kwargs):
            return generation

    with pytest.raises(SleepGenerationError) as raised:
        await compact_manual_draft(
            project,
            None,
            provider_factory=lambda: EmptyObservedProvider(),
        )
    assert raised.value.generation is generation
    assert raised.value.kind == "invalid_model_output"


async def test_manual_fallback_truncates_unbroken_content_at_hard_boundary():
    project = Project(
        id="manual-boundary-project",
        name="Manual boundary",
        root_path="/tmp/manual-boundary",
    )

    class Offline:
        async def generate(self, **_kwargs):
            raise SleepProviderError("dependency_unavailable", "offline")

    result = await compact_manual_draft(
        project,
        OperationalManual(
            project_id=project.id,
            content="X" * (MANUAL_DRAFT_MAX_CHARACTERS + 50),
            version=2,
        ),
        provider_factory=lambda: Offline(),
    )
    assert len(result["draft"]) == MANUAL_DRAFT_MAX_CHARACTERS
    assert result["draft"].endswith("…")
    assert result["based_on_version"] == 2
    assert result["source"] == "deterministic_fallback"


async def _seed_team(db_factory):
    project_id = str(uuid.uuid4())
    other_project_id = str(uuid.uuid4())
    manager_token = _device_token("m")
    async with db_factory() as db:
        db.add_all(
            [
                Project(id=project_id, name="Shared", root_path="/tmp/shared"),
                Project(id=other_project_id, name="Other", root_path="/tmp/other"),
            ]
        )
        manager = TeamMember(
            project_id=project_id,
            display_name="Manager",
            capability="infrastructure_manager",
        )
        db.add(manager)
        await db.flush()
        access = TeamAccessToken(
            project_id=project_id,
            member_id=manager.id,
            token_hash=hash_secret(manager_token),
            device_id="manager-device",
        )
        db.add(access)
        await db.commit()
        return project_id, other_project_id, manager, access, manager_token


async def _remote_client(db_factory, monkeypatch, *, base_url="https://memory.test"):
    async def override_session():
        async with db_factory() as session:
            yield session

    monkeypatch.setenv("DDUO_AUTH_REQUIRED", "true")
    app.dependency_overrides[get_session] = override_session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.20", 43110)),
        base_url=base_url,
    )


async def test_team_security_helpers_reject_invalid_and_unbound_state(db_factory, monkeypatch):
    assert team_auth._release_order("not-a-release") is None
    incompatible = _request(
        headers=[
            (b"x-dduo-client-component", b"hook"),
            (b"x-dduo-client-protocol", b"not-an-integer"),
        ]
    )
    with pytest.raises(HTTPException) as protocol_error:
        team_auth.enforce_client_compatibility(incompatible, Response())
    assert protocol_error.value.status_code == 426

    anonymous = team_auth.TeamPrincipal(
        project_id=None,
        member_id=None,
        access_token_id=None,
        browser_session_id=None,
        display_name="Anonymous",
        capability=team_auth.PROJECT_MEMBER,
        anonymous=True,
    )
    assert team_auth.serialize_current_member(anonymous) is None
    with pytest.raises(HTTPException) as missing_principal:
        team_auth.principal_from_request(_request())
    assert missing_principal.value.status_code == 401

    monkeypatch.delenv("DDUO_NODE_AUTHORITY_SECRET", raising=False)
    with pytest.raises(HTTPException) as missing_authority:
        team_auth.require_node_authority(_request())
    assert missing_authority.value.status_code == 403

    trusted = team_auth.TeamPrincipal(
        project_id=None,
        member_id=None,
        access_token_id=None,
        browser_session_id=None,
        display_name="Local owner",
        capability=team_auth.INFRASTRUCTURE_MANAGER,
        trusted_local=True,
    )
    team_auth.require_project(trusted, "any-project")
    with pytest.raises(HTTPException) as wrong_project:
        team_auth.require_project(anonymous, "another-project")
    assert wrong_project.value.status_code == 404

    monkeypatch.setenv("DDUO_NODE_ID", "node-current")
    assert not team_auth.project_authority_writable(
        Project(
            id="project-pending",
            name="Pending",
            root_path="/tmp/pending",
            authority_state="transfer_pending",
            authority_node_id="node-current",
        )
    )
    assert not team_auth.project_authority_writable(
        Project(
            id="project-elsewhere",
            name="Elsewhere",
            root_path="/tmp/elsewhere",
            authority_state="active",
            authority_node_id="node-other",
        )
    )

    monkeypatch.delenv("DDUO_AUTH_REQUIRED", raising=False)
    assert team_auth._trusted_local_request(_request(client=("localhost", 1234)))
    assert not team_auth._trusted_local_request(_request(client=("invalid host", 1234)))

    async with db_factory() as db:
        assert await team_auth._bearer_principal(db, "missing-token") is None
        assert await team_auth._browser_principal(db, "missing-project", "missing-cookie") is None
        with pytest.raises(HTTPException) as missing_project:
            await team_auth.lock_writable_project(db, "missing-project")
        assert missing_project.value.status_code == 404

        frozen = Project(
            id=str(uuid.uuid4()),
            name="Frozen",
            root_path="/tmp/frozen",
            authority_state="transferred",
            authority_node_id="node-other",
        )
        db.add(frozen)
        await db.commit()
        with pytest.raises(HTTPException) as frozen_project:
            await team_auth.lock_writable_project(db, frozen.id)
        assert frozen_project.value.status_code == 409

    unbound = AsyncSession()
    try:
        await team_auth._persist_access_token_usage(unbound, "missing-token", team_auth.utcnow())
    finally:
        await unbound.close()


async def test_remote_project_token_cannot_create_another_project(db_factory, monkeypatch):
    project_id, other_project_id, _, _, manager_token = await _seed_team(db_factory)
    async with await _remote_client(db_factory, monkeypatch) as client:
        response = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {manager_token}"},
            json=project_payload(name="Forbidden remote project"),
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "remote project tokens cannot create projects"

    async with db_factory() as db:
        projects = (await db.scalars(select(Project))).all()
    assert {project.id for project in projects} == {project_id, other_project_id}


async def test_access_token_last_used_is_best_effort_and_rate_limited_for_both_auth_flows(
    db_factory, monkeypatch
):
    project_id, _, _, access, manager_token = await _seed_team(db_factory)
    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {manager_token}"}
    try:
        first_request = await client.get(f"/projects/{project_id}/team", headers=headers)
        assert first_request.status_code == 200
        async with db_factory() as db:
            first_used_at = (await db.get(TeamAccessToken, access.id)).last_used_at
        assert first_used_at is not None

        second_request = await client.get(f"/projects/{project_id}/team", headers=headers)
        assert second_request.status_code == 200
        async with db_factory() as db:
            assert (await db.get(TeamAccessToken, access.id)).last_used_at == first_used_at

        ticket_response = await client.post(
            f"/projects/{project_id}/auth/browser-ticket", headers=headers
        )
        exchange = await client.post(
            f"/projects/{project_id}/auth/browser-session",
            json={"ticket": ticket_response.json()["ticket"]},
        )
        assert exchange.status_code == 200
        old_usage = team_auth.utcnow() - timedelta(minutes=16)
        async with db_factory() as db:
            token = await db.get(TeamAccessToken, access.id)
            token.last_used_at = old_usage
            await db.commit()
        browser_request = await client.get(f"/projects/{project_id}/team")
        assert browser_request.status_code == 200
        async with db_factory() as db:
            browser_used_at = (await db.get(TeamAccessToken, access.id)).last_used_at
        assert browser_used_at is not None
        assert team_auth._aware(browser_used_at) > old_usage

        async def fail_telemetry(*_args, **_kwargs):
            raise RuntimeError("telemetry database unavailable")

        monkeypatch.setattr(team_auth, "_persist_access_token_usage", fail_telemetry)
        async with db_factory() as db:
            token = await db.get(TeamAccessToken, access.id)
            token.last_used_at = old_usage
            await db.commit()
        valid_request = await client.get(f"/projects/{project_id}/team", headers=headers)
        assert valid_request.status_code == 200
        async with db_factory() as db:
            stored_usage = (await db.get(TeamAccessToken, access.id)).last_used_at
        assert stored_usage is not None
        assert team_auth._aware(stored_usage) == old_usage
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_manual_compaction_failure_records_usage_and_maps_provider_status(
    db_factory, monkeypatch
):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    generation = SleepGeneration(
        output={},
        provider="codex",
        model="gpt-5.6-terra",
        duration_ms=85,
        measurement_source="provider_reported",
        input_tokens=30,
        cached_input_tokens=10,
        cache_write_input_tokens=None,
        output_tokens=2,
        reasoning_tokens=1,
        reported_total_tokens=33,
    )

    async def invalid_output(*_args, **_kwargs):
        raise SleepGenerationError(
            "invalid compacted manual",
            generation,
            kind="invalid_model_output",
        )

    monkeypatch.setattr("dduo_solo_founder.main.compact_manual_draft", invalid_output)
    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {manager_token}"}
    try:
        failed = await client.post(
            f"/projects/{project_id}/team/manual/compact-draft",
            headers=headers,
            json={"expected_version": 0},
        )
        assert failed.status_code == 502
        assert "invalid compacted manual" in failed.json()["detail"]

        async with db_factory() as db:
            event = await db.scalar(
                select(ObservabilityEvent).where(
                    ObservabilityEvent.project_id == project_id,
                    ObservabilityEvent.operation == "sleep.operational_manual_compaction",
                )
            )
        assert event is not None
        assert event.status == "error"
        assert event.input_tokens == 30
        assert event.reported_total_tokens == 33

        async def auth_required(*_args, **_kwargs):
            raise SleepProviderError("auth_required", "connect Codex")

        monkeypatch.setattr("dduo_solo_founder.main.compact_manual_draft", auth_required)
        unavailable = await client.post(
            f"/projects/{project_id}/team/manual/compact-draft",
            headers=headers,
            json={"expected_version": 0},
        )
        assert unavailable.status_code == 503
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_browser_logout_revokes_session_and_deletes_cookie(db_factory, monkeypatch):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    client = await _remote_client(db_factory, monkeypatch)
    try:
        ticket_response = await client.post(
            f"/projects/{project_id}/auth/browser-ticket",
            headers={"Authorization": f"Bearer {manager_token}"},
        )
        ticket = ticket_response.json()["ticket"]
        authenticated = await client.post(
            f"/projects/{project_id}/auth/browser-session",
            json={"ticket": ticket},
        )
        assert authenticated.status_code == 200
        csrf_token = authenticated.json()["csrf_token"]

        forged = await client.post(
            f"/projects/{project_id}/auth/logout",
            headers={"Origin": "https://memory.test:24443"},
        )
        assert forged.status_code == 403
        assert (await client.get(f"/projects/{project_id}/team")).status_code == 200

        logged_out = await client.post(
            f"/projects/{project_id}/auth/logout",
            headers={"X-DDUO-CSRF": csrf_token},
        )
        assert logged_out.status_code == 200
        assert logged_out.json()["logged_out"] is True
        assert "Max-Age=0" in logged_out.headers["set-cookie"]
        assert (await client.get(f"/projects/{project_id}/team")).status_code == 401

        async with db_factory() as db:
            browser_session = await db.scalar(
                select(BrowserAuthCredential).where(
                    BrowserAuthCredential.project_id == project_id,
                    BrowserAuthCredential.kind == "session",
                )
            )
        assert browser_session is not None
        assert browser_session.revoked_at is not None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_frozen_source_blocks_stateful_get_without_touching_access_token(
    db_factory, monkeypatch
):
    project_id, _, _, access, manager_token = await _seed_team(db_factory)
    frozen_usage = team_auth.utcnow() - timedelta(minutes=20)
    async with db_factory() as db:
        project = await db.get(Project, project_id)
        project.authority_state = "transfer_pending"
        project.authority_target_node_id = "node-new"
        token = await db.get(TeamAccessToken, access.id)
        token.last_used_at = frozen_usage
        await db.commit()
    # The SQL predicate is a second fence against a concurrent freeze even if
    # a caller races past the request-level authority check.
    async with db_factory() as db:
        await team_auth._touch_access_token_usage(db, access.id)
    monkeypatch.setattr(
        "dduo_solo_founder.main.embedding_service",
        lambda: (_ for _ in ()).throw(AssertionError("embedding search crossed the fence")),
    )
    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {manager_token}"}
    try:
        blocked = await client.get(
            f"/projects/{project_id}/memories/search",
            headers=headers,
            params={"q": "project context"},
        )
        assert blocked.status_code == 409
        read_only = await client.get(f"/projects/{project_id}/team", headers=headers)
        assert read_only.status_code == 200
        async with db_factory() as db:
            token = await db.get(TeamAccessToken, access.id)
            observations = list(
                (
                    await db.scalars(
                        select(ObservabilityEvent).where(
                            ObservabilityEvent.project_id == project_id
                        )
                    )
                ).all()
            )
        assert token.last_used_at is not None
        assert team_auth._aware(token.last_used_at) == frozen_usage
        assert observations == []
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_remote_bootstrap_requires_node_authority_secret(db_factory, monkeypatch):
    project_id = str(uuid.uuid4())
    async with db_factory() as db:
        db.add(Project(id=project_id, name="Bootstrap", root_path="/tmp/bootstrap"))
        await db.commit()
    monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", "node-authority-secret")
    client = await _remote_client(db_factory, monkeypatch)
    payload = {
        "display_name": "Owner",
        "device_id": "vps",
        "device_token": _device_token("o"),
    }
    try:
        assert (
            await client.post(f"/projects/{project_id}/team/bootstrap", json=payload)
        ).status_code == 401
        response = await client.post(
            f"/projects/{project_id}/team/bootstrap",
            json=payload,
            headers={"X-DDUO-Authority": "node-authority-secret"},
        )
        assert response.status_code == 200
        assert response.json()["current_member"]["capability"] == "infrastructure_manager"
        assert response.json()["idempotent"] is False
        assert "node-authority-secret" not in response.text
        replay = await client.post(
            f"/projects/{project_id}/team/bootstrap",
            json=payload,
            headers={"X-DDUO-Authority": "node-authority-secret"},
        )
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        rebound = await client.post(
            f"/projects/{project_id}/team/bootstrap",
            json={**payload, "device_id": "replacement-vps"},
            headers={"X-DDUO-Authority": "node-authority-secret"},
        )
        assert rebound.status_code == 200
        assert rebound.json()["device_rebound"] is True
        async with db_factory() as db:
            members = list(
                (
                    await db.scalars(select(TeamMember).where(TeamMember.project_id == project_id))
                ).all()
            )
            tokens = list(
                (
                    await db.scalars(
                        select(TeamAccessToken).where(TeamAccessToken.project_id == project_id)
                    )
                ).all()
            )
        assert len(members) == 1
        assert len(tokens) == 1
        assert tokens[0].device_id == "replacement-vps"
        conflict = await client.post(
            f"/projects/{project_id}/team/bootstrap",
            json={**payload, "device_token": _device_token("x")},
            headers={"X-DDUO-Authority": "node-authority-secret"},
        )
        assert conflict.status_code == 409
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_authority_transfer_uses_two_phase_receipts_without_two_writers(
    db_factory, monkeypatch
):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", "node-authority-secret")
    monkeypatch.setenv("DDUO_NODE_ID", "node-old")
    client = await _remote_client(db_factory, monkeypatch)
    manager_headers = {"Authorization": f"Bearer {manager_token}"}
    authority_headers = {"X-DDUO-Authority": "node-authority-secret"}
    try:
        initialized = await client.post(
            f"/projects/{project_id}/authority/initialize",
            headers=authority_headers,
            json={"node_id": "node-old", "expected_generation": 1},
        )
        assert initialized.status_code == 200
        assert initialized.json()["node_id"] == "node-old"

        prepared = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers=manager_headers,
            params={"expected_generation": 1, "target_node_id": "node-new"},
        )
        assert prepared.status_code == 200
        assert prepared.json()["state"] == "transfer_pending"
        async with db_factory() as db:
            prepared_project = await db.get(Project, project_id)
            first_transfer_nonce = prepared_project.authority_transfer_nonce
            assert first_transfer_nonce and len(first_transfer_nonce) == 64
        assert (
            await client.post(
                f"/projects/{project_id}/tasks",
                headers=manager_headers,
                json={"title": "must not diverge"},
            )
        ).status_code == 409
        assert (
            await client.get(f"/projects/{project_id}/team", headers=manager_headers)
        ).status_code == 200

        wrong = await client.post(
            f"/projects/{project_id}/authority/activate",
            headers={"X-DDUO-Authority": "wrong"},
            json={"node_id": "node-new", "expected_generation": 1},
        )
        assert wrong.status_code == 401
        source_impersonation = await client.post(
            f"/projects/{project_id}/authority/activate",
            headers=authority_headers,
            json={"node_id": "node-new", "expected_generation": 1},
        )
        assert source_impersonation.status_code == 409
        assert "another memory node" in source_impersonation.json()["detail"]
        monkeypatch.setenv("DDUO_NODE_ID", "node-new")
        activated = await client.post(
            f"/projects/{project_id}/authority/activate",
            headers=authority_headers,
            json={"node_id": "node-new", "expected_generation": 1},
        )
        assert activated.status_code == 200
        assert activated.json()["state"] == "transfer_pending"
        assert activated.json()["writable"] is False
        abandoned_receipt = activated.json()["activation_receipt"]

        # A restored destination has the manager token and node secret, but it
        # is not the source authority and cannot mutate the source phase.
        destination_cancel = await client.post(
            f"/projects/{project_id}/authority/cancel",
            headers=manager_headers,
            params={"expected_generation": 1},
        )
        assert destination_cancel.status_code == 409
        destination_finalize = await client.post(
            f"/projects/{project_id}/authority/finalize",
            headers=manager_headers,
            params={"expected_generation": 1},
            json={"activation_receipt": abandoned_receipt},
        )
        assert destination_finalize.status_code == 409

        # Readiness never activates the clone: the source can still cancel,
        # and that old receipt cannot finalize a later transfer generation.
        monkeypatch.setenv("DDUO_NODE_ID", "node-old")
        cancelled = await client.post(
            f"/projects/{project_id}/authority/cancel",
            headers=manager_headers,
            params={"expected_generation": 1},
        )
        assert cancelled.status_code == 200
        reprepared = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers=manager_headers,
            params={"expected_generation": 1, "target_node_id": "node-new"},
        )
        assert reprepared.status_code == 200
        async with db_factory() as db:
            prepared_project = await db.get(Project, project_id)
            transfer_nonce = prepared_project.authority_transfer_nonce
            assert transfer_nonce and transfer_nonce != first_transfer_nonce
        stale = await client.post(
            f"/projects/{project_id}/authority/finalize",
            headers=manager_headers,
            params={"expected_generation": 1},
            json={"activation_receipt": abandoned_receipt},
        )
        assert stale.status_code == 409

        monkeypatch.setenv("DDUO_NODE_ID", "node-new")
        activated = await client.post(
            f"/projects/{project_id}/authority/activate",
            headers=authority_headers,
            json={"node_id": "node-new", "expected_generation": 1},
        )
        assert activated.status_code == 200
        activation_receipt = activated.json()["activation_receipt"]
        activation_parts = activation_receipt.split(".")
        activation_parts[-1] = ("A" if activation_parts[-1][0] != "A" else "B") + activation_parts[
            -1
        ][1:]

        monkeypatch.setenv("DDUO_NODE_ID", "node-old")
        tampered = await client.post(
            f"/projects/{project_id}/authority/finalize",
            headers=manager_headers,
            params={"expected_generation": 1},
            json={"activation_receipt": ".".join(activation_parts)},
        )
        assert tampered.status_code == 409
        finalized = await client.post(
            f"/projects/{project_id}/authority/finalize",
            headers=manager_headers,
            params={"expected_generation": 1},
            json={"activation_receipt": activation_receipt},
        )
        assert finalized.status_code == 200
        assert finalized.json()["state"] == "transferred"
        finalization_receipt = finalized.json()["finalization_receipt"]
        finalization_parts = finalization_receipt.split(".")
        finalization_parts[-1] = (
            "A" if finalization_parts[-1][0] != "A" else "B"
        ) + finalization_parts[-1][1:]
        cannot_cancel = await client.post(
            f"/projects/{project_id}/authority/cancel",
            headers=manager_headers,
            params={"expected_generation": 1},
        )
        assert cannot_cancel.status_code == 409

        # The source and destination are separate PostgreSQL volumes in reality.
        # Reconstruct the destination's frozen clone to exercise phase two here.
        async with db_factory() as db:
            destination = await db.get(Project, project_id)
            destination.authority_state = "transfer_pending"
            destination.authority_node_id = "node-old"
            destination.authority_target_node_id = "node-new"
            destination.authority_transfer_nonce = transfer_nonce
            destination.authority_generation = 1
            stale_scheduled = BackupRecord(
                project_id=project_id,
                trigger="automatic",
                status="scheduled",
            )
            stale_running = BackupRecord(
                project_id=project_id,
                trigger="manual",
                status="running",
            )
            db.add_all([stale_scheduled, stale_running])
            await db.commit()
            stale_backup_ids = {stale_scheduled.id, stale_running.id}

        monkeypatch.setenv("DDUO_NODE_ID", "node-new")
        invalid_completion = await client.post(
            f"/projects/{project_id}/authority/complete",
            headers=authority_headers,
            json={
                "node_id": "node-new",
                "expected_generation": 1,
                "finalization_receipt": ".".join(finalization_parts),
            },
        )
        assert invalid_completion.status_code == 409
        completed = await client.post(
            f"/projects/{project_id}/authority/complete",
            headers=authority_headers,
            json={
                "node_id": "node-new",
                "expected_generation": 1,
                "finalization_receipt": finalization_receipt,
            },
        )
        assert completed.status_code == 200
        assert completed.json()["state"] == "active"
        assert completed.json()["generation"] == 2
        replay = await client.post(
            f"/projects/{project_id}/authority/complete",
            headers=authority_headers,
            json={
                "node_id": "node-new",
                "expected_generation": 1,
                "finalization_receipt": finalization_receipt,
            },
        )
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        manager_rebound = await client.post(
            f"/projects/{project_id}/team/bootstrap",
            headers=authority_headers,
            json={
                "display_name": "Manager",
                "device_id": "node-new",
                "device_label": "replacement authority",
                "device_token": manager_token,
            },
        )
        assert manager_rebound.status_code == 200
        assert manager_rebound.json()["device_rebound"] is True
        async with db_factory() as db:
            assert (
                len(
                    list(
                        (
                            await db.scalars(
                                select(TeamMember).where(
                                    TeamMember.project_id == project_id,
                                    TeamMember.capability == "infrastructure_manager",
                                )
                            )
                        ).all()
                    )
                )
                == 1
            )
        assert (
            await client.post(
                f"/projects/{project_id}/tasks",
                headers=manager_headers,
                json={"title": "new authority writes"},
            )
        ).status_code == 200
        async with db_factory() as db:
            interrupted = list(
                (
                    await db.scalars(
                        select(BackupRecord).where(BackupRecord.id.in_(stale_backup_ids))
                    )
                ).all()
            )
            assert {record.status for record in interrupted} == {"failed"}
            assert all(record.completed_at is not None for record in interrupted)
            assert all("authority transition" in (record.error or "") for record in interrupted)

        monkeypatch.setattr(main_module, "_backup_available", lambda _: (True, ""))
        monkeypatch.setattr(main_module, "_backup_due", lambda _: True)
        async with db_factory() as db:
            scheduled = await main_module._schedule_due_backup(
                db,
                project_id,
                BackgroundTasks(),
            )
        assert scheduled["scheduled"] is True
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_transfer_prepare_rejects_an_in_flight_backup(db_factory, monkeypatch):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    monkeypatch.setenv("DDUO_NODE_ID", "node-old")
    async with db_factory() as db:
        project = await db.get(Project, project_id)
        project.authority_node_id = "node-old"
        db.add(
            BackupRecord(
                project_id=project_id,
                trigger="automatic",
                status="running",
            )
        )
        await db.commit()
    client = await _remote_client(db_factory, monkeypatch)
    try:
        response = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers={"Authorization": f"Bearer {manager_token}"},
            params={"expected_generation": 1, "target_node_id": "node-new"},
        )
        assert response.status_code == 409
        async with db_factory() as db:
            project = await db.get(Project, project_id)
            assert project.authority_state == "active"
            assert project.authority_target_node_id is None
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_transfer_prepare_retry_repairs_frozen_proof_and_rejects_new_target(
    db_factory, monkeypatch
):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    monkeypatch.setenv("DDUO_NODE_ID", "node-old")
    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {manager_token}"}
    try:
        first = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers=headers,
            params={"expected_generation": 1, "target_node_id": "node-new"},
        )
        assert first.status_code == 200
        assert first.json()["idempotent"] is False

        # Simulate a legacy/interrupted freeze that persisted the phase but not
        # the proof fields. Retrying the same command must repair it safely.
        async with db_factory() as db:
            project = await db.get(Project, project_id)
            project.authority_node_id = None
            project.authority_transfer_nonce = None
            await db.commit()

        replay = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers=headers,
            params={"expected_generation": 1, "target_node_id": "node-new"},
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent"] is True
        assert replay.json()["node_id"] == "node-old"
        async with db_factory() as db:
            repaired = await db.get(Project, project_id)
            assert repaired.authority_transfer_nonce

        wrong_target = await client.post(
            f"/projects/{project_id}/authority/prepare",
            headers=headers,
            params={"expected_generation": 1, "target_node_id": "node-other"},
        )
        assert wrong_target.status_code == 409
        assert "another target" in wrong_target.json()["detail"]
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_disaster_recovery_rebinds_once_and_replays_idempotently(db_factory, monkeypatch):
    project_id, _, _, _, _ = await _seed_team(db_factory)
    async with db_factory() as db:
        project = await db.get(Project, project_id)
        project.authority_node_id = "node-lost"
        project.authority_generation = 1
        project.authority_state = "active"
        await db.commit()

    monkeypatch.setenv("DDUO_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DDUO_NODE_AUTHORITY_SECRET", "node-authority-secret")
    monkeypatch.setenv("DDUO_NODE_ID", "node-recovered")
    client = await _remote_client(db_factory, monkeypatch)
    try:
        payload = {
            "node_id": "node-recovered",
            "expected_generation": 1,
            "old_node_unreachable": True,
        }
        headers = {"X-DDUO-Authority": "node-authority-secret"}
        recovered = await client.post(
            f"/projects/{project_id}/authority/recover",
            headers=headers,
            json=payload,
        )
        assert recovered.status_code == 200
        assert recovered.json()["node_id"] == "node-recovered"
        assert recovered.json()["generation"] == 2
        assert recovered.json()["idempotent"] is False

        replay = await client.post(
            f"/projects/{project_id}/authority/recover",
            headers=headers,
            json=payload,
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent"] is True
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("authority_state", ["transfer_pending", "transferred"])
async def test_startup_task_reconciliation_skips_frozen_and_retired_projects(
    db_factory, authority_state
):
    project_id = str(uuid.uuid4())
    async with db_factory() as db:
        project = Project(id=project_id, name="Frozen", root_path="/tmp/frozen")
        project.authority_state = authority_state
        project.authority_node_id = "node-old"
        project.authority_target_node_id = "node-new"
        db.add(project)
        db.add(Task(project_id=project_id, title="must remain untouched"))
        await db.commit()

    class ForbiddenIndex:
        def inventory(self, *_args):
            raise AssertionError("frozen project reached the vector store")

    async with db_factory() as db:
        assert (
            await enqueue_task_reindex_events(
                db,
                project_id=None,
                origin="bootstrap",
                force=False,
                index_service=ForbiddenIndex(),
            )
            == 0
        )
        await db.commit()
        events = list(
            (
                await db.scalars(select(OutboxEvent).where(OutboxEvent.project_id == project_id))
            ).all()
        )
        project = await db.get(Project, project_id)
    assert events == []
    assert project.task_index_reconciled is False


async def test_member_cannot_attach_events_or_context_to_another_members_turn(
    db_factory, monkeypatch
):
    project_id, _, _, _, _ = await _seed_team(db_factory)
    member_token = _device_token("c")
    async with db_factory() as db:
        manager = await db.scalar(
            select(TeamMember).where(
                TeamMember.project_id == project_id,
                TeamMember.capability == "infrastructure_manager",
            )
        )
        member = TeamMember(
            project_id=project_id,
            display_name="Collaborator",
            capability="project_member",
        )
        db.add(member)
        await db.flush()
        access = TeamAccessToken(
            project_id=project_id,
            member_id=member.id,
            token_hash=hash_secret(member_token),
            device_id="collaborator-device",
        )
        db.add(access)
        manager_session = Session(
            project_id=project_id,
            member_id=manager.id,
            client="codex",
            external_id="manager-session",
        )
        db.add(manager_session)
        await db.flush()
        manager_turn = Turn(
            project_id=project_id,
            session_id=manager_session.id,
            external_id="manager-turn",
            user_prompt="private manager work",
        )
        db.add(manager_turn)
        await db.commit()
        turn_id = manager_turn.id

    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {member_token}"}
    try:
        event = await client.post(
            f"/projects/{project_id}/events",
            headers=headers,
            json={
                "turn_id": turn_id,
                "event_type": "tool_result",
                "payload": {"text": "cross-member contamination"},
            },
        )
        assert event.status_code == 404
        context = await client.post(
            f"/projects/{project_id}/observability/context-events/batch",
            headers=headers,
            json={
                "items": [
                    {
                        "event_id": "cross-member-context",
                        "operation": "context.turn_injection",
                        "scope": "automatic",
                        "client": "codex",
                        "turn_id": turn_id,
                        "characters": 0,
                        "utf8_bytes": 0,
                        "estimated_tokens": 0,
                        "estimator_version": "utf8_bytes_div_4_v1",
                    }
                ]
            },
        )
        assert context.status_code == 404
        async with db_factory() as db:
            raw = list(
                (await db.scalars(select(RawEvent).where(RawEvent.project_id == project_id))).all()
            )
            observations = list(
                (
                    await db.scalars(
                        select(ObservabilityEvent).where(
                            ObservabilityEvent.project_id == project_id
                        )
                    )
                ).all()
            )
        assert raw == []
        assert observations == []
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_remote_team_invite_scope_replay_and_revocation(db_factory, monkeypatch):
    project_id, other_project_id, manager, _, manager_token = await _seed_team(db_factory)
    client = await _remote_client(db_factory, monkeypatch)
    try:
        assert (await client.get(f"/projects/{project_id}/team")).status_code == 401
        headers = {"Authorization": f"Bearer {manager_token}"}
        team = await client.get(f"/projects/{project_id}/team", headers=headers)
        assert team.status_code == 200
        assert team.json()["current_member"]["id"] == manager.id
        assert team.json()["capabilities"] == {
            "manage_infrastructure": True,
            "participate_in_project": True,
        }
        assert (
            await client.get(f"/projects/{other_project_id}/team", headers=headers)
        ).status_code == 404

        incomplete_invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={"display_name": "Incomplete", "api_url": "https://memory.example/api"},
        )
        assert incomplete_invite.status_code == 422
        assert "provided together" in incomplete_invite.json()["detail"]

        missing_project = await client.post(
            "/projects/missing-project/team/invites",
            headers=headers,
            json={"display_name": "Nobody"},
        )
        assert missing_project.status_code == 404

        unsafe_invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={
                "display_name": "Unsafe",
                "api_url": "https://user:password@memory.example/api",
                "dashboard_url": "https://memory.example",
            },
        )
        assert unsafe_invite.status_code == 422
        assert "must not contain credentials" in unsafe_invite.json()["detail"]

        unsupported_language = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={"display_name": "French", "language": "fr"},
        )
        assert unsupported_language.status_code == 422

        legacy_invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={"display_name": "Legacy collaborator", "expires_in_hours": 2},
        )
        assert legacy_invite.status_code == 200
        legacy_invitation = legacy_invite.json()["invitation"]
        assert legacy_invitation["invitation_code"].startswith("dduo_inv_")
        assert "invite_payload" not in legacy_invitation
        assert "setup_prompt" not in legacy_invitation

        invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={
                "display_name": "Collaborator",
                "expires_in_hours": 2,
                "language": "en",
                "api_url": "https://memory.example:443/api",
                "dashboard_url": "https://memory.example",
            },
        )
        assert invite.status_code == 200
        invitation = invite.json()["invitation"]
        decoded_invite = decode_invite_payload(invitation["invite_payload"])
        invitation_code = decoded_invite["invitation_code"]
        assert invitation["invitation_code"] == invitation_code
        assert decoded_invite == {
            "project_id": project_id,
            "name": "Shared",
            "api_url": "https://memory.example/api",
            "dashboard_url": "https://memory.example",
            "invitation_code": invitation_code,
        }
        assert invitation["setup_prompt"] == invitation_setup_prompt(
            invitation["invite_payload"], language="en"
        )
        assert invitation["release_version"] == __version__
        assert "You received access to the shared memory" in invitation["setup_prompt"]
        assert invitation_code not in invitation["setup_prompt"]

        missing_exchange = await client.post(
            f"/projects/{project_id}/auth/exchange",
            json={
                "invitation_code": f"dduo_inv_{'m' * 43}",
                "device_id": "missing-invitation-device",
                "device_token": _device_token("m"),
            },
        )
        assert missing_exchange.status_code == 404

        expired_invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={"display_name": "Expired collaborator", "expires_in_hours": 1},
        )
        assert expired_invite.status_code == 200
        expired_invitation = expired_invite.json()["invitation"]
        async with db_factory() as db:
            stored_expired = await db.get(TeamInvitation, expired_invitation["id"])
            assert stored_expired is not None
            stored_expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await db.commit()
        expired_exchange = await client.post(
            f"/projects/{project_id}/auth/exchange",
            json={
                "invitation_code": expired_invitation["invitation_code"],
                "device_id": "expired-invitation-device",
                "device_token": _device_token("e"),
            },
        )
        assert expired_exchange.status_code == 410

        member_token = _device_token("c")
        exchange_payload = {
            "invitation_code": invitation_code,
            "device_id": "collaborator-device",
            "device_label": "Laptop",
            "device_token": member_token,
        }
        exchanged = await client.post(
            f"/projects/{project_id}/auth/exchange", json=exchange_payload
        )
        assert exchanged.status_code == 200
        member_id = exchanged.json()["member"]["id"]
        assert exchanged.json()["capabilities"]["manage_infrastructure"] is False
        replay = await client.post(f"/projects/{project_id}/auth/exchange", json=exchange_payload)
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        changed_replay = await client.post(
            f"/projects/{project_id}/auth/exchange",
            json={**exchange_payload, "device_token": _device_token("x")},
        )
        assert changed_replay.status_code == 409

        duplicate_token_invite = await client.post(
            f"/projects/{project_id}/team/invites",
            headers=headers,
            json={"display_name": "Duplicate token collaborator"},
        )
        assert duplicate_token_invite.status_code == 200
        duplicate_token_exchange = await client.post(
            f"/projects/{project_id}/auth/exchange",
            json={
                "invitation_code": duplicate_token_invite.json()["invitation"][
                    "invitation_code"
                ],
                "device_id": "duplicate-token-device",
                "device_token": member_token,
            },
        )
        assert duplicate_token_exchange.status_code == 409

        member_headers = {"Authorization": f"Bearer {member_token}"}
        second_member_token = _device_token("s")
        device_payload = {
            "device_id": "collaborator-phone",
            "device_label": "Phone",
            "device_token": second_member_token,
        }
        device = await client.post(
            f"/projects/{project_id}/team/device-tokens",
            headers=member_headers,
            json=device_payload,
        )
        assert device.status_code == 200 and device.json()["idempotent"] is False
        device_retry = await client.post(
            f"/projects/{project_id}/team/device-tokens",
            headers=member_headers,
            json=device_payload,
        )
        assert device_retry.status_code == 200 and device_retry.json()["idempotent"] is True
        assert (
            await client.post(
                f"/projects/{project_id}/team/device-tokens",
                headers=member_headers,
                json={**device_payload, "device_token": _device_token("z")},
            )
        ).status_code == 409
        assert (
            await client.get(
                f"/projects/{project_id}/team",
                headers={"Authorization": f"Bearer {second_member_token}"},
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/projects/{project_id}/team/invites",
                headers=member_headers,
                json={"display_name": "Nope"},
            )
        ).status_code == 403
        revoked = await client.delete(
            f"/projects/{project_id}/team/members/{member_id}", headers=headers
        )
        assert revoked.status_code == 200
        assert (
            await client.get(f"/projects/{project_id}/team", headers=member_headers)
        ).status_code == 401
        assert (
            await client.get(
                f"/projects/{project_id}/team",
                headers={"Authorization": f"Bearer {second_member_token}"},
            )
        ).status_code == 401

        async with db_factory() as db:
            stored = await db.scalar(
                select(TeamAccessToken).where(TeamAccessToken.member_id == member_id)
            )
            assert stored.token_hash == hash_secret(member_token)
            assert member_token not in stored.token_hash
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_team_routes_reject_missing_or_unscoped_device_access(monkeypatch):
    class MissingProjectDb:
        async def get(self, *_args, **_kwargs):
            return None

    with pytest.raises(HTTPException) as missing_project:
        await main_module.get_team("missing-project", _request(), MissingProjectDb())
    assert missing_project.value.status_code == 404

    monkeypatch.setattr(
        main_module,
        "principal_from_request",
        lambda _request: SimpleNamespace(trusted_local=False, member_id="manager-1"),
    )
    monkeypatch.setattr(main_module, "require_infrastructure_manager", lambda _principal: None)
    with pytest.raises(HTTPException) as missing_invite_project:
        await main_module.create_team_invite(
            "missing-project",
            SimpleNamespace(),
            _request(method="POST"),
            MissingProjectDb(),
        )
    assert missing_invite_project.value.status_code == 404

    payload = SimpleNamespace(
        device_id="new-device",
        device_label="Laptop",
        device_token=_device_token("n"),
    )
    monkeypatch.setattr(
        main_module,
        "principal_from_request",
        lambda _request: SimpleNamespace(
            anonymous=True,
            trusted_local=False,
            member_id=None,
        ),
    )
    with pytest.raises(HTTPException) as anonymous:
        await main_module.create_team_device_token(
            "project-1", payload, _request(method="POST"), MissingProjectDb()
        )
    assert anonymous.value.status_code == 403

    monkeypatch.setattr(
        main_module,
        "principal_from_request",
        lambda _request: SimpleNamespace(
            anonymous=False,
            trusted_local=False,
            member_id="missing-member",
        ),
    )

    class MissingMemberDb:
        async def scalar(self, *_args, **_kwargs):
            return None

    with pytest.raises(HTTPException) as revoked:
        await main_module.create_team_device_token(
            "project-1", payload, _request(method="POST"), MissingMemberDb()
        )
    assert revoked.value.status_code == 401


async def test_team_device_token_cannot_be_reused_for_another_device(monkeypatch):
    member = SimpleNamespace(id="member-1")
    results = iter((member, None, "existing-token-id"))

    class DuplicateTokenDb:
        async def scalar(self, *_args, **_kwargs):
            return next(results)

    monkeypatch.setattr(
        main_module,
        "principal_from_request",
        lambda _request: SimpleNamespace(
            anonymous=False,
            trusted_local=False,
            member_id=member.id,
        ),
    )
    payload = SimpleNamespace(
        device_id="another-device",
        device_label="Phone",
        device_token=_device_token("r"),
    )
    with pytest.raises(HTTPException) as duplicate:
        await main_module.create_team_device_token(
            "project-1", payload, _request(method="POST"), DuplicateTokenDb()
        )
    assert duplicate.value.status_code == 409


async def test_dduo_clients_receive_safe_protocol_update_directives(db_factory, monkeypatch):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    client = await _remote_client(db_factory, monkeypatch)
    authorization = {"Authorization": f"Bearer {manager_token}"}
    try:
        blocked = await client.get(
            f"/projects/{project_id}/team",
            headers={
                **authorization,
                "X-DDUO-Client-Component": "hook",
                "X-DDUO-Client-Protocol": "0",
                "X-DDUO-Client-Version": "0.1.0-alpha.1",
            },
        )
        assert blocked.status_code == 426
        assert blocked.headers["X-DDUO-Client-Status"] == "blocked"
        assert blocked.headers["X-DDUO-Target-Release"] == __version__

        grace = await client.get(
            f"/projects/{project_id}/team",
            headers={
                **authorization,
                "X-DDUO-Client-Component": "mcp",
                "X-DDUO-Client-Protocol": "1",
                "X-DDUO-Client-Version": "0.1.0-alpha.1",
            },
        )
        assert grace.status_code == 200
        assert grace.headers["X-DDUO-Client-Status"] == "grace"
        assert grace.headers["X-DDUO-Target-Release"] == __version__

        newer = await client.get(
            f"/projects/{project_id}/team",
            headers={
                **authorization,
                "X-DDUO-Client-Component": "hook",
                "X-DDUO-Client-Protocol": "1",
                    "X-DDUO-Client-Version": "99.0.0-beta.999",
            },
        )
        assert newer.status_code == 200
        assert newer.headers["X-DDUO-Client-Status"] == "compatible"
        assert "X-DDUO-Target-Release" not in newer.headers

        launcher = await client.get(
            f"/projects/{project_id}/team",
            headers={
                **authorization,
                "X-DDUO-Client-Component": "launcher",
                "X-DDUO-Client-Protocol": "0",
                "X-DDUO-Client-Version": "0.1.0-alpha.1",
            },
        )
        assert launcher.status_code == 426
        assert launcher.headers["X-DDUO-Client-Status"] == "blocked"
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_local_project_cannot_issue_an_unusable_remote_invitation(api_client):
    client, _ = api_client
    project = project_payload()
    assert (await client.post("/projects", json=project)).status_code == 200

    response = await client.post(
        f"/projects/{project['id']}/team/invites",
        json={"display_name": "Collaborator", "expires_in_hours": 2},
    )

    assert response.status_code == 409
    assert "promote this project first" in response.json()["detail"]


async def test_manual_versions_capabilities_briefing_and_actor(db_factory, monkeypatch):
    project_id, _, manager, _, manager_token = await _seed_team(db_factory)
    member_token = _device_token("p")
    async with db_factory() as db:
        member = TeamMember(
            project_id=project_id,
            display_name="Member",
            capability="project_member",
        )
        db.add(member)
        await db.flush()
        db.add(
            TeamAccessToken(
                project_id=project_id,
                member_id=member.id,
                token_hash=hash_secret(member_token),
                device_id="member-device",
            )
        )
        await db.commit()
        member_id = member.id

    client = await _remote_client(db_factory, monkeypatch)
    manager_headers = {"Authorization": f"Bearer {manager_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}
    try:
        update = {
            "content": "# Regole\n\n- Eseguire test mirati.",
            "expected_version": 0,
            "idempotency_key": "manual-update-0001",
        }
        saved = await client.patch(
            f"/projects/{project_id}/team/manual",
            headers=manager_headers,
            json=update,
        )
        assert saved.status_code == 200
        assert saved.json()["manual"]["version"] == 1
        replay = await client.patch(
            f"/projects/{project_id}/team/manual",
            headers=manager_headers,
            json=update,
        )
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        conflict = await client.patch(
            f"/projects/{project_id}/team/manual",
            headers=manager_headers,
            json={**update, "content": "different"},
        )
        assert conflict.status_code == 409
        assert (
            await client.patch(
                f"/projects/{project_id}/team/manual",
                headers=member_headers,
                json={
                    "content": "not allowed",
                    "expected_version": 1,
                    "idempotency_key": "manual-update-0002",
                },
            )
        ).status_code == 403
        readable = await client.get(f"/projects/{project_id}/team/manual", headers=member_headers)
        assert readable.status_code == 200
        assert (
            await client.post(f"/projects/{project_id}/backups", headers=member_headers)
        ).status_code == 403
        assert (
            await client.get(f"/projects/{project_id}/backups", headers=member_headers)
        ).status_code == 403
        assert (
            await client.post(f"/projects/{project_id}/setup", headers=member_headers)
        ).status_code == 403
        remote_manager_setup = await client.post(
            f"/projects/{project_id}/setup",
            headers=manager_headers,
        )
        assert remote_manager_setup.status_code == 409
        assert remote_manager_setup.json()["detail"] == (
            "Setup is available only from the project's local infrastructure host."
        )

        async def fake_compact(_project, manual):
            return {
                "draft": "# Bozza compatta",
                "based_on_version": manual.version,
                "source": "provider",
                "characters": 17,
                "warnings": [],
                "persisted": False,
                "_telemetry": {
                    "status": "success",
                    "provider": "codex",
                    "model": "gpt-5.6-terra",
                    "duration_ms": 120,
                    "measurement_source": "provider_reported",
                    "input_tokens": 90,
                    "cached_input_tokens": 40,
                    "cache_write_input_tokens": None,
                    "output_tokens": 12,
                    "reasoning_tokens": 3,
                    "reported_total_tokens": 105,
                },
            }

        monkeypatch.setattr("dduo_solo_founder.main.compact_manual_draft", fake_compact)
        draft = await client.post(
            f"/projects/{project_id}/team/manual/compact-draft",
            headers=manager_headers,
            json={"expected_version": 1},
        )
        assert draft.status_code == 200
        assert draft.json()["draft"]["persisted"] is False
        assert (
            await client.post(
                f"/projects/{project_id}/team/manual/compact-draft",
                headers=member_headers,
                json={"expected_version": 1},
            )
        ).status_code == 403
        briefing = await client.get(f"/projects/{project_id}/briefing", headers=member_headers)
        assert briefing.json()["operational_manual"]["content"] == update["content"]

        created_task = await client.post(
            f"/projects/{project_id}/tasks",
            headers=member_headers,
            json={"title": "Concurrent safe task", "status": "todo"},
        )
        assert created_task.status_code == 200
        task_id = created_task.json()["id"]
        current_version = created_task.json()["version"]
        changed = await client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            headers=member_headers,
            json={"status": "in_progress", "expected_version": current_version},
        )
        assert changed.status_code == 200
        idempotent_retry = await client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            headers=member_headers,
            json={"status": "in_progress", "expected_version": current_version},
        )
        assert idempotent_retry.status_code == 200
        stale_change = await client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            headers=member_headers,
            json={"status": "blocked", "expected_version": current_version},
        )
        assert stale_change.status_code == 409
        missing_version = await client.patch(
            f"/projects/{project_id}/tasks/{task_id}",
            headers=member_headers,
            json={"status": "blocked"},
        )
        assert missing_version.status_code == 428

        async with db_factory() as db:
            revisions = list(
                (
                    await db.scalars(select(TaskRevision).where(TaskRevision.task_id == task_id))
                ).all()
            )
            assert {item.actor_member_id for item in revisions} == {member_id}
            manual_revision = await db.scalar(
                select(OperationalManualRevision).where(
                    OperationalManualRevision.project_id == project_id
                )
            )
            assert manual_revision.actor_member_id == manager.id
            manual_usage = await db.scalar(
                select(ObservabilityEvent).where(
                    ObservabilityEvent.project_id == project_id,
                    ObservabilityEvent.operation == "sleep.operational_manual_compaction",
                )
            )
            assert manual_usage.actor_member_id == manager.id
            assert manual_usage.input_tokens == 90
            assert manual_usage.cached_input_tokens == 40
            assert await db.scalar(
                select(Activity.id).where(
                    Activity.project_id == project_id,
                    Activity.actor_member_id == member_id,
                )
            )
            db.add_all(
                [
                    ObservabilityEvent(
                        project_id=project_id,
                        actor_member_id=member_id,
                        idempotency_key="actor-member-event",
                        category="retrieval",
                        operation="retrieval.pipeline",
                    ),
                    ObservabilityEvent(
                        project_id=project_id,
                        actor_member_id=manager.id,
                        idempotency_key="actor-manager-event",
                        category="retrieval",
                        operation="retrieval.pipeline",
                    ),
                    ObservabilityEvent(
                        project_id=project_id,
                        actor_member_id=None,
                        idempotency_key="actor-system-event",
                        category="retrieval",
                        operation="retrieval.pipeline",
                    ),
                ]
            )
            await db.commit()
        events = await client.get(
            f"/projects/{project_id}/observability/events",
            headers=member_headers,
            params={"member_id": member_id, "range": "all"},
        )
        assert events.status_code == 200
        assert len(events.json()["items"]) == 1
        assert events.json()["items"][0]["actor_member"]["id"] == member_id
        summary = await client.get(
            f"/projects/{project_id}/observability/summary",
            headers=member_headers,
            params={"actor": member_id, "range": "all"},
        )
        assert summary.status_code == 200
        assert summary.json()["coverage"]["events"] == 1
        system_events = await client.get(
            f"/projects/{project_id}/observability/events",
            headers=member_headers,
            params={"actor": "system", "range": "all"},
        )
        assert system_events.status_code == 200
        assert len(system_events.json()["items"]) == 1
        assert system_events.json()["items"][0]["actor_member"] is None
        system_summary = await client.get(
            f"/projects/{project_id}/observability/summary",
            headers=member_headers,
            params={"actor": "system", "range": "all"},
        )
        assert system_summary.status_code == 200
        assert system_summary.json()["coverage"]["events"] == 1
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_project_scoped_browser_ticket_cookie_replay_expiry_and_revoke(
    db_factory, monkeypatch
):
    project_id, other_project_id, manager, access, manager_token = await _seed_team(db_factory)
    client = await _remote_client(db_factory, monkeypatch)
    headers = {"Authorization": f"Bearer {manager_token}"}
    try:
        ticket_response = await client.post(
            f"/projects/{project_id}/auth/browser-ticket", headers=headers
        )
        assert ticket_response.status_code == 200
        ticket = ticket_response.json()["ticket"]
        exchange = await client.post(
            f"/projects/{project_id}/auth/browser-session", json={"ticket": ticket}
        )
        assert exchange.status_code == 200
        csrf_token = exchange.json()["csrf_token"]
        assert csrf_token.startswith("dduo_csrf_")
        cookie_header = exchange.headers["set-cookie"]
        assert browser_cookie_name(project_id) in cookie_header
        assert "HttpOnly" in cookie_header and "Secure" in cookie_header
        assert "SameSite=strict" in cookie_header
        assert (
            await client.post(
                f"/projects/{project_id}/auth/browser-session", json={"ticket": ticket}
            )
        ).status_code == 409
        team = await client.get(f"/projects/{project_id}/team")
        assert team.status_code == 200
        assert team.json()["current_member"]["id"] == manager.id
        assert (await client.get(f"/projects/{other_project_id}/team")).status_code == 401

        # A same-IP page on another Caddy port receives this host-wide cookie,
        # but cannot read the per-origin token kept by the real dashboard.
        forged = await client.post(
            f"/projects/{project_id}/auth/logout",
            headers={"Origin": "https://memory.test:24443"},
        )
        assert forged.status_code == 403
        invalid_csrf = await client.post(
            f"/projects/{project_id}/auth/logout",
            headers={"X-DDUO-CSRF": "dduo_csrf_wrong"},
        )
        assert invalid_csrf.status_code == 403
        assert (await client.get(f"/projects/{project_id}/team")).status_code == 200

        expiring_ticket = await client.post(
            f"/projects/{project_id}/auth/browser-ticket", headers=headers
        )
        raw_expiring = expiring_ticket.json()["ticket"]
        async with db_factory() as db:
            row = await db.scalar(
                select(BrowserAuthCredential).where(
                    BrowserAuthCredential.secret_hash == hash_secret(raw_expiring)
                )
            )
            row.expires_at = row.created_at - timedelta(seconds=1)
            await db.commit()
        assert (
            await client.post(
                f"/projects/{project_id}/auth/browser-session",
                json={"ticket": raw_expiring},
            )
        ).status_code == 410

        async with db_factory() as db:
            token = await db.get(TeamAccessToken, access.id)
            token.revoked_at = token.created_at
            await db.commit()
        assert (await client.get(f"/projects/{project_id}/team")).status_code == 401
    finally:
        await client.aclose()
        app.dependency_overrides.clear()


async def test_frozen_project_can_issue_a_browser_ticket_for_read_only_inspection(
    db_factory, monkeypatch
):
    project_id, _, _, _, manager_token = await _seed_team(db_factory)
    async with db_factory() as db:
        project = await db.get(Project, project_id)
        project.authority_state = "transfer_pending"
        project.authority_target_node_id = "node-new"
        await db.commit()

    async with await _remote_client(db_factory, monkeypatch) as client:
        response = await client.post(
            f"/projects/{project_id}/auth/browser-ticket",
            headers={"Authorization": f"Bearer {manager_token}"},
        )
        assert response.status_code == 200
        assert response.json()["ticket"].startswith("dduo_web_")
