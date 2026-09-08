from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from dduo_solo_founder.models import (
    Activity,
    Memory,
    OutboxEvent,
    Plan,
    PlanWorkItem,
    ProfileRevision,
    Project,
    Session,
    SleepJob,
    Task,
    TaskRevision,
    Turn,
)
from dduo_solo_founder.memory_engine import apply_sleep_action
from dduo_solo_founder.schemas import (
    CompactionRecord,
    MemoryActionPayload,
    ProjectUpdate,
    TaskAction,
    TurnCommit,
)
from dduo_solo_founder.service import (
    BRIEFING_TASK_BYTES_BUDGET,
    apply_task_action,
    briefing,
    close_segment,
    commit_turn,
    compact_task_briefing,
    ensure_open_segment,
    memory_health,
    persist_compaction,
    update_profile,
    validate_task_hierarchy,
)


async def seed_turn(db, *, project_id=None, client="codex"):
    project_id = project_id or str(uuid.uuid4())
    project = Project(id=project_id, name="Example", root_path="/tmp/example")
    session = Session(project_id=project_id, client=client, external_id=str(uuid.uuid4()))
    db.add_all([project, session])
    await db.flush()
    segment = await ensure_open_segment(db, session)
    turn = Turn(
        project_id=project_id,
        session_id=session.id,
        segment_id=segment.id,
        external_id=str(uuid.uuid4()),
        user_prompt="What next?",
    )
    db.add(turn)
    await db.commit()
    return project, session, turn


async def test_briefing_and_profile_revision(db_factory):
    async with db_factory() as db:
        with pytest.raises(LookupError):
            await briefing(db, "missing")
        project, _, _ = await seed_turn(db)
        in_progress = Task(
            project_id=project.id,
            title="In progress",
            status="in_progress",
            priority="medium",
            description="Full description must not enter the briefing",
            objective="Ship the compact briefing",
            next_action="Verify the compact fields",
            labels=["context"],
            rationale="Internal rationale",
            completion_evidence="Internal evidence",
        )
        blocked = Task(project_id=project.id, title="Blocked", status="blocked", priority="low")
        critical = Task(project_id=project.id, title="Critical", priority="critical")
        high = Task(project_id=project.id, title="High", priority="high")
        medium = Task(project_id=project.id, title="Medium", priority="medium")
        low = Task(project_id=project.id, title="Low", priority="low")
        done = Task(project_id=project.id, title="Done", status="done")
        cancelled = Task(project_id=project.id, title="Cancelled", status="cancelled")
        plan = Plan(
            project_id=project.id,
            title="Release design",
            objective="Choose a safe release sequence",
            content="Keep rollback ready.",
            status="decided",
        )
        db.add_all([in_progress, blocked, critical, high, medium, low, done, cancelled, plan])
        await db.flush()
        db.add(PlanWorkItem(plan_id=plan.id, task_id=in_progress.id, position=0))
        await db.commit()
        result = await briefing(db, project.id)
        assert result["onboarding_required"] is True
        assert [item["title"] for item in result["tasks"]] == [
            "In progress",
            "Blocked",
            "Critical",
            "High",
            "Medium",
            "Low",
        ]
        assert set(result["tasks"][0]) == {
            "id",
            "kind",
            "epic_id",
            "sprint_id",
            "title",
            "status",
            "priority",
            "objective",
            "next_action",
            "labels",
            "due_at",
            "version",
            "updated_at",
        }
        assert result["tasks"][0]["objective"] == "Ship the compact briefing"
        assert result["tasks"][0]["next_action"] == "Verify the compact fields"
        assert result["tasks"][0]["labels"] == ["context"]
        assert result["task_counts"] == {
            "total": 8,
            "nonterminal": 6,
            "selected": 6,
            "omitted": 0,
            "by_status": {
                "blocked": 1,
                "cancelled": 1,
                "done": 1,
                "in_progress": 1,
                "todo": 4,
            },
        }
        assert result["task_index_status"] == "degraded"
        assert "operating cofounder" in result["instruction"]
        assert result["plans"][0]["title"] == "Release design"
        assert result["plans"][0]["work_item_ids"] == [in_progress.id]
        assert "broad strategy, design, or decisions" in result["instruction"]
        assert "Plan, Epic, or Task structure" in result["instruction"]
        assert "human title" in result["instruction"]
        assert "solo-or-team operating manual" in result["instruction"]

        await update_profile(
            db,
            project,
            ProjectUpdate(cause="A cause", objectives=["An objective"], expected_version=1),
        )
        await db.commit()
        assert project.profile_version == 2
        assert await db.scalar(
            select(ProfileRevision).where(ProfileRevision.project_id == project.id)
        )
        await update_profile(db, project, ProjectUpdate(rationale="nothing changed"))
        with pytest.raises(ValueError, match="version conflict"):
            await update_profile(db, project, ProjectUpdate(cause="stale", expected_version=1))


