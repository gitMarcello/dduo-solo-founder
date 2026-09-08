from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from conftest import project_payload
import dduo_solo_founder.main as main_module
from dduo_solo_founder.models import (
    ContextEventPayload,
    Memory,
    ObservabilityEvent,
    RetrievalRun,
    Turn,
)
from dduo_solo_founder.observability import (
    EMBEDDING_PRICING_VERSION,
    ContextSnapshot,
    Observation,
    _component_bytes,
    _detail_values,
    _reconcile_context_snapshot,
    _timeline,
    date_window,
    embedding_price,
    estimated_tokens_for_bytes,
    estimated_tokens_for_text,
    record,
    record_context,
    safe_details,
)
from dduo_solo_founder.schemas import StopCheck


async def create_project(client, **overrides):
    payload = project_payload(**overrides)
    response = await client.post("/projects", json=payload)
    assert response.status_code == 200
    return payload


def exact_context_observation(
    content: str,
    *,
    event_id: str,
    operation: str = "context.session_start",
    scope: str = "automatic",
    component: str = "overhead",
    **references,
) -> dict:
    encoded = content.encode("utf-8")
    estimated_tokens = (len(encoded) + 3) // 4
    return {
        "event_id": event_id,
        "operation": operation,
        "scope": scope,
        "client": "codex",
        "characters": len(content),
        "utf8_bytes": len(encoded),
        "estimated_tokens": estimated_tokens,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {component: len(encoded)},
        "components": [
            {
                "name": component,
                "utf8_bytes": len(encoded),
                "estimated_tokens": estimated_tokens,
                "item_count": 1,
                "references": [],
            }
        ],
        "content": content,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "producer_version": "0.1.0-test",
        "render_version": "test-render-v1",
        "occurred_at": "2026-08-25T12:34:56+00:00",
        **references,
    }


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _FailingContextSession:
    def __init__(self, error: SQLAlchemyError, scalar_results: list[object | None]):
        self.error = error
        self.scalar_results = iter(scalar_results)

    async def scalar(self, _statement):
        return next(self.scalar_results)

    def begin_nested(self):
        return _NestedTransaction()

    def add(self, _value):
        return None

    async def flush(self):
        raise self.error


async def test_exact_context_persistence_isolates_integrity_and_database_failures():
    observation = Observation(
        project_id="project",
        idempotency_key="context:test-failure",
        category="context",
        operation="context.session_start",
        scope="automatic",
    )
    duplicate = _FailingContextSession(
        IntegrityError("insert", {}, Exception("duplicate")),
        [None, object()],
    )
    assert await record_context(duplicate, observation, None) == "duplicate"

    unavailable = _FailingContextSession(SQLAlchemyError("database unavailable"), [None])
    assert await record_context(unavailable, observation, None) == "failed"

    snapshot = ContextSnapshot(
        content="contesto",
        content_sha256=hashlib.sha256(b"contesto").hexdigest(),
        producer_version="test",
        render_version="test-v1",
        estimator_version="utf8_bytes_div_4_v1",
        captured_at=datetime.now(timezone.utc),
        components=[],
    )
    failed_reconcile = _FailingContextSession(
        IntegrityError("insert", {}, Exception("payload race")),
        [None, None],
    )
    assert (
        await _reconcile_context_snapshot(
            failed_reconcile,
            SimpleNamespace(id="event-id"),
            snapshot,
        )
        == "failed"
    )
    duplicate_reconcile = _FailingContextSession(
        IntegrityError("insert", {}, Exception("payload race")),
        [None, snapshot.content_sha256],
    )
    assert (
        await _reconcile_context_snapshot(
            duplicate_reconcile,
            SimpleNamespace(id="event-id"),
            snapshot,
        )
        == "duplicate"
    )
    conflict_reconcile = _FailingContextSession(
        IntegrityError("insert", {}, Exception("payload race")),
        [None, "different-hash"],
    )
    assert (
        await _reconcile_context_snapshot(
            conflict_reconcile,
            SimpleNamespace(id="event-id"),
            snapshot,
        )
        == "conflict"
    )
    unavailable_reconcile = _FailingContextSession(
        SQLAlchemyError("database unavailable"),
        [None],
    )
    assert (
        await _reconcile_context_snapshot(
            unavailable_reconcile,
            SimpleNamespace(id="event-id"),
            snapshot,
        )
        == "failed"
    )
    missing_after_integrity = _FailingContextSession(
        IntegrityError("insert", {}, Exception("event race")),
        [None, None],
    )
    assert await record_context(missing_after_integrity, observation, None) == "failed"

    assert _component_bytes(
        [
            SimpleNamespace(
                details={
                    "component_valid": 2,
                    "component_text": "2",
                    "component_flag": True,
                    "component_negative": -1,
                    "status": 4,
                }
            )
        ]
    ) == {"valid": 2}
    assert _detail_values(
        [
            SimpleNamespace(details={"value": True}),
            SimpleNamespace(details={"value": "4"}),
            SimpleNamespace(details={"value": -1}),
            SimpleNamespace(details={"value": 3.8}),
        ],
        "value",
    ) == [3]


async def test_existing_context_event_accepts_one_snapshot_then_detects_conflicts(
    api_client,
    db_factory,
):
    client, _ = api_client
    project = await create_project(client)
    observation = Observation(
        project_id=project["id"],
        idempotency_key="context:late-snapshot",
        category="context",
        operation="context.session_start",
        scope="automatic",
    )
    snapshot = ContextSnapshot(
        content="manuale verificato",
        content_sha256=hashlib.sha256(b"manuale verificato").hexdigest(),
        producer_version="test",
        render_version="test-v1",
        estimator_version="utf8_bytes_div_4_v1",
        captured_at=datetime.now(timezone.utc),
        components=[],
    )
    conflicting = ContextSnapshot(
        content="contenuto diverso",
        content_sha256=hashlib.sha256(b"contenuto diverso").hexdigest(),
        producer_version="test",
        render_version="test-v1",
        estimator_version="utf8_bytes_div_4_v1",
        captured_at=datetime.now(timezone.utc),
        components=[],
    )
    async with db_factory() as db:
        assert await record(db, observation) is True
        await db.commit()
        assert await record_context(db, observation, snapshot) == "created"
        await db.commit()
        assert await record_context(db, observation, snapshot) == "duplicate"
        assert await record_context(db, observation, conflicting) == "conflict"


