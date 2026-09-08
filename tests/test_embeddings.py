from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from dduo_solo_founder.config import Settings
from dduo_solo_founder.embeddings import (
    EmbeddingCall,
    EmbeddingProviderError,
    EmbeddingService,
    EmbeddingUsage,
    VectorOperationError,
    embedding_service,
    local_model,
    provider_request_count,
)


class FakeQdrant:
    def __init__(self, *args, **kwargs):
        self.collections = []
        self.created = []
        self.upserts = []
        self.points = []
        self.scrolled = []
        self.deleted = []
        self.deleted_collections = []
        self.retrieved = []
        self.retrieved_points = []
        self.payload_updates = []
        self.queries = []
        self.counts = []
        self.count_value = 0

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.collections]
        )

    def create_collection(self, name, vectors_config):
        self.collections.append(name)
        self.created.append((name, vectors_config))

    def upsert(self, collection, points, **kwargs):
        self.upserts.append((collection, points))

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        return SimpleNamespace(points=self.points)

    def scroll(self, **kwargs):
        return self.scrolled.pop(0)

    def delete(self, **kwargs):
        self.deleted.append(kwargs)

    def delete_collection(self, name):
        self.deleted_collections.append(name)
        self.collections.remove(name)

    def retrieve(self, **kwargs):
        self.retrieved.append(kwargs)
        return self.retrieved_points

    def set_payload(self, **kwargs):
        self.payload_updates.append(kwargs)

    def count(self, **kwargs):
        self.counts.append(kwargs)
        return SimpleNamespace(count=self.count_value)


def settings(**overrides):
    values = {"_env_file": None, "qdrant_url": "http://unused"}
    values.update(overrides)
    return Settings(**values)


def service(monkeypatch, **overrides):
    monkeypatch.setattr("dduo_solo_founder.embeddings.QdrantClient", FakeQdrant)
    return EmbeddingService(settings(**overrides))


def test_openai_is_quality_first_default():
    defaults = settings()
    assert defaults.embedding_provider == "openai"
    assert defaults.embedding_model == "text-embedding-3-large"


@pytest.mark.parametrize(
    ("provider", "model", "dimension"),
    [
        ("openai", "text-embedding-3-large", 3072),
        ("fastembed", "sentence-transformers/all-MiniLM-L6-v2", 384),
        ("fastembed", "BAAI/bge-small-en-v1.5", 384),
        ("fastembed", "sentence-transformers/all-mpnet-base-v2", 768),
        ("fastembed", "intfloat/multilingual-e5-large", 1024),
    ],
)
def test_dimensions(monkeypatch, provider, model, dimension):
    assert (
        service(monkeypatch, embedding_provider=provider, embedding_model=model).dimension
        == dimension
    )


def test_collection_identity_and_creation(monkeypatch):
    first = service(
        monkeypatch,
        embedding_provider="openai",
        embedding_model="text-embedding-3-large",
        embedding_index_version="v1",
    )
    name = first.collection("11111111-1111-4111-8111-111111111111")
    assert name.endswith("openai_text_embedding_3_large_v1")
    assert first.ensure_collection("11111111-1111-4111-8111-111111111111") == name
    assert len(first.qdrant.created) == 1
    first.ensure_collection("11111111-1111-4111-8111-111111111111")
    assert len(first.qdrant.created) == 1


def test_task_collection_has_an_independent_identity(monkeypatch):
    current = service(
        monkeypatch,
        embedding_index_version="memory-v2",
        task_embedding_index_version="task-v4",
    )
    memory = current.collection("p1")
    tasks = current.task_collection("p1")
    assert tasks != memory
    assert "_tasks_p1_" in tasks
    assert tasks.endswith("task-v4")
    assert current.ensure_task_collection("p1") == tasks


def test_task_collection_marker_is_provider_free_and_detects_replacement(monkeypatch):
    current = service(monkeypatch)
    assert current.task_marker_epoch("p1") is None
    assert current.task_projection_count("p1") is None

    current.write_task_marker("p1", "epoch-1", 3)
    collection, points = current.qdrant.upserts[-1]
    marker = points[0]
    assert collection == current.task_collection("p1")
    assert marker.payload == {
        "record_type": "task_index_marker",
        "project_id": "p1",
        "epoch": "epoch-1",
        "task_count": 3,
    }
    assert len(marker.vector) == current.dimension
    current.qdrant.retrieved_points = [
        SimpleNamespace(id=marker.id, payload=marker.payload)
    ]
    assert current.task_marker_epoch("p1") == "epoch-1"
    current.qdrant.count_value = 4
    assert current.task_projection_count("p1") == 3
    assert current.task_integrity_snapshot("p1") == ("epoch-1", 3)
    assert current.qdrant.counts == [
        {"collection_name": current.task_collection("p1"), "exact": True},
        {"collection_name": current.task_collection("p1"), "exact": True},
    ]

    current.qdrant.collections.clear()
    assert current.task_marker_epoch("p1") is None


