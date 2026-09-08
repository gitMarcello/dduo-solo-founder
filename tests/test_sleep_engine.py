from __future__ import annotations

import uuid
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dduo_solo_founder.memory_engine import explain_memory
from dduo_solo_founder import sleep_engine
from dduo_solo_founder.models import (
    Artifact,
    Memory,
    ObservabilityEvent,
    OutboxEvent,
    Project,
    RawEvent,
    Session,
    SleepJob,
    Turn,
    TurnArtifact,
)
from dduo_solo_founder.sleep_engine import (
    CliSleepProvider,
    MEMORY_CONSOLIDATION_INSTRUCTIONS,
    SleepGeneration,
    SleepGenerationError,
    SleepProviderError,
    process_one_sleep_job,
    recover_interrupted_jobs,
    resume_project_sleep,
    schedule_due_sleep,
    schedule_project_sleep,
    schedule_sleep,
    utcnow,
)
from dduo_solo_founder.schemas import MemoryActionPayload


def test_sleep_instructions_keep_control_plane_and_task_echoes_out_of_memory():
    instructions = " ".join(MEMORY_CONSOLIDATION_INSTRUCTIONS.split())
    assert "task CRUD echoes" in instructions
    assert "Do not create tasks" in instructions
    assert "plugin telemetry" in instructions
    assert "language (`it` or `en`)" in instructions
    assert sleep_engine.MEMORY_LANGUAGE_POLICY_VERSION in instructions
    assert "last substantive user message" in instructions
    assert "Never translate topic_id" in instructions
    assert "Write memory text, labels and queries in Italian by default" in instructions
    assert "clearly English user request or an explicit request to use English" in instructions