async def test_context_batch_is_idempotent_and_persists_exact_unicode_separately(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "observability-session"},
        )
    ).json()
    content = "Contesto esatto: caffè, qualità e lancio 🚀"
    event = exact_context_observation(
        content,
        event_id="context-session-start-1",
        component="profile",
        session_id=session["id"],
    )
    endpoint = f"/projects/{project['id']}/observability/context-events/batch"
    first = await client.post(endpoint, json={"items": [event]})
    second = await client.post(endpoint, json={"items": [event]})
    assert first.status_code == 202 and first.json() == {"accepted": 1}
    assert second.status_code == 202 and second.json() == {"accepted": 0}

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    automatic = summary["context"]["automatic"]
    assert automatic["injections"] == 1
    assert automatic["characters"] == len(content)
    assert automatic["utf8_bytes"] == len(content.encode("utf-8"))
    assert automatic["estimated_tokens"] == (len(content.encode("utf-8")) + 3) // 4
    assert automatic["estimated_tokens_p50"] == automatic["estimated_tokens"]
    assert automatic["estimated_tokens_p95"] == automatic["estimated_tokens"]
    assert automatic["component_bytes"] == {"profile": len(content.encode("utf-8"))}
    assert automatic["coverage"] == {"reported": 0, "estimated": 1, "unavailable": 0}
    assert summary["coverage"]["estimated"] == 1
    statements: list[str] = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db_factory.kw["bind"].sync_engine
    sqlalchemy_event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        rows = (
            await client.get(f"/projects/{project['id']}/observability/events?range=all")
        ).json()
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture_sql)
    payload_selects = [
        statement.lower()
        for statement in statements
        if "select" in statement.lower() and "context_event_payloads" in statement.lower()
    ]
    assert len(payload_selects) == 1
    assert "context_event_payloads.content" not in payload_selects[0]
    item = rows["items"][0]
    assert item["details"] == {"component_profile": len(content.encode("utf-8"))}
    assert item["component_breakdown"] == {"profile": len(content.encode("utf-8"))}
    assert item["has_content"] is True
    assert "content" not in item

    detail_response = await client.get(
        f"/projects/{project['id']}/observability/context-events/{item['id']}"
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["event"]["has_content"] is True
    assert detail["content"] == content
    assert detail["content_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert detail["producer_version"] == "0.1.0-test"
    assert detail["render_version"] == "test-render-v1"
    captured_at = datetime.fromisoformat(detail["captured_at"])
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    assert captured_at == datetime(2026, 8, 25, 12, 34, 56, tzinfo=timezone.utc)
    assert detail["components"] == event["components"]
    assert detail["turn"] is None
    assert detail["retrieval"] is None
    assert detail["tool_name"] is None

    other_project = await create_project(client)
    wrong_project = await client.get(
        f"/projects/{other_project['id']}/observability/context-events/{item['id']}"
    )
    assert wrong_project.status_code == 404
    assert wrong_project.json() == {"detail": "context event not found"}

    async with db_factory() as db:
        stored = await db.scalar(select(ObservabilityEvent))
        assert stored is not None
        assert content not in str(stored.details)
        payload = await db.get(ContextEventPayload, stored.id)
        assert payload is not None
        assert isinstance(payload.content, str)
        assert payload.content == content

    conflict = exact_context_observation(
        "Contesto diverso",
        event_id=event["event_id"],
        component="profile",
        session_id=session["id"],
    )
    rejected = await client.post(endpoint, json={"items": [conflict]})
    assert rejected.status_code == 409

    unchanged = (
        await client.get(f"/projects/{project['id']}/observability/context-events/{item['id']}")
    ).json()
    assert unchanged["content"] == content


async def test_context_budget_summary_preserves_candidate_emitted_and_avoided_boundaries(
    api_client,
):
    client, _ = api_client
    project = await create_project(client)
    content = "Founder brief emitted"
    event = exact_context_observation(
        content,
        event_id="budgeted-founder-brief",
        component="memories",
    )
    emitted_characters = len(content)
    emitted_bytes = len(content.encode("utf-8"))
    candidate_characters = emitted_characters + 80
    candidate_bytes = emitted_bytes + 80
    event["render_version"] = "hook-context-v4"
    event["components"] = [
        {
            **event["components"][0],
            "candidate_item_count": 3,
            "partial_item_count": 1,
            "omitted_item_count": 1,
            "references": ["memory-emitted", "memory-partial"],
            "omitted_references": ["memory-omitted"],
            "item_count": 2,
        }
    ]
    event["budget"] = {
        "limit_characters": 9_000,
        "client_character_units": emitted_characters,
        "candidate_characters": candidate_characters,
        "candidate_utf8_bytes": candidate_bytes,
        "candidate_estimated_tokens": (candidate_bytes + 3) // 4,
        "avoided_characters": 80,
        "avoided_utf8_bytes": 80,
        "avoided_estimated_tokens": 20,
        "included_items": 2,
        "partial_items": 1,
        "omitted_items": 1,
        "outcome": "budgeted",
        "delivery_expectation": "inline_expected",
    }
    response = await client.post(
        f"/projects/{project['id']}/observability/context-events/batch",
        json={"items": [event]},
    )
    assert response.status_code == 202

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()["context"]["automatic"]
    assert summary["budget"] == {
        "measured_injections": 1,
        "budget_limit_characters": 9_000,
        "budgeted_injections": 1,
        "fallback_injections": 0,
        "inline_expected_injections": 1,
        "candidate_characters": candidate_characters,
        "candidate_utf8_bytes": candidate_bytes,
        "candidate_estimated_tokens": (candidate_bytes + 3) // 4,
        "avoided_characters": 80,
        "avoided_utf8_bytes": 80,
        "avoided_estimated_tokens": 20,
        "included_items": 2,
        "partial_items": 1,
        "omitted_items": 1,
        "budget_utilization_p50_percent": round(emitted_characters / 9_000 * 100, 1),
        "budget_utilization_p95_percent": round(emitted_characters / 9_000 * 100, 1),
    }
    rows = (await client.get(f"/projects/{project['id']}/observability/events?range=all")).json()[
        "items"
    ]
    assert rows[0]["details"]["budget_outcome"] == "budgeted"
    assert rows[0]["details"]["avoided_characters"] == 80
    assert rows[0]["candidate_count"] == 3
    assert rows[0]["selected_count"] == 2
    assert rows[0]["dropped_count"] == 1


async def test_context_delivery_summary_separates_snapshot_delta_fallback_and_legacy(
    api_client,
    db_factory,
):
    client, _ = api_client
    project = await create_project(client)
    endpoint = f"/projects/{project['id']}/observability/context-events/batch"

    snapshot = exact_context_observation("snapshot", event_id="delivery-snapshot")
    snapshot["delivery"] = {
        "kind": "snapshot",
        "reason": "startup",
        "foundation_changed": True,
        "work_changed": True,
    }
    delta = exact_context_observation(
        "delta",
        event_id="delivery-delta",
        operation="context.turn_injection",
    )
    delta["delivery"] = {
        "kind": "delta",
        "reason": "unchanged",
        "reused_characters": 120,
        "reused_utf8_bytes": 128,
        "reused_estimated_tokens": 32,
    }
    fallback = exact_context_observation(
        "fallback",
        event_id="delivery-fallback",
        operation="context.turn_injection",
    )
    fallback["delivery"] = {
        "kind": "fallback",
        "reason": "composition_failed",
    }
    legacy = exact_context_observation("legacy", event_id="delivery-legacy")

    response = await client.post(
        endpoint,
        json={"items": [snapshot, delta, fallback, legacy]},
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 4}

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()["context"]["automatic"]
    assert summary["delivery"] == {
        "measured_injections": 3,
        "snapshot_injections": 1,
        "delta_injections": 1,
        "fallback_injections": 1,
        "unknown_injections": 1,
        "reused_characters": 120,
        "reused_utf8_bytes": 128,
        "reused_estimated_tokens": 32,
    }
    # Stable context already present in the live session is not context removed
    # by the 9,000-character selector. These remain independent measurements.
    assert summary["budget"]["measured_injections"] == 0
    assert summary["budget"]["avoided_estimated_tokens"] is None

    async with db_factory() as db:
        rows = (
            await db.scalars(
                select(ObservabilityEvent).where(
                    ObservabilityEvent.project_id == project["id"],
                    ObservabilityEvent.category == "context",
                )
            )
        ).all()
    by_kind = {row.details.get("delivery_kind", "legacy"): row for row in rows}
    assert by_kind["snapshot"].status == "success"
    assert by_kind["delta"].status == "success"
    assert by_kind["fallback"].status == "degraded"
    assert by_kind["delta"].details == {
        "component_overhead": len("delta".encode("utf-8")),
        "delivery_kind": "delta",
        "delivery_reason": "unchanged",
        "foundation_changed": False,
        "work_changed": False,
        "reused_characters": 120,
        "reused_utf8_bytes": 128,
        "reused_estimated_tokens": 32,
    }


async def test_context_delivery_summary_keeps_legacy_unknown_values_explicit(api_client):
    client, _ = api_client
    project = await create_project(client)
    event = exact_context_observation("legacy only", event_id="legacy-delivery-state")
    response = await client.post(
        f"/projects/{project['id']}/observability/context-events/batch",
        json={"items": [event]},
    )
    assert response.status_code == 202

    delivery = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()["context"]["automatic"]["delivery"]
    assert delivery == {
        "measured_injections": 0,
        "snapshot_injections": 0,
        "delta_injections": 0,
        "fallback_injections": 0,
        "unknown_injections": 1,
        "reused_characters": None,
        "reused_utf8_bytes": None,
        "reused_estimated_tokens": None,
    }


async def test_context_snapshot_conflict_rolls_back_the_strict_batch(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    endpoint = f"/projects/{project['id']}/observability/context-events/batch"
    original = exact_context_observation("originale", event_id="stable-event")
    assert (await client.post(endpoint, json={"items": [original]})).status_code == 202

    new_event = exact_context_observation("nuovo", event_id="must-roll-back")
    conflicting = exact_context_observation("cambiato", event_id="stable-event")
    response = await client.post(endpoint, json={"items": [new_event, conflicting]})
    assert response.status_code == 409

    async with db_factory() as db:
        rolled_back = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.idempotency_key == "context:must-roll-back"
            )
        )
        assert rolled_back is None
        stored = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.idempotency_key == "context:stable-event"
            )
        )
        assert stored is not None
        payload = await db.get(ContextEventPayload, stored.id)
        assert payload is not None and payload.content == "originale"


