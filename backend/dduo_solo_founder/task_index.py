"""Rebuildable semantic projection for project tasks.

PostgreSQL remains authoritative. Qdrant contains only a deterministic search
document and metadata needed to locate a current Task row; callers must always
hydrate search hits from PostgreSQL before returning project content.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from typing import Literal

from sqlalchemy import and_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.embeddings import (
    EmbeddingService,
    VectorSearchResult,
    VectorUpsertResult,
    embedding_service,
)
from dduo_solo_founder.models import OutboxEvent, Project, Task

TASK_INDEX_RENDER_VERSION = "task_semantic_v2"
MIN_TASK_DOCUMENT_CHARACTERS = 512


async def lock_task_index_project(db: AsyncSession, project_id: str) -> None:
    """Serialize destructive reset and one-time full reconciliation."""
    if db.get_bind().dialect.name != "postgresql":
        return
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:task_index_project_key, 1))"
        ),
        {"task_index_project_key": f"task-index-project:{project_id}"},
    )


async def advance_task_index_generation(db: AsyncSession, project_id: str) -> None:
    """Atomically invalidate in-flight semantic snapshots in the caller transaction."""
    result = await db.execute(
        update(Project)
        .where(Project.id == project_id)
        .values(task_index_generation=Project.task_index_generation + 1)
    )
    if result.rowcount != 1:
        raise LookupError("project not found")


async def current_task_index_generation(db: AsyncSession, project_id: str) -> int:
    value = await db.scalar(
        select(Project.task_index_generation).where(Project.id == project_id)
    )
    if value is None:
        raise LookupError("project not found")
    return int(value)


def pending_task_projection_ids(project_id: str):
    """Select only events that still represent the authoritative task version."""
    return (
        select(OutboxEvent.aggregate_id)
        .join(
            Task,
            and_(
                Task.id == OutboxEvent.aggregate_id,
                Task.project_id == OutboxEvent.project_id,
            ),
        )
        .where(
            OutboxEvent.project_id == project_id,
            OutboxEvent.event_type == "task.upsert",
            OutboxEvent.status.in_(["pending", "failed"]),
            OutboxEvent.payload["task_version"].as_integer() == Task.version,
        )
    )


@dataclass(frozen=True, slots=True)
class TaskDocument:
    text: str
    semantic_hash: str
    render_version: str
    truncated: bool
    source_characters: int
    source_utf8_bytes: int


TaskIndexStatus = Literal[
    "indexed",
    "payload_updated",
    "current",
    "stale",
]


@dataclass(slots=True)
class TaskIndexOutcome:
    status: TaskIndexStatus
    source_version: int
    document: TaskDocument | None = None
    observed_result: VectorUpsertResult | None = None
    vector_store_duration_ms: int | None = None


def _normalized(value: object | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _labels(value: object | None) -> str:
    if not isinstance(value, list | tuple | set):
        return ""
    # Label ordering is not semantic. Sorting avoids a paid re-embedding when
    # the same set is written in a different order. The original value is the
    # tie-breaker because distinct labels can share the same case-folded form.
    normalized = {_normalized(item) for item in value}
    return ", ".join(
        sorted((item for item in normalized if item), key=lambda item: (item.casefold(), item))
    )


def _truncate_middle(value: str, budget: int) -> str:
    if len(value) <= budget:
        return value
    if budget <= 1:
        return "…"[:budget]
    head = (budget - 1 + 1) // 2
    tail = budget - 1 - head
    return f"{value[:head]}…{value[-tail:] if tail else ''}"


def _truncate_utf8_middle(value: str, budget: int) -> str:
    """Bound a string by encoded bytes without splitting Unicode code points."""
    encoded = value.encode("utf-8")
    if len(encoded) <= budget:
        return value
    marker = "…"
    marker_bytes = len(marker.encode("utf-8"))
    if budget <= marker_bytes:
        return encoded[:budget].decode("utf-8", errors="ignore")
    remaining = budget - marker_bytes
    head_budget = (remaining * 2) // 3
    tail_budget = remaining - head_budget
    head = encoded[:head_budget].decode("utf-8", errors="ignore")
    tail = encoded[-tail_budget:].decode("utf-8", errors="ignore") if tail_budget else ""
    return f"{head}{marker}{tail}"


def render_task_document(
    task: Task,
    *,
    max_characters: int = 16_000,
    max_utf8_bytes: int = 7_500,
) -> TaskDocument:
    """Render the five approved semantic fields within character and byte budgets."""
    if max_characters < MIN_TASK_DOCUMENT_CHARACTERS:
        raise ValueError(f"max_characters must be at least {MIN_TASK_DOCUMENT_CHARACTERS}")
    if max_utf8_bytes < MIN_TASK_DOCUMENT_CHARACTERS:
        raise ValueError(f"max_utf8_bytes must be at least {MIN_TASK_DOCUMENT_CHARACTERS}")

    fields = [
        ("title", _normalized(task.title)),
        ("objective", _normalized(task.objective)),
        ("next_action", _normalized(task.next_action)),
        ("description", _normalized(task.description)),
        ("labels", _labels(task.labels)),
    ]
    source_characters = sum(len(value) for _, value in fields)
    source_utf8_bytes = sum(len(value.encode("utf-8")) for _, value in fields)
    overhead = sum(len(name) + 2 for name, _ in fields) + len(fields) - 1
    available = max_characters - overhead

    # Give every field a deterministic share, then spend unused capacity in
    # usefulness order. This protects identity/action fields while still
    # retaining both ends of unusually long values.
    weights = (15, 22, 22, 34, 7)
    allocations = [
        min(len(value), available * weight // 100)
        for (_, value), weight in zip(fields, weights, strict=True)
    ]
    remaining = available - sum(allocations)
    for index in (0, 1, 2, 4, 3):
        extra = min(remaining, len(fields[index][1]) - allocations[index])
        allocations[index] += extra
        remaining -= extra
        if remaining == 0:
            break

    character_bounded_values = [
        _truncate_middle(value, allocation)
        for (_, value), allocation in zip(fields, allocations, strict=True)
    ]
    byte_overhead = len("\n".join(f"{name}: " for name, _ in fields).encode("utf-8"))
    byte_available = max(0, max_utf8_bytes - byte_overhead)
    byte_lengths = [len(value.encode("utf-8")) for value in character_bounded_values]
    byte_allocations = [
        min(length, byte_available * weight // 100)
        for length, weight in zip(byte_lengths, weights, strict=True)
    ]
    byte_remaining = byte_available - sum(byte_allocations)
    for index in (0, 1, 2, 4, 3):
        extra = min(byte_remaining, byte_lengths[index] - byte_allocations[index])
        byte_allocations[index] += extra
        byte_remaining -= extra
        if byte_remaining == 0:
            break
    rendered_values = [
        _truncate_utf8_middle(value, allocation)
        for value, allocation in zip(character_bounded_values, byte_allocations, strict=True)
    ]
    text = "\n".join(
        f"{name}: {value}" for (name, _), value in zip(fields, rendered_values, strict=True)
    )
    digest = sha256(f"{TASK_INDEX_RENDER_VERSION}\0{text}".encode("utf-8")).hexdigest()
    return TaskDocument(
        text=text,
        semantic_hash=digest,
        render_version=TASK_INDEX_RENDER_VERSION,
        truncated=(
            any(
                len(value) > allocation
                for (_, value), allocation in zip(fields, allocations, strict=True)
            )
            or any(
                length > allocation
                for length, allocation in zip(byte_lengths, byte_allocations, strict=True)
            )
        ),
        source_characters=source_characters,
        source_utf8_bytes=source_utf8_bytes,
    )


def task_index_payload(task: Task, document: TaskDocument) -> dict:
    """Return content-free metadata used to validate and hydrate one hit."""
    return {
        "record_type": "task",
        "task_id": task.id,
        "project_id": task.project_id,
        "source_version": task.version,
        "semantic_hash": document.semantic_hash,
        "index_render_version": document.render_version,
        "status": task.status,
        "priority": task.priority,
        "kind": task.kind,
        "epic_id": task.epic_id,
        "sprint_id": task.sprint_id,
        "labels": list(task.labels or []),
        "document_truncated": document.truncated,
        "source_characters": document.source_characters,
        "source_utf8_bytes": document.source_utf8_bytes,
        "indexed_characters": len(document.text),
        "indexed_utf8_bytes": len(document.text.encode("utf-8")),
    }


def enqueue_task_projection(
    db: AsyncSession,
    task: Task,
    *,
    origin: str = "live",
    actor_member_id: str | None = None,
) -> OutboxEvent:
    """Enqueue the current task version in the caller's transaction."""
    if origin not in {"live", "bootstrap", "restore"}:
        raise ValueError("origin must be live, bootstrap, or restore")
    if actor_member_id is None:
        from dduo_solo_founder.team import request_principal

        principal = request_principal()
        actor_member_id = principal.member_id if principal and not principal.anonymous else None
    payload = {"task_version": int(task.version), "origin": origin}
    if actor_member_id:
        payload["actor_member_id"] = actor_member_id
    event = OutboxEvent(
        project_id=task.project_id,
        event_type="task.upsert",
        aggregate_id=task.id,
        payload=payload,
        delivery_priority=0 if origin == "live" else 10,
    )
    db.add(event)
    return event


