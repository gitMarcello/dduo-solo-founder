from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.models import (
    Activity,
    OperationalManual,
    Plan,
    PlanWorkItem,
    ProfileRevision,
    Project,
    RawEvent,
    Segment,
    Session,
    Sprint,
    Task,
    TaskRevision,
    Turn,
)
from dduo_solo_founder.operating_contract import COFOUNDER_CONTRACT, TASK_CONTRACT
from dduo_solo_founder.schemas import (
    CompactionRecord,
    ProjectUpdate,
    TaskAction,
    TurnCommit,
)
from dduo_solo_founder.sleep_engine import schedule_sleep
from dduo_solo_founder.team import request_principal
from dduo_solo_founder.task_index import (
    advance_task_index_generation,
    enqueue_task_projection,
    lock_task_index_project,
    pending_task_projection_ids,
)
from dduo_solo_founder.task_views import compact_task
from dduo_solo_founder.team_manual import serialize_manual


BRIEFING_TASK_BYTES_BUDGET = 20_000


def compact_task_briefing(rows: list[Task]) -> list[dict]:
    """Select ordered compact cards within a deterministic UTF-8 budget."""
    selected: list[dict] = []
    used = 2  # JSON list brackets.
    for task in rows:
        card = compact_task(task)
        encoded = json.dumps(
            card,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        separator = 1 if selected else 0
        if used + separator + len(encoded) > BRIEFING_TASK_BYTES_BUDGET:
            break
        selected.append(card)
        used += separator + len(encoded)
    return selected


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def memory_health(sleep_counts: dict, latest_sleep) -> dict:
    """Return a dashboard-safe view of asynchronous memory consolidation."""
    waiting = int(sleep_counts.get("waiting") or 0)
    pending = int(sleep_counts.get("pending") or 0) + int(sleep_counts.get("running") or 0)
    kind = getattr(latest_sleep, "error_kind", None) if latest_sleep else None
    provider = getattr(latest_sleep, "provider", None) if latest_sleep else None
    retry_at = getattr(latest_sleep, "retry_at", None) if latest_sleep else None
    if waiting and kind == "auth_required":
        summary = (
            f"Connect {(provider or 'the selected client').title()} in Setup to resume memory."
        )
        state = "connection_required"
    elif waiting and kind == "rate_limited":
        summary = "Memory has a temporary usage limit and will retry automatically."
        state = "limited"
    elif waiting:
        summary = "Local memory will retry automatically."
        state = "waiting"
    elif pending:
        summary = "Recent turns are waiting to be consolidated."
        state = "updating"
    else:
        summary = "Memory is up to date."
        state = "updated"
    return {
        "available": not bool(waiting),
        "state": state,
        "summary": summary,
        "retry_at": retry_at,
        "provider": provider,
        "error_kind": kind,
    }


async def project_memory_health(
    db: AsyncSession,
    project_id: str,
    *,
    provider: str | None = None,
    include_providers: bool = False,
) -> dict:
    """Return global or provider-scoped consolidation health without mixing clients."""

    async def counts_for(selected_provider: str | None) -> dict[str, int]:
        from dduo_solo_founder.models import SleepJob

        statement = select(SleepJob.status, func.count(SleepJob.id)).where(
            SleepJob.project_id == project_id
        )
        if selected_provider is not None:
            statement = statement.where(SleepJob.provider == selected_provider)
        return {
            status: int(count)
            for status, count in (await db.execute(statement.group_by(SleepJob.status))).all()
        }

    async def latest_for(selected_provider: str | None):
        from dduo_solo_founder.models import SleepJob

        statement = select(SleepJob).where(SleepJob.project_id == project_id)
        if selected_provider is not None:
            statement = statement.where(SleepJob.provider == selected_provider)
        waiting = await db.scalar(
            statement.where(SleepJob.status == "waiting")
            .order_by(desc(SleepJob.created_at))
            .limit(1)
        )
        return waiting or await db.scalar(statement.order_by(desc(SleepJob.created_at)).limit(1))

    counts = await counts_for(provider)
    latest = await latest_for(provider)
    result = {**memory_health(counts, latest), "jobs": counts}
    if include_providers:
        result["providers"] = {}
        for name in ("codex", "claude"):
            provider_counts = await counts_for(name)
            provider_latest = await latest_for(name)
            result["providers"][name] = {
                **memory_health(provider_counts, provider_latest),
                "jobs": provider_counts,
            }
    return result


async def activity(
    db: AsyncSession,
    project_id: str,
    kind: str,
    summary: str,
    detail=None,
    *,
    actor: str | None = None,
    actor_member_id: str | None = None,
):
    principal = request_principal()
    member_id = actor_member_id or (principal.member_id if principal else None)
    db.add(
        Activity(
            project_id=project_id,
            kind=kind,
            summary=summary,
            detail=detail or {},
            actor=actor or (f"member:{member_id}" if member_id else "agent"),
            actor_member_id=member_id,
        )
    )
    if not kind.startswith("backup."):
        await mark_project_dirty(db, project_id)


async def mark_project_dirty(db: AsyncSession, project_id: str) -> None:
    """Advance the generation used to detect writes racing an in-progress backup."""
    project = await db.get(Project, project_id)
    if project:
        project.backup_dirty = True
        project.backup_generation += 1
        project.updated_at = utcnow()


async def briefing(db: AsyncSession, project_id: str, *, client: str | None = None) -> dict:
    project = await db.get(Project, project_id)
    if not project:
        raise LookupError("project not found")
    operational_manual = await db.get(OperationalManual, project_id)
    active_sprint = await db.scalar(
        select(Sprint).where(Sprint.project_id == project_id, Sprint.status == "active")
    )
    live_placement = Task.sprint_id == active_sprint.id if active_sprint else Task.sprint_id.is_(None)
    status_rank = case(
        (Task.status == "in_progress", 0),
        (Task.status == "blocked", 1),
        else_=2,
    )
    priority_rank = case(
        (Task.priority == "critical", 0),
        (Task.priority == "high", 1),
        (Task.priority == "medium", 2),
        else_=3,
    )
    tasks = list(
        (
            await db.scalars(
                select(Task)
                .where(
                    Task.project_id == project_id,
                    Task.status.not_in(("done", "cancelled")),
                    live_placement,
                )
                .order_by(status_rank, priority_rank, desc(Task.updated_at), Task.id)
                .limit(12)
            )
        ).all()
    )
    task_status_counts = {
        status: int(count)
        for status, count in sorted(
            (
                await db.execute(
                    select(Task.status, func.count(Task.id))
                    .where(Task.project_id == project_id)
                    .group_by(Task.status)
                )
            ).all()
        )
    }
    total_tasks = sum(task_status_counts.values())
    nonterminal_tasks = total_tasks - sum(
        task_status_counts.get(status, 0) for status in ("done", "cancelled")
    )
    plan_status_rank = case(
        (Plan.status == "executing", 0),
        (Plan.status == "decided", 1),
        else_=2,
    )
    plans = list(
        (
            await db.scalars(
                select(Plan)
                .where(Plan.project_id == project_id, Plan.status.not_in(("completed", "superseded")))
                .order_by(plan_status_rank, desc(Plan.updated_at), Plan.id)
                .limit(3)
            )
        ).all()
    )
    plan_ids = [plan.id for plan in plans]
    links_by_plan = {plan_id: [] for plan_id in plan_ids}
    if plan_ids:
        links = (
            await db.execute(
                select(PlanWorkItem.plan_id, PlanWorkItem.task_id)
                .where(PlanWorkItem.plan_id.in_(plan_ids))
                .order_by(PlanWorkItem.plan_id, PlanWorkItem.position)
            )
        ).all()
        for plan_id, task_id in links:
            links_by_plan[plan_id].append(task_id)
    recent_segments = list(
        (
            await db.scalars(
                select(Segment)
                .where(
                    Segment.project_id == project_id,
                    Segment.status == "closed",
                    Segment.summary != "",
                )
                .order_by(desc(Segment.closed_at), desc(Segment.started_at))
                .limit(3)
            )
        ).all()
    )
    consolidation_health = await project_memory_health(
        db,
        project_id,
        # Sleep execution belongs to the server, not to the interactive client
        # that requested this briefing. Every client therefore sees the same queue.
        provider=None,
        include_providers=True,
    )
    task_indexing_pending = bool(await db.scalar(pending_task_projection_ids(project_id).limit(1)))
    compact_tasks = compact_task_briefing(tasks)
    return {
        "project": serialize(project),
        "operational_manual": serialize_manual(operational_manual),
        "tasks": compact_tasks,
        "task_counts": {
            "total": total_tasks,
            "nonterminal": nonterminal_tasks,
            "selected": len(compact_tasks),
            "omitted": max(nonterminal_tasks - len(compact_tasks), 0),
            "by_status": task_status_counts,
        },
        "task_indexing_pending": task_indexing_pending,
        "task_index_status": (
            "indexing"
            if task_indexing_pending
            else "ready"
            if project.task_index_reconciled
            else "degraded"
        ),
        "plans": [
            {
                "id": plan.id,
                "title": plan.title,
                "objective": plan.objective,
                "status": plan.status,
                "labels": plan.labels,
                "work_item_ids": links_by_plan[plan.id],
                "content_excerpt": plan.content[:1_200],
                "version": plan.version,
                "updated_at": plan.updated_at,
            }
            for plan in plans
        ],
        "recent_handoffs": [
            {
                "id": segment.id,
                "summary": segment.summary[:2_500],
                "closed_at": segment.closed_at,
            }
            for segment in recent_segments
        ],
        "onboarding_required": not project.cause or not project.objectives,
        "memory_status": consolidation_health,
        "instruction": (
            "Treat the project operating manual as explicit standing instructions for solo and team use; "
            "it takes precedence over inferred memories and historical handoffs, while direct current "
            "user instructions remain authoritative. "
            + COFOUNDER_CONTRACT
            + TASK_CONTRACT
            + " The Stop hook records turns and asynchronous sleep owns semantic-memory consolidation."
        ),
    }


async def update_profile(db: AsyncSession, project: Project, patch: ProjectUpdate) -> None:
    locked = await db.scalar(
        select(Project)
        .where(Project.id == project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise LookupError("project not found")
    project = locked
    requested = patch.model_dump(exclude_none=True, exclude={"rationale", "expected_version"})
    changes = {key: value for key, value in requested.items() if getattr(project, key) != value}
    if not changes:
        return
    if patch.expected_version is not None and patch.expected_version != project.profile_version:
        raise ValueError("project profile version conflict")
    before = {key: getattr(project, key) for key in changes}
    principal = request_principal()
    for key, value in changes.items():
        setattr(project, key, value)
    project.profile_version += 1
    project.updated_at = utcnow()
    db.add(
        ProfileRevision(
            project_id=project.id,
            version=project.profile_version,
            snapshot={
                "cause": project.cause,
                "principles": project.principles,
                "objectives": project.objectives,
                "context": project.context,
            },
            rationale=patch.rationale,
            actor_member_id=principal.member_id if principal else None,
        )
    )
    await activity(
        db,
        project.id,
        "profile.updated",
        "Project profile updated",
        {
            "before": before,
            "after": changes,
            "rationale": patch.rationale,
        },
    )


async def ensure_open_segment(db: AsyncSession, session: Session) -> Segment:
    if session.open_segment_id:
        segment = await db.get(Segment, session.open_segment_id)
        if segment and segment.status == "open":
            return segment
    segment = Segment(project_id=session.project_id, session_id=session.id)
    db.add(segment)
    await db.flush()
    session.open_segment_id = segment.id
    return segment


async def close_segment(db: AsyncSession, session: Session, summary: str) -> Segment:
    segment = await ensure_open_segment(db, session)
    segment.status = "closed"
    segment.summary = summary
    segment.closed_at = utcnow()
    segment.sleep_status = "pending"
    replacement = Segment(project_id=session.project_id, session_id=session.id)
    db.add(replacement)
    await db.flush()
    session.open_segment_id = replacement.id
    return segment


async def persist_compaction(
    db: AsyncSession,
    session: Session,
    payload: CompactionRecord,
) -> dict:
    """Store a compaction handoff as sleep input, never as final semantic memory."""
    summary = payload.summary.strip()
    if payload.phase == "post" and summary:
        from dduo_solo_founder.models import SleepJob

        existing_job = await db.scalar(
            select(SleepJob)
            .join(Segment, Segment.id == SleepJob.segment_id)
            .where(
                Segment.session_id == session.id,
                Segment.summary == summary,
                SleepJob.trigger == "compaction",
            )
            .order_by(desc(SleepJob.created_at))
            .limit(1)
        )
        if existing_job:
            return {
                "recorded": True,
                "idempotent": True,
                "summary_saved": True,
                "sleep_job_id": existing_job.id,
            }
    event = RawEvent(
        project_id=session.project_id,
        session_id=session.id,
        event_type=f"compaction_{payload.phase}",
        payload={"trigger": payload.trigger, "summary": summary},
        actor=session.client,
        actor_member_id=session.member_id,
    )
    db.add(event)

    job = None
    if payload.phase == "post" and summary:
        closed = await close_segment(db, session, summary)
        job = await schedule_sleep(
            db,
            project_id=session.project_id,
            session_id=session.id,
            segment_id=closed.id,
            trigger="compaction",
        )

    await activity(
        db,
        session.project_id,
        f"compaction.{payload.phase}",
        "Context compaction checkpoint queued for sleep"
        if job
        else f"Context compaction {payload.phase} recorded",
        {
            "session_id": session.id,
            "client": session.client,
            "trigger": payload.trigger,
            "summary_saved": bool(summary),
            "sleep_job_id": job.id if job else None,
        },
        actor_member_id=session.member_id,
    )
    await db.commit()
    return {
        "recorded": True,
        "idempotent": bool(job and job.status == "completed"),
        "summary_saved": bool(summary),
        "sleep_job_id": job.id if job else None,
    }


async def apply_task_action(
    db: AsyncSession,
    project_id: str,
    action: TaskAction,
    *,
    actor: str = "agent",
    actor_member_id: str | None = None,
    session_id: str | None = None,
    dedupe_since: datetime | None = None,
) -> Task:
    if actor_member_id is None:
        principal = request_principal()
        actor_member_id = principal.member_id if principal else None
    # Task mutations are short database transactions. Serializing them per
    # project prevents hierarchy row-lock inversions and keeps full inventory
    # reconciliation from racing authoritative changes; semantic search never
    # holds this lock during provider latency.
    await lock_task_index_project(db, project_id)
    task = None
    created = False
    if action.action == "create":
        if not action.title:
            raise ValueError("task title is required")
        if dedupe_since is not None:
            task = await db.scalar(
                select(Task)
                .where(
                    Task.project_id == project_id,
                    Task.kind == (action.kind or "task"),
                    func.lower(Task.title) == action.title.strip().lower(),
                    Task.created_at >= dedupe_since,
                )
                .order_by(desc(Task.created_at))
                .with_for_update()
                .execution_options(populate_existing=True)
                .limit(1)
            )
        if task is None:
            task = Task(project_id=project_id, title=action.title, kind=action.kind or "task")
            db.add(task)
            created = True
    else:
        task = await db.scalar(
            select(Task)
            .where(Task.id == action.task_id, Task.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not task:
            raise ValueError("task not found in project")
    values = action.model_dump(
        exclude_unset=True,
        exclude={"action", "task_id", "expected_version"},
    )
    has_requested_state = bool(values) or action.action == "complete"
    already_applied = (
        has_requested_state
        and all(getattr(task, key) == value for key, value in values.items())
        and (action.action != "complete" or task.status == "done")
    )
    if task and action.expected_version is not None and task.version != action.expected_version:
        if not created and already_applied:
            return task
        raise ValueError("task version conflict")
    next_kind = values.get("kind", task.kind)
    next_epic_id = values.get("epic_id", task.epic_id)
    await validate_task_sprint(db, project_id, task=task, values=values)
    previous_sprint_id = task.sprint_id
    await validate_task_hierarchy(
        db,
        project_id,
        kind=next_kind,
        epic_id=next_epic_id,
        task_id=task.id,
        converting_epic=task.kind == "epic" and next_kind == "task",
    )
    changed = action.action == "complete" and task.status != "done"
    for key, value in values.items():
        if getattr(task, key) != value:
            setattr(task, key, value)
            changed = True
    if action.action == "complete":
        task.status = "done"
    if not created and not changed:
        return task
    await touch_task_sprints(db, project_id, previous_sprint_id, task.sprint_id)
    await advance_task_index_generation(db, project_id)
    task.version = (task.version or 1) + (0 if created else 1)
    task.updated_at = utcnow()
    await db.flush()
    db.add(
        TaskRevision(
            project_id=project_id,
            task_id=task.id,
            version=task.version,
            snapshot=serialize_json(task),
            actor=actor,
            actor_member_id=actor_member_id,
            session_id=session_id,
            rationale=action.rationale or "",
        )
    )
    enqueue_task_projection(db, task)
    recorded_action = (
        "create" if created else ("complete" if action.action == "complete" else "update")
    )
    await activity(
        db,
        project_id,
        f"task.{recorded_action}",
        f"Task {recorded_action}: {task.title}",
        {"id": task.id},
        actor_member_id=actor_member_id,
    )
    return task


async def validate_task_sprint(
    db: AsyncSession, project_id: str, *, task: Task | None = None, values: dict,
) -> None:
    """Validate placement separately from execution status and epic ownership."""
    kind = values.get("kind", task.kind if task else "task")
    sprint_id = values.get("sprint_id", task.sprint_id if task else None)
    if sprint_id == "":
        raise ValueError("sprint_id must be null or identify a sprint")
    if kind == "epic" and sprint_id:
        raise ValueError("epics are project-wide and cannot belong to a sprint")
    if sprint_id:
        sprint = await db.scalar(
            select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project_id)
        )
        if sprint is None:
            raise ValueError("sprint not found in project")
        if sprint.status == "archived":
            if task is None or task.sprint_id != sprint_id or "sprint_id" in values:
                raise ValueError("cannot assign work to an archived sprint")
            if values.get("status", task.status) not in {"done", "cancelled"}:
                raise ValueError("reopening archived work requires an explicit sprint or backlog destination")


async def touch_task_sprints(db: AsyncSession, project_id: str, *sprint_ids: str | None) -> None:
    """Any membership/content change invalidates a previously displayed close preview."""
    for sprint_id in sorted({value for value in sprint_ids if value}):
        sprint = await db.scalar(
            select(Sprint).where(Sprint.project_id == project_id, Sprint.id == sprint_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if sprint is not None and sprint.status != "archived":
            sprint.version += 1
            sprint.updated_at = utcnow()
    await db.flush()


async def validate_task_hierarchy(
    db: AsyncSession,
    project_id: str,
    *,
    kind: str,
    epic_id: str | None,
    task_id: str | None = None,
    converting_epic: bool = False,
) -> None:
    """Enforce the intentionally shallow Epic -> Task hierarchy."""
    if kind == "epic" and epic_id:
        raise ValueError("an epic cannot belong to another epic")
    if epic_id:
        if epic_id == task_id:
            raise ValueError("a task cannot be its own epic")
        epic = await db.scalar(
            select(Task)
            .where(Task.id == epic_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not epic or epic.project_id != project_id or epic.kind != "epic":
            raise ValueError("epic not found in project")
    if converting_epic and task_id:
        child = await db.scalar(select(Task.id).where(Task.epic_id == task_id).limit(1))
        if child:
            raise ValueError("an epic with tasks cannot be converted to a task")


async def commit_turn(
    db: AsyncSession,
    turn: Turn,
    payload: TurnCommit,
) -> dict:
    if turn.committed:
        return {
            "turn_id": turn.id,
            "committed": True,
            "idempotent": True,
            "receipt": turn.semantic_commit.get("receipt", ""),
        }
    project = await db.get(Project, turn.project_id)
    if payload.profile_update:
        await update_profile(db, project, payload.profile_update)
    session = await db.get(Session, turn.session_id)
    tasks = [
        await apply_task_action(
            db,
            turn.project_id,
            item,
            actor=session.client if session else "agent",
            actor_member_id=session.member_id if session else None,
            session_id=turn.session_id,
            dedupe_since=turn.created_at,
        )
        for item in payload.task_actions
    ]
    unknown_memory_ids = set(payload.used_memory_ids or []) - set(turn.retrieved_memory_ids)
    if unknown_memory_ids:
        raise ValueError("used_memory_ids contains memories that were not injected into this turn")
    turn.assistant_response = payload.assistant_response
    turn.committed = True
    turn.semantic_commit = payload.model_dump(exclude={"assistant_response"})
    turn.status = "committed"
    turn.sleep_status = "skipped_off_record" if turn.off_record else "pending"
    turn.used_memory_ids = (
        payload.used_memory_ids
        if payload.used_memory_ids is not None
        else turn.retrieved_memory_ids
    )
    turn.committed_at = utcnow()
    if session:
        session.last_activity_at = turn.committed_at
    db.add(
        RawEvent(
            project_id=turn.project_id,
            session_id=turn.session_id,
            turn_id=turn.id,
            event_type="assistant_response",
            payload={"text": payload.assistant_response},
            actor_member_id=session.member_id if session else None,
        )
    )
    db.add(
        RawEvent(
            project_id=turn.project_id,
            session_id=turn.session_id,
            turn_id=turn.id,
            event_type="semantic_commit",
            payload=payload.model_dump(exclude={"assistant_response"}),
            actor_member_id=session.member_id if session else None,
        )
    )
    if payload.topic_changed:
        closed = await close_segment(db, session, payload.segment_summary)
        if not turn.off_record:
            await schedule_sleep(
                db,
                project_id=turn.project_id,
                session_id=turn.session_id,
                segment_id=closed.id,
                trigger="topic_boundary",
            )
    await activity(
        db,
        turn.project_id,
        "turn.committed",
        payload.receipt or "Turn committed",
        {
            "turn_id": turn.id,
            "topic_changed": payload.topic_changed,
            "task_count": len(tasks),
            "sleep_status": turn.sleep_status,
        },
        actor_member_id=session.member_id if session else None,
    )
    await db.commit()
    return {
        "turn_id": turn.id,
        "committed": True,
        "idempotent": False,
        "receipt": payload.receipt,
        "task_count": len(tasks),
        "tasks": [serialize(item) for item in tasks],
    }


def serialize(value) -> dict:
    return {
        attribute.columns[0].name: getattr(value, attribute.key)
        for attribute in value.__mapper__.column_attrs
    }


def serialize_json(value) -> dict:
    """Serialize an ORM row into values safe for a JSON database column."""
    return {
        key: item.isoformat() if isinstance(item, datetime) else item
        for key, item in serialize(value).items()
    }