async def test_mcp_snapshot_is_project_scoped_without_false_turn_correlation(api_client):
    client, _ = api_client
    project = await create_project(client)
    content = '{"items":[{"text":"risultato esatto già pronto"}]}'
    observation = exact_context_observation(
        content,
        event_id="mcp-exact-result",
        operation="context.mcp_tool_result",
        scope="requested",
        component="result",
        tool_name="search_memory",
    )
    response = await client.post(
        f"/projects/{project['id']}/observability/context-events/batch",
        json={"items": [observation]},
    )
    assert response.status_code == 202
    listed = (
        await client.get(
            f"/projects/{project['id']}/observability/events",
            params={"range": "all", "operation": "context.mcp_tool_result"},
        )
    ).json()["items"]
    assert listed[0]["session_id"] is None
    assert listed[0]["turn_id"] is None
    assert listed[0]["retrieval_run_id"] is None
    assert listed[0]["has_content"] is True
    detail = (
        await client.get(
            f"/projects/{project['id']}/observability/context-events/{listed[0]['id']}"
        )
    ).json()
    assert detail["content"] == content
    assert detail["tool_name"] == "search_memory"
    assert detail["turn"] is None
    assert detail["retrieval"] is None


async def test_observability_endpoints_reject_unknown_projects_and_invalid_queries(api_client):
    client, _ = api_client
    missing_project_id = project_payload()["id"]
    event = {
        "event_id": "missing-project-event",
        "operation": "context.session_start",
        "scope": "automatic",
        "client": "codex",
        "characters": 0,
        "utf8_bytes": 0,
        "estimated_tokens": 0,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {},
    }
    endpoints = (
        await client.post(
            f"/projects/{missing_project_id}/observability/context-events/batch",
            json={"items": [event]},
        ),
        await client.get(f"/projects/{missing_project_id}/observability/summary"),
        await client.get(f"/projects/{missing_project_id}/observability/events"),
    )
    assert [response.status_code for response in endpoints] == [404, 404, 404]

    project = await create_project(client)
    summary_endpoint = f"/projects/{project['id']}/observability/summary"
    events_endpoint = f"/projects/{project['id']}/observability/events"
    invalid_responses = (
        await client.get(summary_endpoint, params={"range": "forever"}),
        await client.get(summary_endpoint, params={"timezone": "Not/A-Timezone"}),
        await client.get(events_endpoint, params={"range": "forever"}),
        await client.get(events_endpoint, params={"cursor": "not-a-date|event-id"}),
    )
    assert [response.status_code for response in invalid_responses] == [422, 422, 422, 422]
    assert invalid_responses[-1].json() == {"detail": "cursor is invalid"}


async def test_retired_usage_guard_history_is_not_exposed_as_current_observability(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    async with db_factory() as db:
        assert await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="legacy-usage-guard-event",
                category="usage_guard",
                operation="usage_guard.threshold_reached",
                scope="interactive",
                status="blocked",
                provider="codex",
                measurement_source="unavailable",
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        await db.commit()

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    events = (
        await client.get(f"/projects/{project['id']}/observability/events?range=all")
    ).json()
    assert summary["coverage"]["events"] == 0
    assert summary["coverage"]["collection_started_at"] is None
    assert summary["reliability"]["requests"] == 0
    assert events["items"] == []


async def test_context_batch_is_atomic_and_rejects_cross_project_references(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    other_project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "atomic-local"},
        )
    ).json()
    other_session = (
        await client.post(
            f"/projects/{other_project['id']}/sessions",
            json={"client": "codex", "external_id": "atomic-foreign"},
        )
    ).json()
    base = {
        "operation": "context.session_start",
        "scope": "automatic",
        "client": "codex",
        "characters": 0,
        "utf8_bytes": 0,
        "estimated_tokens": 0,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {},
    }
    endpoint = f"/projects/{project['id']}/observability/context-events/batch"
    response = await client.post(
        endpoint,
        json={
            "items": [
                {**base, "event_id": "atomic-local", "session_id": session["id"]},
                {
                    **base,
                    "event_id": "atomic-foreign",
                    "session_id": other_session["id"],
                },
            ]
        },
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}

    async with db_factory() as db:
        local_event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.project_id == project["id"],
                ObservabilityEvent.idempotency_key == "context:atomic-local",
            )
        )
        assert local_event is None

    retry = await client.post(
        endpoint,
        json={"items": [{**base, "event_id": "atomic-local", "session_id": session["id"]}]},
    )
    assert retry.status_code == 202
    assert retry.json() == {"accepted": 1}