def test_sleep_executor_preference_migration_leaves_projects_neutral_and_preserves_jobs():
    source = (
        Path(__file__).parents[1]
        / "migrations/versions/f3b4c5d6e7f8_add_project_sleep_executor_preference.py"
    )
    spec = importlib.util.spec_from_file_location("sleep_executor_migration", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert ScriptDirectory(str(source.parents[1])).get_current_head() == module.revision

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE sleep_jobs (id VARCHAR(36) PRIMARY KEY, provider VARCHAR(20) NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO projects VALUES ('p1')")
        connection.exec_driver_sql("INSERT INTO sleep_jobs VALUES ('job-1', 'claude')")

        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()

        assert connection.exec_driver_sql(
            "SELECT sleep_executor_preference FROM projects WHERE id = 'p1'"
        ).scalar() is None
        assert connection.exec_driver_sql(
            "SELECT executor_preference FROM sleep_jobs WHERE id = 'job-1'"
        ).scalar() == "claude"
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO projects (id, sleep_executor_preference) VALUES ('p2', 'unsupported')"
            )
    engine.dispose()


def test_topic_language_resolution_prioritizes_requests_and_is_conservative():
    topic = SimpleNamespace(source_turn_ids=["first", "second"])
    turns = {
        "first": {"user_message": {"content": "La release deve essere sempre sicura."}},
        "second": {"user_message": {"content": "Ricordalo in inglese, per favore."}},
    }
    assert sleep_engine._topic_memory_language(topic, turns) == "en"

    turns["second"]["user_message"]["content"] = "Traduci da English into Italian."
    assert sleep_engine._topic_memory_language(topic, turns) == "it"

    turns["second"]["user_message"]["content"] = "Scrivilo in lingua inglese."
    assert sleep_engine._topic_memory_language(topic, turns) == "en"

    turns["second"]["user_message"]["content"] = "ok"
    assert sleep_engine._topic_memory_language(topic, turns) == "it"

    turns["second"]["user_message"]["content"] = "La release deve work safely with the database"
    assert sleep_engine._topic_memory_language(topic, turns) is None

    turns["second"]["user_message"]["content"] = "```python\nprint('the result')\n```"
    assert sleep_engine._topic_memory_language(topic, turns) is None

    turns["second"]["user_message"]["content"] = (
        "Questo è importante:\n> The release must always preserve database safety."
    )
    assert sleep_engine._topic_memory_language(topic, turns) == "it"


def test_ambiguous_revision_uses_existing_language_not_the_model_enum():
    existing = Memory(
        id="existing-memory",
        project_id="project",
        node_type="episode",
        node_key="release",
        text="The release must always preserve database safety.",
        metadata_json={"language": "en"},
    )
    revision = MemoryActionPayload(
        action="replace_current",
        target_node_type="episode",
        target_node_key="rilascio",
        text="The release must always preserve database safety.",
        source_node_ids=[existing.id],
    )
    topic = SimpleNamespace(language="it", memory_actions=[revision])
    related = {existing.id: existing}
    assert sleep_engine._resolved_topic_language(None, topic, related) == "en"
    assert sleep_engine._resolved_action_language(None, revision, related) == "en"

    create = revision.model_copy(
        update={"action": "create", "target_node_key": "new-memory", "source_node_ids": []}
    )
    assert sleep_engine._resolved_action_language(None, create, related) == "it"


@pytest.mark.parametrize(
    "source, expected",
    [
        ("# Files pasted by the user:\n## Trace: /tmp/trace.txt\n\n"
         "## My request:\nho fatto due scansioni", None),
        ("# Files mentioned by the user:\n## Image: /tmp/image.png\n\n"
         "## My request:\nPlease preserve the existing release tests.", "en"),
        ("# Files pasted by the user:\n## Trace: /tmp/trace.txt", None),
        ("2026-09-08 13:02:27\napi-1 | the server is starting\n"
         "&#x20; elapsedMs: 3023\n\"reason\": the process is running\n"
         "ho fatto due scansioni", None),
        ("Language: English", "en"),
        ("Lingua: italiano", "it"),
        ("Note: please preserve the existing release tests.", "en"),
        ("Remember: the release must always preserve database safety.", "en"),
        ("Please preserve the existing release tests.\n"
         "api-1 | lingua: italiano", "en"),
    ],
)
def test_language_preference_ignores_attachment_headers_and_unfenced_logs(source, expected):
    topic = SimpleNamespace(source_turn_ids=["turn"])
    turns = {"turn": {"user_message": {"content": source}}}
    assert sleep_engine._topic_memory_language(topic, turns) == expected
    # No source text is changed by preference detection.
    assert turns["turn"]["user_message"]["content"] == source


def test_generated_language_difference_is_advisory():
    assert sleep_engine._generated_language_differs(
        "The release must always preserve database safety.", "it"
    )
    assert not sleep_engine._generated_language_differs("Android release API v2", "it")


def test_stored_language_preserves_status_target_and_uses_declared_for_ambiguous_text():
    target = Memory(id="existing", text="API", metadata_json={"language": "en"})
    action = MemoryActionPayload(
        action="set_status", target_node_type="episode", target_node_key="api",
        status="superseded", source_node_ids=[target.id],
    )
    assert sleep_engine._stored_action_language(action, "it", {target.id: target}) == "en"
    ambiguous = action.model_copy(update={"action": "create", "text": "Android API v2"})
    assert sleep_engine._stored_action_language(ambiguous, "en", {}) == "en"


def test_sleep_batch_bounds_and_retry_messages_are_deterministic():
    oversized = {
        "turn_id": "one",
        "user_message": {"content": "u" * 20_000, "artifacts": [{"extracted_text": "a" * 5_000}]},
        "assistant_message": {"content": "a" * 20_000},
    }
    normal = {
        "turn_id": "two",
        "user_message": {"content": "short", "artifacts": []},
        "assistant_message": {"content": "short"},
    }
    bounded = sleep_engine._bounded_turn_batch([oversized, normal], 3_000)
    assert [item["turn_id"] for item in bounded] == ["one"]
    assert len(bounded[0]["user_message"]["content"]) == 2_000
    assert sleep_engine._retry_delay(SleepProviderError("auth_required", "x"), 1) is None
    assert (
        sleep_engine._retry_delay(SleepProviderError("rate_limited", "x", retry_after_seconds=7), 1)
        == 7
    )
    assert sleep_engine._retry_delay(SleepProviderError("bridge_unavailable", "x"), 5) == 3_600
    assert (
        sleep_engine._failure_summary(SleepProviderError("rate_limited", "x"), "claude")
        == "Claude has a temporary usage limit."
    )
    assert "invalid structured" in sleep_engine._failure_summary(
        SleepProviderError("invalid_model_output", "x"), "codex"
    )


class NoHits:
    def search(self, project_id, query, limit):
        return []


class SuccessfulProvider:
    def __init__(self, turn_id: str, db=None):
        self.turn_id = turn_id
        self.db = db
        self.calls = []
        self.message_id = f"user:{turn_id}"

    async def generate(self, **kwargs):
        if self.db is not None:
            assert not self.db.in_transaction()
        self.calls.append(kwargs)
        self.message_id = kwargs["payload"]["conversation_turns"][0]["user_message"]["message_id"]
        return {
            "topics": [
                {
                    "topic_id": "release",
                    "label": "Android release",
                    "query_text": "Android real-user release testing",
                    "language": "en",
                    "source_turn_ids": [self.turn_id],
                    "source_message_ids": [self.message_id],
                    "memory_actions": [
                        {
                            "action": "create",
                            "target_node_type": "episode",
                            "target_node_key": "android-release",
                            "text": "The founder is completing Android real-user release testing.",
                            "status": "active",
                            "source_message_ids": [self.message_id],
                            "source_node_ids": [],
                            "artifact_ids": [
                                item["artifact_id"]
                                for item in kwargs["payload"]["candidate_artifacts"][:1]
                            ],
                            "metadata": {"occurred_at": "2026-07-12"},
                        }
                    ],
                }
            ]
        }


class FailingProvider:
    async def generate(self, **kwargs):
        raise SleepProviderError(
            "auth_required",
            "Claude needs to be connected again.",
            diagnostic="Claude CLI: authentication expired",
        )


class InvalidSourceProvider:
    async def generate(self, **kwargs):
        return {
            "topics": [
                {
                    "topic_id": "invalid",
                    "label": "Invalid",
                    "query_text": "Invalid source",
                    "language": "en",
                    "source_turn_ids": ["outside-batch"],
                    "source_message_ids": [],
                }
            ]
        }


class NoisyMessageProvider(SuccessfulProvider):
    async def generate(self, **kwargs):
        result = await super().generate(**kwargs)
        result["topics"][0]["source_message_ids"] = ["invented-message-id"]
        result["topics"][0]["memory_actions"][0]["source_message_ids"] = ["invented-message-id"]
        return result


async def seed_pending(
    db, *, client="codex", off_record=False, user_prompt="I must test Android as a real user."
):
    project = Project(id=str(uuid.uuid4()), name="Project", root_path="/tmp/project")
    session = Session(project_id=project.id, client=client, external_id=str(uuid.uuid4()))
    db.add_all([project, session])
    await db.flush()
    turn = Turn(
        project_id=project.id,
        session_id=session.id,
        external_id=str(uuid.uuid4()),
        user_prompt=user_prompt,
        assistant_response="Run the release flow.",
        committed=True,
        sleep_status="pending",
        off_record=off_record,
    )
    db.add(turn)
    await db.flush()
    db.add_all(
        [
            RawEvent(
                project_id=project.id,
                session_id=session.id,
                turn_id=turn.id,
                event_type="user_prompt",
                payload={"text": turn.user_prompt},
                actor="user",
            ),
            RawEvent(
                project_id=project.id,
                session_id=session.id,
                turn_id=turn.id,
                event_type="assistant_response",
                payload={"text": turn.assistant_response},
            ),
        ]
    )
    artifact = Artifact(
        project_id=project.id,
        content_hash=uuid.uuid4().hex * 2,
        kind="document",
        filename="release.md",
        mime_type="text/markdown",
        extracted_text="Android release evidence",
        summary="Release checklist",
    )
    db.add(artifact)
    await db.flush()
    db.add(TurnArtifact(turn_id=turn.id, artifact_id=artifact.id))
    await db.commit()
    return project, session, turn


async def test_sleep_runs_one_model_pass_and_commits_memory_atomically(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="manual",
        )
        duplicate = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="manual",
        )
        await db.commit()
        assert duplicate.id == job.id
        provider = SuccessfulProvider(turn.id, db)
        assert await process_one_sleep_job(db, NoHits(), provider) is True
        await db.refresh(job)
        await db.refresh(turn)
        memory = await db.scalar(select(Memory))
        assert job.status == "completed" and turn.sleep_status == "consolidated"
        assert memory.node_key == "android-release" and memory.sleep_job_id == job.id
        assert memory.metadata_json["language"] == "en"
        assert (
            memory.metadata_json["language_policy_version"]
            == sleep_engine.MEMORY_LANGUAGE_POLICY_VERSION
        )
        assert memory.source_message_ids[0].count("-") == 4
        provenance = await explain_memory(db, project.id, memory.id)
        assert provenance["source_messages"][0]["event_type"] == "user_prompt"
        assert provenance["source_artifacts"][0]["filename"] == "release.md"
        assert len(provider.calls) == 1
        assert provider.calls[0]["task"] == "memory_consolidation"
        assert job.result["topics"] == 1 and job.result["created"] == 1
        assert await db.scalar(select(func.count(OutboxEvent.id))) == 1


