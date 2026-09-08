from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from dduo_solo_founder.embeddings import (
    EmbeddingCall,
    EmbeddingProviderError,
    EmbeddingUsage,
    VectorUpsertResult,
)
from dduo_solo_founder.models import (
    ObservabilityEvent,
    OutboxEvent,
    Project,
    Task,
    TeamMember,
)
from dduo_solo_founder import outbox
from dduo_solo_founder.outbox import OutboxProcessor
from dduo_solo_founder.service import utcnow
from dduo_solo_founder.task_index import TaskIndexOutcome, render_task_document


class FakeEmbeddings:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def upsert(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error


class FakeTaskIndex:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def index_task(self, task, *, expected_version=None):
        self.calls.append((task.id, expected_version))
        return self.outcome


class FakeObservedEmbeddings(FakeEmbeddings):
    def upsert_observed(self, *args):
        self.calls.append(args)
        embedding = EmbeddingCall(
            vectors=[[0.1]],
            usage=EmbeddingUsage(
                provider="openai",
                model="text-embedding-3-large",
                input_tokens=4,
                reported_total_tokens=4,
                measurement_source="provider_reported",
            ),
            provider_duration_ms=1,
        )
        return VectorUpsertResult(embedding=embedding, vector_store_duration_ms=1)


async def add_event(db, *, event_type="memory.upsert", due=True):
    project = Project(id="p1", name="Test", root_path="/tmp/test")
    db.add(project)
    await db.flush()
    event = OutboxEvent(
        project_id=project.id,
        event_type=event_type,
        aggregate_id="m1",
        payload={"text": "Memory", "status": "active"},
        next_attempt_at=utcnow() if due else utcnow() + timedelta(hours=1),
    )
    db.add(event)
    await db.commit()
    return event


async def test_outbox_empty_and_future_are_idle(db_factory):
    async with db_factory() as db:
        processor = OutboxProcessor(FakeEmbeddings())
        assert await processor.process_one(db) is False
        await add_event(db, due=False)
        assert await processor.process_one(db) is False


async def test_outbox_ready_query_keeps_partial_index_predicate_literal():
    statements = []

    class EmptyDb:
        async def scalar(self, statement):
            statements.append(statement)
            return None

    assert await OutboxProcessor(FakeEmbeddings()).process_one(EmptyDb()) is False
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "status IN ('pending', 'failed')" in sql
    assert "status_1" not in sql
    assert "ORDER BY outbox_events.delivery_priority" in sql


async def test_outbox_processes_success(db_factory):
    async with db_factory() as db:
        event = await add_event(db)
        embeddings = FakeEmbeddings()
        assert await OutboxProcessor(embeddings).process_one(db) is True
        await db.refresh(event)
        assert event.status == "processed" and event.processed_at and event.attempts == 1
        assert embeddings.calls[0][0:3] == ("p1", "m1", "Memory")


async def test_outbox_prefers_observed_memory_projection(db_factory):
    async with db_factory() as db:
        event = await add_event(db)
        member = TeamMember(
            project_id="p1",
            display_name="Actor",
            capability="project_member",
        )
        db.add(member)
        await db.flush()
        event.payload = {**event.payload, "actor_member_id": member.id}
        await db.commit()
        embeddings = FakeObservedEmbeddings()
        assert await OutboxProcessor(embeddings).process_one(db) is True
        observation = await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.outbox_event_id == event.id)
        )
        assert observation.input_tokens == 4
        assert observation.actor_member_id == member.id


async def test_outbox_records_failures_and_retries(db_factory):
    async with db_factory() as db:
        event = await add_event(db)
        processor = OutboxProcessor(FakeEmbeddings(RuntimeError("qdrant unavailable")))
        assert await processor.process_one(db) is True
        await db.refresh(event)
        assert event.status == "failed" and "unavailable" in event.last_error
        assert event.next_attempt_at is not None
        event.next_attempt_at = utcnow() - timedelta(seconds=1)
        event.event_type = "unknown"
        await db.commit()
        assert await processor.process_one(db) is True
        refreshed = await db.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert "unsupported outbox event" in refreshed.last_error and refreshed.attempts == 2