async def test_stop_check_persists_context_with_the_private_turn_and_summary_is_separated(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "stop-session"},
        )
    ).json()
    begun = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={"session_id": session["id"], "external_id": "turn", "user_prompt": "Ship it"},
        )
    ).json()
    turn = begun["turn"]
    replay = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={"session_id": session["id"], "external_id": "turn", "user_prompt": "Ship it"},
        )
    ).json()
    assert replay["retrieval"]["metadata"]["replayed"] is True
    content = "Turn context esatto: già pronto 🚀"
    observation = exact_context_observation(
        content,
        event_id="turn-injection-1",
        operation="context.turn_injection",
        component="memories",
        retrieval_run_id=begun["retrieval"]["retrieval_run_id"],
    )
    response = await client.post(
        f"/turns/{turn['id']}/stop-check",
        json={"assistant_response": "Done", "context_observations": [observation]},
    )
    assert response.status_code == 200
    duplicate = await client.post(
        f"/turns/{turn['id']}/stop-check",
        json={"context_observations": [observation]},
    )
    assert duplicate.status_code == 200
    conflicting = exact_context_observation(
        "Snapshot differente durante il retry",
        event_id="turn-injection-1",
        operation="context.turn_injection",
        component="memories",
        retrieval_run_id=begun["retrieval"]["retrieval_run_id"],
    )
    conflict_response = await client.post(
        f"/turns/{turn['id']}/stop-check",
        json={"context_observations": [conflicting]},
    )
    assert conflict_response.status_code == 200

    async with db_factory() as db:
        context_rows = list(
            (
                await db.scalars(
                    select(ObservabilityEvent).where(
                        ObservabilityEvent.operation == "context.turn_injection"
                    )
                )
            ).all()
        )
        assert len(context_rows) == 1
        assert context_rows[0].turn_id == turn["id"]
        assert context_rows[0].measurement_source == "local_estimate"
        # Direct retrieval is the only paid query when it already provides
        # sufficient context; replay performs no additional embedding.
        assert (
            len(
                list(
                    (
                        await db.scalars(
                            select(ObservabilityEvent).where(
                                ObservabilityEvent.category == "embedding"
                            )
                        )
                    ).all()
                )
            )
            == 1
        )
        assert await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.operation == "retrieval.pipeline")
        )
        first_memory = Memory(
            id="00000000-0000-0000-0000-000000000001",
            project_id=project["id"],
            node_type="decision",
            node_key="first",
            text="Prima memoria",
        )
        second_memory = Memory(
            id="00000000-0000-0000-0000-000000000002",
            project_id=project["id"],
            node_type="heuristic",
            node_key="second",
            text="Seconda memoria",
        )
        db.add_all([first_memory, second_memory])
        retrieval = await db.get(RetrievalRun, begun["retrieval"]["retrieval_run_id"])
        assert retrieval is not None
        retrieval.selected_memory_ids = [second_memory.id, first_memory.id]
        retrieval.selected_scores = {first_memory.id: 0.75}
        await db.commit()

    listed = (
        await client.get(
            f"/projects/{project['id']}/observability/events",
            params={"range": "all", "operation": "context.turn_injection"},
        )
    ).json()["items"]
    detail = (
        await client.get(
            f"/projects/{project['id']}/observability/context-events/{listed[0]['id']}"
        )
    ).json()
    assert detail["content"] == content
    assert detail["turn"] == {
        "id": turn["id"],
        "user_prompt": "Ship it",
        "assistant_response": "Done",
    }
    assert detail["retrieval"]["id"] == begun["retrieval"]["retrieval_run_id"]
    assert detail["retrieval"]["status"] == begun["retrieval"]["status"]
    assert [item["id"] for item in detail["retrieval"]["memories"]] == [
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    ]
    assert [item["score"] for item in detail["retrieval"]["memories"]] == [None, 0.75]

    async with db_factory() as db:
        stored_turn = await db.get(Turn, turn["id"])
        assert stored_turn is not None
        stored_turn.off_record = True
        await db.commit()

    private_detail = (
        await client.get(
            f"/projects/{project['id']}/observability/context-events/{listed[0]['id']}"
        )
    ).json()
    assert private_detail["turn"] is None
    assert private_detail["turn_off_record"] is True
    assert "Ship it" not in json.dumps(private_detail)
    assert "Done" not in json.dumps(private_detail)


async def test_context_inspector_lists_only_memories_actually_emitted_by_v4(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "selection-aware-session"},
        )
    ).json()
    begun = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "selection-aware-turn",
                "user_prompt": "Recall the selected decision",
            },
        )
    ).json()
    included_id = "00000000-0000-0000-0000-000000000011"
    omitted_id = "00000000-0000-0000-0000-000000000012"
    async with db_factory() as db:
        db.add_all(
            [
                Memory(
                    id=included_id,
                    project_id=project["id"],
                    node_type="decision",
                    node_key="included",
                    text="This memory was emitted.",
                ),
                Memory(
                    id=omitted_id,
                    project_id=project["id"],
                    node_type="decision",
                    node_key="omitted",
                    text="This memory stayed outside the budget.",
                ),
            ]
        )
        retrieval = await db.get(RetrievalRun, begun["retrieval"]["retrieval_run_id"])
        assert retrieval is not None
        retrieval.selected_memory_ids = [included_id, omitted_id]
        retrieval.selected_scores = {included_id: 0.91, omitted_id: 0.90}
        await db.commit()

    content = "Selection-aware exact context"
    event = exact_context_observation(
        content,
        event_id="selection-aware-context",
        operation="context.turn_injection",
        component="memories",
        retrieval_run_id=begun["retrieval"]["retrieval_run_id"],
    )
    candidate_characters = len(content) + 10
    candidate_bytes = len(content.encode("utf-8")) + 10
    event["render_version"] = "hook-context-v4"
    event["components"] = [
        {
            **event["components"][0],
            "candidate_item_count": 2,
            "partial_item_count": 0,
            "omitted_item_count": 1,
            "references": [included_id],
            "omitted_references": [omitted_id],
        }
    ]
    event["budget"] = {
        "limit_characters": 9_000,
        "client_character_units": len(content),
        "candidate_characters": candidate_characters,
        "candidate_utf8_bytes": candidate_bytes,
        "candidate_estimated_tokens": (candidate_bytes + 3) // 4,
        "avoided_characters": 10,
        "avoided_utf8_bytes": 10,
        "avoided_estimated_tokens": 3,
        "included_items": 1,
        "partial_items": 0,
        "omitted_items": 1,
        "outcome": "budgeted",
        "delivery_expectation": "inline_expected",
    }
    stopped = await client.post(
        f"/turns/{begun['turn']['id']}/stop-check",
        json={"assistant_response": "Done", "context_observations": [event]},
    )
    assert stopped.status_code == 200
    listed = (
        await client.get(
            f"/projects/{project['id']}/observability/events",
            params={"range": "all", "operation": "context.turn_injection"},
        )
    ).json()["items"]
    detail = (
        await client.get(
            f"/projects/{project['id']}/observability/context-events/{listed[0]['id']}"
        )
    ).json()

    assert [memory["id"] for memory in detail["retrieval"]["memories"]] == [included_id]
    assert detail["retrieval"]["memories"][0]["score"] == 0.91


async def test_context_inspector_does_not_invent_memories_for_v4_fallback(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "fallback-inspector-session"},
        )
    ).json()
    begun = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "fallback-inspector-turn",
                "user_prompt": "Recall the selected decision",
            },
        )
    ).json()
    selected_id = "00000000-0000-0000-0000-000000000013"
    async with db_factory() as db:
        db.add(
            Memory(
                id=selected_id,
                project_id=project["id"],
                node_type="decision",
                node_key="selected-but-not-emitted",
                text="This memory was selected but the hook emitted fallback context.",
            )
        )
        retrieval = await db.get(RetrievalRun, begun["retrieval"]["retrieval_run_id"])
        assert retrieval is not None
        retrieval.selected_memory_ids = [selected_id]
        retrieval.selected_scores = {selected_id: 0.88}
        await db.commit()

    content = '{"_dduo_health":{"status":"fallback"}}'
    event = exact_context_observation(
        content,
        event_id="selection-aware-fallback-context",
        operation="context.turn_injection",
        component="health",
        retrieval_run_id=begun["retrieval"]["retrieval_run_id"],
    )
    event["render_version"] = "hook-context-v4"
    stored = await client.post(
        f"/projects/{project['id']}/observability/context-events/batch",
        json={"items": [event]},
    )
    assert stored.status_code == 202
    listed = (
        await client.get(
            f"/projects/{project['id']}/observability/events",
            params={"range": "all", "operation": "context.turn_injection"},
        )
    ).json()["items"]
    detail = (
        await client.get(
            f"/projects/{project['id']}/observability/context-events/{listed[0]['id']}"
        )
    ).json()

    assert detail["retrieval"]["memories"] == []


async def test_summary_keeps_reported_embedding_cost_separate_from_estimated_context(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    async with db_factory() as db:
        await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="embedding-cost",
                category="embedding",
                operation="embedding.manual_search",
                provider="openai",
                model="text-embedding-3-large",
                measurement_source="provider_reported",
                input_tokens=1_000,
                reported_total_tokens=1_000,
                duration_ms=20,
                cost_usd=Decimal("0.000130"),
                unit_price_usd_per_million=Decimal("0.13"),
                pricing_version="test",
            ),
        )
        await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="payload-only-task-refresh",
                category="embedding",
                operation="embedding.task_index",
                status="success",
                measurement_source="unavailable",
                request_count=0,
                duration_ms=3,
            ),
        )
        await db.commit()

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    assert summary["embeddings"]["input_tokens_reported"] == 1_000
    assert summary["embeddings"]["cost_usd"] == "0.000130000000"
    assert summary["embeddings"]["requests"] == 1
    assert summary["context"]["automatic"]["estimated_tokens"] is None
    assert summary["sleep"]["input_tokens_reported"] is None
    assert summary["embeddings"]["coverage"] == {
        "reported": 1,
        "estimated": 0,
        "unavailable": 0,
    }
    assert [item["operation"] for item in summary["embeddings"]["by_operation"]] == [
        "embedding.manual_search"
    ]
    hourly = (
        await client.get(
            f"/projects/{project['id']}/observability/summary?range=24h&timezone=Europe/Rome"
        )
    ).json()
    assert hourly["period"]["bucket"] == "hour"
    assert len(hourly["embeddings"]["timeline"]) == 1