@pytest.mark.parametrize(
    "user_language, declared, text_language",
    [("it", "en", "en"), ("it", "it", "en"), ("en", "it", "it"),
     ("en", "en", "it"), ("it", "en", "it")],
)
async def test_sleep_saves_valid_memory_despite_language_mismatch(
    db_factory, user_language, declared, text_language
):
    texts = {
        "it": "La release deve sempre preservare la sicurezza del database.",
        "en": "The release must always preserve database safety.",
    }
    async with db_factory() as db:
        project, session, turn = await seed_pending(db, user_prompt=texts[user_language])
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class DifferentLanguageProvider(SuccessfulProvider):
            async def generate(self, **kwargs):
                result = await super().generate(**kwargs)
                topic = result["topics"][0]
                topic["language"] = declared
                topic["label"] = texts[text_language]
                topic["query_text"] = texts[text_language]
                topic["memory_actions"][0]["text"] = texts[text_language]
                return result

        provider = DifferentLanguageProvider(turn.id)
        assert await process_one_sleep_job(db, NoHits(), provider) is True
        await db.refresh(job)
        await db.refresh(turn)
        memory = await db.scalar(select(Memory))
        assert job.status == "completed" and job.error_kind is None and job.retry_at is None
        assert turn.sleep_status == "consolidated"
        assert job.result["language_warnings"] == 1
        assert memory.text == texts[text_language]
        assert memory.metadata_json["language"] == text_language
        assert memory.node_key == "android-release"
        assert memory.source_turn_ids == [turn.id]
        assert await process_one_sleep_job(db, NoHits(), provider) is False
        assert len(provider.calls) == 1
        assert await db.scalar(select(func.count(Memory.id))) == 1


async def test_sleep_accepts_mixed_language_actions_without_splitting_the_topic(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(
            db, user_prompt="La release deve preservare il database e richiede test Android."
        )
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class MixedLanguageProvider(SuccessfulProvider):
            async def generate(self, **kwargs):
                result = await super().generate(**kwargs)
                topic = result["topics"][0]
                topic["language"] = "it"
                topic["label"] = topic["query_text"] = "Test della release Android"
                topic["memory_actions"].append({
                    **topic["memory_actions"][0],
                    "target_node_key": "database-safety",
                    "text": "La release deve sempre preservare la sicurezza del database.",
                })
                return result

        provider = MixedLanguageProvider(turn.id)
        assert await process_one_sleep_job(db, NoHits(), provider)
        await db.refresh(job)
        memories = list((await db.scalars(select(Memory))).all())
        assert job.status == "completed" and job.retry_at is None
        assert job.result["topics"] == 1 and job.result["language_warnings"] == 1
        assert {memory.metadata_json["language"] for memory in memories} == {"it", "en"}
        assert {memory.node_key for memory in memories} == {"android-release", "database-safety"}
        assert all(memory.source_turn_ids == [turn.id] for memory in memories)
        assert len(provider.calls) == 1


async def test_sleep_with_attachment_wrappers_consolidates_without_retries(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(
            db, user_prompt="# Files pasted by the user:\n## Trace: /tmp/trace.txt\n\n"
            "## My request:\napi-1 | the server is starting\nho fatto due scansioni",
        )
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        assert await process_one_sleep_job(db, NoHits(), SuccessfulProvider(turn.id)) is True
        await db.refresh(job)
        assert job.status == "completed" and job.retry_at is None
        assert job.result["language_warnings"] == 1
        assert await db.scalar(select(func.count(Memory.id))) == 1


async def test_failed_cli_keeps_job_waiting_and_turn_pending(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db, client="claude")
        job = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="idle",
        )
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), FailingProvider()) is True
        await db.refresh(job)
        await db.refresh(turn)
        assert job.status == "waiting"
        assert job.error_kind == "auth_required"
        assert job.last_error == "Claude CLI: authentication expired"
        assert job.retry_at is None
        assert turn.sleep_status == "pending"
        assert await db.scalar(select(func.count(Memory.id))) == 0
        assert await process_one_sleep_job(db, NoHits(), FailingProvider()) is False


async def test_invalid_sleep_output_records_partial_provider_usage(db_factory):
    class InvalidObservedProvider:
        async def generate_observed(self, **kwargs):
            return SleepGeneration.from_bridge(
                output={"topics": "private invalid model output"},
                provider="codex",
                model="gpt-5-codex",
                duration_ms=321,
                usage={"cached_input_tokens": 17},
                instructions=kwargs["instructions"],
                payload=kwargs["payload"],
            )

    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), InvalidObservedProvider()) is True
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "sleep.memory_consolidation"
            )
        )
        await db.refresh(job)
        assert job.status == "waiting" and event.status == "failed"
        assert event.provider == "codex" and event.model == "gpt-5-codex"
        assert event.provider_duration_ms == 321
        assert event.measurement_source == "provider_reported"
        assert event.cached_input_tokens == 17
        assert event.input_tokens is None and event.output_tokens is None
        assert "private" not in str(event.details)


async def test_sleep_persists_api_equivalent_cost_at_measurement_time(db_factory):
    class PricedInvalidProvider:
        async def generate_observed(self, **kwargs):
            return SleepGeneration.from_bridge(
                output={"topics": "invalid but never persisted as telemetry"},
                provider="codex",
                model="gpt-5.6-terra",
                duration_ms=12,
                usage={
                    "input_tokens": 1_000,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 100,
                    "reasoning_tokens": 50,
                },
                instructions=kwargs["instructions"],
                payload=kwargs["payload"],
            )

    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        await schedule_sleep(db, project_id=project.id, session_id=session.id, trigger="manual")
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), PricedInvalidProvider()) is True
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "sleep.memory_consolidation"
            )
        )
        assert event is not None
        assert event.status == "failed"
        assert event.cost_usd == Decimal("0.002840000000")
        assert event.unit_price_usd_per_million is None
        assert event.pricing_version == "api-list-prices-2026-08-29"
        assert event.details == {
            "cost_kind": "api_equivalent",
            "uncached_input_cost_pico_usd": 1_600_000_000,
            "cached_input_cost_pico_usd": 40_000_000,
            "cache_write_input_cost_pico_usd": 0,
            "output_cost_pico_usd": 1_200_000_000,
        }


async def test_invalid_json_error_records_available_bridge_telemetry(db_factory):
    class InvalidJsonObservedProvider:
        async def generate_observed(self, **kwargs):
            generation = SleepGeneration.from_bridge(
                output=None,
                provider="claude",
                model="claude-sonnet",
                duration_ms=456,
                usage={"total_tokens": 29},
                instructions=kwargs["instructions"],
                payload=kwargs["payload"],
            )
            raise SleepGenerationError("invalid structured output", generation)

    async with db_factory() as db:
        project, session, _ = await seed_pending(db, client="claude")
        await schedule_sleep(db, project_id=project.id, session_id=session.id, trigger="manual")
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), InvalidJsonObservedProvider()) is True
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "sleep.memory_consolidation"
            )
        )
        assert event.status == "failed" and event.provider == "claude"
        assert event.model == "claude-sonnet" and event.provider_duration_ms == 456
        assert event.measurement_source == "provider_reported"
        assert event.reported_total_tokens == 29
        assert event.input_tokens is None and event.output_tokens is None


