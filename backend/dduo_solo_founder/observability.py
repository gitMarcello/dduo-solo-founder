"""Project-local usage telemetry and exact delivered-context snapshots."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.models import ContextEventPayload, ObservabilityEvent
from dduo_solo_founder.pricing import (
    API_PRICING_VERSION,
    EMBEDDING_PRICE_PER_MILLION as _EMBEDDING_PRICE_PER_MILLION,
    EMBEDDING_PRICING_VERSION as _EMBEDDING_PRICING_VERSION,
    PICO_USD,
    api_equivalent_cost,
    embedding_price as _embedding_price,
)

ESTIMATOR_VERSION = "utf8_bytes_div_4_v1"
# Backward-compatible exports for callers that already import pricing from the
# observability module.  The canonical immutable snapshot lives in pricing.py.
EMBEDDING_PRICING_VERSION = _EMBEDDING_PRICING_VERSION
EMBEDDING_PRICE_PER_MILLION = _EMBEDDING_PRICE_PER_MILLION
APPROVED_DETAIL_ENUMS = frozenset(
    {
        "assistant_response",
        "compaction_post",
        "compaction_pre",
        "index_failed",
        "memory.upsert",
        "task.upsert",
        "exact_id",
        "exact_title",
        "semantic",
        "lexical_fallback",
        "task_index_unavailable",
        "stale_hit",
        "semantic_commit",
        "subscription_not_attributable",
        "user_prompt",
        "vector_operation_failed",
        "within_budget",
        "budgeted",
        "fallback",
        "inline_expected",
        "api_equivalent",
        "snapshot",
        "delta",
        "startup",
        "resume",
        "clear",
        "compact",
        "session_start",
        "session_unknown",
        "state_missing",
        "foundation_changed",
        "work_changed",
        "foundation_and_work_changed",
        "unchanged",
        "remote_offline",
        "memory_unavailable",
        "composition_failed",
        "client_upgrade_required",
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def estimated_tokens_for_text(value: str) -> tuple[int, int, int]:
    """Return an explicit, tokenizer-independent estimate plus exact sizes."""
    characters = len(value)
    utf8_bytes = len(value.encode("utf-8"))
    return estimated_tokens_for_bytes(utf8_bytes), characters, utf8_bytes


def estimated_tokens_for_bytes(utf8_bytes: int) -> int:
    """Apply the canonical server-side estimator to an exact UTF-8 byte count."""
    if utf8_bytes < 0:
        raise ValueError("utf8_bytes must be non-negative")
    return math.ceil(utf8_bytes / 4)


def embedding_price(
    model: str | None, input_tokens: int | None
) -> tuple[Decimal | None, Decimal | None, str | None]:
    return _embedding_price(model, input_tokens)


def safe_details(value: dict[str, Any] | None) -> dict[str, Any]:
    """Allow only small numeric/enumerated diagnostics; never persist content."""
    if not value:
        return {}
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(item, bool | int | float):
            safe[key] = item
        elif isinstance(item, str) and item in APPROVED_DETAIL_ENUMS:
            safe[key] = item
        elif (
            isinstance(item, list)
            and len(item) <= 20
            and all(isinstance(entry, (int, float, bool)) for entry in item)
        ):
            safe[key] = item
    return safe


@dataclass(slots=True)
class Observation:
    project_id: str
    idempotency_key: str
    category: str
    operation: str
    scope: str = ""
    status: str = "success"
    provider: str | None = None
    model: str | None = None
    attempt: int = 1
    measurement_source: str = "unavailable"
    session_id: str | None = None
    turn_id: str | None = None
    retrieval_run_id: str | None = None
    sleep_job_id: str | None = None
    outbox_event_id: str | None = None
    actor_member_id: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_total_tokens: int | None = None
    characters: int | None = None
    utf8_bytes: int | None = None
    duration_ms: int | None = None
    provider_duration_ms: int | None = None
    vector_store_duration_ms: int | None = None
    request_count: int | None = None
    item_count: int | None = None
    candidate_count: int | None = None
    selected_count: int | None = None
    dropped_count: int | None = None
    cost_usd: Decimal | None = None
    unit_price_usd_per_million: Decimal | None = None
    pricing_version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utcnow)

    def to_model(self) -> ObservabilityEvent:
        actor_member_id = self.actor_member_id
        if actor_member_id is None:
            # Import lazily to keep the telemetry core independent from HTTP auth.
            from dduo_solo_founder.team import request_principal

            principal = request_principal()
            actor_member_id = principal.member_id if principal else None
        return ObservabilityEvent(
            project_id=self.project_id,
            idempotency_key=self.idempotency_key,
            category=self.category,
            operation=self.operation,
            scope=self.scope,
            status=self.status,
            provider=self.provider,
            model=self.model,
            attempt=max(1, self.attempt),
            measurement_source=self.measurement_source,
            session_id=self.session_id,
            turn_id=self.turn_id,
            retrieval_run_id=self.retrieval_run_id,
            sleep_job_id=self.sleep_job_id,
            outbox_event_id=self.outbox_event_id,
            actor_member_id=actor_member_id,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            reported_total_tokens=self.reported_total_tokens,
            characters=self.characters,
            utf8_bytes=self.utf8_bytes,
            duration_ms=self.duration_ms,
            provider_duration_ms=self.provider_duration_ms,
            vector_store_duration_ms=self.vector_store_duration_ms,
            request_count=self.request_count,
            item_count=self.item_count,
            candidate_count=self.candidate_count,
            selected_count=self.selected_count,
            dropped_count=self.dropped_count,
            cost_usd=self.cost_usd,
            unit_price_usd_per_million=self.unit_price_usd_per_million,
            pricing_version=self.pricing_version,
            details=safe_details(self.details),
            occurred_at=self.occurred_at,
        )


@dataclass(slots=True)
class ContextSnapshot:
    content: str
    content_sha256: str
    producer_version: str
    render_version: str
    estimator_version: str
    captured_at: datetime
    components: list[dict[str, Any]] = field(default_factory=list)
    tool_name: str | None = None

    def to_model(self, observability_event_id: str) -> ContextEventPayload:
        return ContextEventPayload(
            observability_event_id=observability_event_id,
            content=self.content,
            content_sha256=self.content_sha256,
            producer_version=self.producer_version,
            render_version=self.render_version,
            estimator_version=self.estimator_version,
            captured_at=self.captured_at,
            components=self.components,
            tool_name=self.tool_name,
        )


ContextRecordStatus = Literal["created", "duplicate", "conflict", "failed"]


async def _reconcile_context_snapshot(
    db: AsyncSession,
    event: ObservabilityEvent,
    snapshot: ContextSnapshot | None,
) -> ContextRecordStatus:
    if snapshot is None:
        return "duplicate"
    existing_hash = await db.scalar(
        select(ContextEventPayload.content_sha256).where(
            ContextEventPayload.observability_event_id == event.id
        )
    )
    if existing_hash is not None:
        return "duplicate" if existing_hash == snapshot.content_sha256 else "conflict"
    try:
        async with db.begin_nested():
            db.add(snapshot.to_model(event.id))
            await db.flush()
        return "created"
    except IntegrityError:
        existing_hash = await db.scalar(
            select(ContextEventPayload.content_sha256).where(
                ContextEventPayload.observability_event_id == event.id
            )
        )
        if existing_hash is None:
            return "failed"
        return "duplicate" if existing_hash == snapshot.content_sha256 else "conflict"
    except SQLAlchemyError:
        return "failed"


async def record_context(
    db: AsyncSession,
    observation: Observation,
    snapshot: ContextSnapshot | None,
) -> ContextRecordStatus:
    """Persist one numeric event and optional exact payload atomically and idempotently."""
    existing = await db.scalar(
        select(ObservabilityEvent).where(
            ObservabilityEvent.project_id == observation.project_id,
            ObservabilityEvent.idempotency_key == observation.idempotency_key,
        )
    )
    if existing is not None:
        return await _reconcile_context_snapshot(db, existing, snapshot)

    event = observation.to_model()
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
            if snapshot is not None:
                db.add(snapshot.to_model(event.id))
                await db.flush()
        return "created"
    except IntegrityError:
        existing = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.project_id == observation.project_id,
                ObservabilityEvent.idempotency_key == observation.idempotency_key,
            )
        )
        if existing is None:
            return "failed"
        return await _reconcile_context_snapshot(db, existing, snapshot)
    except SQLAlchemyError:
        return "failed"


async def record(db: AsyncSession, observation: Observation) -> bool:
    """Best-effort idempotent persistence that cannot poison the caller transaction."""
    try:
        async with db.begin_nested():
            db.add(observation.to_model())
            await db.flush()
        return True
    except IntegrityError:
        return False
    except SQLAlchemyError:
        return False


async def record_many(db: AsyncSession, observations: list[Observation]) -> int:
    persisted = 0
    for observation in observations:
        persisted += int(await record(db, observation))
    return persisted


def date_window(
    range_name: str, timezone_name: str
) -> tuple[datetime | None, datetime, str, ZoneInfo]:
    if range_name not in {"24h", "7d", "30d", "all"}:
        raise ValueError("range must be 24h, 7d, 30d, or all")
    try:
        display_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    end = utcnow()
    offsets = {"24h": 24 * 3600, "7d": 7 * 24 * 3600, "30d": 30 * 24 * 3600}
    start = (
        datetime.fromtimestamp(end.timestamp() - offsets[range_name], tz=timezone.utc)
        if range_name != "all"
        else None
    )
    bucket = "hour" if range_name == "24h" else "day" if range_name in {"7d", "30d"} else "month"
    return start, end, bucket, display_timezone


def _sum(rows: list[ObservabilityEvent], attribute: str, source: str | None = None) -> int | None:
    eligible = [row for row in rows if source is None or row.measurement_source == source]
    if not eligible:
        return None
    values = [getattr(row, attribute) for row in eligible]
    # A partial provider sum looks exact and is therefore worse than an honest
    # unavailable value. Pricing has its own explicit priced/unavailable
    # coverage, while token/context totals remain all-or-nothing per slice.
    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values)


def _decimal_sum(rows: list[ObservabilityEvent], attribute: str) -> Decimal | None:
    values = [
        Decimal(value) for row in rows for value in [getattr(row, attribute)] if value is not None
    ]
    return sum(values, Decimal(0)) if values else None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _coverage(rows: list[ObservabilityEvent]) -> dict[str, int]:
    return {
        "reported": sum(item.measurement_source == "provider_reported" for item in rows),
        "estimated": sum(item.measurement_source == "local_estimate" for item in rows),
        "unavailable": sum(item.measurement_source == "unavailable" for item in rows),
    }


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    index = round((len(values) - 1) * percentile)
    return values[index]


def _request_total(rows: list[ObservabilityEvent]) -> int:
    # Pre-alpha.44 provider events can have no explicit count; one event was
    # one request under that contract. An explicit zero always means that no
    # provider call happened (for example, a payload-only Qdrant refresh).
    return sum(1 if item.request_count is None else int(item.request_count) for item in rows)


def _by_operation(
    rows: list[ObservabilityEvent], *, recorded_request_count: bool = False
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ObservabilityEvent]] = {}
    for row in rows:
        grouped.setdefault(row.operation, []).append(row)
    return [
        {
            "operation": operation,
            "requests": _request_total(items) if recorded_request_count else len(items),
            "successes": sum(item.status == "success" for item in items),
            "failures": sum(
                item.status in {"failed", "partial_failure", "degraded"} for item in items
            ),
            "input_tokens_reported": _sum(items, "input_tokens", "provider_reported"),
            "input_tokens_estimated": _sum(items, "input_tokens", "local_estimate"),
            "cost_usd": _decimal_text(_decimal_sum(items, "cost_usd")),
            "coverage": _coverage(items),
        }
        for operation, items in sorted(grouped.items())
    ]


def _pricing_coverage(rows: list[ObservabilityEvent]) -> dict[str, int]:
    return {
        "priced": sum(item.cost_usd is not None for item in rows),
        "unavailable": sum(item.cost_usd is None for item in rows),
    }


_COST_COMPONENT_DETAIL_KEYS = {
    "uncached_input_usd": "uncached_input_cost_pico_usd",
    "cached_input_usd": "cached_input_cost_pico_usd",
    "cache_write_input_usd": "cache_write_input_cost_pico_usd",
    "output_usd": "output_cost_pico_usd",
}


def _event_cost_components(row: ObservabilityEvent) -> dict[str, Decimal] | None:
    details = row.details or {}
    persisted: dict[str, Decimal] = {}
    for output_key, detail_key in _COST_COMPONENT_DETAIL_KEYS.items():
        value = details.get(detail_key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            persisted = {}
            break
        persisted[output_key] = Decimal(value) / PICO_USD
    if len(persisted) == len(_COST_COMPONENT_DETAIL_KEYS):
        return persisted

    # beta.2 persisted the immutable catalog version and total but not its
    # components. Reconstruct only against that exact snapshot and only when
    # it reproduces the stored amount; unknown/client-derived prices stay
    # intentionally unallocated.
    if row.cost_usd is None or row.pricing_version != API_PRICING_VERSION:
        return None
    calculated = api_equivalent_cost(
        provider=row.provider,
        model=row.model,
        input_tokens=row.input_tokens,
        cached_input_tokens=row.cached_input_tokens,
        cache_write_input_tokens=row.cache_write_input_tokens,
        output_tokens=row.output_tokens,
        reasoning_tokens=row.reasoning_tokens,
        occurred_at=row.occurred_at,
    )
    if calculated is None or calculated.pricing_version != row.pricing_version:
        return None
    precision = Decimal("0.000000000001")
    if calculated.cost_usd.quantize(precision) != Decimal(row.cost_usd).quantize(precision):
        return None
    return {
        "uncached_input_usd": calculated.uncached_input_cost_usd,
        "cached_input_usd": calculated.cached_input_cost_usd,
        "cache_write_input_usd": calculated.cache_write_input_cost_usd,
        "output_usd": calculated.output_cost_usd,
    }


def _cost_breakdown(rows: list[ObservabilityEvent]) -> dict[str, Any]:
    totals = {key: Decimal(0) for key in _COST_COMPONENT_DETAIL_KEYS}
    priced = 0
    unavailable = 0
    for row in rows:
        components = _event_cost_components(row)
        if components is None:
            unavailable += 1
            continue
        priced += 1
        for key, value in components.items():
            totals[key] += value
    precision = Decimal("0.000000000001")
    return {
        key: _decimal_text(value.quantize(precision)) if priced else None
        for key, value in totals.items()
    } | {"priced": priced, "unavailable": unavailable}


def _uncached_input_tokens(rows: list[ObservabilityEvent]) -> int | None:
    values: list[int] = []
    measured = [row for row in rows if row.measurement_source == "provider_reported"]
    if not measured:
        return None
    for row in measured:
        if row.input_tokens is None:
            return None
        if row.provider in {"codex", "openai"}:
            if row.cached_input_tokens is None or row.cache_write_input_tokens is None:
                return None
            value = row.input_tokens - row.cached_input_tokens - row.cache_write_input_tokens
            if value < 0:
                return None
            values.append(value)
        elif row.provider in {"claude", "anthropic"}:
            values.append(row.input_tokens)
        else:
            return None
    return sum(values)


def _cache_hit_percent(rows: list[ObservabilityEvent]) -> float | None:
    cached_total = 0
    eligible_total = 0
    measured = [row for row in rows if row.measurement_source == "provider_reported"]
    if not measured:
        return None
    for row in measured:
        if row.input_tokens is None or row.cached_input_tokens is None:
            return None
        if row.provider in {"codex", "openai"}:
            denominator = row.input_tokens
        elif row.provider in {"claude", "anthropic"}:
            if row.cache_write_input_tokens is None:
                return None
            denominator = (
                row.input_tokens + row.cached_input_tokens + row.cache_write_input_tokens
            )
        else:
            return None
        if denominator < 0 or row.cached_input_tokens > denominator:
            return None
        cached_total += row.cached_input_tokens
        eligible_total += denominator
    if eligible_total == 0:
        return None
    return round(cached_total * 100 / eligible_total, 2)


def _by_provider_model(rows: list[ObservabilityEvent]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None], list[ObservabilityEvent]] = {}
    for row in rows:
        grouped.setdefault((row.provider, row.model), []).append(row)
    return [
        {
            "provider": provider,
            "model": model,
            **_equivalent_metric_block(items),
            "equivalent_api_cost_usd": _decimal_text(_decimal_sum(items, "cost_usd")),
            "pricing_coverage": _pricing_coverage(items),
        }
        for (provider, model), items in sorted(
            grouped.items(), key=lambda item: (item[0][0] or "", item[0][1] or "")
        )
    ]


def _timeline(
    rows: list[ObservabilityEvent],
    bucket: str,
    display_timezone: ZoneInfo,
    category: str,
    *,
    recorded_request_count: bool = False,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[ObservabilityEvent]] = {}
    for row in rows:
        timestamp = row.occurred_at
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        local = timestamp.astimezone(display_timezone)
        if bucket == "hour":
            key = local.replace(minute=0, second=0, microsecond=0).isoformat()
        elif bucket == "day":
            key = local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        else:
            key = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        buckets.setdefault(key, []).append(row)
    timeline = []
    for start, items in sorted(buckets.items()):
        entry: dict[str, Any] = {
            "start": start,
            "requests": _request_total(items) if recorded_request_count else len(items),
            "input_tokens_reported": _sum(items, "input_tokens", "provider_reported"),
            "input_tokens_estimated": _sum(items, "input_tokens", "local_estimate"),
            "output_tokens_reported": _sum(items, "output_tokens", "provider_reported"),
            "output_tokens_estimated": _sum(items, "output_tokens", "local_estimate"),
            "coverage": _coverage(items),
        }
        if category == "context":
            automatic = [item for item in items if item.scope == "automatic"]
            requested = [item for item in items if item.scope == "requested"]
            entry.update(
                {
                    "automatic_estimated_tokens": _sum(automatic, "input_tokens"),
                    "requested_estimated_tokens": _sum(requested, "input_tokens"),
                }
            )
        if category == "embedding":
            entry["cost_usd"] = _decimal_text(_decimal_sum(items, "cost_usd"))
        elif category in {"agent_usage", "sleep"}:
            entry["equivalent_api_cost_usd"] = _decimal_text(_decimal_sum(items, "cost_usd"))
            entry["pricing_coverage"] = _pricing_coverage(items)
        timeline.append(entry)
    return timeline


def _metric_block(
    rows: list[ObservabilityEvent], *, recorded_request_count: bool = False
) -> dict[str, Any]:
    durations = [int(item.duration_ms) for item in rows if item.duration_ms is not None]
    return {
        "requests": _request_total(rows) if recorded_request_count else len(rows),
        "successes": sum(item.status == "success" for item in rows),
        "failures": sum(item.status in {"failed", "partial_failure", "degraded"} for item in rows),
        "input_tokens_reported": _sum(rows, "input_tokens", "provider_reported"),
        "input_tokens_estimated": _sum(rows, "input_tokens", "local_estimate"),
        "cached_input_tokens_reported": _sum(rows, "cached_input_tokens", "provider_reported"),
        "uncached_input_tokens_reported": _uncached_input_tokens(rows),
        "cache_write_input_tokens_reported": _sum(
            rows, "cache_write_input_tokens", "provider_reported"
        ),
        "cache_hit_percent": _cache_hit_percent(rows),
        "output_tokens_reported": _sum(rows, "output_tokens", "provider_reported"),
        "output_tokens_estimated": _sum(rows, "output_tokens", "local_estimate"),
        "duration_p50_ms": _percentile(durations, 0.5),
        "duration_p95_ms": _percentile(durations, 0.95),
        "coverage": _coverage(rows),
        "by_operation": _by_operation(rows, recorded_request_count=recorded_request_count),
    }


def _equivalent_metric_block(rows: list[ObservabilityEvent]) -> dict[str, Any]:
    block = _metric_block(rows)
    block["equivalent_api_cost_breakdown"] = _cost_breakdown(rows)
    for operation in block["by_operation"]:
        operation["equivalent_api_cost_usd"] = operation.pop("cost_usd")
    return block


def _component_bytes(rows: list[ObservabilityEvent]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        for key, value in (row.details or {}).items():
            if not key.startswith("component_") or isinstance(value, bool | str):
                continue
            if isinstance(value, int | float) and value >= 0:
                name = key.removeprefix("component_")
                totals[name] = totals.get(name, 0) + int(value)
    return totals


def _detail_values(rows: list[ObservabilityEvent], key: str) -> list[int]:
    values: list[int] = []
    for row in rows:
        value = (row.details or {}).get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            continue
        values.append(int(value))
    return values


def _detail_sum(rows: list[ObservabilityEvent], key: str) -> int | None:
    values = _detail_values(rows, key)
    return sum(values) if values else None


def _context_budget_summary(rows: list[ObservabilityEvent]) -> dict[str, Any]:
    measured = [
        row
        for row in rows
        if isinstance((row.details or {}).get("budget_limit_characters"), int | float)
        and not isinstance((row.details or {}).get("budget_limit_characters"), bool)
    ]
    utilization = [
        round(
            1000
            * int(row.details["client_character_units"])
            / int(row.details["budget_limit_characters"])
        )
        for row in measured
        if isinstance(row.details.get("client_character_units"), int | float)
        and not isinstance(row.details.get("client_character_units"), bool)
        and int(row.details["budget_limit_characters"]) > 0
    ]
    limits = {
        int(row.details["budget_limit_characters"])
        for row in measured
        if int(row.details["budget_limit_characters"]) > 0
    }
    return {
        "measured_injections": len(measured),
        "budget_limit_characters": next(iter(limits)) if len(limits) == 1 else None,
        "budgeted_injections": sum(
            (row.details or {}).get("budget_outcome") == "budgeted" for row in measured
        ),
        "fallback_injections": sum(
            (row.details or {}).get("budget_outcome") == "fallback" for row in measured
        ),
        "inline_expected_injections": sum(
            (row.details or {}).get("delivery_expectation") == "inline_expected" for row in measured
        ),
        "candidate_characters": _detail_sum(measured, "candidate_characters"),
        "candidate_utf8_bytes": _detail_sum(measured, "candidate_utf8_bytes"),
        "candidate_estimated_tokens": _detail_sum(measured, "candidate_estimated_tokens"),
        "avoided_characters": _detail_sum(measured, "avoided_characters"),
        "avoided_utf8_bytes": _detail_sum(measured, "avoided_utf8_bytes"),
        "avoided_estimated_tokens": _detail_sum(measured, "avoided_estimated_tokens"),
        "included_items": _detail_sum(measured, "included_items"),
        "partial_items": _detail_sum(measured, "partial_items"),
        "omitted_items": _detail_sum(measured, "omitted_items"),
        # Basis points preserve deterministic integer aggregation. The API exposes
        # percentages with one decimal place to avoid false precision.
        "budget_utilization_p50_percent": (
            _percentile(utilization, 0.5) / 10 if utilization else None
        ),
        "budget_utilization_p95_percent": (
            _percentile(utilization, 0.95) / 10 if utilization else None
        ),
    }


def _context_delivery_summary(rows: list[ObservabilityEvent]) -> dict[str, Any]:
    measured = [
        row
        for row in rows
        if (row.details or {}).get("delivery_kind") in {"snapshot", "delta", "fallback"}
    ]
    return {
        "measured_injections": len(measured),
        "snapshot_injections": sum(
            (row.details or {}).get("delivery_kind") == "snapshot" for row in measured
        ),
        "delta_injections": sum(
            (row.details or {}).get("delivery_kind") == "delta" for row in measured
        ),
        "fallback_injections": sum(
            (row.details or {}).get("delivery_kind") == "fallback" for row in measured
        ),
        "unknown_injections": max(len(rows) - len(measured), 0),
        "reused_characters": _detail_sum(measured, "reused_characters"),
        "reused_utf8_bytes": _detail_sum(measured, "reused_utf8_bytes"),
        "reused_estimated_tokens": _detail_sum(measured, "reused_estimated_tokens"),
    }


def _reliability_block(rows: list[ObservabilityEvent]) -> dict[str, Any]:
    """Keep reliability operational; usage remains separated by model boundary."""
    block = _metric_block(rows)
    for metric_name in (
        "input_tokens_reported",
        "input_tokens_estimated",
        "cached_input_tokens_reported",
        "uncached_input_tokens_reported",
        "cache_write_input_tokens_reported",
        "cache_hit_percent",
        "output_tokens_reported",
        "output_tokens_estimated",
    ):
        block[metric_name] = None
    return block


async def build_summary(
    db: AsyncSession,
    project_id: str,
    *,
    range_name: str,
    timezone_name: str,
    actor_member_id: str | None = None,
    actor_is_system: bool = False,
) -> dict[str, Any]:
    start, end, bucket, display_timezone = date_window(range_name, timezone_name)
    statement = select(ObservabilityEvent).where(
        ObservabilityEvent.project_id == project_id,
        ObservabilityEvent.occurred_at <= end,
        ObservabilityEvent.category != "usage_guard",
    )
    if start:
        statement = statement.where(ObservabilityEvent.occurred_at >= start)
    if actor_is_system:
        statement = statement.where(ObservabilityEvent.actor_member_id.is_(None))
    elif actor_member_id:
        statement = statement.where(ObservabilityEvent.actor_member_id == actor_member_id)
    rows = list((await db.scalars(statement.order_by(ObservabilityEvent.occurred_at))).all())
    context_rows = [item for item in rows if item.category == "context"]
    agent_rows = [item for item in rows if item.category == "agent_usage"]
    sleep_rows = [item for item in rows if item.category == "sleep_model"]
    embedding_rows = [item for item in rows if item.category == "embedding"]
    # Payload-only Qdrant maintenance remains visible in reliability/recent
    # operations, but it is not an Embedding API request and has no provider
    # measurement coverage. `request_count` is the authoritative boundary.
    embedding_provider_rows = [item for item in embedding_rows if item.request_count != 0]
    retrieval_rows = [item for item in rows if item.category == "retrieval"]
    automatic_context = [item for item in context_rows if item.scope == "automatic"]
    requested_context = [item for item in context_rows if item.scope == "requested"]
    coverage_statement = (
        select(ObservabilityEvent.occurred_at)
        .where(
            ObservabilityEvent.project_id == project_id,
            ObservabilityEvent.category != "usage_guard",
        )
        .order_by(ObservabilityEvent.occurred_at)
        .limit(1)
    )
    if actor_is_system:
        coverage_statement = coverage_statement.where(ObservabilityEvent.actor_member_id.is_(None))
    elif actor_member_id:
        coverage_statement = coverage_statement.where(
            ObservabilityEvent.actor_member_id == actor_member_id
        )
    collection_started_at = await db.scalar(coverage_statement)
    return {
        "period": {
            "range": range_name,
            "from": start.isoformat() if start else None,
            "to": end.isoformat(),
            "bucket": bucket,
            "timezone": timezone_name,
        },
        "coverage": {
            "collection_started_at": collection_started_at.isoformat()
            if collection_started_at
            else None,
            "events": len(rows),
            **_coverage(rows),
        },
        "context": {
            "coverage": _coverage(context_rows),
            "automatic": {
                "injections": len(automatic_context),
                "characters": _sum(automatic_context, "characters"),
                "utf8_bytes": _sum(automatic_context, "utf8_bytes"),
                "estimated_tokens": _sum(automatic_context, "input_tokens"),
                "estimated_tokens_p50": _percentile(
                    [
                        int(item.input_tokens)
                        for item in automatic_context
                        if item.input_tokens is not None
                    ],
                    0.5,
                ),
                "estimated_tokens_p95": _percentile(
                    [
                        int(item.input_tokens)
                        for item in automatic_context
                        if item.input_tokens is not None
                    ],
                    0.95,
                ),
                "component_bytes": _component_bytes(automatic_context),
                "budget": _context_budget_summary(automatic_context),
                "delivery": _context_delivery_summary(automatic_context),
                "coverage": _coverage(automatic_context),
            },
            "requested": {
                "results": len(requested_context),
                "characters": _sum(requested_context, "characters"),
                "utf8_bytes": _sum(requested_context, "utf8_bytes"),
                "estimated_tokens": _sum(requested_context, "input_tokens"),
                "coverage": _coverage(requested_context),
            },
            "by_operation": _by_operation(context_rows),
            "timeline": _timeline(context_rows, bucket, display_timezone, "context"),
        },
        "agent_usage": {
            **_equivalent_metric_block(agent_rows),
            "equivalent_api_cost_usd": _decimal_text(_decimal_sum(agent_rows, "cost_usd")),
            "pricing_coverage": _pricing_coverage(agent_rows),
            "by_provider_model": _by_provider_model(agent_rows),
            "timeline": _timeline(agent_rows, bucket, display_timezone, "agent_usage"),
        },
        "sleep": {
            **_equivalent_metric_block(sleep_rows),
            "equivalent_api_cost_usd": _decimal_text(_decimal_sum(sleep_rows, "cost_usd")),
            "pricing_coverage": _pricing_coverage(sleep_rows),
            "by_provider_model": _by_provider_model(sleep_rows),
            "timeline": _timeline(sleep_rows, bucket, display_timezone, "sleep"),
        },
        "embeddings": {
            **_metric_block(embedding_provider_rows, recorded_request_count=True),
            "cost_usd": _decimal_text(_decimal_sum(embedding_provider_rows, "cost_usd")),
            "timeline": _timeline(
                embedding_provider_rows,
                bucket,
                display_timezone,
                "embedding",
                recorded_request_count=True,
            ),
        },
        "reliability": {
            **_reliability_block(rows),
            "retrieval": _metric_block(retrieval_rows),
        },
    }


def serialize_event(
    event: ObservabilityEvent,
    payload: ContextEventPayload | None = None,
    *,
    has_content: bool | None = None,
) -> dict[str, Any]:
    component_breakdown = {
        key.removeprefix("component_"): int(value)
        for key, value in (event.details or {}).items()
        if key.startswith("component_")
        and not isinstance(value, bool | str)
        and isinstance(value, int | float)
    }
    return {
        "id": event.id,
        "project_id": event.project_id,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "retrieval_run_id": event.retrieval_run_id,
        "sleep_job_id": event.sleep_job_id,
        "outbox_event_id": event.outbox_event_id,
        "actor_member_id": event.actor_member_id,
        "category": event.category,
        "operation": event.operation,
        "scope": event.scope,
        "status": event.status,
        "provider": event.provider,
        "model": event.model,
        "attempt": event.attempt,
        "measurement_source": event.measurement_source,
        "input_tokens": event.input_tokens,
        "cached_input_tokens": event.cached_input_tokens,
        "cache_write_input_tokens": event.cache_write_input_tokens,
        "output_tokens": event.output_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "reported_total_tokens": event.reported_total_tokens,
        "characters": event.characters,
        "utf8_bytes": event.utf8_bytes,
        "duration_ms": event.duration_ms,
        "provider_duration_ms": event.provider_duration_ms,
        "vector_store_duration_ms": event.vector_store_duration_ms,
        "request_count": event.request_count,
        "item_count": event.item_count,
        "candidate_count": event.candidate_count,
        "selected_count": event.selected_count,
        "dropped_count": event.dropped_count,
        "cost_usd": str(event.cost_usd) if event.cost_usd is not None else None,
        "pricing_version": event.pricing_version,
        "details": event.details,
        "component_breakdown": component_breakdown,
        "has_content": payload is not None if has_content is None else has_content,
        "tool_name": payload.tool_name if payload is not None else None,
        "occurred_at": event.occurred_at.isoformat(),
    }