def test_task_marker_rejects_wrong_payload_and_uses_collection_exists(monkeypatch):
    current = service(monkeypatch)
    current.qdrant.collection_exists = lambda name: True
    current.qdrant.retrieved_points = [
        SimpleNamespace(id="wrong", payload={"record_type": "task", "epoch": "e1"})
    ]
    assert current.task_marker_epoch("p1") is None
    assert current.task_integrity_snapshot("p1") is None
    assert current.qdrant.counts == []
    current.qdrant.retrieved_points = [
        SimpleNamespace(id="marker", payload={"record_type": "task_index_marker"})
    ]
    assert current.task_marker_epoch("p1") is None


def test_collection_creation_accepts_only_a_confirmed_concurrent_winner(monkeypatch):
    current = service(monkeypatch)

    def won_race(name, vectors_config):
        current.qdrant.collections.append(name)
        raise RuntimeError("already exists")

    current.qdrant.create_collection = won_race
    assert current.ensure_collection("p1") == current.collection("p1")

    current.qdrant.collections.clear()
    current.qdrant.create_collection = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("offline")
    )
    with pytest.raises(RuntimeError, match="offline"):
        current.ensure_collection("p1")


def test_openai_embed_is_lazy(monkeypatch):
    current = service(monkeypatch)

    class Embeddings:
        def create(self, **kwargs):
            assert kwargs["input"] == ["one", "two"]
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0]), SimpleNamespace(embedding=[2.0])]
            )

    monkeypatch.setattr(
        "dduo_solo_founder.embeddings.OpenAI",
        lambda **kwargs: SimpleNamespace(embeddings=Embeddings()),
    )
    assert current.embed(["one", "two"]) == [[1.0], [2.0]]
    assert current._openai is not None


def test_observed_openai_embedding_retains_exact_provider_usage(monkeypatch):
    current = service(monkeypatch)

    class Embeddings:
        def create(self, **kwargs):
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0])],
                usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
            )

    monkeypatch.setattr(
        "dduo_solo_founder.embeddings.OpenAI",
        lambda **kwargs: SimpleNamespace(embeddings=Embeddings()),
    )
    observed = current.embed_observed(["one"])
    assert observed.vectors == [[1.0]]
    assert observed.usage.input_tokens == 7
    assert observed.usage.reported_total_tokens == 7
    assert observed.usage.measurement_source == "provider_reported"
    assert observed.provider_duration_ms >= 0
    assert provider_request_count(observed) == 1


def test_provider_failure_counts_one_logical_call_without_inventing_usage(monkeypatch):
    current = service(monkeypatch)

    class Embeddings:
        def create(self, **kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "dduo_solo_founder.embeddings.OpenAI",
        lambda **kwargs: SimpleNamespace(embeddings=Embeddings()),
    )
    with pytest.raises(EmbeddingProviderError, match="provider unavailable") as raised:
        current.embed_observed(["one"])
    error = raised.value
    assert error.provider == "openai"
    assert error.model == current.settings.embedding_model
    assert provider_request_count(None, error) == 1
    assert provider_request_count(None, RuntimeError("qdrant before provider")) == 0


def test_local_e5_prefixes_and_plain_models(monkeypatch):
    seen = []

    class Vector:
        def __init__(self, value):
            self.value = value

        def tolist(self):
            return [self.value]

    fake = SimpleNamespace(
        embed=lambda values: seen.extend(values) or [Vector(i) for i, _ in enumerate(values)]
    )
    monkeypatch.setattr("dduo_solo_founder.embeddings.local_model", lambda _: fake)
    e5 = service(monkeypatch, embedding_provider="fastembed", embedding_model="intfloat/e5-large")
    assert e5.embed(["question"], query=True) == [[0]]
    assert seen.pop() == "query: question"
    assert e5.embed(["fact"]) == [[0]]
    assert seen.pop() == "passage: fact"
    plain = service(monkeypatch, embedding_provider="fastembed", embedding_model="model")
    plain.embed(["raw"])
    assert seen.pop() == "raw"


def test_upsert_and_search(monkeypatch):
    current = service(monkeypatch)
    monkeypatch.setattr(current, "embed", lambda values, **kwargs: [[0.1, 0.2]])
    current.upsert("p1", "11111111-1111-4111-8111-111111111111", "memory", {"status": "active"})
    assert current.qdrant.upserts[0][1][0].payload["status"] == "active"
    current.qdrant.points = [SimpleNamespace(id="m1", score=0.9, payload={"text": "memory"})]
    assert current.search("p1", "query", 4) == [{"id": "m1", "score": 0.9, "text": "memory"}]