def test_compact_task_briefing_has_a_deterministic_utf8_budget():
    tasks = [
        Task(
            id=str(uuid.uuid4()),
            project_id="p1",
            title=f"Task {index}",
            status="todo",
            priority="medium",
            kind="task",
            labels=["🚀" * 100 for _ in range(20)],
            objective="漢" * 5_000,
            next_action="🚀" * 5_000,
            version=1,
        )
        for index in range(20)
    ]
    cards = compact_task_briefing(tasks)
    encoded = json.dumps(
        cards,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    assert len(encoded) <= BRIEFING_TASK_BYTES_BUDGET
    assert 0 < len(cards) < len(tasks)


async def test_memory_actions_and_outbox(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_turn(db)
        job = SleepJob(
            project_id=project.id,
            session_id=session.id,
            segment_id=turn.segment_id,
            provider="codex",
            trigger="manual",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[turn.id],
        )
        db.add(job)
        await db.flush()
        created, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="create",
                target_node_type="episode",
                target_node_key="Release plan",
                text="Test first",
                source_message_ids=[f"user:{turn.id}"],
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        await db.commit()
        assert created.revision == 1 and outcome == "created"
        assert len((await db.scalars(select(OutboxEvent))).all()) == 1

        replacement, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="replace_current",
                target_node_type="episode",
                target_node_key="release-plan",
                text="Test twice",
                source_node_ids=[created.id],
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        await db.commit()
        assert outcome == "replaced"
        assert created.status == "superseded" and replacement.revision == 2
        assert created.superseded_by_id == replacement.id

        status_memory, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="set_status",
                target_node_type="heuristic",
                target_node_key="piano-rilascio-tradotto",
                source_node_ids=[replacement.id],
                status="inactive",
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="en",
        )
        await db.commit()
        assert status_memory.status == "inactive" and outcome == "status_updated"
        assert status_memory.id == replacement.id
        assert status_memory.node_type == "episode" and status_memory.node_key == "release-plan"
        missing_replacement, outcome = await apply_sleep_action(
            db,
            job=job,
            action=MemoryActionPayload(
                action="replace_current",
                target_node_type="episode",
                target_node_key="missing",
                text="replacement",
            ),
            source_turn_ids=[turn.id],
            source_artifact_ids=[],
            memory_language="it",
        )
        assert outcome == "created" and missing_replacement.revision == 1


async def test_task_actions_cover_transitions_and_conflicts(db_factory):
    async with db_factory() as db:
        project, _, _ = await seed_turn(db)
        with pytest.raises(ValueError, match="title"):
            await apply_task_action(db, project.id, TaskAction(action="create"))
        task = await apply_task_action(
            db, project.id, TaskAction(action="create", title="Test Android", priority="high")
        )
        await db.commit()
        assert task.version == 1
        create_revision = await db.scalar(
            select(TaskRevision).where(TaskRevision.task_id == task.id)
        )
        create_event = await db.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == task.id)
        )
        assert create_revision and create_revision.version == 1
        assert create_event and create_event.event_type == "task.upsert"
        assert create_event.payload == {"task_version": 1, "origin": "live"}
        task = await apply_task_action(
            db,
            project.id,
            TaskAction(
                action="update", task_id=task.id, next_action="Run flow", expected_version=1
            ),
        )
        assert task.version == 2
        assert len(
            (await db.scalars(select(TaskRevision).where(TaskRevision.task_id == task.id))).all()
        ) == 2
        assert len(
            (await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == task.id))).all()
        ) == 2
        task = await apply_task_action(
            db,
            project.id,
            TaskAction(
                action="update",
                task_id=task.id,
                next_action="Run flow",
                expected_version=2,
            ),
        )
        assert task.version == 2
        assert len(
            (await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == task.id))).all()
        ) == 2
        with pytest.raises(ValueError, match="version conflict"):
            await apply_task_action(
                db, project.id, TaskAction(action="update", task_id=task.id, expected_version=1)
            )
        task = await apply_task_action(
            db, project.id, TaskAction(action="complete", task_id=task.id)
        )
        assert task.status == "done" and task.version == 3
        assert len(
            (await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == task.id))).all()
        ) == 3
        task = await apply_task_action(
            db,
            project.id,
            TaskAction(action="complete", task_id=task.id, expected_version=3),
        )
        assert task.version == 3
        assert len(
            (await db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == task.id))).all()
        ) == 3
        with pytest.raises(ValueError, match="task not found"):
            await apply_task_action(
                db, project.id, TaskAction(action="update", task_id=str(uuid.uuid4()))
            )
        epic = await apply_task_action(
            db,
            project.id,
            TaskAction(action="create", title="Launch", kind="epic"),
        )
        child = await apply_task_action(
            db,
            project.id,
            TaskAction(action="create", title="Store listing", epic_id=epic.id),
        )
        assert child.epic_id == epic.id
        with pytest.raises(ValueError, match="own epic"):
            await apply_task_action(
                db,
                project.id,
                TaskAction(action="update", task_id=child.id, epic_id=child.id),
            )
        with pytest.raises(ValueError, match="with tasks"):
            await apply_task_action(
                db,
                project.id,
                TaskAction(action="update", task_id=epic.id, kind="task"),
            )