class TaskIndexService:
    def __init__(self, embeddings: EmbeddingService):
        self.embeddings = embeddings
        self.max_characters = getattr(
            getattr(embeddings, "settings", None),
            "task_index_max_characters",
            16_000,
        )
        self.max_utf8_bytes = getattr(
            getattr(embeddings, "settings", None),
            "task_index_max_utf8_bytes",
            7_500,
        )

    def ensure_collection(self, project_id: str) -> str:
        return self.embeddings.ensure_task_collection(project_id)

    def reset_collection(self, project_id: str) -> str:
        return self.embeddings.reset_task_collection(project_id)

    def inventory(self, project_id: str) -> dict[str, dict]:
        return self.embeddings.task_inventory(project_id)

    def delete_points(self, project_id: str, task_ids: list[str]) -> None:
        self.embeddings.delete_task_points(project_id, task_ids)

    def marker_matches(self, project_id: str, epoch: str | None) -> bool:
        return bool(epoch) and self.embeddings.task_marker_epoch(project_id) == epoch

    def projection_count(self, project_id: str) -> int | None:
        return self.embeddings.task_projection_count(project_id)

    def integrity_snapshot(self, project_id: str) -> tuple[str, int] | None:
        return self.embeddings.task_integrity_snapshot(project_id)

    def write_marker(self, project_id: str, epoch: str, task_count: int) -> None:
        self.embeddings.write_task_marker(project_id, epoch, task_count)

    def render(self, task: Task) -> TaskDocument:
        return render_task_document(
            task,
            max_characters=self.max_characters,
            max_utf8_bytes=self.max_utf8_bytes,
        )

    def index_task(self, task: Task, *, expected_version: int | None = None) -> TaskIndexOutcome:
        """Project one current Task without allowing an old event to overwrite it."""
        source_version = int(task.version)
        if expected_version is not None and source_version != expected_version:
            return TaskIndexOutcome(status="stale", source_version=source_version)

        document = self.render(task)
        payload = task_index_payload(task, document)
        existing = self.embeddings.task_point(task.project_id, task.id)
        if existing is not None and all(
            existing.get(key) == value for key, value in payload.items()
        ):
            return TaskIndexOutcome(
                status="current", source_version=source_version, document=document
            )

        if (
            existing is not None
            and existing.get("semantic_hash") == document.semantic_hash
            and existing.get("index_render_version") == document.render_version
        ):
            updated = self.embeddings.update_task_payload(task.project_id, task.id, payload)
            return TaskIndexOutcome(
                status="payload_updated",
                source_version=source_version,
                document=document,
                vector_store_duration_ms=updated.vector_store_duration_ms,
            )

        observed = self.embeddings.upsert_task_observed(
            task.project_id, task.id, document.text, payload
        )
        return TaskIndexOutcome(
            status="indexed",
            source_version=source_version,
            document=document,
            observed_result=observed,
            vector_store_duration_ms=observed.vector_store_duration_ms,
        )

    def search_observed(
        self,
        project_id: str,
        query: str,
        *,
        limit: int,
        overfetch: int | None = None,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        epic_id: str | None = None,
        label: str | None = None,
        placement: str = "all",
        sprint_ids: list[str] | None = None,
        include_unsprinted: bool = True,
    ) -> VectorSearchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        qdrant_limit = max(limit, overfetch or limit)
        return self.embeddings.search_tasks_observed(
            project_id,
            query,
            qdrant_limit,
            statuses=statuses,
            kinds=kinds,
            epic_id=epic_id,
            label=label,
            **({"placement": placement, "sprint_ids": sprint_ids,
                "include_unsprinted": include_unsprinted}
               if placement != "all" or sprint_ids is not None else {}),
        )


@lru_cache
def _default_task_index_service() -> TaskIndexService:
    return TaskIndexService(embedding_service())


def task_index_service(embeddings: EmbeddingService | None = None) -> TaskIndexService:
    return TaskIndexService(embeddings) if embeddings is not None else _default_task_index_service()