def observed_embedding() -> EmbeddingCall:
    return EmbeddingCall(
        vectors=[[0.1, 0.2]],
        usage=EmbeddingUsage(
            provider="openai",
            model="text-embedding-3-large",
            input_tokens=3,
            reported_total_tokens=3,
            measurement_source="provider_reported",
        ),
        provider_duration_ms=2,
    )


def test_observed_upsert_and_search_return_separate_timings(monkeypatch):
    current = service(monkeypatch)
    embedding = observed_embedding()
    monkeypatch.setattr(current, "embed_observed", lambda values, **kwargs: embedding)

    upsert = current.upsert_observed(
        "p1",
        "11111111-1111-4111-8111-111111111111",
        "memory",
        {"status": "active"},
    )
    assert upsert.embedding is embedding
    assert upsert.vector_store_duration_ms >= 0
    assert current.qdrant.upserts[0][1][0].vector == [0.1, 0.2]

    current.qdrant.points = [
        SimpleNamespace(id="m1", score=0.9, payload={"text": "memory"}),
        SimpleNamespace(id="m2", score=0.7, payload=None),
    ]
    search = current.search_observed("p1", "query", 4)
    assert search.embedding is embedding
    assert search.vector_store_duration_ms >= 0
    assert search.items == [
        {"id": "m1", "score": 0.9, "text": "memory"},
        {"id": "m2", "score": 0.7},
    ]


def test_task_point_payload_update_upsert_and_filtered_search(monkeypatch):
    current = service(monkeypatch)
    embedding = observed_embedding()
    monkeypatch.setattr(current, "embed_observed", lambda values, **kwargs: embedding)
    task_id = "11111111-1111-4111-8111-111111111111"

    current.qdrant.retrieved_points = [SimpleNamespace(id=task_id, payload={"source_version": 2})]
    assert current.task_point("p1", task_id) == {
        "id": task_id,
        "source_version": 2,
    }

    updated = current.update_task_payload("p1", task_id, {"status": "done"})
    assert updated.vector_store_duration_ms >= 0
    assert current.qdrant.payload_updates[-1]["points"] == [task_id]

    upserted = current.upsert_task_observed("p1", task_id, "task document", {"task_id": task_id})
    assert upserted.embedding is embedding
    assert current.qdrant.upserts[-1][0] == current.task_collection("p1")

    current.qdrant.points = [SimpleNamespace(id=task_id, score=0.8, payload={"task_id": task_id})]
    searched = current.search_tasks_observed(
        "p1",
        "semantic query",
        12,
        statuses=["todo"],
        kinds=["task"],
        epic_id="epic-1",
        label="mobile",
    )
    assert searched.items == [{"id": task_id, "score": 0.8, "task_id": task_id}]
    qdrant_query = current.qdrant.queries[-1]
    assert qdrant_query["collection_name"] == current.task_collection("p1")
    assert qdrant_query["limit"] == 12
    conditions = qdrant_query["query_filter"].must
    assert [condition.key for condition in conditions] == [
        "project_id",
        "record_type",
        "status",
        "kind",
        "epic_id",
        "labels",
    ]


@pytest.mark.parametrize("placement,sprint_ids,include_unsprinted", [
    ("current", ["active-1"], True),
    ("current", [], False),
    ("backlog", None, True),
    ("archive", ["closed-1"], True),
    ("archive", ["closed-1"], False),
])
def test_task_semantic_placement_filters_are_sent_to_qdrant(monkeypatch, placement, sprint_ids, include_unsprinted):
    current = service(monkeypatch)
    monkeypatch.setattr(current, "embed_observed", lambda values, **kwargs: observed_embedding())
    current.search_tasks_observed("p1", "query", 8, placement=placement,
                                  sprint_ids=sprint_ids, include_unsprinted=include_unsprinted)
    conditions = current.qdrant.queries[-1]["query_filter"].model_dump(mode="json")
    assert conditions["must"][0]["match"]["value"] == "p1"
    if placement == "backlog":
        assert conditions["must"][2]["is_empty"]["key"] == "sprint_id"
        assert conditions["must"][-1]["match"]["any"] == ["todo", "in_progress", "blocked"]
    else:
        alternatives = conditions["must"][-1]["should"]
        assert alternatives[0]["match"]["any"] == (sprint_ids or ["__no_sprint_match__"])
        assert len(alternatives) == (2 if placement == "archive" and include_unsprinted else 1)


