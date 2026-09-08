"""Atomic, provider-free sprint lifecycle and immutable closure history."""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.models import (
    Project,
    Sprint,
    SprintMutation,
    SprintTaskSnapshot,
    Task,
    TaskRevision,
)
from dduo_solo_founder.schemas import (
    SprintArchive,
    SprintCreate,
    SprintHistoryCreate,
    SprintTransition,
    SprintUpdate,
)
from dduo_solo_founder.service import activity, serialize_json, utcnow
from dduo_solo_founder.task_index import (
    advance_task_index_generation,
    enqueue_task_projection,
    lock_task_index_project,
)
from dduo_solo_founder.task_views import compact_task, snapshot_hash
from dduo_solo_founder.team import request_principal


TERMINAL = ("done", "cancelled")


def placement_predicate(project_id: str, placement: str, sprint_id: str | None = None):
    """SQL is authoritative even when a semantic projection has stale placement."""
    clauses = [Task.project_id == project_id]
    if sprint_id is not None:
        clauses.append(Task.sprint_id == sprint_id)
    sprint_ids = select(Sprint.id).where(Sprint.project_id == project_id)
    if placement == "current":
        clauses.append(Task.sprint_id.in_(sprint_ids.where(Sprint.status == "active")))
    elif placement == "backlog":
        clauses.extend(
            [Task.sprint_id.is_(None), Task.status.not_in(TERMINAL), Task.kind == "task"]
        )
    elif placement == "archive":
        clauses.append(
            or_(
                Task.sprint_id.in_(sprint_ids.where(Sprint.status == "archived")),
                and_(Task.sprint_id.is_(None), Task.status.in_(TERMINAL)),
            )
        )
    return and_(*clauses)


async def get_sprint(db: AsyncSession, project_id: str, sprint_id: str) -> Sprint:
    sprint = await db.scalar(
        select(Sprint)
        .where(Sprint.project_id == project_id, Sprint.id == sprint_id)
        .execution_options(populate_existing=True)
    )
    if sprint is None:
        raise HTTPException(404, "sprint not found")
    return sprint


async def close_preview(db: AsyncSession, project_id: str, sprint_id: str) -> dict:
    sprint = await get_sprint(db, project_id, sprint_id)
    counts = dict(
        (
            await db.execute(
                select(Task.status, func.count(Task.id))
                .where(Task.project_id == project_id, Task.sprint_id == sprint_id)
                .group_by(Task.status)
            )
        ).all()
    )
    total = sum(counts.values())
    completed = sum(counts.get(status, 0) for status in TERMINAL)
    return {
        "sprint": serialize_json(sprint),
        "total": total,
        "completed_count": completed,
        "unfinished_count": total - completed,
    }


async def _save_task_placement(db: AsyncSession, task: Task, sprint_id: str | None) -> None:
    task.sprint_id = sprint_id
    task.version += 1
    task.updated_at = utcnow()
    principal = request_principal()
    db.add(
        TaskRevision(
            project_id=task.project_id,
            task_id=task.id,
            version=task.version,
            snapshot=serialize_json(task),
            actor="agent",
            actor_member_id=principal.member_id if principal else None,
            rationale="Sprint placement changed; execution status preserved",
        )
    )
    enqueue_task_projection(db, task)


def _closure_snapshot(sprint: Sprint, task: Task, destination: str | None) -> SprintTaskSnapshot:
    return SprintTaskSnapshot(
        project_id=sprint.project_id,
        sprint_id=sprint.id,
        closure_version=sprint.version,
        task_id=task.id,
        snapshot=json.loads(json.dumps(compact_task(task), default=str)),
        outcome=task.status,
        destination_sprint_id=destination,
    )