async def test_outbox_dispatches_task_projection_and_records_task_usage(db_factory):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        task = Task(
            id="11111111-1111-4111-8111-111111111111",
            project_id="p1",
            title="Semantic task",
            version=3,
        )
        event = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=task.id,
            payload={"task_version": 3},
        )
        db.add_all([project, task, event])
        await db.commit()
        embedding = EmbeddingCall(
            vectors=[[0.1]],
            usage=EmbeddingUsage(
                provider="openai",
                model="text-embedding-3-large",
                input_tokens=8,
                reported_total_tokens=8,
                measurement_source="provider_reported",
            ),
            provider_duration_ms=2,
        )
        task_index = FakeTaskIndex(
            TaskIndexOutcome(
                status="indexed",
                source_version=3,
                document=render_task_document(task),
                observed_result=VectorUpsertResult(embedding=embedding, vector_store_duration_ms=1),
                vector_store_duration_ms=1,
            )
        )

        processor = OutboxProcessor(FakeEmbeddings(), task_index)
        assert await processor.process_one(db) is True
        await db.refresh(event)
        assert event.status == "processed"
        assert task_index.calls == [(task.id, 3)]
        observation = await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.outbox_event_id == event.id)
        )
        assert observation.operation == "embedding.task_index"
        assert observation.input_tokens == 8
        assert observation.request_count == 1
        assert observation.characters == len(render_task_document(task).text)
        assert observation.details["document_truncated"] is False
        assert observation.details["source_utf8_bytes"] > 0


async def test_outbox_marks_stale_task_event_processed_without_embedding(db_factory):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        task = Task(
            id="11111111-1111-4111-8111-111111111111",
            project_id="p1",
            title="New version",
            version=2,
        )
        event = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=task.id,
            payload={"task_version": 1},
        )
        db.add_all([project, task, event])
        await db.commit()
        task_index = FakeTaskIndex(TaskIndexOutcome(status="stale", source_version=2))

        assert await OutboxProcessor(FakeEmbeddings(), task_index).process_one(db) is True
        await db.refresh(event)
        assert event.status == "processed"
        observation = await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.outbox_event_id == event.id)
        )
        assert observation.operation == "embedding.task_index"
        assert observation.request_count == 0
        assert observation.item_count == 0


async def test_task_outbox_distinguishes_provider_attempt_from_preprovider_failure(db_factory):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        task = Task(
            id="11111111-1111-4111-8111-111111111111",
            project_id="p1",
            title="Provider boundary",
        )
        event = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=task.id,
            payload={"task_version": 1},
        )
        db.add_all([project, task, event])
        await db.commit()

        class FailedProviderIndex:
            def index_task(self, task, *, expected_version=None):
                raise EmbeddingProviderError(
                    "provider failed",
                    provider="openai",
                    model="text-embedding-3-large",
                    duration_ms=4,
                )

        assert await OutboxProcessor(FakeEmbeddings(), FailedProviderIndex()).process_one(db)
        observation = await db.scalar(
            select(ObservabilityEvent).where(ObservabilityEvent.outbox_event_id == event.id)
        )
        assert observation.request_count == 1
        assert observation.provider == "openai"
        assert observation.provider_duration_ms == 4