async def test_reliability_does_not_sum_usage_across_model_boundaries(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    async with db_factory() as db:
        for observation in (
            Observation(
                project_id=project["id"],
                idempotency_key="reliability-sleep",
                category="sleep_model",
                operation="sleep.topic_segmentation",
                status="success",
                measurement_source="provider_reported",
                input_tokens=11,
                output_tokens=4,
                duration_ms=10,
            ),
            Observation(
                project_id=project["id"],
                idempotency_key="reliability-embedding",
                category="embedding",
                operation="embedding.manual_search",
                status="failed",
                measurement_source="provider_reported",
                input_tokens=7,
                duration_ms=20,
                cost_usd=Decimal("0.000007"),
            ),
            Observation(
                project_id=project["id"],
                idempotency_key="reliability-context",
                category="context",
                operation="context.turn_injection",
                scope="automatic",
                status="degraded",
                measurement_source="local_estimate",
                input_tokens=3,
                duration_ms=30,
            ),
        ):
            assert await record(db, observation)
        await db.commit()

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    reliability = summary["reliability"]
    assert reliability["requests"] == 3
    assert reliability["successes"] == 1 and reliability["failures"] == 2
    assert reliability["duration_p50_ms"] == 20 and reliability["duration_p95_ms"] == 30
    assert reliability["coverage"] == {"reported": 2, "estimated": 1, "unavailable": 0}
    for field in (
        "input_tokens_reported",
        "input_tokens_estimated",
        "cached_input_tokens_reported",
        "cache_write_input_tokens_reported",
        "output_tokens_reported",
        "output_tokens_estimated",
    ):
        assert reliability[field] is None
    assert "cost_usd" not in reliability
    operations = {item["operation"]: item for item in reliability["by_operation"]}
    assert operations["sleep.topic_segmentation"]["input_tokens_reported"] == 11
    assert operations["embedding.manual_search"]["cost_usd"] == "0.000007000000"
    assert operations["context.turn_injection"]["input_tokens_estimated"] == 3


async def test_api_equivalent_costs_remain_separate_for_agent_sleep_and_embeddings(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    async with db_factory() as db:
        for observation in (
            Observation(
                project_id=project["id"],
                idempotency_key="agent-equivalent-cost",
                category="agent_usage",
                operation="agent.turn_usage",
                provider="codex",
                model="gpt-5.6-terra",
                measurement_source="provider_reported",
                input_tokens=1_000,
                output_tokens=100,
                cost_usd=Decimal("0.0032"),
                pricing_version="api-list-prices-2026-08-29",
            ),
            Observation(
                project_id=project["id"],
                idempotency_key="agent-unknown-price",
                category="agent_usage",
                operation="agent.turn_usage",
                provider="codex",
                model="future-model",
                measurement_source="provider_reported",
                input_tokens=1_000,
            ),
            Observation(
                project_id=project["id"],
                idempotency_key="sleep-equivalent-cost",
                category="sleep_model",
                operation="sleep.memory_consolidation",
                provider="codex",
                model="gpt-5.6-terra",
                measurement_source="provider_reported",
                input_tokens=2_000,
                output_tokens=200,
                cost_usd=Decimal("0.0064"),
                pricing_version="api-list-prices-2026-08-29",
            ),
            Observation(
                project_id=project["id"],
                idempotency_key="embedding-actual-cost",
                category="embedding",
                operation="embedding.manual_search",
                provider="openai",
                model="text-embedding-3-large",
                measurement_source="provider_reported",
                input_tokens=1_000,
                request_count=1,
                cost_usd=Decimal("0.00013"),
                pricing_version="openai-text-embedding-3-large-2026-08",
            ),
        ):
            assert await record(db, observation)
        await db.commit()

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    assert summary["agent_usage"]["equivalent_api_cost_usd"] == "0.003200000000"
    assert summary["agent_usage"]["pricing_coverage"] == {
        "priced": 1,
        "unavailable": 1,
    }
    assert summary["sleep"]["equivalent_api_cost_usd"] == "0.006400000000"
    assert summary["sleep"]["pricing_coverage"] == {"priced": 1, "unavailable": 0}
    assert summary["embeddings"]["cost_usd"] == "0.000130000000"
    # There is deliberately no mixed grand total across these three meanings.
    assert "cost_usd" not in summary
    assert "equivalent_api_cost_usd" not in summary

    by_model = summary["agent_usage"]["by_provider_model"]
    assert [item["model"] for item in by_model] == ["future-model", "gpt-5.6-terra"]
    priced = next(item for item in by_model if item["model"] == "gpt-5.6-terra")
    assert priced["equivalent_api_cost_usd"] == "0.003200000000"
    assert priced["pricing_coverage"] == {"priced": 1, "unavailable": 0}
    assert priced["by_operation"][0]["equivalent_api_cost_usd"] == "0.003200000000"
    assert "cost_usd" not in priced["by_operation"][0]
    assert summary["sleep"]["by_operation"][0]["equivalent_api_cost_usd"] == ("0.006400000000")
    assert summary["agent_usage"]["timeline"][0]["equivalent_api_cost_usd"] == ("0.003200000000")


async def test_client_event_batch_is_strict_idempotent_and_prices_at_occurrence_time(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "usage-session"},
        )
    ).json()
    turn = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "usage-turn",
                "user_prompt": "Measure this turn",
            },
        )
    ).json()["turn"]
    endpoint = f"/projects/{project['id']}/observability/client-events/batch"
    payload = {
        "items": [
            {
                "event_id": "agent.codex.turn-1",
                "provider": "codex",
                "session_id": session["id"],
                "turn_id": turn["id"],
                "model": "gpt-5.6-terra",
                "measurement_source": "provider_reported",
                "input_tokens": 1_000,
                "cached_input_tokens": 200,
                "cache_write_input_tokens": 0,
                "output_tokens": 100,
                "reasoning_tokens": 50,
                "occurred_at": "2026-08-29T12:00:00+00:00",
            },
            {
                "event_id": "agent.claude.sonnet-5-current",
                "provider": "claude",
                "model": "claude-sonnet-5",
                "measurement_source": "local_estimate",
                "input_tokens": 1_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 100,
                "occurred_at": "2026-09-01T00:00:00+00:00",
            },
            {
                "event_id": "agent.claude.client-cost",
                "provider": "claude",
                "measurement_source": "local_estimate",
                "client_cost_usd": "0.123",
                "cost_source": "claude_code_client_estimate",
                "occurred_at": "2026-08-29T12:01:00+00:00",
            },
        ]
    }
    created = await client.post(endpoint, json=payload)
    assert created.status_code == 202 and created.json() == {"accepted": 3}

    # Replaying a known event is successful but cannot mutate its historical cost.
    replay = await client.post(
        endpoint,
        json={
            "items": [
                {
                    **payload["items"][0],
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                }
            ]
        },
    )
    assert replay.status_code == 202 and replay.json() == {"accepted": 0}

    rejected_content = await client.post(
        endpoint,
        json={"items": [{**payload["items"][0], "content": "not telemetry"}]},
    )
    assert rejected_content.status_code == 422

    async with db_factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(ObservabilityEvent)
                    .where(ObservabilityEvent.project_id == project["id"])
                    .order_by(ObservabilityEvent.idempotency_key)
                )
            ).all()
        )
    by_key = {row.idempotency_key: row for row in rows}
    codex = by_key["client:agent.codex.turn-1"]
    assert codex.cost_usd == Decimal("0.002840000000")
    assert codex.input_tokens == 1_000
    assert codex.session_id == session["id"]
    assert codex.turn_id == turn["id"]
    assert codex.pricing_version == "api-list-prices-2026-08-29"
    assert codex.details == {
        "cost_kind": "api_equivalent",
        "uncached_input_cost_pico_usd": 1_600_000_000,
        "cached_input_cost_pico_usd": 40_000_000,
        "cache_write_input_cost_pico_usd": 0,
        "output_cost_pico_usd": 1_200_000_000,
    }
    sonnet = by_key["client:agent.claude.sonnet-5-current"]
    assert sonnet.cost_usd == Decimal("0.003000000000")
    assert sonnet.pricing_version == "api-list-prices-2026-08-29"
    client_cost = by_key["client:agent.claude.client-cost"]
    assert client_cost.cost_usd == Decimal("0.123000000000")
    assert client_cost.pricing_version == "claude-code-client-estimate-v1"
    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()
    codex_summary = next(
        item
        for item in summary["agent_usage"]["by_provider_model"]
        if item["provider"] == "codex"
    )
    assert codex_summary["uncached_input_tokens_reported"] == 800
    assert codex_summary["cache_hit_percent"] == 20.0
    assert codex_summary["equivalent_api_cost_breakdown"] == {
        "uncached_input_usd": "0.001600000000",
        "cached_input_usd": "0.000040000000",
        "cache_write_input_usd": "0.000000000000",
        "output_usd": "0.001200000000",
        "priced": 1,
        "unavailable": 0,
    }


