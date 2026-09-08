from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from dduo_solo_founder.embeddings import (
    EmbeddingCall,
    EmbeddingUsage,
    VectorOperationError,
    VectorSearchResult,
)
from dduo_solo_founder.models import (
    Memory,
    ObservabilityEvent,
    Project,
    RetrievalRun,
    Session,
    Turn,
)
from dduo_solo_founder.retrieval import (
    SEMANTIC_QUERY_CHAR_LIMIT,
    bounded_semantic_query,
    build_retrieval_query,
    is_elliptical_prompt,
    replay_retrieval,
    retrieve_context,
)
from dduo_solo_founder import retrieval as retrieval_module


class RetrievalEmbeddings:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.queries = []

    def search(self, project_id, query, limit):
        self.queries.append((project_id, query, limit))
        if self.error:
            raise self.error
        return self.results


class QueryAwareEmbeddings(RetrievalEmbeddings):
    def __init__(self, results_by_query):
        super().__init__()
        self.results_by_query = results_by_query

    def search(self, project_id, query, limit):
        self.queries.append((project_id, query, limit))
        result = self.results_by_query.get(query, [])
        if isinstance(result, Exception):
            raise result
        return result


def test_retrieval_prompt_and_score_helpers_handle_empty_and_duplicate_inputs():
    assert is_elliptical_prompt("!!!") is True
    assert retrieval_module._score_map(
        [
            {"score": 0.99},
            {"id": "memory", "score": 0.2},
            {"id": "memory", "score": 0.8},
        ]
    ) == {"memory": 0.8}

    long_prompt = "ORIGINAL-" + ("x" * 20_000) + "-FINAL-CORRECTION"
    bounded = bounded_semantic_query(long_prompt)
    assert len(bounded) == SEMANTIC_QUERY_CHAR_LIMIT
    assert bounded.startswith("ORIGINAL-")
    assert bounded.endswith("-FINAL-CORRECTION")
    assert "semantic query compacted" in bounded


async def seed_retrieval(db):
    project = Project(id=str(uuid.uuid4()), name="Project", root_path="/tmp/project")
    session = Session(project_id=project.id, client="codex", external_id="s1")
    db.add_all([project, session])
    await db.flush()
    previous = Turn(
        project_id=project.id,
        session_id=session.id,
        external_id="t0",
        user_prompt="Android release checks",
        assistant_response="Run the real-user flow",
        committed=True,
    )
    current = Turn(
        project_id=project.id,
        session_id=session.id,
        external_id="t1",
        user_prompt="What is next?",
    )
    memories = [
        Memory(
            project_id=project.id,
            node_type="episode",
            node_key="release",
            text="Android release testing is underway.",
        ),
        Memory(
            project_id=project.id,
            node_type="reusable_fact",
            node_key="platform",
            text="The iOS app is already live.",
        ),
        Memory(
            project_id=project.id,
            node_type="heuristic",
            node_key="release-gate",
            text="Do not release with blocking test failures.",
        ),
    ]
    db.add_all([previous, current, *memories])
    await db.commit()
    return project, session, current, memories


async def test_query_uses_recent_session_context(db_factory):
    async with db_factory() as db:
        _, session, _, _ = await seed_retrieval(db)
        query = await build_retrieval_query(
            db, session_id=session.id, current_prompt="What is next?"
        )
        assert "Android release checks" in query
        assert query.endswith("What is next?")


async def test_query_never_reuses_committed_off_record_turns(db_factory):
    async with db_factory() as db:
        project, session, _, _ = await seed_retrieval(db)
        secret = await db.scalar(
            select(Turn).where(
                Turn.session_id == session.id,
                Turn.external_id == "t0",
            )
        )
        secret.user_prompt = "VPS_PASSWORD=must-never-reappear"
        secret.off_record = True
        db.add(
            Turn(
                project_id=project.id,
                session_id=session.id,
                external_id="visible-history",
                user_prompt="Continue the Android release",
                assistant_response="Continue",
                committed=True,
                off_record=False,
            )
        )
        await db.commit()

        query = await build_retrieval_query(
            db,
            session_id=session.id,
            current_prompt="What is next?",
        )

        assert "VPS_PASSWORD" not in query
        assert "Continue the Android release" in query
        assert query.endswith("What is next?")