async def mutate_sprint(
    db: AsyncSession,
    project_id: str,
    operation: str,
    payload: SprintCreate | SprintUpdate | SprintTransition | SprintArchive | SprintHistoryCreate,
    sprint_id: str | None = None,
) -> dict:
    # The same per-project advisory lock is used by every task write. Thus no
    # assignment/status update can slip between preview-version validation and close.
    await lock_task_index_project(db, project_id)
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    digest = snapshot_hash(
        {"operation": operation, "sprint_id": sprint_id, "payload": payload.model_dump(mode="json")}
    )
    receipt = await db.get(SprintMutation, (project_id, payload.idempotency_key))
    if receipt is not None:
        if receipt.request_hash != digest:
            raise HTTPException(
                409, "idempotency key already used for a different sprint operation"
            )
        return receipt.response

    if operation in {"create", "history"}:
        sprint = Sprint(project_id=project_id, title=payload.title, objective=payload.objective)
        db.add(sprint)
        await db.flush()
    else:
        sprint = await get_sprint(db, project_id, sprint_id)
        if sprint.version != payload.expected_version:
            raise HTTPException(409, "sprint version conflict; refresh the close preview or sprint")
        sprint.version += 1
        sprint.updated_at = utcnow()

    if operation == "update":
        if sprint.status == "archived":
            raise HTTPException(409, "reopen the sprint before editing it")
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key in {"title", "objective"}:
                setattr(sprint, key, value)
    elif operation == "start":
        if sprint.status != "planned":
            raise HTTPException(409, "only a planned sprint can start")
        active = await db.scalar(
            select(Sprint.id).where(Sprint.project_id == project_id, Sprint.status == "active")
        )
        if active is not None:
            raise HTTPException(409, "project already has an active sprint")
        sprint.status, sprint.started_at = "active", utcnow()
        await advance_task_index_generation(db, project_id)
    elif operation == "reopen":
        if sprint.status != "archived":
            raise HTTPException(409, "only an archived sprint can reopen")
        sprint.status = "planned"
        await advance_task_index_generation(db, project_id)
    elif operation in {"archive", "history"}:
        destination = None
        if operation == "archive":
            if sprint.status == "archived":
                raise HTTPException(409, "sprint is already archived")
            if payload.destination_sprint_id:
                destination = await get_sprint(db, project_id, payload.destination_sprint_id)
                if destination.id == sprint.id or destination.status != "planned":
                    raise HTTPException(
                        422, "unfinished work destination must be another planned sprint"
                    )
            statement = select(Task).where(
                Task.project_id == project_id, Task.sprint_id == sprint.id
            )
        else:
            statement = select(Task).where(
                Task.project_id == project_id, Task.id.in_(payload.task_ids)
            )
        tasks = list((await db.scalars(statement.order_by(Task.id).with_for_update())).all())
        if operation == "history" and (
            len(tasks) != len(payload.task_ids)
            or any(
                task.kind != "task" or task.status not in TERMINAL or task.sprint_id is not None
                for task in tasks
            )
        ):
            raise HTTPException(422, "select completed, unsprinted tasks from this project")
        if tasks:
            await advance_task_index_generation(db, project_id)
        carried = False
        for task in tasks:
            target = destination.id if destination is not None else None
            if operation == "history":
                await _save_task_placement(db, task, sprint.id)
            db.add(
                _closure_snapshot(
                    sprint, task, target if task.status not in TERMINAL else sprint.id
                )
            )
            if operation == "archive" and task.status not in TERMINAL:
                await _save_task_placement(db, task, target)
                carried = True
        if carried and destination is not None:
            destination.version += 1
            destination.updated_at = utcnow()
        sprint.status = "archived"
        sprint.archive_version = sprint.version
        sprint.archived_at = utcnow()
    await db.flush()
    response = serialize_json(sprint)
    db.add(
        SprintMutation(
            project_id=project_id,
            idempotency_key=payload.idempotency_key,
            request_hash=digest,
            response=response,
        )
    )
    await activity(
        db,
        project_id,
        f"sprint.{operation}",
        f"Sprint {operation}: {sprint.title}",
        {"id": sprint.id, "version": sprint.version},
    )
    await db.commit()
    return response