async def test_client_event_batch_drains_retired_guard_items_without_persisting_them(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    endpoint = f"/projects/{project['id']}/observability/client-events/batch"
    usage = {
        "event_id": "agent.codex.after-retired-guard",
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "measurement_source": "provider_reported",
        "input_tokens": 100,
        "output_tokens": 10,
        "occurred_at": "2026-08-29T12:00:00+00:00",
    }
    legacy_guard = {
        "kind": "usage_guard",
        "event_id": "guard.codex.retired-threshold",
        "provider": "codex",
        "operation": "usage_guard.threshold_reached",
        "measurement_source": "provider_reported",
        "occurred_at": "2026-08-29T12:00:01+00:00",
    }

    mixed = await client.post(endpoint, json={"items": [legacy_guard, usage]})
    assert mixed.status_code == 202 and mixed.json() == {"accepted": 1}

    only_legacy = await client.post(
        endpoint,
        json={
            "items": [
                {
                    **legacy_guard,
                    "event_id": "guard.codex.retired-resume",
                    "operation": "usage_guard.resumed",
                    "measurement_source": "local_estimate",
                }
            ]
        },
    )
    assert only_legacy.status_code == 202 and only_legacy.json() == {"accepted": 0}

    arbitrary_legacy = await client.post(
        endpoint,
        json={"items": [{**legacy_guard, "prompt": "must never enter telemetry"}]},
    )
    assert arbitrary_legacy.status_code == 422

    async with db_factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(ObservabilityEvent).where(
                        ObservabilityEvent.project_id == project["id"]
                    )
                )
            ).all()
        )
    assert [row.idempotency_key for row in rows] == [
        "client:agent.codex.after-retired-guard"
    ]
    assert rows[0].category == "agent_usage"


async def test_client_event_batch_degrades_missing_restore_links_without_poisoning(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    endpoint = f"/projects/{project['id']}/observability/client-events/batch"
    response = await client.post(
        endpoint,
        json={
            "items": [
                {
                    "event_id": "agent.codex.restored-stale-link",
                    "provider": "codex",
                    "session_id": str(uuid.uuid4()),
                    "turn_id": str(uuid.uuid4()),
                    "model": "gpt-5.6-sol",
                    "measurement_source": "provider_reported",
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "occurred_at": "2026-08-29T12:00:00+00:00",
                },
                {
                    "event_id": "agent.codex.after-restored-link",
                    "provider": "codex",
                    "model": "gpt-5.6-sol",
                    "measurement_source": "provider_reported",
                    "input_tokens": 20,
                    "output_tokens": 2,
                    "occurred_at": "2026-08-29T12:00:01+00:00",
                },
            ]
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": 2}

    async with db_factory() as db:
        restored = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.project_id == project["id"],
                ObservabilityEvent.idempotency_key
                == "client:agent.codex.restored-stale-link",
            )
        )
    assert restored is not None
    assert restored.session_id is None
    assert restored.turn_id is None


async def test_client_event_batch_rejects_existing_cross_project_links(api_client, db_factory):
    client, _ = api_client
    project = await create_project(client)
    other = await create_project(client, name="Other project")
    foreign_session = (
        await client.post(
            f"/projects/{other['id']}/sessions",
            json={"client": "codex", "external_id": "foreign-usage-session"},
        )
    ).json()
    endpoint = f"/projects/{project['id']}/observability/client-events/batch"
    response = await client.post(
        endpoint,
        json={
            "items": [
                {
                    "event_id": "agent.codex.foreign-link",
                    "provider": "codex",
                    "session_id": foreign_session["id"],
                    "model": "gpt-5.6-sol",
                    "measurement_source": "provider_reported",
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "occurred_at": "2026-08-29T12:00:00+00:00",
                }
            ]
        },
    )
    assert response.status_code == 404

    async with db_factory() as db:
        persisted = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.project_id == project["id"],
                ObservabilityEvent.idempotency_key == "client:agent.codex.foreign-link",
            )
        )
    assert persisted is None


async def test_reported_cache_totals_are_unavailable_when_any_sample_is_unknown(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    now = datetime.now(timezone.utc)
    async with db_factory() as db:
        for key, cached in (("known", 80), ("unknown", None)):
            await record(
                db,
                Observation(
                    project_id=project["id"],
                    idempotency_key=f"partial-cache-{key}",
                    category="agent_usage",
                    operation="agent.interactive",
                    scope="interactive",
                    status="success",
                    provider="codex",
                    model="gpt-5.6-sol",
                    measurement_source="provider_reported",
                    input_tokens=100,
                    cached_input_tokens=cached,
                    cache_write_input_tokens=0 if cached is not None else None,
                    output_tokens=10,
                    occurred_at=now,
                ),
            )
        await db.commit()
    block = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=all")
    ).json()["agent_usage"]
    assert block["input_tokens_reported"] == 200
    assert block["cached_input_tokens_reported"] is None
    assert block["uncached_input_tokens_reported"] is None
    assert block["cache_write_input_tokens_reported"] is None
    assert block["cache_hit_percent"] is None


