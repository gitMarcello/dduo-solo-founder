from __future__ import annotations

from types import SimpleNamespace

import pytest

from dduo_solo_founder.embeddings import (
    EmbeddingCall,
    EmbeddingUsage,
    VectorPayloadUpdateResult,
    VectorSearchResult,
    VectorUpsertResult,
)
from dduo_solo_founder.models import Project, Task
from dduo_solo_founder.task_index import (
    TASK_INDEX_RENDER_VERSION,
    TaskIndexService,
    _labels,
    _truncate_middle,
    _truncate_utf8_middle,
    advance_task_index_generation,
    current_task_index_generation,
    enqueue_task_projection,
    lock_task_index_project,
    render_task_document,
    task_index_payload,
    task_index_service,
)


TASK_ID = "11111111-1111-4111-8111-111111111111"


async def test_generation_helpers_reject_unknown_projects(db_factory):
    async with db_factory() as db:
        with pytest.raises(LookupError, match="project not found"):
            await advance_task_index_generation(db, "missing")
        with pytest.raises(LookupError, match="project not found"):
            await current_task_index_generation(db, "missing")


async def test_project_lock_uses_postgresql_transaction_advisory_lock():
    calls = []

    class FakeDb:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement, parameters):
            calls.append((str(statement), parameters))

    await lock_task_index_project(FakeDb(), "project-1")
    assert "pg_advisory_xact_lock" in calls[0][0]
    assert calls[0][1] == {"task_index_project_key": "task-index-project:project-1"}


def task(**overrides) -> Task:
    values = {
        "id": TASK_ID,
        "project_id": "p1",
        "title": "Ottimizzare il recupero task",
        "objective": "Consegnare solo il contesto utile",
        "next_action": "Aggiungere la ricerca semantica",
        "description": "Indicizzare i campi operativi nel database vettoriale.",
        "labels": ["Backend", "Ricerca"],
        "status": "todo",
        "priority": "high",
        "kind": "task",
        "epic_id": None,
        "version": 1,
    }
    values.update(overrides)
    return Task(**values)


def embedding_result() -> VectorUpsertResult:
    return VectorUpsertResult(
        embedding=EmbeddingCall(
            vectors=[[0.1, 0.2]],
            usage=EmbeddingUsage(
                provider="openai",
                model="text-embedding-3-large",
                input_tokens=12,
                reported_total_tokens=12,
                measurement_source="provider_reported",
            ),
            provider_duration_ms=3,
        ),
        vector_store_duration_ms=2,
    )


class FakeTaskEmbeddings:
    def __init__(self):
        self.settings = SimpleNamespace(
            task_index_max_characters=16_000,
            task_index_max_utf8_bytes=7_500,
        )
        self.point = None
        self.point_calls = []
        self.upserts = []
        self.payload_updates = []
        self.searches = []
        self.deleted = []
        self.marker_epoch = None
        self.projection_count = None
        self.markers = []

    def ensure_task_collection(self, project_id):
        return f"tasks_{project_id}"

    def reset_task_collection(self, project_id):
        return f"tasks_{project_id}"

    def task_inventory(self, project_id):
        return {TASK_ID: self.point} if self.point else {}

    def delete_task_points(self, project_id, task_ids):
        self.deleted.append((project_id, task_ids))

    def task_point(self, project_id, task_id):
        self.point_calls.append((project_id, task_id))
        return self.point

    def task_marker_epoch(self, project_id):
        return self.marker_epoch

    def task_projection_count(self, project_id):
        return self.projection_count

    def task_integrity_snapshot(self, project_id):
        if self.marker_epoch is None or self.projection_count is None:
            return None
        return self.marker_epoch, self.projection_count

    def write_task_marker(self, project_id, epoch, task_count):
        self.markers.append((project_id, epoch, task_count))

    def upsert_task_observed(self, project_id, task_id, text, payload):
        self.upserts.append((project_id, task_id, text, payload))
        return embedding_result()

    def update_task_payload(self, project_id, task_id, payload):
        self.payload_updates.append((project_id, task_id, payload))
        return VectorPayloadUpdateResult(vector_store_duration_ms=1)

    def search_tasks_observed(
        self,
        project_id,
        query,
        limit,
        *,
        statuses=None,
        kinds=None,
        epic_id=None,
        label=None,
    ):
        self.searches.append((project_id, query, limit, statuses, kinds, epic_id, label))
        return VectorSearchResult(
            items=[{"id": TASK_ID, "score": 0.91, "task_id": TASK_ID}],
            embedding=embedding_result().embedding,
            vector_store_duration_ms=1,
        )