def test_task_inventory_reset_and_missing_point(monkeypatch):
    current = service(monkeypatch)
    task_id = "11111111-1111-4111-8111-111111111111"
    assert current.task_point("p1", task_id) is None
    current.qdrant.scrolled = [
        (
            [
                SimpleNamespace(id=task_id, payload={"source_version": 1}),
                SimpleNamespace(
                    id=current.task_marker_id("p1"),
                    payload={"record_type": "task_index_marker", "epoch": "e1"},
                ),
            ],
            "next",
        ),
        ([SimpleNamespace(id="22222222-2222-4222-8222-222222222222", payload=None)], None),
    ]
    assert current.task_inventory("p1") == {
        task_id: {"source_version": 1},
        "22222222-2222-4222-8222-222222222222": {},
    }
    current.delete_task_points("p1", [])
    assert current.qdrant.deleted == []
    current.delete_task_points("p1", ["22222222-2222-4222-8222-222222222222"])
    assert current.qdrant.deleted[0]["collection_name"] == current.task_collection("p1")
    assert current.qdrant.deleted[0]["wait"] is True
    name = current.task_collection("p1")
    assert current.reset_task_collection("p1") == name
    assert current.qdrant.deleted_collections == [name]


def test_observed_task_vector_failures_preserve_provider_usage(monkeypatch):
    current = service(monkeypatch)
    embedding = observed_embedding()
    monkeypatch.setattr(current, "embed_observed", lambda values, **kwargs: embedding)
    task_id = "11111111-1111-4111-8111-111111111111"

    def fail(*args, **kwargs):
        raise RuntimeError("qdrant offline")

    current.qdrant.upsert = fail
    with pytest.raises(VectorOperationError, match="qdrant offline") as upsert_error:
        current.upsert_task_observed("p1", task_id, "document", {"task_id": task_id})
    assert upsert_error.value.embedding is embedding

    current.qdrant.set_payload = fail
    with pytest.raises(VectorOperationError, match="qdrant offline") as payload_error:
        current.update_task_payload("p1", task_id, {"source_version": 2})
    assert payload_error.value.embedding is None
    assert payload_error.value.vector_store_duration_ms >= 0

    current.qdrant.query_points = fail
    with pytest.raises(VectorOperationError, match="qdrant offline") as search_error:
        current.search_tasks_observed("p1", "query", 5)
    assert search_error.value.embedding is embedding


def test_observed_vector_failures_preserve_successful_embedding(monkeypatch):
    current = service(monkeypatch)
    embedding = observed_embedding()
    monkeypatch.setattr(current, "embed_observed", lambda values, **kwargs: embedding)

    def fail(*args, **kwargs):
        raise RuntimeError("qdrant offline")

    current.qdrant.upsert = fail
    with pytest.raises(VectorOperationError, match="qdrant offline") as upsert_error:
        current.upsert_observed("p1", "m1", "memory", {"status": "active"})
    assert upsert_error.value.embedding is embedding
    assert upsert_error.value.vector_store_duration_ms >= 0
    assert isinstance(upsert_error.value.__cause__, RuntimeError)

    current.qdrant.query_points = fail
    with pytest.raises(VectorOperationError, match="qdrant offline") as search_error:
        current.search_observed("p1", "query", 4)
    assert search_error.value.embedding is embedding
    assert search_error.value.vector_store_duration_ms >= 0
    assert isinstance(search_error.value.__cause__, RuntimeError)


def test_inventory_delete_and_reset(monkeypatch):
    current = service(monkeypatch)
    current.qdrant.scrolled = [
        ([SimpleNamespace(id="m1", payload={"status": "active"})], "next"),
        ([SimpleNamespace(id="m2", payload=None)], None),
    ]
    assert current.inventory("p1") == {"m1": {"status": "active"}, "m2": {}}
    current.delete_points("p1", [])
    assert not current.qdrant.deleted
    current.delete_points("p1", ["m2"])
    assert current.qdrant.deleted[0]["points_selector"].points == ["m2"]
    name = current.collection("p1")
    assert current.reset_collection("p1") == name
    assert current.qdrant.deleted_collections == [name]


def test_cached_factories(monkeypatch):
    local_model.cache_clear()
    embedding_service.cache_clear()
    fake_embedding = SimpleNamespace()
    monkeypatch.setitem(
        __import__("sys").modules,
        "fastembed",
        SimpleNamespace(TextEmbedding=lambda **_: fake_embedding),
    )
    assert local_model("model") is fake_embedding
    monkeypatch.setattr("dduo_solo_founder.embeddings.EmbeddingService", lambda: "service")
    assert embedding_service() == "service"
    embedding_service.cache_clear()


def test_local_provider_reports_missing_optional_dependency(monkeypatch):
    local_model.cache_clear()
    monkeypatch.setitem(sys.modules, "fastembed", None)
    with pytest.raises(RuntimeError, match="optional dDuo Solo Founder 'local' dependency"):
        local_model("model")
    local_model.cache_clear()