async def test_client_event_batch_rolls_back_partial_persistence_and_returns_retryable_error(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project = await create_project(client)

    async def persist_only_first(db, observations, _existing_keys):
        db.add(observations[0].to_model())
        await db.flush()
        raise SQLAlchemyError("simulated batch write failure")

    monkeypatch.setattr(
        main_module,
        "_persist_client_observations_atomic",
        persist_only_first,
    )
    response = await client.post(
        f"/projects/{project['id']}/observability/client-events/batch",
        json={
            "items": [
                {
                    "event_id": f"agent.codex.partial-{index}",
                    "provider": "codex",
                    "model": "gpt-5.6-terra",
                    "measurement_source": "provider_reported",
                    "input_tokens": 10,
                    "occurred_at": f"2026-08-29T12:00:0{index}+00:00",
                }
                for index in (1, 2)
            ]
        },
    )
    assert response.status_code == 503
    async with db_factory() as db:
        count = len(
            list(
                (
                    await db.scalars(
                        select(ObservabilityEvent).where(
                            ObservabilityEvent.project_id == project["id"],
                            ObservabilityEvent.category == "agent_usage",
                        )
                    )
                ).all()
            )
        )
    assert count == 0


async def test_summary_preserves_missing_and_measured_zero_in_mixed_time_buckets(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with db_factory() as db:
        await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="missing-measurement",
                category="embedding",
                operation="embedding.manual_search",
                provider="openai",
                measurement_source="unavailable",
                occurred_at=recent - timedelta(hours=2),
            ),
        )
        await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="measured-zero",
                category="embedding",
                operation="embedding.manual_search",
                provider="openai",
                measurement_source="provider_reported",
                input_tokens=0,
                reported_total_tokens=0,
                cost_usd=Decimal(0),
                occurred_at=recent,
            ),
        )
        await db.commit()

    summary = (
        await client.get(f"/projects/{project['id']}/observability/summary?range=24h&timezone=UTC")
    ).json()
    assert summary["embeddings"]["input_tokens_reported"] == 0
    assert summary["embeddings"]["output_tokens_reported"] is None
    assert Decimal(summary["embeddings"]["cost_usd"]) == Decimal(0)
    assert summary["embeddings"]["coverage"] == {
        "reported": 1,
        "estimated": 0,
        "unavailable": 1,
    }
    timeline = summary["embeddings"]["timeline"]
    assert [item["input_tokens_reported"] for item in timeline] == [None, 0]
    assert [item["cost_usd"] for item in timeline] == [None, "0.000000000000"]
    assert [item["coverage"] for item in timeline] == [
        {"reported": 0, "estimated": 0, "unavailable": 1},
        {"reported": 1, "estimated": 0, "unavailable": 0},
    ]
    assert summary["sleep"]["input_tokens_reported"] is None
    assert summary["sleep"]["coverage"] == {
        "reported": 0,
        "estimated": 0,
        "unavailable": 0,
    }


async def test_events_filters_paginate_equal_timestamps_and_isolate_projects(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    other_project = await create_project(client)
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    common = {
        "category": "embedding",
        "operation": "embedding.manual_search",
        "status": "failed",
        "provider": "openai",
        "measurement_source": "provider_reported",
        "input_tokens": 0,
        "cost_usd": Decimal(0),
        "occurred_at": recent,
    }
    project_observations = [
        Observation(project_id=project["id"], idempotency_key="match-a", **common),
        Observation(project_id=project["id"], idempotency_key="match-b", **common),
        Observation(
            project_id=project["id"],
            idempotency_key="wrong-category",
            **{**common, "category": "retrieval"},
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="wrong-operation",
            **{**common, "operation": "embedding.index"},
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="wrong-status",
            **{**common, "status": "success"},
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="wrong-provider",
            **{**common, "provider": "local"},
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="outside-window",
            **{**common, "occurred_at": recent - timedelta(days=2)},
        ),
    ]
    async with db_factory() as db:
        for observation in (
            *project_observations,
            Observation(project_id=other_project["id"], idempotency_key="foreign", **common),
        ):
            assert await record(db, observation)
        await db.commit()

    endpoint = f"/projects/{project['id']}/observability/events"
    filters = {
        "range": "24h",
        "category": "embedding",
        "operation": "embedding.manual_search",
        "status": "failed",
        "provider": "openai",
        "limit": 1,
    }
    first = await client.get(endpoint, params=filters)
    assert first.status_code == 200
    first_page = first.json()
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"] is not None
    first_item = first_page["items"][0]
    assert first_item["project_id"] == project["id"]
    assert first_item["input_tokens"] == 0
    assert first_item["output_tokens"] is None
    assert Decimal(first_item["cost_usd"]) == Decimal(0)

    second = await client.get(
        endpoint,
        params={**filters, "cursor": first_page["next_cursor"]},
    )
    assert second.status_code == 200
    second_page = second.json()
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None
    assert second_page["items"][0]["id"] != first_item["id"]

    cursor_timestamp, cursor_id = first_page["next_cursor"].rsplit("|", 1)
    aware_timestamp = datetime.fromisoformat(cursor_timestamp)
    if aware_timestamp.tzinfo is None:
        aware_timestamp = aware_timestamp.replace(tzinfo=timezone.utc)
    aware_cursor = f"{aware_timestamp.isoformat()}|{cursor_id}"
    aware_page = await client.get(endpoint, params={**filters, "cursor": aware_cursor})
    assert aware_page.status_code == 200
    assert aware_page.json()["items"] == second_page["items"]

    all_events = (await client.get(endpoint, params={"range": "all", "limit": 100})).json()
    assert len(all_events["items"]) == len(project_observations)
    assert {item["project_id"] for item in all_events["items"]} == {project["id"]}
    other_summary = (
        await client.get(f"/projects/{other_project['id']}/observability/summary?range=all")
    ).json()
    assert other_summary["coverage"]["events"] == 1


async def test_summary_preserves_zeroes_across_requested_sleep_and_retrieval_blocks(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    occurred_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    observations = (
        Observation(
            project_id=project["id"],
            idempotency_key="requested-zero",
            category="context",
            operation="context.mcp_tool_result",
            scope="requested",
            measurement_source="local_estimate",
            input_tokens=0,
            characters=0,
            utf8_bytes=0,
            occurred_at=occurred_at,
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="sleep-zero",
            category="sleep_model",
            operation="sleep.memory_extraction",
            status="partial_failure",
            measurement_source="provider_reported",
            input_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=0,
            duration_ms=0,
            occurred_at=occurred_at,
        ),
        Observation(
            project_id=project["id"],
            idempotency_key="retrieval-unavailable",
            category="retrieval",
            operation="retrieval.pipeline",
            measurement_source="unavailable",
            occurred_at=occurred_at,
        ),
    )
    async with db_factory() as db:
        for observation in observations:
            assert await record(db, observation)
        await db.commit()

    response = await client.get(
        f"/projects/{project['id']}/observability/summary",
        params={"range": "7d", "timezone": "UTC"},
    )
    assert response.status_code == 200
    summary = response.json()
    assert summary["period"]["bucket"] == "day"
    assert summary["context"]["automatic"]["estimated_tokens"] is None
    assert summary["context"]["requested"]["estimated_tokens"] == 0
    assert summary["context"]["timeline"][0]["automatic_estimated_tokens"] is None
    assert summary["context"]["timeline"][0]["requested_estimated_tokens"] == 0
    assert summary["sleep"]["failures"] == 1
    assert summary["sleep"]["input_tokens_reported"] == 0
    assert summary["sleep"]["cached_input_tokens_reported"] == 0
    assert summary["sleep"]["cache_write_input_tokens_reported"] == 0
    assert summary["sleep"]["output_tokens_reported"] == 0
    assert summary["sleep"]["duration_p50_ms"] == 0
    assert summary["reliability"]["retrieval"]["requests"] == 1
    assert summary["reliability"]["retrieval"]["input_tokens_reported"] is None


async def test_context_identifier_estimate_and_breakdown_are_server_hardened(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    endpoint = f"/projects/{project['id']}/observability/context-events/batch"
    valid = {
        "event_id": "a" * 192,
        "operation": "context.session_start",
        "scope": "automatic",
        "client": "codex",
        "characters": 8,
        "utf8_bytes": 8,
        "estimated_tokens": 999,
        "estimator_version": "utf8_bytes_div_4_v1",
        # A residual byte count remains valid for backward compatibility.
        "component_bytes": {"profile": 3},
    }
    accepted = await client.post(endpoint, json={"items": [valid]})
    assert accepted.status_code == 202 and accepted.json() == {"accepted": 1}

    async with db_factory() as db:
        stored = await db.scalar(select(ObservabilityEvent))
        assert stored is not None
        assert len(stored.idempotency_key) == 200
        assert stored.input_tokens == 2
        assert stored.details == {"component_profile": 3, "component_overhead": 5}
        assert sum(stored.details.values()) == stored.utf8_bytes

    legacy_item = (
        await client.get(f"/projects/{project['id']}/observability/events?range=all")
    ).json()["items"][0]
    assert legacy_item["has_content"] is False
    legacy_detail = await client.get(
        f"/projects/{project['id']}/observability/context-events/{legacy_item['id']}"
    )
    assert legacy_detail.status_code == 404
    assert legacy_detail.json() == {"detail": "context payload not available"}

    too_long = await client.post(endpoint, json={"items": [{**valid, "event_id": "a" * 193}]})
    assert too_long.status_code == 422
    unsafe = await client.post(endpoint, json={"items": [{**valid, "event_id": "bad/id"}]})
    assert unsafe.status_code == 422
    oversized_breakdown = await client.post(
        endpoint,
        json={
            "items": [
                {
                    **valid,
                    "event_id": "breakdown-too-large",
                    "component_bytes": {"profile": 5, "overhead": 4},
                }
            ]
        },
    )
    assert oversized_breakdown.status_code == 422
    impossible_characters = await client.post(
        endpoint,
        json={
            "items": [
                {
                    **valid,
                    "event_id": "impossible-character-count",
                    "characters": 9,
                }
            ]
        },
    )
    assert impossible_characters.status_code == 422
    nonempty_zero_bytes = await client.post(
        endpoint,
        json={
            "items": [
                {
                    **valid,
                    "event_id": "nonempty-zero-bytes",
                    "characters": 1,
                    "utf8_bytes": 0,
                    "component_bytes": {},
                }
            ]
        },
    )
    assert nonempty_zero_bytes.status_code == 422


async def test_stop_discards_invalid_context_telemetry_without_losing_the_turn(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "invalid-observation-stop"},
        )
    ).json()
    turn = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "invalid-observation-turn",
                "user_prompt": "Persist this turn",
            },
        )
    ).json()["turn"]
    invalid_observation = {
        "event_id": "bad/id",
        "operation": "context.turn_injection",
        "scope": "automatic",
        "client": "codex",
        "characters": 10,
        "utf8_bytes": 4,
        "estimated_tokens": 999,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {"instructions": 5},
    }
    response = await client.post(
        f"/turns/{turn['id']}/stop-check",
        json={
            "assistant_response": "Durably persisted",
            "context_observations": [invalid_observation],
        },
    )
    assert response.status_code == 200

    async with db_factory() as db:
        stored_turn = await db.get(Turn, turn["id"])
        assert stored_turn is not None
        assert stored_turn.committed is True
        assert stored_turn.assistant_response == "Durably persisted"
        context_event = await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.category == "context")
        )
        assert context_event is None


