from __future__ import annotations

import asyncio
from datetime import timedelta
from time import perf_counter

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.embeddings import (
    EmbeddingService,
    VectorOperationError,
    provider_request_count,
)
from dduo_solo_founder.models import OutboxEvent, Project, Task
from dduo_solo_founder.observability import Observation, embedding_price, record
from dduo_solo_founder.service import utcnow
from dduo_solo_founder.task_index import TaskIndexOutcome, TaskIndexService
from dduo_solo_founder.team import project_authority_predicate, project_authority_writable


class OutboxProcessor:
    """Project committed memory and task events into Qdrant retry-safely."""

    def __init__(
        self,
        embeddings: EmbeddingService,
        task_index: TaskIndexService | None = None,
    ):
        self.embeddings = embeddings
        self.task_index = task_index or TaskIndexService(embeddings)

    async def process_one(self, db: AsyncSession) -> bool:
        event = await db.scalar(
            select(OutboxEvent)
            .join(Project, Project.id == OutboxEvent.project_id)
            .where(
                # Keep the fixed active-state predicate literal so PostgreSQL
                # generic prepared plans can prove the partial-index predicate.
                text("status IN ('pending', 'failed')"),
                OutboxEvent.next_attempt_at <= utcnow(),
                project_authority_predicate(),
            )
            # The stored priority makes this lookup indexable even during a
            # large one-time bootstrap: live task/memory work (0) precedes
            # rebuild work (10) without re-sorting JSON payloads each pass.
            .order_by(
                OutboxEvent.delivery_priority,
                OutboxEvent.next_attempt_at,
                OutboxEvent.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not event:
            return False
        project = await db.scalar(
            select(Project).where(Project.id == event.project_id).with_for_update()
        )
        if project is None or not project_authority_writable(project):
            await db.rollback()
            return False
        event.attempts += 1
        observed_result = None
        task_outcome: TaskIndexOutcome | None = None
        operation_error: Exception | None = None
        operation = "embedding.memory_index"
        item_count = 1
        started = perf_counter()
        try:
            if event.event_type == "memory.upsert":
                observed_upsert = getattr(self.embeddings, "upsert_observed", None)
                if callable(observed_upsert):
                    observed_result = observed_upsert(
                        event.project_id,
                        event.aggregate_id,
                        event.payload["text"],
                        event.payload,
                    )
                else:
                    self.embeddings.upsert(
                        event.project_id,
                        event.aggregate_id,
                        event.payload["text"],
                        event.payload,
                    )
            elif event.event_type == "task.upsert":
                operation = "embedding.task_index"
                expected_version = event.payload.get("task_version")
                if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                    raise ValueError("task.upsert requires integer task_version")
                bind = db.get_bind()
                if bind.dialect.name == "postgresql":
                    # Serialize projections without locking the authoritative
                    # Task row while OpenAI or Qdrant is slow. User mutations
                    # remain responsive and newer projection events wait.
                    await db.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtextextended(:aggregate_id, 0))"
                        ),
                        {"aggregate_id": event.aggregate_id},
                    )
                task = await db.scalar(
                    select(Task)
                    .where(
                        Task.id == event.aggregate_id,
                        Task.project_id == event.project_id,
                    )
                    .execution_options(populate_existing=True)
                )
                if task is None:
                    # PostgreSQL is authoritative. A projection event for an
                    # absent task is terminal rather than retried forever.
                    item_count = 0
                else:
                    task_outcome = await asyncio.to_thread(
                        self.task_index.index_task,
                        task,
                        expected_version=expected_version,
                    )
                    observed_result = task_outcome.observed_result
                    if task_outcome.status == "stale":
                        item_count = 0
            else:
                raise ValueError(f"unsupported outbox event: {event.event_type}")
            event.status = "processed"
            event.processed_at = utcnow()
            event.last_error = None
        except Exception as exc:
            operation_error = exc
            event.status = "failed"
            event.last_error = str(exc)[:2000]
            event.next_attempt_at = utcnow() + timedelta(seconds=min(2**event.attempts, 300))
        embedding = (
            observed_result.embedding
            if observed_result is not None
            else operation_error.embedding
            if isinstance(operation_error, VectorOperationError)
            else None
        )
        usage = embedding.usage if embedding else None
        cost, unit_price, pricing_version = embedding_price(
            usage.model if usage else None, usage.input_tokens if usage else None
        )
        await record(
            db,
            Observation(
                project_id=event.project_id,
                actor_member_id=(
                    str(event.payload.get("actor_member_id"))
                    if event.payload.get("actor_member_id")
                    else None
                ),
                idempotency_key=f"outbox:{event.id}:attempt:{event.attempts}",
                category="embedding",
                operation=operation,
                status=(
                    "success"
                    if operation_error is None
                    else "partial_failure"
                    if embedding is not None
                    else "failed"
                ),
                provider=usage.provider if usage else getattr(operation_error, "provider", None),
                model=usage.model if usage else getattr(operation_error, "model", None),
                attempt=event.attempts,
                measurement_source=usage.measurement_source if usage else "unavailable",
                outbox_event_id=event.id,
                input_tokens=usage.input_tokens if usage else None,
                reported_total_tokens=usage.reported_total_tokens if usage else None,
                characters=(
                    len(task_outcome.document.text)
                    if task_outcome is not None and task_outcome.document is not None
                    else None
                ),
                utf8_bytes=(
                    len(task_outcome.document.text.encode("utf-8"))
                    if task_outcome is not None and task_outcome.document is not None
                    else None
                ),
                duration_ms=round((perf_counter() - started) * 1000),
                provider_duration_ms=(
                    embedding.provider_duration_ms
                    if embedding
                    else getattr(operation_error, "provider_duration_ms", None)
                ),
                vector_store_duration_ms=(
                    observed_result.vector_store_duration_ms
                    if observed_result is not None
                    else task_outcome.vector_store_duration_ms
                    if task_outcome is not None
                    else operation_error.vector_store_duration_ms
                    if isinstance(operation_error, VectorOperationError)
                    else None
                ),
                request_count=provider_request_count(embedding, operation_error),
                item_count=item_count,
                cost_usd=cost,
                unit_price_usd_per_million=unit_price,
                pricing_version=pricing_version,
                details={
                    "event_type": event.event_type,
                    "error_code": "index_failed" if operation_error else "",
                    **(
                        {
                            "document_truncated": task_outcome.document.truncated,
                            "source_characters": task_outcome.document.source_characters,
                            "source_utf8_bytes": task_outcome.document.source_utf8_bytes,
                        }
                        if task_outcome is not None and task_outcome.document is not None
                        else {}
                    ),
                },
            ),
        )
        project = await db.scalar(
            select(Project).where(Project.id == event.project_id).with_for_update()
        )
        if project is None or not project_authority_writable(project):
            # A transfer that raced an external provider response owns the
            # barrier. PostgreSQL remains unchanged; Qdrant is derived and is
            # reconciled on the destination node.
            await db.rollback()
            return True
        await db.commit()
        return True