async def test_outbox_handles_missing_task_as_terminal_and_invalid_version_as_retryable(
    db_factory,
):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        missing = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id="11111111-1111-4111-8111-111111111111",
            payload={"task_version": 1},
        )
        db.add_all([project, missing])
        await db.commit()
        task_index = FakeTaskIndex(TaskIndexOutcome(status="indexed", source_version=1))
        processor = OutboxProcessor(FakeEmbeddings(), task_index)
        assert await processor.process_one(db) is True
        await db.refresh(missing)
        assert missing.status == "processed"
        assert task_index.calls == []

        current = Task(
            id="22222222-2222-4222-8222-222222222222",
            project_id="p1",
            title="Invalid event",
            version=1,
        )
        invalid = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=current.id,
            payload={"task_version": "1"},
        )
        db.add_all([current, invalid])
        await db.commit()
        assert await processor.process_one(db) is True
        await db.refresh(invalid)
        assert invalid.status == "failed"
        assert "integer task_version" in invalid.last_error


async def test_outbox_prioritizes_memory_and_live_tasks_over_bootstrap(db_factory):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        bootstrap_task = Task(
            id="11111111-1111-4111-8111-111111111111",
            project_id="p1",
            title="Bootstrap",
        )
        live_task = Task(
            id="22222222-2222-4222-8222-222222222222",
            project_id="p1",
            title="Live",
        )
        bootstrap = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=bootstrap_task.id,
            payload={"task_version": 1, "origin": "bootstrap"},
            delivery_priority=10,
            created_at=utcnow() - timedelta(minutes=2),
        )
        live = OutboxEvent(
            project_id="p1",
            event_type="task.upsert",
            aggregate_id=live_task.id,
            payload={"task_version": 1, "origin": "live"},
            delivery_priority=0,
            created_at=utcnow() - timedelta(minutes=1),
        )
        db.add_all([project, bootstrap_task, live_task, bootstrap, live])
        await db.commit()
        task_index = FakeTaskIndex(TaskIndexOutcome(status="current", source_version=1))
        processor = OutboxProcessor(FakeEmbeddings(), task_index)
        assert await processor.process_one(db) is True
        assert task_index.calls == [(live_task.id, 1)]
        await db.refresh(bootstrap)
        assert bootstrap.status == "pending"

        memory = OutboxEvent(
            project_id="p1",
            event_type="memory.upsert",
            aggregate_id="m-live",
            payload={"text": "Current memory", "status": "active"},
        )
        db.add(memory)
        await db.commit()
        embeddings = FakeEmbeddings()
        assert await OutboxProcessor(embeddings, task_index).process_one(db) is True
        assert embeddings.calls[0][1] == "m-live"


async def test_outbox_rolls_back_when_claimed_event_loses_its_project():
    event = SimpleNamespace(project_id="p1")

    class MissingProjectDb:
        def __init__(self):
            self.values = iter([event, None])
            self.rollbacks = 0

        async def scalar(self, _statement):
            return next(self.values)

        async def rollback(self):
            self.rollbacks += 1

    db = MissingProjectDb()
    assert await OutboxProcessor(FakeEmbeddings()).process_one(db) is False
    assert db.rollbacks == 1


async def test_outbox_discards_post_provider_commit_when_authority_freezes(monkeypatch):
    event = SimpleNamespace(
        id="event-1",
        project_id="p1",
        event_type="memory.upsert",
        aggregate_id="memory-1",
        payload={"text": "Durable fact"},
        attempts=0,
        status="pending",
        processed_at=None,
        last_error=None,
    )
    active = SimpleNamespace(authority_state="active", authority_node_id=None)
    frozen = SimpleNamespace(authority_state="transfer_pending", authority_node_id=None)

    class RacingDb:
        def __init__(self):
            self.values = iter([event, active, frozen])
            self.rollbacks = 0
            self.commits = 0

        async def scalar(self, _statement):
            return next(self.values)

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            self.commits += 1

    observations = []

    async def capture(_db, observation):
        observations.append(observation)

    monkeypatch.setattr(outbox, "record", capture)
    db = RacingDb()
    embeddings = FakeEmbeddings()
    assert await OutboxProcessor(embeddings).process_one(db) is True
    assert embeddings.calls and observations
    assert db.rollbacks == 1
    assert db.commits == 0