async def test_retrieval_selects_all_qualified_direct_memories_and_persists_audit(
    db_factory,
):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        embeddings = RetrievalEmbeddings(
            [{"id": item.id, "score": 0.8 - index * 0.05} for index, item in enumerate(memories)]
        )
        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )
        await db.commit()
        assert result["status"] == "context_ready"
        assert {item["node_type"] for item in result["items"]} == {
            "episode",
            "reusable_fact",
            "heuristic",
        }
        run = await db.scalar(select(RetrievalRun))
        assert run.turn_id == current.id
        assert set(run.selected_memory_ids) == {item.id for item in memories}
        assert len(embeddings.queries) == 1
        assert result["metadata"]["selection_mode"] == "direct"
        assert set(result["metadata"]["selected_reasons"].values()) == {"direct"}
        observations = list((await db.scalars(select(ObservabilityEvent))).all())
        assert {item.operation for item in observations if item.category == "embedding"} == {
            "embedding.retrieval_latest"
        }
        pipeline = next(item for item in observations if item.operation == "retrieval.pipeline")
        assert pipeline.request_count == 1
        replayed = await replay_retrieval(db, run.id)
        assert replayed["metadata"]["replayed"] is True
        assert [item["id"] for item in replayed["items"]] == run.selected_memory_ids
        assert replayed["metadata"]["selection_mode"] == "direct"
        assert replayed["metadata"]["selected_reasons"] == result["metadata"]["selected_reasons"]
        assert await replay_retrieval(db, str(uuid.uuid4())) is None


async def test_retrieval_degrades_without_discarding_the_turn(db_factory):
    async with db_factory() as db:
        project, session, current, _ = await seed_retrieval(db)
        embeddings = RetrievalEmbeddings(error=RuntimeError("offline"))
        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )
        assert result["status"] == "degraded"
        assert result["items"] == []
        assert "offline" in result["metadata"]["degraded_reasons"][0]
        assert len(result["metadata"]["degraded_reasons"]) == 1
        assert len(embeddings.queries) == 1

        observations = list((await db.scalars(select(ObservabilityEvent))).all())
        embedding_observations = [item for item in observations if item.category == "embedding"]
        assert len(embedding_observations) == 1
        assert {item.status for item in embedding_observations} == {"failed"}
        assert {item.measurement_source for item in embedding_observations} == {"unavailable"}
        assert {item.request_count for item in embedding_observations} == {0}
        pipeline = next(item for item in observations if item.operation == "retrieval.pipeline")
        assert pipeline.status == "degraded"
        assert pipeline.details["degraded_count"] == 1
        assert pipeline.request_count == 1


async def test_retrieval_cannot_persist_after_authority_freezes_mid_provider_call(
    db_factory, monkeypatch
):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        embeddings = RetrievalEmbeddings([{"id": memories[0].id, "score": 0.9}])

        async def frozen_after_provider(_db, _project_id):
            raise HTTPException(409, "project authority is read-only on this memory node")

        monkeypatch.setattr(
            retrieval_module, "lock_writable_project", frozen_after_provider
        )
        with pytest.raises(HTTPException) as error:
            await retrieve_context(
                db,
                embeddings,
                project_id=project.id,
                session_id=session.id,
                turn_id=current.id,
                current_prompt=current.user_prompt,
            )
        assert error.value.status_code == 409
        assert len(embeddings.queries) == 1
        assert list((await db.scalars(select(RetrievalRun))).all()) == []
        assert list((await db.scalars(select(ObservabilityEvent))).all()) == []


async def test_retrieval_preflight_failure_skips_searches_and_is_observed(db_factory):
    async with db_factory() as db:
        project, session, current, _ = await seed_retrieval(db)

        class FailingPreflightEmbeddings(RetrievalEmbeddings):
            def __init__(self):
                super().__init__()
                self.collections = []

            def ensure_collection(self, project_id):
                self.collections.append(project_id)
                raise RuntimeError("collection offline")

        embeddings = FailingPreflightEmbeddings()
        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )

        assert result["status"] == "degraded"
        assert result["items"] == []
        assert result["metadata"]["degraded_reasons"] == [
            "vector_search_unavailable:collection offline"
        ]
        assert embeddings.collections == [project.id]
        assert embeddings.queries == []

        observations = list((await db.scalars(select(ObservabilityEvent))).all())
        embedding_observations = [item for item in observations if item.category == "embedding"]
        assert embedding_observations == []
        pipeline = next(item for item in observations if item.operation == "retrieval.pipeline")
        assert pipeline.request_count == 0
        assert pipeline.details["preflight_failed"] is True