async def test_task_mutation_is_atomic_on_rollback(db_factory):
    async with db_factory() as db:
        project, _, _ = await seed_turn(db)
        task = await apply_task_action(
            db,
            project.id,
            TaskAction(action="create", title="Rollback all task projections"),
        )
        project_id = project.id
        task_id = task.id
        await db.flush()
        assert await db.scalar(select(TaskRevision).where(TaskRevision.task_id == task_id))
        assert await db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == task_id))
        assert await db.scalar(
            select(Activity).where(
                Activity.project_id == project_id,
                Activity.kind == "task.create",
            )
        )
        await db.rollback()

    async with db_factory() as db:
        assert await db.get(Task, task_id) is None
        assert await db.scalar(select(TaskRevision).where(TaskRevision.task_id == task_id)) is None
        assert await db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == task_id)) is None
        assert await db.scalar(
            select(Activity).where(
                Activity.project_id == project_id,
                Activity.kind == "task.create",
            )
        ) is None


async def test_segments_and_commit_are_idempotent(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_turn(db)
        current = await ensure_open_segment(db, session)
        assert current.id == turn.segment_id
        closed = await close_segment(db, session, "Previous topic")
        assert closed.id == current.id and current.status == "closed"
        open_segment = await ensure_open_segment(db, session)
        turn = Turn(
            project_id=project.id,
            session_id=session.id,
            segment_id=open_segment.id,
            external_id=str(uuid.uuid4()),
            user_prompt="Now what?",
        )
        db.add(turn)
        await db.commit()

        payload = TurnCommit(
            assistant_response="Answer",
            topic_changed=True,
            segment_summary="Summary",
            task_actions=[TaskAction(action="create", title="Next")],
            profile_update=ProjectUpdate(cause="Cause"),
            receipt="Saved",
        )
        first = await commit_turn(db, turn, payload)
        second = await commit_turn(db, turn, payload)
        assert first["idempotent"] is False and second["idempotent"] is True
        assert turn.status == "committed"
        assert turn.sleep_status == "pending"
        assert len((await db.scalars(select(Memory))).all()) == 0
        assert len((await db.scalars(select(SleepJob))).all()) == 1


async def test_legacy_commit_reuses_mutations_already_applied_in_the_same_turn(db_factory):
    async with db_factory() as db:
        project, _, turn = await seed_turn(db)
        epic = Task(
            project_id=project.id,
            kind="epic",
            title="Pre-release beta",
            priority="high",
            labels=["beta"],
        )
        child = Task(
            project_id=project.id,
            title="Redesign logo",
            next_action="Choose a concept",
            version=2,
        )
        db.add_all([epic, child])
        await db.flush()
        child.epic_id = epic.id
        await db.commit()

        result = await commit_turn(
            db,
            turn,
            TurnCommit(
                assistant_response="Done",
                task_actions=[
                    TaskAction(
                        action="create",
                        kind="epic",
                        title="Pre-release beta",
                        priority="high",
                        labels=["beta"],
                    ),
                    TaskAction(
                        action="update",
                        task_id=child.id,
                        epic_id=epic.id,
                        next_action="Choose a concept",
                        expected_version=1,
                    ),
                ],
            ),
        )

        epics = list(
            (
                await db.scalars(
                    select(Task).where(
                        Task.project_id == project.id,
                        Task.kind == "epic",
                        Task.title == "Pre-release beta",
                    )
                )
            ).all()
        )
        await db.refresh(child)
        assert len(epics) == 1
        assert result["tasks"][0]["id"] == epic.id
        assert child.version == 2


def test_memory_health_keeps_dashboard_messages_readable():
    auth = memory_health({"waiting": 1}, type("Job", (), {"error_kind": "auth_required", "provider": "claude", "retry_at": None})())
    limited = memory_health({"waiting": 1}, type("Job", (), {"error_kind": "rate_limited", "provider": "codex", "retry_at": None})())
    waiting = memory_health({"waiting": 1}, type("Job", (), {"error_kind": "bridge_unavailable", "provider": "codex", "retry_at": None})())
    updating = memory_health({"pending": 1, "running": 1}, None)
    updated = memory_health({}, None)
    assert auth["state"] == "connection_required" and "Connect Codex or Claude" in auth["summary"]
    assert limited["state"] == "limited" and "temporary usage limit" in limited["summary"]
    assert waiting["state"] == "waiting"
    assert updating["state"] == "updating"
    assert updated["state"] == "updated"


async def test_compaction_idempotence_hierarchy_and_unknown_memory_guards(db_factory):
    async with db_factory() as db:
        project, session, turn = await seed_turn(db)
        pre = await persist_compaction(db, session, CompactionRecord(session_id=session.id, phase="pre"))
        assert pre == {"recorded": True, "idempotent": False, "summary_saved": False, "sleep_job_id": None}
        turn.sleep_status = "pending"
        await db.commit()
        post_payload = CompactionRecord(session_id=session.id, phase="post", summary="Finished release discussion")
        post = await persist_compaction(db, session, post_payload)
        assert post["summary_saved"] is True and post["sleep_job_id"]
        repeated = await persist_compaction(db, session, post_payload)
        assert repeated["idempotent"] is True and repeated["sleep_job_id"] == post["sleep_job_id"]

        with pytest.raises(ValueError, match="epic not found"):
            await validate_task_hierarchy(
                db, project.id, kind="task", epic_id=str(uuid.uuid4())
            )
        with pytest.raises(ValueError, match="not injected"):
            await commit_turn(
                db,
                turn,
                TurnCommit(assistant_response="Answer", used_memory_ids=[str(uuid.uuid4())]),
            )