async def test_claude_sleep_persists_the_client_computed_estimate(db_factory):
    class PricedClaudeProvider:
        async def generate_observed(self, **kwargs):
            return SleepGeneration.from_bridge(
                output={"topics": "invalid"},
                provider="claude",
                model="claude-sonnet-4-6",
                duration_ms=14,
                usage={
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_input_tokens": 10,
                    "output_tokens": 5,
                },
                instructions=kwargs["instructions"],
                payload=kwargs["payload"],
                client_cost_usd="0.004321",
                cost_source="claude_code_client_estimate",
            )

    async with db_factory() as db:
        project, session, _ = await seed_pending(db, client="claude")
        await schedule_sleep(db, project_id=project.id, session_id=session.id, trigger="manual")
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), PricedClaudeProvider()) is True
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "sleep.memory_consolidation"
            )
        )
        assert event is not None
        assert event.cost_usd == Decimal("0.004321000000")
        assert event.unit_price_usd_per_million is None
        assert event.pricing_version == "claude-code-client-estimate-v1"


async def test_large_batches_are_split_without_losing_pending_turns(db_factory):
    class AmbiguousInputProvider(SuccessfulProvider):
        async def generate(self, **kwargs):
            result = await super().generate(**kwargs)
            topic = result["topics"][0]
            topic["language"] = "it"
            topic["label"] = "Test release Android"
            topic["query_text"] = "Test release Android"
            topic["memory_actions"][0]["text"] = "La memoria deve restare in italiano."
            return result

    async with db_factory() as db:
        project, session, first = await seed_pending(db)
        first.user_prompt = "A" * 30_000
        first.assistant_response = "B" * 30_000
        second = Turn(
            project_id=project.id,
            session_id=session.id,
            external_id="second-large-turn",
            user_prompt="C" * 30_000,
            assistant_response="D" * 30_000,
            committed=True,
            sleep_status="pending",
        )
        db.add(second)
        await db.flush()
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), AmbiguousInputProvider(first.id)) is True
        await db.refresh(job)
        await db.refresh(first)
        await db.refresh(second)
        assert job.input_turn_ids == [first.id]
        assert first.sleep_status == "consolidated"
        assert second.sleep_status == "pending"


async def test_auth_jobs_wait_for_setup_but_rate_limited_jobs_keep_a_retry_time(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db, client="claude")
        auth = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        auth.status = "waiting"
        auth.error_kind = "auth_required"
        auth.not_before = utcnow() + timedelta(days=1)
        rate = SleepJob(
            project_id=project.id,
            session_id=session.id,
            provider="claude",
            trigger="manual",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="rate_limited",
            retry_at=utcnow() + timedelta(minutes=30),
            not_before=utcnow() + timedelta(minutes=30),
        )
        db.add(rate)
        await db.commit()
        assert await resume_project_sleep(db, project.id) == []
        await db.refresh(auth)
        await db.refresh(rate)
        assert auth.status == "waiting" and rate.status == "waiting"


async def test_due_scheduler_retries_rate_limits_but_never_retries_an_expired_login(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db, client="claude")
        auth = SleepJob(
            project_id=project.id,
            session_id=session.id,
            provider="claude",
            trigger="idle",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="auth_required",
            not_before=utcnow() + timedelta(days=365),
        )
        limited = SleepJob(
            project_id=project.id,
            session_id=session.id,
            provider="claude",
            trigger="idle",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="rate_limited",
            retry_at=utcnow() - timedelta(seconds=1),
            not_before=utcnow() - timedelta(seconds=1),
        )
        db.add_all([auth, limited])
        await db.commit()

        assert await schedule_due_sleep(db) >= 1
        await db.refresh(auth)
        await db.refresh(limited)
        assert auth.status == "waiting"
        assert limited.status == "pending"
        assert limited.error_kind is None and limited.retry_at is None


async def test_setup_resumes_legacy_jobs_without_rewriting_their_executor_preference(db_factory):
    async with db_factory() as db:
        project, claude, _ = await seed_pending(db, client="claude")
        codex = Session(project_id=project.id, client="codex", external_id="codex")
        db.add(codex)
        await db.flush()
        claude_job = await schedule_sleep(
            db, project_id=project.id, session_id=claude.id, trigger="manual"
        )
        legacy_claude_executor_job = SleepJob(
            project_id=project.id,
            session_id=codex.id,
            provider="claude",
            trigger="manual",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="auth_required",
            not_before=utcnow() + timedelta(days=365),
        )
        claude_job.status = "waiting"
        claude_job.error_kind = "auth_required"
        claude_job.not_before = utcnow() + timedelta(days=365)
        db.add(legacy_claude_executor_job)
        await db.commit()

        resumed = await resume_project_sleep(db, project.id, provider="codex", resume_auth=True)
        await db.refresh(claude_job)
        await db.refresh(legacy_claude_executor_job)
        assert {job.id for job in resumed} == {
            claude_job.id,
            legacy_claude_executor_job.id,
        }
        assert claude_job.status == "pending"
        assert legacy_claude_executor_job.status == "pending"
        assert {job.provider for job in resumed} == {"claude"}
        assert {job.executor_preference for job in resumed} == {"claude"}


async def test_off_record_and_unsupported_sessions_are_not_consolidated(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db, off_record=True)
        assert (
            await schedule_sleep(
                db,
                project_id=project.id,
                session_id=session.id,
                trigger="manual",
            )
            is None
        )
        other = Session(project_id=project.id, client="other", external_id="other")
        db.add(other)
        await db.flush()
        try:
            await schedule_sleep(
                db,
                project_id=project.id,
                session_id=other.id,
                trigger="manual",
                turn_ids=[str(uuid.uuid4())],
            )
        except ValueError as exc:
            assert "Codex or Claude" in str(exc)
        else:
            raise AssertionError("unsupported clients must not own sleep jobs")


async def test_invalid_model_sources_leave_the_job_recoverable(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), InvalidSourceProvider()) is True
        await db.refresh(job)
        await db.refresh(turn)
        assert job.status == "waiting"
        assert job.error_kind == "invalid_model_output"
        assert turn.sleep_status == "pending"