async def test_stop_never_fails_the_turn_when_snapshot_processing_raises(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project = await create_project(client)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "snapshot-failure-session"},
        )
    ).json()
    turn = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "snapshot-failure-turn",
                "user_prompt": "Persist even if observability fails",
            },
        )
    ).json()["turn"]

    def fail_snapshot(_item):
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr("dduo_solo_founder.main.context_snapshot", fail_snapshot)
    response = await client.post(
        f"/turns/{turn['id']}/stop-check",
        json={
            "assistant_response": "Turn remains durable",
            "context_observations": [
                exact_context_observation(
                    "exact context",
                    event_id="snapshot-processing-failure",
                    operation="context.turn_injection",
                )
            ],
        },
    )
    assert response.status_code == 200

    async with db_factory() as db:
        stored_turn = await db.get(Turn, turn["id"])
        assert stored_turn is not None
        assert stored_turn.committed is True
        assert stored_turn.assistant_response == "Turn remains durable"
        context_event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.idempotency_key == "context:snapshot-processing-failure"
            )
        )
        assert context_event is None


def test_stop_context_telemetry_filters_before_applying_the_hundred_item_cap():
    base = {
        "operation": "context.turn_injection",
        "scope": "automatic",
        "client": "codex",
        "characters": 4,
        "utf8_bytes": 4,
        "estimated_tokens": 1,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {"overhead": 4},
    }
    observations = [{**base, "event_id": "bad/id"}]
    observations.extend({**base, "event_id": f"event-{index}"} for index in range(101))
    payload = StopCheck(context_observations=observations)
    assert len(payload.context_observations) == 100
    assert payload.context_observations[0].event_id == "event-0"
    assert payload.context_observations[-1].event_id == "event-99"


def test_utf8_estimator_is_explicit_and_deterministic():
    assert estimated_tokens_for_text("") == (0, 0, 0)
    assert estimated_tokens_for_text("caffè") == (2, 5, 6)
    with pytest.raises(ValueError, match="non-negative"):
        estimated_tokens_for_bytes(-1)


def test_timeline_normalizes_naive_and_aware_datetimes():
    rows = [
        Observation(
            project_id="project",
            idempotency_key="naive",
            category="context",
            operation="context.session_start",
            occurred_at=datetime(2026, 8, 25, 10),
        ).to_model(),
        Observation(
            project_id="project",
            idempotency_key="aware",
            category="context",
            operation="context.session_start",
            occurred_at=datetime(2026, 8, 25, 11, tzinfo=timezone.utc),
        ).to_model(),
    ]
    timeline = _timeline(rows, "day", ZoneInfo("UTC"), "context")
    assert len(timeline) == 1
    assert timeline[0]["start"] == "2026-08-25T00:00:00+00:00"
    assert timeline[0]["requests"] == 2


def test_observability_pricing_and_details_are_intentionally_constrained():
    cost, price, version = embedding_price("text-embedding-3-large", 1_000_000)
    assert cost == Decimal("0.13")
    assert price == Decimal("0.13")
    assert version == EMBEDDING_PRICING_VERSION
    assert embedding_price("unknown", 100) == (None, None, None)
    assert safe_details(
        {
            "candidate_count": 4,
            "cost_kind": "subscription_not_attributable",
            "attempts": [1, 2, 3],
            "phase": "topic_segmentation",
            "raw_error": "contains free-form content",
            "x" * 81: 1,
        }
    ) == {
        "candidate_count": 4,
        "cost_kind": "subscription_not_attributable",
        "attempts": [1, 2, 3],
    }


def test_observability_date_window_rejects_invalid_inputs():
    start, end, bucket, display_timezone = date_window("7d", "Europe/Rome")
    assert start is not None and start < end
    assert bucket == "day"
    assert display_timezone.key == "Europe/Rome"
    with pytest.raises(ValueError, match="range"):
        date_window("forever", "UTC")
    with pytest.raises(ValueError, match="IANA"):
        date_window("7d", "Not/A-Timezone")


async def test_observability_nonnegative_constraints_reject_invalid_measurements(
    api_client, db_factory
):
    client, _ = api_client
    project = await create_project(client)
    async with db_factory() as db:
        persisted = await record(
            db,
            Observation(
                project_id=project["id"],
                idempotency_key="negative-duration",
                category="retrieval",
                operation="retrieval.pipeline",
                duration_ms=-1,
            ),
        )
        await db.commit()
        assert not persisted
        assert await db.scalar(select(ObservabilityEvent)) is None


async def test_observability_record_swallows_generic_database_errors():
    class FailingDatabase:
        def begin_nested(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def add(self, value):
            assert isinstance(value, ObservabilityEvent)

        async def flush(self):
            raise SQLAlchemyError("database unavailable")

    persisted = await record(
        FailingDatabase(),
        Observation(
            project_id="project",
            idempotency_key="database-error",
            category="retrieval",
            operation="retrieval.pipeline",
        ),
    )
    assert persisted is False