def test_renderer_is_deterministic_bounded_and_uses_only_approved_fields():
    first = task(
        description="inizio " + "corpo " * 500 + " fine",
        labels=["Ricerca", "Backend", "Backend"],
        rationale="SEGRETO_RAZIONALE",
        completion_evidence="SEGRETO_EVIDENZA",
        dependencies=["SEGRETO_DIPENDENZA"],
    )
    rendered = render_task_document(first, max_characters=512)
    reordered = render_task_document(
        task(
            description=first.description,
            labels=["Backend", "Ricerca"],
            rationale="altro",
            completion_evidence="altro",
        ),
        max_characters=512,
    )

    assert len(rendered.text) <= 512
    assert rendered.truncated is True
    assert rendered.render_version == TASK_INDEX_RENDER_VERSION
    assert rendered.semantic_hash == reordered.semantic_hash
    assert "SEGRETO" not in rendered.text
    assert [line.split(":", 1)[0] for line in rendered.text.splitlines()] == [
        "title",
        "objective",
        "next_action",
        "description",
        "labels",
    ]


def test_label_normalization_has_a_total_order_for_casefold_collisions():
    expected = "Mobile, mobile"
    assert _labels(["mobile", "Mobile"]) == expected
    assert _labels(["Mobile", "mobile"]) == expected


def test_payload_contains_only_hydration_and_freshness_metadata():
    current = task(epic_id="22222222-2222-4222-8222-222222222222")
    rendered = render_task_document(current)
    payload = task_index_payload(current, rendered)
    assert payload == {
        "record_type": "task",
        "task_id": TASK_ID,
        "project_id": "p1",
        "source_version": 1,
        "semantic_hash": rendered.semantic_hash,
        "index_render_version": TASK_INDEX_RENDER_VERSION,
        "status": "todo",
        "priority": "high",
        "kind": "task",
        "epic_id": "22222222-2222-4222-8222-222222222222",
        "sprint_id": None,
        "labels": ["Backend", "Ricerca"],
        "document_truncated": False,
        "source_characters": rendered.source_characters,
        "source_utf8_bytes": rendered.source_utf8_bytes,
        "indexed_characters": len(rendered.text),
        "indexed_utf8_bytes": len(rendered.text.encode("utf-8")),
    }


def test_index_skips_stale_events_and_postgres_repairs_newer_derived_points():
    embeddings = FakeTaskEmbeddings()
    index = TaskIndexService(embeddings)
    current = task(version=2)

    stale = index.index_task(current, expected_version=1)
    assert stale.status == "stale"
    assert embeddings.point_calls == []

    document = index.render(current)
    current_payload = task_index_payload(current, document)
    embeddings.point = current_payload
    assert index.index_task(current, expected_version=2).status == "current"
    assert embeddings.upserts == []

    embeddings.point = {**current_payload, "source_version": 3}
    assert index.index_task(current, expected_version=2).status == "payload_updated"
    assert embeddings.upserts == []


def test_index_updates_payload_without_embedding_when_semantics_are_unchanged():
    embeddings = FakeTaskEmbeddings()
    index = TaskIndexService(embeddings)
    current = task(version=2, status="done", priority="low")
    document = index.render(current)
    embeddings.point = {
        **task_index_payload(current, document),
        "source_version": 1,
        "status": "doing",
        "priority": "high",
    }

    outcome = index.index_task(current, expected_version=2)
    assert outcome.status == "payload_updated"
    assert outcome.observed_result is None
    assert outcome.vector_store_duration_ms == 1
    assert len(embeddings.payload_updates) == 1
    assert embeddings.upserts == []


