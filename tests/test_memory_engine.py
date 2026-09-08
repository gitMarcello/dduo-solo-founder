from __future__ import annotations

import uuid

import pytest

from dduo_solo_founder.memory_engine import (
    MEMORY_LANGUAGE_POLICY_VERSION,
    apply_sleep_action,
    existing_memory_language,
    explain_memory,
    forget_memory,
    infer_text_language,
    normalize_node_key,
)
from dduo_solo_founder.models import Artifact, Memory, Project, Session, SleepJob, Turn
from dduo_solo_founder.schemas import MemoryActionPayload


async def seed_job(db):
    project = Project(id=str(uuid.uuid4()), name="P", root_path="/tmp/p")
    session = Session(project_id=project.id, client="codex", external_id="s")
    db.add_all([project, session])
    await db.flush()
    turn = Turn(
        project_id=project.id,
        session_id=session.id,
        external_id="t",
        user_prompt="The release moved to Friday",
        assistant_response="Noted",
        committed=True,
        sleep_status="pending",
    )
    db.add(turn)
    await db.flush()
    job = SleepJob(
        project_id=project.id,
        session_id=session.id,
        provider="codex",
        trigger="manual",
        dedupe_key=str(uuid.uuid4()),
        input_turn_ids=[turn.id],
    )
    db.add(job)
    await db.flush()
    return project, turn, job


async def test_duplicate_create_becomes_a_revision_and_provenance_is_explainable(db_factory):
    async with db_factory() as db:
        project, turn, job = await seed_job(db)
        action = MemoryActionPayload(
            action="create",
            target_node_type="reusable_fact",
            target_node_key="Release Date",
            text="The release is planned for Thursday.",
            source_message_ids=[f"user:{turn.id}"],
        )
        first, _ = await apply_sleep_action(
            db,
            job=job,
            action=action,
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        second, outcome = await apply_sleep_action(
            db,
            job=job,
            action=action.model_copy(update={"text": "The release moved to Friday."}),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        third, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="replace_current",
                target_node_type="heuristic",
                target_node_key="data-rilascio",
                text="The release remains planned for Friday.",
                source_node_ids=["missing-memory", first.id],
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        await db.commit()
        assert outcome == "replaced"
        assert first.status == "superseded" and first.superseded_by_id == second.id
        assert second.status == "superseded" and second.superseded_by_id == third.id
        assert third.node_type == "reusable_fact" and third.node_key == "release-date"
        assert third.memory_group_id == first.memory_group_id and third.revision == 3
        provenance = await explain_memory(db, project.id, third.id)
        assert len(provenance["revisions"]) == 3
        assert provenance["sources"][0]["user_prompt"].endswith("Friday")
        forgotten = await forget_memory(db, project.id, third.id, "No longer relevant")
        await db.commit()
        assert len(forgotten) == 3
        assert all(item.status == "inactive" for item in forgotten)


async def test_memory_engine_handles_missing_targets_conservatively(db_factory):
    assert normalize_node_key(" Release / Android ") == "release-/-android"
    with pytest.raises(ValueError, match="empty"):
        normalize_node_key("***")
    async with db_factory() as db:
        project, turn, job = await seed_job(db)
        missing, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="set_status",
                target_node_type="episode",
                target_node_key="missing",
                status="inactive",
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="it",
        )
        assert missing is None and outcome == "skipped"
        replacement, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="replace_current",
                target_node_type="episode",
                target_node_key="new-story",
                text="A new story that had no current revision.",
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="it",
        )
        assert outcome == "created" and replacement.revision == 1
        with pytest.raises(LookupError, match="memory not found"):
            await explain_memory(db, project.id, str(uuid.uuid4()))
        with pytest.raises(LookupError, match="memory not found"):
            await forget_memory(db, project.id, str(uuid.uuid4()), "requested")


async def test_related_sources_do_not_become_replacement_targets_and_artifacts_survive(db_factory):
    async with db_factory() as db:
        _, turn, job = await seed_job(db)
        artifact = Artifact(
            project_id=job.project_id,
            content_hash=uuid.uuid4().hex * 2,
            kind="document",
            filename="plan.md",
        )
        db.add(artifact)
        await db.flush()
        existing, _ = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="create",
                target_node_type="episode",
                target_node_key="existing-story",
                text="Existing story.",
                artifact_ids=[artifact.id],
                metadata={"language": "it", "language_policy_version": "model-value"},
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        separate, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="create",
                target_node_type="episode",
                target_node_key="separate-story",
                text="Separate story informed by the existing one.",
                source_node_ids=[existing.id],
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        revised, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="replace_current",
                target_node_type="episode",
                target_node_key="storia-esistente-tradotta",
                text="Existing story, revised.",
                source_node_ids=[existing.id],
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language=existing_memory_language(existing),
        )
        assert outcome == "replaced"
        assert existing.status == "superseded" and separate.revision == 1
        assert revised.node_key == "existing-story" and revised.node_type == existing.node_type
        assert revised.memory_group_id == existing.memory_group_id
        assert existing.metadata_json["language"] == "en"
        assert existing.metadata_json["language_policy_version"] == MEMORY_LANGUAGE_POLICY_VERSION
        assert revised.metadata_json["language"] == "en"
        assert revised.source_artifact_ids == [artifact.id]


def test_language_inference_is_conservative_for_mixed_and_code_text():
    assert infer_text_language("The release must always preserve database safety.") == "en"
    assert infer_text_language("Please implement it") == "en"
    assert infer_text_language("Fix login bug") == "en"
    assert infer_text_language("La release deve sempre preservare la sicurezza del database.") == "it"
    assert infer_text_language("Correggi login") == "it"
    assert infer_text_language("Android API v2") is None
    assert infer_text_language("La release deve work safely with the database") is None
    assert infer_text_language("```python\nprint('the result')\n```") is None
    legacy = Memory(
        project_id="project",
        node_type="episode",
        node_key="legacy",
        text="The release must always preserve database safety.",
        metadata_json={},
    )
    assert existing_memory_language(legacy) == "en"
    assert legacy.metadata_json == {}