async def test_sleep_rebuilds_invented_message_references_from_valid_turns(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        assert await process_one_sleep_job(db, NoHits(), NoisyMessageProvider(turn.id)) is True
        await db.refresh(job)
        memory = await db.scalar(select(Memory))
        assert job.status == "completed"
        assert memory.source_message_ids
        assert "invented-message-id" not in memory.source_message_ids


async def test_scheduler_threshold_project_scope_and_recovery(db_factory, monkeypatch):
    async with db_factory() as db:
        project, session, first = await seed_pending(db)
        second = Turn(
            project_id=project.id,
            session_id=session.id,
            external_id="second",
            user_prompt="Second turn",
            assistant_response="Second answer",
            committed=True,
            sleep_status="pending",
        )
        db.add(second)
        await db.commit()
        monkeypatch.setattr(
            "dduo_solo_founder.sleep_engine.get_settings",
            lambda: SimpleNamespace(
                sleep_turn_threshold=2, sleep_idle_seconds=3600, sleep_batch_size=24
            ),
        )
        assert await schedule_due_sleep(db) == 1
        jobs = await schedule_project_sleep(db, project.id, trigger="manual")
        assert len(jobs) == 1
        job = jobs[0]
        job.status = "running"
        await db.commit()
        assert await recover_interrupted_jobs(db) == 1
        await db.refresh(job)
        assert job.status == "waiting"
        assert set(job.input_turn_ids) == {first.id, second.id}


async def test_scheduler_never_creates_overlapping_batches_and_manual_retry_wakes_waiting_job(
    db_factory,
):
    async with db_factory() as db:
        project, session, first = await seed_pending(db)
        first_job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="threshold"
        )
        second = Turn(
            project_id=project.id,
            session_id=session.id,
            external_id="new-turn",
            user_prompt="A later turn",
            assistant_response="A later answer",
            committed=True,
            sleep_status="pending",
        )
        db.add(second)
        await db.flush()
        second_job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="threshold"
        )
        assert first_job.id != second_job.id
        assert first_job.input_turn_ids == [first.id]
        assert second_job.input_turn_ids == [second.id]
        first_job.status = "waiting"
        first_job.last_error = "Codex CLI unavailable"
        await db.flush()
        retried = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        assert retried.id == first_job.id
        assert retried.status == "pending" and retried.last_error is None


async def test_manual_schedule_reactivates_the_existing_waiting_batch(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        waiting = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="idle",
        )
        waiting.status = "waiting"
        waiting.last_error = "temporary bridge failure"
        waiting.error_kind = "bridge_unavailable"
        waiting.retry_at = utcnow() + timedelta(hours=1)
        waiting.not_before = waiting.retry_at
        await db.commit()

        resumed = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="manual",
        )

        assert resumed.id == waiting.id
        assert resumed.status == "pending"
        assert resumed.last_error is None
        assert resumed.error_kind is None
        assert resumed.retry_at is None
        assert resumed.not_before <= utcnow()


async def test_session_start_resumes_waiting_jobs_and_queues_unassigned_turns(db_factory):
    async with db_factory() as db:
        project, session, first = await seed_pending(db, client="claude")
        waiting = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="idle"
        )
        waiting.status = "waiting"
        waiting.last_error = "Claude CLI unavailable"
        second = Turn(
            project_id=project.id,
            session_id=session.id,
            external_id="later-turn",
            user_prompt="A later topic",
            assistant_response="A later answer",
            committed=True,
            sleep_status="pending",
        )
        db.add(second)
        await db.flush()

        resumed = await resume_project_sleep(db, project.id)
        await db.commit()
        assert waiting.status == "pending" and waiting.last_error is None
        assert len(resumed) == 2
        assert {item.provider for item in resumed} == {"claude"}
        assert {item.executor_preference for item in resumed} == {"claude"}
        assert {item.source_client for item in resumed} == {"claude"}
        assert {tuple(item.input_turn_ids) for item in resumed} == {(first.id,), (second.id,)}

        repeated = await resume_project_sleep(db, project.id)
        assert repeated == []
        jobs = list((await db.scalars(select(SleepJob))).all())
        assert len(jobs) == 2


async def test_sleep_returns_false_when_queue_is_empty(db_factory):
    async with db_factory() as db:
        assert await process_one_sleep_job(db, NoHits(), FailingProvider()) is False


async def test_project_row_lock_defers_competing_sleep_jobs(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        pending = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        running = SleepJob(
            project_id=project.id,
            session_id=session.id,
            provider="codex",
            trigger="manual",
            status="running",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[str(uuid.uuid4())],
        )
        db.add(running)
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), FailingProvider()) is False
        await db.refresh(pending)
        assert pending.status == "pending"