def test_index_embeds_changed_semantics_and_search_supports_overfetch_filters():
    embeddings = FakeTaskEmbeddings()
    index = TaskIndexService(embeddings)
    current = task()
    embeddings.point = None

    outcome = index.index_task(current, expected_version=1)
    assert outcome.status == "indexed"
    assert outcome.observed_result is not None
    assert embeddings.upserts[0][3]["task_id"] == TASK_ID

    result = index.search_observed(
        "p1",
        "recupero task",
        limit=5,
        overfetch=15,
        statuses=["todo", "doing"],
        kinds=["task"],
        epic_id="epic-1",
        label="Backend",
    )
    assert result.items[0]["id"] == TASK_ID
    assert embeddings.searches == [
        (
            "p1",
            "recupero task",
            15,
            ["todo", "doing"],
            ["task"],
            "epic-1",
            "Backend",
        )
    ]


def test_service_projection_lifecycle_validation_and_fake_compatibility():
    embeddings = FakeTaskEmbeddings()
    embeddings.point = {"source_version": "2"}
    index = task_index_service(embeddings)
    assert index.ensure_collection("p1") == "tasks_p1"
    assert index.inventory("p1") == {TASK_ID: {"source_version": "2"}}
    index.delete_points("p1", [TASK_ID])
    assert embeddings.deleted == [("p1", [TASK_ID])]
    assert index.reset_collection("p1") == "tasks_p1"
    embeddings.marker_epoch = "epoch-1"
    embeddings.projection_count = 1
    assert index.marker_matches("p1", "epoch-1") is True
    assert index.marker_matches("p1", None) is False
    assert index.projection_count("p1") == 1
    assert index.integrity_snapshot("p1") == ("epoch-1", 1)
    index.write_marker("p1", "epoch-2", 1)
    assert embeddings.markers == [("p1", "epoch-2", 1)]
    assert index.index_task(task(version=1), expected_version=1).status == "indexed"
    embeddings.point = {"source_version": True}
    assert index.index_task(task(version=1), expected_version=1).status == "indexed"
    with pytest.raises(ValueError, match="limit must be positive"):
        index.search_observed("p1", "query", limit=0)


def test_renderer_and_enqueue_reject_invalid_technical_inputs():
    with pytest.raises(ValueError, match="at least 512"):
        render_task_document(task(), max_characters=511)
    with pytest.raises(ValueError, match="max_utf8_bytes"):
        render_task_document(task(), max_utf8_bytes=511)
    assert _truncate_middle("long", 1) == "…"
    assert _truncate_utf8_middle("🚀", 2) == ""
    with pytest.raises(ValueError, match="origin must be"):
        enqueue_task_projection(SimpleNamespace(add=lambda _: None), task(), origin="unknown")
    explicitly_attributed = enqueue_task_projection(
        SimpleNamespace(add=lambda _: None),
        task(),
        actor_member_id="member-1",
    )
    assert explicitly_attributed.payload["actor_member_id"] == "member-1"

    optional_empty = render_task_document(task(objective=None, next_action=None, labels="bad"))
    assert "objective: \n" in optional_empty.text
    assert optional_empty.text.endswith("labels: ")

    unicode_dense = render_task_document(
        task(description="🚀漢字" * 10_000),
        max_characters=100_000,
        max_utf8_bytes=7_500,
    )
    assert len(unicode_dense.text.encode("utf-8")) <= 7_500
    assert unicode_dense.truncated is True
    assert unicode_dense.source_utf8_bytes > 7_500


async def test_enqueue_task_projection_joins_the_callers_transaction(db_factory):
    async with db_factory() as db:
        project = Project(id="p1", name="Test", root_path="/tmp/test")
        current = task(version=4)
        db.add_all([project, current])
        event = enqueue_task_projection(db, current, origin="bootstrap")
        assert event.event_type == "task.upsert"
        assert event.aggregate_id == TASK_ID
        assert event.payload == {"task_version": 4, "origin": "bootstrap"}
        assert event.id is None
        await db.flush()
        assert event.id is not None