async def test_retrieval_observes_direct_partial_failure_without_retrying_history(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        embedding = EmbeddingCall(
            vectors=[[0.1]],
            usage=EmbeddingUsage(
                provider="openai",
                model="text-embedding-3-large",
                input_tokens=7,
                reported_total_tokens=7,
                measurement_source="provider_reported",
            ),
            provider_duration_ms=3,
        )

        class PartialFailureEmbeddings:
            def __init__(self):
                self.collections = []
                self.queries = []

            def ensure_collection(self, project_id):
                self.collections.append(project_id)

            def search_observed(self, project_id, query, limit):
                self.queries.append((project_id, query, limit))
                if query == current.user_prompt:
                    raise VectorOperationError(
                        "qdrant offline",
                        embedding=embedding,
                        vector_store_duration_ms=5,
                    )
                return VectorSearchResult(
                    items=[{"id": memories[0].id, "score": 0.8}],
                    embedding=embedding,
                    vector_store_duration_ms=4,
                )

        embeddings = PartialFailureEmbeddings()
        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )

        assert result["status"] == "degraded"
        assert result["items"] == []
        assert result["metadata"]["degraded_reasons"] == [
            "vector_search_unavailable:qdrant offline"
        ]
        assert embeddings.collections == [project.id]
        assert len(embeddings.queries) == 1

        observations = {
            item.operation: item for item in (await db.scalars(select(ObservabilityEvent))).all()
        }
        latest = observations["embedding.retrieval_latest"]
        assert "embedding.retrieval_history" not in observations
        assert latest.status == "partial_failure"
        assert latest.input_tokens == 7
        assert latest.reported_total_tokens == 7
        assert latest.provider_duration_ms == 3
        assert latest.request_count == 1
        assert latest.vector_store_duration_ms == 5
        assert latest.cost_usd is not None
        assert latest.details["error_code"] == "vector_operation_failed"
        assert observations["retrieval.pipeline"].request_count == 1
        assert observations["retrieval.pipeline"].candidate_count == 0
        assert observations["retrieval.pipeline"].selected_count == 0