async def test_concurrent_privacy_cancellation_prevents_memory_commit(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class CancellingProvider(SuccessfulProvider):
            async def generate(self, **kwargs):
                if not self.calls:
                    async with db_factory() as other:
                        current = await other.get(SleepJob, job.id)
                        current.status = "cancelled"
                        await other.commit()
                return await super().generate(**kwargs)

        assert await process_one_sleep_job(db, NoHits(), CancellingProvider(turn.id)) is True
        await db.refresh(job)
        assert job.status == "cancelled"
        assert await db.scalar(select(func.count(Memory.id))) == 0


async def test_cli_sleep_provider_validates_bridge_responses(monkeypatch):
    requests = []

    class Response:
        def __init__(self, status=200, body=None):
            self.status_code = status
            self._body = body or {"ok": True, "output": {"topics": []}}
            self.text = "bridge error"

        def json(self):
            return self._body

    responses = [Response(), Response(503), Response(body={"ok": False})]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            requests.append(kwargs)
            return responses.pop(0)

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    provider = CliSleepProvider(url="http://bridge", token="secret")
    result = await provider.generate(
        provider="codex",
        task="topics",
        instructions="segment",
        payload={"observed_at": datetime(2026, 7, 13, 21, 30, tzinfo=timezone.utc)},
        schema={"type": "object"},
        project_id="project-a",
    )
    assert result == {"topics": []}
    assert requests[0]["json"]["input"]["observed_at"] == "2026-07-13T21:30:00+00:00"
    assert requests[0]["json"]["project_id"] == "project-a"
    try:
        await provider.generate(
            provider="codex",
            task="topics",
            instructions="segment",
            payload={},
            schema={"type": "object"},
        )
    except SleepProviderError as exc:
        assert exc.kind == "bridge_unavailable"
    else:
        raise AssertionError("bridge HTTP failures must be explicit")
    try:
        await provider.generate(
            provider="codex",
            task="topics",
            instructions="segment",
            payload={},
            schema={"type": "object"},
        )
    except SleepProviderError as exc:
        assert exc.kind == "invalid_model_output"
    else:
        raise AssertionError("invalid bridge payloads must be rejected")
    try:
        await CliSleepProvider(url="http://bridge", token="").generate(
            provider="codex",
            task="topics",
            instructions="segment",
            payload={},
            schema={"type": "object"},
        )
    except SleepProviderError as exc:
        assert exc.kind == "dependency_unavailable"
    else:
        raise AssertionError("an authenticated bridge token is required")


async def test_cli_sleep_provider_preserves_typed_failures_and_network_outages(monkeypatch):
    class Response:
        status_code = 503

        def json(self):
            return {"error": "auth_required", "detail": "expired"}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    provider = CliSleepProvider(url="http://bridge", token="secret")
    try:
        await provider.generate(
            provider="claude", task="x", instructions="x", payload={}, schema={}
        )
    except SleepProviderError as exc:
        assert exc.kind == "auth_required"
        assert exc.diagnostic is None
    else:
        raise AssertionError("typed bridge failures must survive the provider boundary")

    class OfflineClient(Client):
        async def post(self, *args, **kwargs):
            raise sleep_engine.httpx.ConnectError("offline")

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", OfflineClient)
    try:
        await provider.generate(
            provider="claude", task="x", instructions="x", payload={}, schema={}
        )
    except SleepProviderError as exc:
        assert exc.kind == "bridge_unavailable"
    else:
        raise AssertionError("network outages must be retryable")


async def test_sleep_marks_empty_and_interrupted_batches_without_losing_jobs(db_factory):
    async with db_factory() as db:
        project = Project(id=str(uuid.uuid4()), name="Project", root_path="/tmp/project")
        session = Session(project_id=project.id, client="codex", external_id="empty")
        db.add_all([project, session])
        await db.flush()
        job = SleepJob(
            project_id=project.id,
            session_id=session.id,
            provider="codex",
            trigger="manual",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[str(uuid.uuid4())],
        )
        db.add(job)
        await db.commit()
        assert await process_one_sleep_job(db, NoHits(), FailingProvider()) is True
        await db.refresh(job)
        assert job.status == "completed" and job.result == {"skipped_reason": "no_recordable_turns"}

        job.status = "running"
        await db.commit()
        assert await recover_interrupted_jobs(db) == 1
        await db.refresh(job)
        assert job.status == "waiting"
        assert job.error_kind == "bridge_unavailable"
        assert job.retry_at is not None


async def test_sleep_rejects_duplicate_topics_and_unknown_artifact_references(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class DuplicateTopicsProvider:
            async def generate(self, **kwargs):
                return {
                    "topics": [
                        {
                            "topic_id": "same",
                            "label": "One",
                            "query_text": "one",
                            "language": "en",
                            "source_turn_ids": [turn.id],
                            "source_message_ids": [],
                            "memory_actions": [],
                        },
                        {
                            "topic_id": "same",
                            "label": "Two",
                            "query_text": "two",
                            "language": "en",
                            "source_turn_ids": [turn.id],
                            "source_message_ids": [],
                            "memory_actions": [],
                        },
                    ]
                }

        assert await process_one_sleep_job(db, NoHits(), DuplicateTopicsProvider()) is True
        await db.refresh(job)
        assert job.status == "waiting" and job.error_kind == "invalid_model_output"


async def test_schedule_sleep_rejects_unknown_session_and_idle_threshold(db_factory, monkeypatch):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        with pytest.raises(LookupError, match="session"):
            await schedule_sleep(
                db,
                project_id=project.id,
                session_id=str(uuid.uuid4()),
                trigger="manual",
            )
        turn.created_at = utcnow() - timedelta(hours=1)
        turn.committed_at = utcnow() - timedelta(hours=1)
        await db.commit()
        monkeypatch.setattr(
            "dduo_solo_founder.sleep_engine.get_settings",
            lambda: SimpleNamespace(
                sleep_turn_threshold=8, sleep_idle_seconds=1, sleep_batch_size=8
            ),
        )
        assert await schedule_due_sleep(db) == 1
        job = await db.scalar(select(SleepJob))
        assert job.trigger == "idle" and job.provider == "codex"


async def test_cli_sleep_provider_never_leaks_untyped_or_malformed_bridge_failures(monkeypatch):
    class Response:
        def __init__(self, status, body=None, broken=False):
            self.status_code = status
            self.body = body
            self.broken = broken

        def json(self):
            if self.broken:
                raise ValueError("not json")
            return self.body

    responses = iter(
        [
            Response(503, broken=True),
            Response(503, {"error": "unknown", "detail": "private diagnostics"}),
            Response(200, broken=True),
        ]
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return next(responses)

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    provider = CliSleepProvider(url="http://bridge", token="secret")
    for expected in ("bridge_unavailable", "bridge_unavailable", "bridge_unavailable"):
        with pytest.raises(SleepProviderError) as error:
            await provider.generate(
                provider="codex", task="x", instructions="x", payload={}, schema={}
            )
        assert error.value.kind == expected


async def test_sleep_rejects_unknown_memory_artifacts_and_internal_provider_errors(db_factory):
    async def assert_invalid(action: dict, expected_kind: str = "invalid_model_output"):
        project, session, turn = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class Provider:
            async def generate(self, **kwargs):
                return {
                    "topics": [
                        {
                            "topic_id": str(uuid.uuid4()),
                            "label": "Invalid",
                            "query_text": "invalid",
                            "language": "en",
                            "source_turn_ids": [turn.id],
                            "source_message_ids": [],
                            "memory_actions": [action],
                        }
                    ]
                }

        assert await process_one_sleep_job(db, NoHits(), Provider()) is True
        await db.refresh(job)
        assert job.status == "waiting" and job.error_kind == expected_kind

    async with db_factory() as db:
        base = {
            "action": "create",
            "target_node_type": "episode",
            "target_node_key": "release",
            "text": "Release detail",
            "status": "active",
            "source_message_ids": [],
            "metadata": {},
        }
        await assert_invalid({**base, "source_node_ids": ["unknown-memory"], "artifact_ids": []})
        await assert_invalid({**base, "source_node_ids": [], "artifact_ids": ["unknown-artifact"]})

        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class BrokenProvider:
            async def generate(self, **kwargs):
                raise RuntimeError("untyped provider failure")

        assert await process_one_sleep_job(db, NoHits(), BrokenProvider()) is True
        await db.refresh(job)
        assert job.status == "waiting" and job.error_kind == "invalid_model_output"


async def test_sleep_exposes_every_user_prompt_fragment_as_valid_provenance(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        second = RawEvent(
            project_id=project.id,
            session_id=session.id,
            turn_id=turn.id,
            event_type="user_prompt",
            payload={"text": "Also preserve production data", "prompt_event_id": "message-two"},
            actor="user",
            created_at=utcnow() + timedelta(seconds=1),
        )
        db.add(second)
        await db.commit()
        await db.refresh(second)

        by_turn = await sleep_engine._message_ids_by_turn(db, [turn.id])
        fragments = await sleep_engine._prompt_fragments_by_turn(db, [turn.id])
        serialized = sleep_engine._serialize_turn(
            turn, [], by_turn[turn.id], fragments[turn.id]
        )

        user_ids = serialized["user_message"]["source_message_ids"]
        assert len(user_ids) == 2
        assert serialized["user_message"]["message_id"] == user_ids[0]
        assert second.id in user_ids
        assert serialized["user_message"]["fragments"] == [
            {"message_id": message_id, "ordinal": index + 1}
            for index, message_id in enumerate(user_ids)
        ]
        assert "Also preserve production data" in serialized["user_message"]["content"]
        assert set(user_ids) == set(
            sleep_engine._serialized_message_ids(serialized["user_message"])
        )


def test_long_fragment_rendering_gives_every_declared_source_real_bounded_content():
    fragments = [
        {
            "message_id": f"message-{index}",
            "content": f"opening-{index} " + (str(index) * 4_000) + f" final-{index}",
        }
        for index in range(1, 4)
    ]
    turn = SimpleNamespace(
        id="turn-long-fragments",
        user_prompt="\n\n".join(item["content"] for item in fragments),
        assistant_response="Done",
        created_at=utcnow(),
        committed_at=utcnow(),
    )

    serialized = sleep_engine._serialize_turn(
        turn,
        [],
        {
            "user_prompt": [item["message_id"] for item in fragments],
            "assistant_response": ["assistant-message"],
        },
        fragments,
        message_char_limit=2_000,
    )
    user = serialized["user_message"]
    assert len(user["content"]) <= 2_000
    assert user["source_message_ids"] == [item["message_id"] for item in fragments]
    assert [item["message_id"] for item in user["fragments"]] == user[
        "source_message_ids"
    ]
    for index in range(1, 4):
        assert f"message_id=message-{index}" in user["content"]
        assert f"final-{index}" in user["content"]


def test_omitted_fragment_ids_cannot_influence_provenance_or_language():
    fragments = [
        {
            "message_id": f"message-{index}",
            "content": (
                "Please remember this permanently in English. " + ("x" * 400)
                if index == 1
                else (
                    f"Ricorda sempre che questa regola italiana numero {index} deve restare "
                    "valida per tutto il progetto e non deve essere modificata senza una "
                    "decisione esplicita. "
                )
                * 8
            ),
        }
        for index in range(20)
    ]
    turn = SimpleNamespace(
        id="turn-many-fragments",
        user_prompt="\n\n".join(item["content"] for item in fragments),
        assistant_response="Fatto",
        created_at=utcnow(),
        committed_at=utcnow(),
    )
    message_ids = [item["message_id"] for item in fragments]
    presented = sleep_engine._presented_prompt_fragments(
        turn, message_ids, fragments, limit=2_000
    )
    presented_ids = [str(item["message_id"]) for item in presented]
    assert "message-1" not in presented_ids
    assert "message-0" in presented_ids
    assert "message-19" in presented_ids

    serialized = sleep_engine._serialize_turn(
        turn,
        [],
        {"user_prompt": message_ids, "assistant_response": ["assistant-message"]},
        fragments,
        message_char_limit=2_000,
    )
    assert serialized["user_message"]["source_message_ids"] == presented_ids
    topic = SimpleNamespace(source_turn_ids=[turn.id], source_message_ids=[])
    language = sleep_engine._topic_memory_language(
        topic,
        {turn.id: serialized},
        {
            turn.id: [
                {"message_id": str(item["message_id"]), "content": str(item["content"])}
                for item in presented
            ]
        },
    )
    assert language == "it"


async def test_sleep_related_memory_query_preserves_a_late_correction(db_factory):
    class TrackingNoHits:
        def __init__(self):
            self.queries = []

        def search(self, _project_id, query, _limit):
            self.queries.append(query)
            return []

    async with db_factory() as db:
        project, session, turn = await seed_pending(db)
        turn.user_prompt = (
            "The original release decision is valid. "
            + ("x" * 12_000)
            + " FINAL-CORRECTION-PRESERVE-PRODUCTION"
        )
        await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()
        embeddings = TrackingNoHits()
        assert await process_one_sleep_job(db, embeddings, SuccessfulProvider(turn.id)) is True
        assert embeddings.queries
        assert len(embeddings.queries[0]) == 8_000
        assert embeddings.queries[0].endswith("FINAL-CORRECTION-PRESERVE-PRODUCTION")


async def test_sleep_helper_empty_and_project_filter_paths(db_factory):
    async with db_factory() as db:
        assert await sleep_engine._artifacts_by_turn(db, []) == {}
        assert await sleep_engine._message_ids_by_turn(db, []) == {}
        assert await recover_interrupted_jobs(db) == 0
        project, session, _ = await seed_pending(db, client="claude")
        with pytest.raises(LookupError, match="session"):
            await schedule_project_sleep(
                db, project.id, trigger="topic_boundary", session_id=str(uuid.uuid4())
            )
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="topic_boundary"
        )
        await db.commit()
        assert job.provider == "claude" and job.executor_preference == "claude"
        assert job.source_client == "claude"
        assert job.trigger == "topic_boundary"
        resumed = await resume_project_sleep(
            db, project.id, session_id=session.id, provider="claude"
        )
        assert resumed == []


async def test_cli_sleep_provider_preserves_failed_bridge_telemetry(monkeypatch):
    class Response:
        status_code = 422

        def json(self):
            return {
                "error": "invalid_structured_output",
                "provider": "claude",
                "model": "claude-sonnet",
                "duration_seconds": 1.25,
                "usage": {"cached_input_tokens": 9},
                "client_cost_usd": "0.0025",
                "cost_source": "claude_code_client_estimate",
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    provider = CliSleepProvider(url="http://bridge", token="secret")
    with pytest.raises(SleepGenerationError) as raised:
        await provider.generate_observed(
            provider="claude",
            task="topics",
            instructions="segment",
            payload={},
            schema={"type": "object"},
        )
    generation = raised.value.generation
    assert generation.provider == "claude" and generation.model == "claude-sonnet"
    assert generation.duration_ms == 1250
    assert generation.measurement_source == "provider_reported"
    assert generation.cached_input_tokens == 9
    assert generation.input_tokens is None and generation.output_tokens is None
    assert generation.client_cost_usd == Decimal("0.0025")
    assert generation.cost_source == "claude_code_client_estimate"


@pytest.mark.parametrize(
    ("status", "error_code", "usage", "expected_source"),
    [
        (503, "cli_unavailable", {"cached_input_tokens": 9}, "provider_reported"),
        (504, "cli_timeout", {"total_tokens": 14}, "provider_reported"),
        (504, "cli_timeout", {}, "unavailable"),
    ],
)
async def test_cli_sleep_operational_errors_never_invent_usage(
    monkeypatch, status, error_code, usage, expected_source
):
    class Response:
        status_code = status

        def json(self):
            return {
                "error": error_code,
                "provider": "codex",
                "model": "gpt-5-codex",
                "duration_seconds": 2.5,
                "usage": usage,
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    provider = CliSleepProvider(url="http://bridge", token="secret")
    with pytest.raises(SleepGenerationError) as raised:
        await provider.generate_observed(
            provider="codex",
            task="topics",
            instructions="segment private project context",
            payload={"private": "payload"},
            schema={"type": "object"},
        )
    generation = raised.value.generation
    assert generation.model == "gpt-5-codex" and generation.duration_ms == 2500
    assert generation.measurement_source == expected_source
    assert generation.cached_input_tokens == usage.get("cached_input_tokens")
    assert generation.reported_total_tokens == usage.get("total_tokens")
    assert generation.input_tokens is None and generation.output_tokens is None


async def test_sleep_generation_handles_nonfinite_usage_and_invalid_compatibility_output(
    monkeypatch,
):
    assert sleep_engine._usage_number({"tokens": float("inf")}, "tokens") is None
    assert sleep_engine._duration_ms(float("inf")) is None

    generation = SleepGeneration.from_bridge(
        output=None,
        provider="codex",
        model="gpt-5.6-codex",
        duration_ms=1,
        usage={},
        instructions="segment",
        payload={"turns": []},
    )
    assert generation.measurement_source == "local_estimate"
    assert generation.input_tokens is not None and generation.output_tokens is None

    class NonObjectProvider:
        async def generate(self, **kwargs):
            return ["not", "an", "object"]

    with pytest.raises(SleepGenerationError) as invalid:
        await sleep_engine._generate_sleep(
            NonObjectProvider(),
            provider_name="codex",
            task="topics",
            instructions="segment",
            payload={},
            schema={},
        )
    assert invalid.value.generation.output is None

    async def empty_observed(**kwargs):
        return generation

    provider = CliSleepProvider(url="http://bridge", token="secret")
    monkeypatch.setattr(provider, "generate_observed", empty_observed)
    with pytest.raises(SleepProviderError) as empty:
        await provider.generate(
            provider="codex", task="topics", instructions="segment", payload={}, schema={}
        )
    assert empty.value.kind == "invalid_model_output"


async def test_cli_sleep_provider_rejects_non_object_bridge_body(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return ["not", "an", "object"]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("dduo_solo_founder.sleep_engine.httpx.AsyncClient", Client)
    with pytest.raises(SleepGenerationError) as raised:
        await CliSleepProvider(url="http://bridge", token="secret").generate_observed(
            provider="codex", task="topics", instructions="segment", payload={}, schema={}
        )
    assert raised.value.kind == "bridge_unavailable"
    assert raised.value.generation.measurement_source == "unavailable"


async def test_related_memory_observability_records_success_and_partial_usage(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        memory = Memory(
            project_id=project.id,
            node_type="reusable_fact",
            node_key="release-platform",
            text="The release targets Android.",
        )
        db.add(memory)
        await db.commit()

        usage = SimpleNamespace(
            provider="openai",
            model="text-embedding-3-large",
            input_tokens=23,
            reported_total_tokens=23,
            measurement_source="provider_reported",
        )
        embedding = sleep_engine.EmbeddingCall(
            vectors=[[0.1]], usage=usage, provider_duration_ms=12
        )

        class ObservedHits:
            def search_observed(self, project_id, query, limit):
                return SimpleNamespace(
                    items=[{"id": memory.id, "score": 0.99}],
                    embedding=embedding,
                    vector_store_duration_ms=7,
                )

        related = await sleep_engine._related_memories(
            db, ObservedHits(), project.id, "android release", job
        )
        assert [item.id for item in related] == [memory.id]
        success = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "embedding.sleep_related"
            )
        )
        assert success.status == "success"
        assert success.measurement_source == "provider_reported"
        assert success.input_tokens == 23
        assert success.provider_duration_ms == 12
        assert success.vector_store_duration_ms == 7

        class PartialFailure:
            def search_observed(self, project_id, query, limit):
                raise sleep_engine.VectorOperationError(
                    "vector store unavailable",
                    embedding=embedding,
                    vector_store_duration_ms=9,
                )

        with pytest.raises(sleep_engine.VectorOperationError):
            await sleep_engine._related_memories(
                db, PartialFailure(), project.id, "different release query", job
            )
        events = list(
            (
                await db.scalars(
                    select(ObservabilityEvent).where(
                        ObservabilityEvent.operation == "embedding.sleep_related"
                    )
                )
            ).all()
        )
        partial = next(item for item in events if item.status == "partial_failure")
        assert partial.input_tokens == 23
        assert partial.provider_duration_ms == 12
        assert partial.vector_store_duration_ms == 9
        assert partial.details == {"limit": 24, "error_code": "vector_operation_failed"}


async def test_related_memory_observability_records_failure_without_usage(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db, project_id=project.id, session_id=session.id, trigger="manual"
        )
        await db.commit()

        class BrokenSearch:
            def search_observed(self, project_id, query, limit):
                raise RuntimeError("private vector failure")

        with pytest.raises(RuntimeError, match="private vector failure"):
            await sleep_engine._related_memories(
                db, BrokenSearch(), project.id, "failed release query", job
            )
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.operation == "embedding.sleep_related"
            )
        )
        assert event.status == "failed"
        assert event.measurement_source == "unavailable"
        assert event.input_tokens is None and event.provider_duration_ms is None
        assert "private" not in str(event.details)


async def test_mark_waiting_preserves_an_already_cancelled_job(db_factory):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="manual",
        )
        job.status = "cancelled"
        await db.commit()

        await sleep_engine._mark_sleep_waiting(
            db,
            job.id,
            SleepProviderError("bridge_unavailable", "temporary failure"),
        )

        await db.refresh(job)
        assert job.status == "cancelled"


async def test_sleep_claim_rolls_back_if_the_authoritative_project_disappears():
    job = SimpleNamespace(
        id="job",
        project_id="project",
        source_client=None,
        provider="codex",
    )

    class MissingProjectDb:
        def __init__(self):
            self.values = iter([job, None])
            self.rollbacks = 0

        async def scalar(self, _statement):
            return next(self.values)

        async def rollback(self):
            self.rollbacks += 1

    db = MissingProjectDb()
    assert await process_one_sleep_job(db, object(), object()) is False
    assert db.rollbacks == 1


async def test_empty_bounded_sleep_batch_becomes_a_retryable_validation_failure(
    db_factory,
    monkeypatch,
):
    async with db_factory() as db:
        project, session, _ = await seed_pending(db)
        job = await schedule_sleep(
            db,
            project_id=project.id,
            session_id=session.id,
            trigger="manual",
        )
        await db.commit()
        monkeypatch.setattr(sleep_engine, "_bounded_turn_batch", lambda *_args, **_kwargs: [])

        assert await process_one_sleep_job(db, object(), object()) is True
        await db.refresh(job)
        assert job.status == "waiting"
        assert job.error_kind == "invalid_model_output"