async def test_direct_retrieval_returns_two_without_filling_to_a_fixed_k(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        embeddings = RetrievalEmbeddings(
            [
                {"id": memories[0].id, "score": 0.84},
                {"id": memories[1].id, "score": 0.67},
                {"id": memories[2].id, "score": 0.49},
            ]
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
            requested_limit=20,
        )

        assert [item["id"] for item in result["items"]] == [
            memories[0].id,
            memories[1].id,
        ]
        assert result["metadata"]["selection_mode"] == "direct"
        assert result["metadata"]["dropped_memory_ids"] == [memories[2].id]
        assert len(embeddings.queries) == 1


async def test_retrieval_returns_zero_when_neither_query_qualifies(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        embeddings = RetrievalEmbeddings([{"id": memory.id, "score": 0.49} for memory in memories])

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )

        assert result["items"] == []
        assert result["status"] == "insufficient_context"
        assert result["metadata"]["selection_mode"] == "history_fallback"
        assert len(embeddings.queries) == 2
        observations = list((await db.scalars(select(ObservabilityEvent))).all())
        pipeline = next(item for item in observations if item.operation == "retrieval.pipeline")
        assert pipeline.request_count == 2


async def test_retrieval_reports_history_fallback_failure_without_losing_direct_audit(
    db_factory,
):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        history_query = f"Android release checks\n{current.user_prompt}"
        embeddings = QueryAwareEmbeddings(
            {
                current.user_prompt: [{"id": memories[0].id, "score": 0.1}],
                history_query: RuntimeError("history index unavailable"),
            }
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )

        assert result["status"] == "degraded"
        assert result["items"] == []
        assert result["metadata"]["selection_mode"] == "history_fallback"
        assert result["metadata"]["degraded_reasons"] == [
            "vector_search_unavailable:history index unavailable"
        ]


async def test_retrieval_returns_seven_same_type_memories_despite_legacy_limit(
    db_factory,
):
    async with db_factory() as db:
        project, session, current, _ = await seed_retrieval(db)
        memories = [
            Memory(
                project_id=project.id,
                node_type="reusable_fact",
                node_key=f"fact-{index}",
                text=f"Relevant project fact {index}.",
            )
            for index in range(7)
        ]
        db.add_all(memories)
        await db.commit()
        embeddings = RetrievalEmbeddings(
            [
                {"id": memory.id, "score": 0.90 - index * 0.01}
                for index, memory in enumerate(memories)
            ]
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
            requested_limit=1,
        )

        assert len(result["items"]) == 7
        assert result["metadata"]["bucket_counts"] == {"reusable_fact": 7}
        assert len(embeddings.queries) == 1


@pytest.mark.parametrize("prompt", ["procedi", "continue"])
async def test_elliptical_prompt_uses_history_fallback_in_italian_and_english(db_factory, prompt):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        current.user_prompt = prompt
        await db.commit()
        history_query = f"Android release checks\n{prompt}"
        embeddings = QueryAwareEmbeddings(
            {
                prompt: [{"id": memories[1].id, "score": 0.8}],
                history_query: [{"id": memories[0].id, "score": 0.85}],
            }
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=prompt,
        )

        assert is_elliptical_prompt(prompt) is True
        assert [item["id"] for item in result["items"]] == [memories[0].id]
        assert result["metadata"]["selection_mode"] == "history_fallback"
        assert result["metadata"]["selected_reasons"] == {memories[0].id: "history_fallback"}
        assert [query for _, query, _ in embeddings.queries] == [prompt, history_query]
        observations = list((await db.scalars(select(ObservabilityEvent))).all())
        pipeline = next(item for item in observations if item.operation == "retrieval.pipeline")
        assert pipeline.request_count == 2


async def test_qualified_direct_result_prevents_history_bleed(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        history_query = f"Android release checks\n{current.user_prompt}"
        embeddings = QueryAwareEmbeddings(
            {
                current.user_prompt: [{"id": memories[1].id, "score": 0.8}],
                history_query: [{"id": memories[0].id, "score": 0.95}],
            }
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
        )

        assert [item["id"] for item in result["items"]] == [memories[1].id]
        assert [query for _, query, _ in embeddings.queries] == [current.user_prompt]
        assert memories[0].id not in result["metadata"]["selected_memory_ids"]


async def test_exact_node_key_reference_qualifies_and_sorts_first(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        prompt = f"procedi {memories[0].node_key}"
        embeddings = RetrievalEmbeddings(
            [
                {"id": memories[1].id, "score": 0.9},
                {"id": memories[0].id, "score": 0.2},
            ]
        )

        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=prompt,
        )

        assert [item["id"] for item in result["items"]] == [
            memories[0].id,
            memories[1].id,
        ]
        assert result["metadata"]["selected_reasons"][memories[0].id] == "exact_reference"
        assert is_elliptical_prompt(prompt) is True
        assert len(embeddings.queries) == 1


async def test_retrieval_keeps_latest_active_duplicate_and_drops_unknown(db_factory):
    async with db_factory() as db:
        project, session, current, memories = await seed_retrieval(db)
        duplicate = Memory(
            project_id=project.id,
            node_type="episode",
            node_key=memories[0].node_key,
            text="Latest authoritative revision",
            revision=2,
        )
        unknown = Memory(
            project_id=project.id,
            node_type="unknown",
            node_key="ignored",
            text="Ignored type",
        )
        db.add_all([duplicate, unknown])
        await db.commit()

        class EnsuringEmbeddings(RetrievalEmbeddings):
            def __init__(self, results):
                super().__init__(results)
                self.collections = []

            def ensure_collection(self, project_id):
                self.collections.append(project_id)

        rows = [*memories, duplicate, unknown]
        embeddings = EnsuringEmbeddings(
            [{"id": item.id, "score": 0.9 - index * 0.01} for index, item in enumerate(rows)]
        )
        result = await retrieve_context(
            db,
            embeddings,
            project_id=project.id,
            session_id=session.id,
            turn_id=current.id,
            current_prompt=current.user_prompt,
            requested_limit=1,
        )
        assert embeddings.collections == [project.id]
        assert duplicate.id in result["metadata"]["selected_memory_ids"]
        assert memories[0].id in result["metadata"]["dropped_memory_ids"]
        assert unknown.id in result["metadata"]["dropped_memory_ids"]
        assert len(result["items"]) == 3
