from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import String, and_, cast, delete, desc, func, or_, select, text, update
from sqlalchemy.orm import load_only
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder import __version__
from dduo_solo_founder.authority_receipts import (
    AuthorityReceipt,
    AuthorityReceiptError,
    issue_authority_receipt,
    verify_authority_receipt,
)
from dduo_solo_founder.backup_service import BackupEngine
from dduo_solo_founder.artifacts import (
    attach_artifact_to_plan,
    attach_artifact_to_task,
    plan_artifacts,
    register_artifact,
    serialize_artifact,
    task_artifacts,
)
from dduo_solo_founder.config import get_settings
from dduo_solo_founder.db import SessionLocal, get_session, init_db
from dduo_solo_founder.embeddings import (
    EmbeddingProviderError,
    VectorOperationError,
    embedding_service,
    provider_request_count,
)
from dduo_solo_founder.invitations import InvitationPayloadError, create_invitation_bundle
from dduo_solo_founder.models import (
    Activity,
    Artifact,
    BackupRecord,
    BrowserAuthCredential,
    ContextEventPayload,
    Memory,
    ObservabilityEvent,
    OutboxEvent,
    Plan,
    PlanArtifact,
    PlanRevision,
    PlanWorkItem,
    ProfileRevision,
    Project,
    RawEvent,
    RetrievalRun,
    Session,
    SleepJob,
    Sprint,
    SprintTaskSnapshot,
    Task,
    TaskArtifact,
    TaskRevision,
    Turn,
    OperationalManual,
    OperationalManualRevision,
    TeamAccessToken,
    TeamInvitation,
    TeamMember,
)
from dduo_solo_founder.memory_engine import (
    enqueue_memory_index,
    explain_memory,
    forget_memory,
    serialize_memory,
)
from dduo_solo_founder.retrieval import (
    bounded_semantic_query,
    replay_retrieval,
    retrieve_context,
)
from dduo_solo_founder.schemas import (
    ArtifactCreate,
    AuthorityCompleteRequest,
    AuthorityFinalizeRequest,
    AuthorityRecoveryRequest,
    AuthorityNodeRequest,
    AuthorityStatusRequest,
    BackupRestoreRegister,
    BrowserSessionExchange,
    CompactionRecord,
    ContextObservation,
    ContextObservationBatch,
    ClientTelemetryBatch,
    MemoryForget,
    PlanCreate,
    PlanUpdate,
    ProjectCreate,
    ProjectUpdate,
    ProjectRename,
    RawEventCreate,
    SessionStart,
    SessionPrivacyUpdate,
    SleepRequest,
    SprintArchive,
    SprintCreate,
    SprintHistoryCreate,
    SprintTransition,
    SprintUpdate,
    StopCheck,
    TaskCreate,
    TaskSearch,
    TaskUpdate,
    TurnBegin,
    TurnCommit,
    OperationalManualCompactDraft,
    OperationalManualUpdate,
    TeamBootstrap,
    TeamDeviceTokenCreate,
    TeamInviteCreate,
    TeamInviteExchange,
)
from dduo_solo_founder.observability import (
    ContextSnapshot,
    Observation,
    build_summary,
    date_window,
    embedding_price,
    estimated_tokens_for_bytes,
    record as record_observation,
    record_context,
    serialize_event,
)
from dduo_solo_founder.pricing import api_equivalent_cost
from dduo_solo_founder.service import (
    activity,
    briefing,
    commit_turn,
    ensure_open_segment,
    memory_health,
    memory_health_job_order,
    persist_compaction,
    mark_project_dirty,
    project_memory_health,
    serialize,
    serialize_json,
    utcnow,
    update_profile,
    validate_task_hierarchy,
    validate_task_sprint,
    touch_task_sprints,
)
from dduo_solo_founder.sprint_work import (
    close_preview, get_sprint as find_sprint, mutate_sprint, placement_predicate,
)
from dduo_solo_founder.sleep_engine import (
    SleepGenerationError,
    SleepProviderError,
    resume_project_sleep,
    schedule_project_sleep,
    sleep_executor_provider,
)
from dduo_solo_founder.team import (
    INFRASTRUCTURE_MANAGER,
    PROJECT_MEMBER,
    TeamPrincipal,
    authenticate_team_request,
    browser_cookie_name,
    browser_csrf_secret,
    browser_secret,
    hash_secret,
    invitation_code,
    lock_writable_project,
    principal_from_request,
    project_authority_predicate,
    project_authority_writable,
    request_principal as current_team_principal,
    require_infrastructure_manager,
    require_member_session,
    require_node_authority,
    serialize_member,
    team_envelope,
)
from dduo_solo_founder.team_manual import compact_manual_draft, serialize_manual
from dduo_solo_founder.task_index import (
    advance_task_index_generation,
    current_task_index_generation,
    enqueue_task_projection,
    lock_task_index_project,
    pending_task_projection_ids,
    task_index_payload,
    task_index_service,
)
from dduo_solo_founder.task_views import (
    WORKING_ATTACHMENT_LIMIT,
    TaskDetail,
    snapshot_response,
    task_view,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        interrupted = (
            await db.scalars(
                select(BackupRecord)
                .join(Project, Project.id == BackupRecord.project_id)
                .where(
                    BackupRecord.status.in_(["scheduled", "running"]),
                    project_authority_predicate(),
                )
            )
        ).all()
        for record in interrupted:
            record.status = "failed"
            record.error = "Backup interrupted before completion"
            record.completed_at = datetime.now(timezone.utc)
        await enqueue_task_reindex_events(db, project_id=None, origin="bootstrap", force=False)
        # Inventory can mark an already-current pending delivery complete
        # without queueing work, so always persist reconciliation.
        await db.commit()
    backup_scheduler = asyncio.create_task(_automatic_backup_loop())
    try:
        yield
    finally:
        backup_scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await backup_scheduler


app = FastAPI(
    title="dDuo Solo Founder",
    version=__version__,
    lifespan=lifespan,
    dependencies=[Depends(authenticate_team_request)],
)
INLINE_IMAGE_MIME_TYPES = frozenset(
    {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}
)


def require_remote_expected_version(principal: TeamPrincipal, expected_version: int | None) -> None:
    if not principal.trusted_local and expected_version is None:
        raise HTTPException(428, "expected_version is required for remote mutations")


async def lock_writable_turn(
    db: AsyncSession,
    turn_id: str,
    principal: TeamPrincipal,
) -> Turn:
    """Resolve a turn-scoped mutation through the same authority fence as project routes."""
    project_id = await db.scalar(select(Turn.project_id).where(Turn.id == turn_id))
    if project_id is None or (not principal.trusted_local and principal.project_id != project_id):
        raise HTTPException(404, "turn not found")
    try:
        await lock_writable_project(db, project_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "turn not found") from exc
        raise
    turn = await db.scalar(select(Turn).where(Turn.id == turn_id).with_for_update())
    if turn is None:
        raise HTTPException(404, "turn not found")
    return turn


async def serialize_tasks(
    db: AsyncSession,
    rows: list[Task],
    detail: TaskDetail = "full",
) -> list[dict]:
    attachments = (
        await task_artifacts(
            db,
            [row.id for row in rows],
            include_extracted_text=detail == "full",
            limit_per_task=WORKING_ATTACHMENT_LIMIT if detail == "working" else None,
        )
        if detail in {"working", "full"}
        else {row.id: [] for row in rows}
    )
    return [task_view(row, detail, attachments[row.id]) for row in rows]


async def enqueue_task_reindex_events(
    db: AsyncSession,
    *,
    project_id: str | None,
    origin: Literal["bootstrap", "restore"],
    force: bool,
    index_service=None,
    strict_inventory: bool = False,
) -> int:
    project_statement = select(Project).where(project_authority_predicate())
    if project_id is not None:
        project_statement = project_statement.where(Project.id == project_id)
    projects = list((await db.scalars(project_statement.order_by(Project.id))).all())
    project_ids = [project.id for project in projects]
    if not project_ids:
        return 0
    starting_generations = {project.id: int(project.task_index_generation) for project in projects}
    statement = select(Task).where(Task.project_id.in_(project_ids))
    tasks = list((await db.scalars(statement.order_by(Task.project_id, Task.id))).all())
    tasks_by_project: dict[str, list[Task]] = {
        current_project_id: [] for current_project_id in project_ids
    }
    for task in tasks:
        tasks_by_project[task.project_id].append(task)
    service = index_service
    inventories: dict[str, dict[str, dict] | None] = {}
    reconciliation_state = {
        current_project_id: "verified" if force else "rebuild" for current_project_id in project_ids
    }
    if not force:
        try:
            service = service or task_index_service()
        except Exception:
            if strict_inventory:
                raise
            service = None
        for current_project_id in project_ids:
            if service is None:
                inventories[current_project_id] = None
                continue
            try:
                inventory = await asyncio.to_thread(service.inventory, current_project_id)
            except Exception:
                if strict_inventory:
                    raise
                inventories[current_project_id] = None
                continue
            authoritative_ids = {task.id for task in tasks_by_project.get(current_project_id, [])}
            orphan_ids = sorted(set(inventory) - authoritative_ids)
            if orphan_ids:
                try:
                    await asyncio.to_thread(service.delete_points, current_project_id, orphan_ids)
                except Exception:
                    if strict_inventory:
                        raise
                    # Do not call an index complete while orphan points can
                    # crowd valid PostgreSQL tasks out of vector results.
                    inventories[current_project_id] = None
                    reconciliation_state[current_project_id] = "blocked"
                    continue
            inventories[current_project_id] = inventory
            reconciliation_state[current_project_id] = "verified"
    existing: dict[tuple[str, int], OutboxEvent] = {}
    if not force:
        event_statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == "task.upsert",
                OutboxEvent.status.in_(["pending", "failed"]),
            )
            .order_by(desc(OutboxEvent.created_at))
        )
        if project_id is not None:
            event_statement = event_statement.where(OutboxEvent.project_id == project_id)
        for event in (await db.scalars(event_statement)).all():
            version = event.payload.get("task_version") if isinstance(event.payload, dict) else None
            if isinstance(version, int) and not isinstance(version, bool):
                existing.setdefault((event.aggregate_id, version), event)
    queued = 0
    for task in tasks:
        previous = existing.get((task.id, int(task.version)))
        if not force:
            inventory = inventories.get(task.project_id)
            if inventory is not None:
                document = service.render(task)
                expected_payload = task_index_payload(task, document)
                point = inventory.get(task.id)
                if point is not None and all(
                    point.get(key) == value for key, value in expected_payload.items()
                ):
                    if previous is not None and previous.status in {"pending", "failed"}:
                        previous.status = "processed"
                        previous.processed_at = utcnow()
                        previous.last_error = None
                    continue
        if force or previous is None:
            enqueue_task_projection(db, task, origin=origin)
            queued += 1
    for project in projects:
        state = reconciliation_state[project.id]
        if await current_task_index_generation(db, project.id) != starting_generations[project.id]:
            # A concurrent authoritative mutation may have raced inventory or
            # orphan cleanup. Never publish readiness for that snapshot; the
            # next reconciliation repairs it from PostgreSQL.
            state = "blocked"
        if state != "verified" or service is None:
            project.task_index_reconciled = False
            continue
        epoch = project.task_index_epoch or str(uuid4())
        try:
            await asyncio.to_thread(
                service.write_marker,
                project.id,
                epoch,
                len(tasks_by_project.get(project.id, [])),
            )
        except Exception:
            project.task_index_reconciled = False
            if strict_inventory:
                raise
        else:
            project.task_index_epoch = epoch
            project.task_index_reconciled = True
    await db.flush()
    return queued


ACTIVE_TASK_STATUSES = ("todo", "in_progress", "blocked")
COMPLETED_TASK_STATUSES = ("done", "cancelled")


def task_search_statuses(payload: TaskSearch) -> list[str] | None:
    if payload.status:
        return [payload.status]
    if payload.scope == "active":
        return list(ACTIVE_TASK_STATUSES)
    if payload.scope == "completed":
        return list(COMPLETED_TASK_STATUSES)
    return None


async def pending_task_repairs(db: AsyncSession, project_id: str) -> set[str]:
    return set((await db.scalars(pending_task_projection_ids(project_id))).all())


async def current_task_index_state(
    db: AsyncSession,
    project_id: str,
) -> tuple[bool, int, str | None]:
    row = (
        await db.execute(
            select(
                Project.task_index_reconciled,
                Project.task_index_generation,
                Project.task_index_epoch,
            ).where(Project.id == project_id)
        )
    ).one_or_none()
    if row is None:
        raise LookupError("project not found")
    return bool(row[0]), int(row[1]), row[2]


async def task_projection_integrity_matches(
    service,
    project_id: str,
    epoch: str | None,
    authoritative_count: int,
) -> bool:
    """Verify collection identity and exact derived cardinality without embedding."""
    snapshot = await asyncio.to_thread(service.integrity_snapshot, project_id)
    return bool(epoch) and snapshot == (epoch, authoritative_count)


def task_matches_search_filters(
    task: Task,
    payload: TaskSearch,
    statuses: list[str] | None,
    sprint_statuses: dict[str, str] | None = None,
) -> bool:
    sprint_status = (sprint_statuses or {}).get(task.sprint_id)
    return not (
        (statuses is not None and task.status not in statuses)
        or (payload.kind is not None and task.kind != payload.kind)
        or (payload.epic_id is not None and task.epic_id != payload.epic_id)
        or (payload.sprint_id is not None and task.sprint_id != payload.sprint_id)
        or (payload.placement == "current" and sprint_status != "active")
        or (payload.placement == "backlog" and (
            task.sprint_id is not None or task.status in COMPLETED_TASK_STATUSES or task.kind != "task"
        ))
        or (payload.placement == "archive" and not (
            sprint_status == "archived" or (
                task.sprint_id is None and task.status in COMPLETED_TASK_STATUSES
            )
        ))
        or (payload.label is not None and payload.label not in (task.labels or []))
    )


def task_search_sprint_ids(payload: TaskSearch, sprint_statuses: dict[str, str]) -> list[str] | None:
    selected = None
    if payload.placement in {"current", "archive"}:
        wanted = "active" if payload.placement == "current" else "archived"
        selected = [key for key, value in sprint_statuses.items() if value == wanted]
    if payload.sprint_id is not None:
        selected = [payload.sprint_id] if selected is None or payload.sprint_id in selected else []
    return selected


def lexical_task_score(task: Task, query: str) -> float:
    normalized_query = " ".join(query.casefold().split())
    title = " ".join(task.title.casefold().split())
    fields = " ".join(
        str(value or "")
        for value in (
            task.title,
            task.objective,
            task.next_action,
            task.description,
            " ".join(task.labels or []),
        )
    ).casefold()
    query_terms = set(re.findall(r"\w+", normalized_query, flags=re.UNICODE))
    field_terms = set(re.findall(r"\w+", fields, flags=re.UNICODE))
    overlap = len(query_terms & field_terms) / max(len(query_terms), 1)
    if title == normalized_query:
        return 1.0
    if normalized_query in title:
        return min(0.99, 0.8 + overlap * 0.19)
    if normalized_query in fields:
        return min(0.89, 0.65 + overlap * 0.24)
    return overlap * 0.6


async def record_task_search_retrieval(
    db: AsyncSession,
    *,
    project_id: str,
    started_at: datetime,
    match_type: str,
    candidate_count: int,
    selected_count: int,
    stale_count: int = 0,
    degraded: bool = False,
) -> None:
    await record_observation(
        db,
        Observation(
            project_id=project_id,
            idempotency_key=f"task-search-retrieval:{uuid4()}",
            category="retrieval",
            operation="retrieval.task_search",
            status="degraded" if degraded else "success",
            measurement_source="unavailable",
            duration_ms=round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
            request_count=1,
            item_count=selected_count,
            candidate_count=candidate_count,
            selected_count=selected_count,
            dropped_count=max(candidate_count - selected_count, 0),
            details={
                "match_type": match_type,
                "stale_hits": stale_count,
            },
        ),
    )


class ContextSnapshotConflictError(Exception):
    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"context snapshot conflicts with existing event {event_id}")


def context_snapshot(item: ContextObservation) -> ContextSnapshot | None:
    """Recalculate every exact-snapshot invariant at the persistence boundary."""
    if item.content is None:
        return None
    encoded = item.content.encode("utf-8")
    expected_hash = hashlib.sha256(encoded).hexdigest()
    expected_tokens = estimated_tokens_for_bytes(len(encoded))
    # Preserve the exact manifest contract sent by older and newer hooks. New
    # optional selection fields must not be invented on historical payloads.
    manifest = [component.model_dump(exclude_unset=True) for component in item.components]
    manifest_bytes = {component["name"]: component["utf8_bytes"] for component in manifest}
    if (
        len(item.content) != item.characters
        or len(encoded) != item.utf8_bytes
        or expected_tokens != item.estimated_tokens
        or expected_hash != item.content_sha256
        or manifest_bytes != item.component_bytes
        or sum(manifest_bytes.values()) != len(encoded)
        or item.occurred_at is None
        or item.producer_version is None
        or item.render_version is None
    ):
        raise ValueError("context snapshot measurements are inconsistent")
    return ContextSnapshot(
        content=item.content,
        content_sha256=expected_hash,
        producer_version=item.producer_version,
        render_version=item.render_version,
        estimator_version=item.estimator_version,
        captured_at=item.occurred_at,
        components=manifest,
        tool_name=item.tool_name,
    )


async def persist_context_observations(
    db: AsyncSession,
    project_id: str,
    observations: list[ContextObservation],
    *,
    fallback_session_id: str | None = None,
    fallback_turn_id: str | None = None,
    strict: bool = True,
) -> int:
    """Persist numeric context records and optional exact payloads without blocking turns."""
    validated: list[tuple[ContextObservation, str | None, str | None, ContextSnapshot | None]] = []
    seen_snapshot_hashes: dict[str, str] = {}
    for item in observations:
        session_id = item.session_id or fallback_session_id
        turn_id = (
            None if item.operation == "context.session_start" else item.turn_id or fallback_turn_id
        )
        try:
            agent_session: Session | None = None
            if session_id:
                agent_session = await db.get(Session, session_id)
                if not agent_session or agent_session.project_id != project_id:
                    raise LookupError("session not found")
                principal = current_team_principal()
                if principal is not None:
                    require_member_session(principal, agent_session.member_id)
            if turn_id:
                turn = await db.get(Turn, turn_id)
                if not turn or turn.project_id != project_id:
                    raise LookupError("turn not found")
                if session_id and turn.session_id != session_id:
                    raise LookupError("turn does not belong to session")
                session_id = session_id or turn.session_id
                if agent_session is None:
                    agent_session = await db.get(Session, turn.session_id)
                    if not agent_session or agent_session.project_id != project_id:
                        raise LookupError("session not found")
                    principal = current_team_principal()
                    if principal is not None:
                        require_member_session(principal, agent_session.member_id)
            if item.retrieval_run_id:
                retrieval = await db.get(RetrievalRun, item.retrieval_run_id)
                if not retrieval or retrieval.project_id != project_id:
                    raise LookupError("retrieval run not found")
                if session_id and retrieval.session_id and retrieval.session_id != session_id:
                    raise LookupError("retrieval run does not belong to session")
                if turn_id and retrieval.turn_id and retrieval.turn_id != turn_id:
                    raise LookupError("retrieval run does not belong to turn")
                if retrieval.session_id:
                    if session_id and retrieval.session_id != session_id:
                        raise LookupError("retrieval run does not belong to session")
                    session_id = session_id or retrieval.session_id
                    if agent_session is None:
                        agent_session = await db.get(Session, retrieval.session_id)
                        if not agent_session or agent_session.project_id != project_id:
                            raise LookupError("session not found")
                        principal = current_team_principal()
                        if principal is not None:
                            require_member_session(principal, agent_session.member_id)
            snapshot = context_snapshot(item)
            if strict and snapshot is not None:
                previous_hash = seen_snapshot_hashes.get(item.event_id)
                if previous_hash is not None and previous_hash != snapshot.content_sha256:
                    raise ContextSnapshotConflictError(item.event_id)
                seen_snapshot_hashes[item.event_id] = snapshot.content_sha256
                existing_event = await db.scalar(
                    select(ObservabilityEvent).where(
                        ObservabilityEvent.project_id == project_id,
                        ObservabilityEvent.idempotency_key == f"context:{item.event_id}",
                    )
                )
                if existing_event is not None:
                    existing_hash = await db.scalar(
                        select(ContextEventPayload.content_sha256).where(
                            ContextEventPayload.observability_event_id == existing_event.id
                        )
                    )
                    if existing_hash is not None and existing_hash != snapshot.content_sha256:
                        raise ContextSnapshotConflictError(item.event_id)
        except Exception:
            if strict:
                raise
            continue
        validated.append((item, session_id, turn_id, snapshot))

    accepted = 0
    for item, session_id, turn_id, snapshot in validated:
        component_bytes = dict(item.component_bytes)
        residual_bytes = item.utf8_bytes - sum(component_bytes.values())
        if residual_bytes:
            component_bytes["overhead"] = component_bytes.get("overhead", 0) + residual_bytes
        details: dict[str, object] = {
            f"component_{key}": value for key, value in component_bytes.items()
        }
        if item.budget is not None:
            details.update(
                {
                    "budget_limit_characters": item.budget.limit_characters,
                    "client_character_units": item.budget.client_character_units,
                    "candidate_characters": item.budget.candidate_characters,
                    "candidate_utf8_bytes": item.budget.candidate_utf8_bytes,
                    "candidate_estimated_tokens": item.budget.candidate_estimated_tokens,
                    "avoided_characters": item.budget.avoided_characters,
                    "avoided_utf8_bytes": item.budget.avoided_utf8_bytes,
                    "avoided_estimated_tokens": item.budget.avoided_estimated_tokens,
                    "included_items": item.budget.included_items,
                    "partial_items": item.budget.partial_items,
                    "omitted_items": item.budget.omitted_items,
                    "budget_outcome": item.budget.outcome,
                    "delivery_expectation": item.budget.delivery_expectation,
                }
            )
        if item.delivery is not None:
            details.update(
                {
                    "delivery_kind": item.delivery.kind,
                    "delivery_reason": item.delivery.reason,
                    "foundation_changed": item.delivery.foundation_changed,
                    "work_changed": item.delivery.work_changed,
                    "reused_characters": item.delivery.reused_characters,
                    "reused_utf8_bytes": item.delivery.reused_utf8_bytes,
                    "reused_estimated_tokens": item.delivery.reused_estimated_tokens,
                }
            )
        try:
            status = await record_context(
                db,
                Observation(
                    project_id=project_id,
                    idempotency_key=f"context:{item.event_id}",
                    category="context",
                    operation=item.operation,
                    scope=item.scope,
                    status=(
                        "degraded"
                        if (
                            (item.budget is not None and item.budget.outcome == "fallback")
                            or (item.delivery is not None and item.delivery.kind == "fallback")
                        )
                        else "success"
                    ),
                    provider=item.client,
                    measurement_source="local_estimate",
                    session_id=session_id,
                    turn_id=turn_id,
                    retrieval_run_id=item.retrieval_run_id,
                    input_tokens=estimated_tokens_for_bytes(item.utf8_bytes),
                    characters=item.characters,
                    utf8_bytes=item.utf8_bytes,
                    request_count=1,
                    item_count=item.budget.included_items if item.budget else None,
                    candidate_count=(
                        item.budget.included_items + item.budget.omitted_items
                        if item.budget
                        else None
                    ),
                    selected_count=item.budget.included_items if item.budget else None,
                    dropped_count=item.budget.omitted_items if item.budget else None,
                    details=details,
                    occurred_at=item.occurred_at or utcnow(),
                ),
                snapshot,
            )
        except Exception:
            if strict:
                raise
            continue
        if status == "conflict":
            if strict:
                raise ContextSnapshotConflictError(item.event_id)
            continue
        accepted += int(status == "created")
    return accepted


async def plan_work_item_ids(db: AsyncSession, plan_ids: list[str]) -> dict[str, list[str]]:
    """Return linked epic/task ids grouped by plan in a stable dashboard order."""
    grouped = {plan_id: [] for plan_id in plan_ids}
    if not plan_ids:
        return grouped
    rows = (
        await db.execute(
            select(PlanWorkItem.plan_id, PlanWorkItem.task_id)
            .where(PlanWorkItem.plan_id.in_(plan_ids))
            .order_by(PlanWorkItem.plan_id, PlanWorkItem.position)
        )
    ).all()
    for plan_id, task_id in rows:
        grouped[plan_id].append(task_id)
    return grouped


async def serialize_plans(db: AsyncSession, rows: list[Plan]) -> list[dict]:
    """Serialize planning documents together with their execution links and files."""
    plan_ids = [row.id for row in rows]
    work_item_ids = await plan_work_item_ids(db, plan_ids)
    attachments = await plan_artifacts(db, plan_ids)
    return [
        {
            **serialize(plan),
            "work_item_ids": work_item_ids[plan.id],
            "attachments": attachments[plan.id],
        }
        for plan in rows
    ]


async def plan_snapshot(db: AsyncSession, plan: Plan) -> dict:
    """Capture the versioned planning document and its current execution links."""
    return {
        **serialize_json(plan),
        "work_item_ids": (await plan_work_item_ids(db, [plan.id]))[plan.id],
    }


async def validate_plan_work_items(
    db: AsyncSession,
    project_id: str,
    work_item_ids: list[str],
) -> list[str]:
    """Reject cross-project or missing work links before a plan is mutated."""
    normalized = list(dict.fromkeys(work_item_ids))
    if not normalized:
        return normalized
    existing = set(
        (
            await db.scalars(
                select(Task.id).where(
                    Task.project_id == project_id,
                    Task.id.in_(normalized),
                )
            )
        ).all()
    )
    missing = sorted(set(normalized) - existing)
    if missing:
        raise ValueError("plan work item not found in project")
    return normalized


async def replace_plan_work_items(
    db: AsyncSession,
    plan: Plan,
    work_item_ids: list[str],
) -> None:
    """Replace links atomically; plans deliberately do not own the work hierarchy."""
    await db.execute(delete(PlanWorkItem).where(PlanWorkItem.plan_id == plan.id))
    db.add_all(ordered_plan_work_items(plan.id, work_item_ids))
    await db.flush()


def ordered_plan_work_items(plan_id: str, work_item_ids: list[str]) -> list[PlanWorkItem]:
    """Build links with the caller's order persisted as an explicit position."""
    return [
        PlanWorkItem(
            plan_id=plan_id,
            task_id=task_id,
            position=position,
        )
        for position, task_id in enumerate(work_item_ids)
    ]


def artifact_content_response(artifact: Artifact) -> Response:
    """Serve one stored attachment with conservative private download headers."""
    if artifact.content is None:
        raise HTTPException(404, "attachment has no stored content")
    filename = artifact.filename or f"attachment-{artifact.id}"
    declared_mime_type = artifact.mime_type.partition(";")[0].strip().lower()
    inline = declared_mime_type in INLINE_IMAGE_MIME_TYPES
    return Response(
        content=artifact.content,
        media_type=declared_mime_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}"
            ),
            "ETag": f'"{artifact.content_hash}"',
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def serialize_sleep_job(job: SleepJob) -> dict:
    """Hide raw provider output; the dashboard only needs a stable operational message."""
    value = serialize(job)
    value.pop("last_error", None)
    value["message"] = memory_health({job.status: 1}, job)["summary"]
    return value


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    service = embedding_service()
    settings = get_settings()
    return {
        "status": "ok",
        "service": "dduo-solo-founder",
        "version": __version__,
        "embedding_provider": service.settings.embedding_provider,
        "embedding_model": service.settings.embedding_model,
        "embedding_index_version": service.settings.embedding_index_version,
        "task_embedding_index_version": service.settings.task_embedding_index_version,
        "task_index_max_characters": service.settings.task_index_max_characters,
        "task_index_max_utf8_bytes": service.settings.task_index_max_utf8_bytes,
        "sleep_engine": "cli",
        "sleep_executor_provider": "automatic",
        "cli_bridge_configured": bool(settings.cli_bridge_token),
    }


@app.post("/projects")
async def create_project(payload: ProjectCreate, db: AsyncSession = Depends(get_session)):
    project = await db.get(Project, payload.id)
    if project:
        if not project_authority_writable(project):
            # A restored foreign/pending authority may be inspected and claimed,
            # but even its checkout path is immutable until that claim succeeds.
            return serialize(project)
        # The local descriptor contains a bootstrap label, not authority to
        # undo an explicit rename when init/restore registers the same UUID.
        if project.root_path != payload.root_path:
            project.root_path = payload.root_path
            await activity(
                db,
                project.id,
                "project.location_updated",
                "Project location updated after restore or checkout move",
            )
            await db.commit()
        return serialize(project)
    project = Project(**payload.model_dump())
    db.add(project)
    await db.flush()
    db.add(
        ProfileRevision(
            project_id=project.id,
            version=project.profile_version,
            snapshot={
                "name": project.name,
                "cause": project.cause,
                "principles": project.principles,
                "objectives": project.objectives,
                "context": project.context,
            },
            rationale="Initial project profile",
        )
    )
    await activity(db, project.id, "project.created", f"Project created: {project.name}")
    await db.commit()
    return serialize(project)


def _member_principal(member: TeamMember, token: TeamAccessToken) -> TeamPrincipal:
    return TeamPrincipal(
        project_id=member.project_id,
        member_id=member.id,
        access_token_id=token.id,
        browser_session_id=None,
        display_name=member.display_name,
        capability=member.capability,
    )


async def _team_payload(db: AsyncSession, project_id: str, principal: TeamPrincipal) -> dict:
    members = list(
        (
            await db.scalars(
                select(TeamMember)
                .where(TeamMember.project_id == project_id)
                .order_by(TeamMember.created_at, TeamMember.id)
            )
        ).all()
    )
    tokens = list(
        (
            await db.scalars(
                select(TeamAccessToken)
                .where(TeamAccessToken.project_id == project_id)
                .order_by(TeamAccessToken.created_at, TeamAccessToken.id)
            )
        ).all()
    )
    devices: dict[str, list[dict]] = {}
    for token in tokens:
        devices.setdefault(token.member_id, []).append(
            {
                "id": token.id,
                "device_id": token.device_id,
                "device_label": token.device_label,
                "created_at": token.created_at,
                "last_used_at": token.last_used_at,
                "revoked_at": token.revoked_at,
            }
        )
    return team_envelope(
        principal,
        members=[
            {**serialize_member(member), "devices": devices.get(member.id, [])}
            for member in members
        ],
    )


def _authority_payload(project: Project) -> dict:
    return {
        "project_id": project.id,
        "node_id": project.authority_node_id,
        "target_node_id": project.authority_target_node_id,
        "generation": project.authority_generation,
        "state": project.authority_state,
        "writable": project_authority_writable(project),
    }


def _authority_secret() -> str:
    value = os.getenv("DDUO_NODE_AUTHORITY_SECRET", "").strip()
    if not value:
        raise HTTPException(503, "node authority credential is unavailable")
    return value


def _project_transfer_receipt(project: Project, kind: str) -> AuthorityReceipt:
    source_node_id = (project.authority_node_id or "").strip()
    target_node_id = (project.authority_target_node_id or "").strip()
    nonce = (project.authority_transfer_nonce or "").strip()
    if not source_node_id or not target_node_id or not nonce:
        raise HTTPException(409, "project transfer proof is unavailable")
    return AuthorityReceipt(
        kind=kind,
        project_id=project.id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        source_generation=project.authority_generation,
        nonce=nonce,
    )


def _verified_transfer_receipt(
    project: Project,
    token: str,
    *,
    expected_kind: str,
) -> AuthorityReceipt:
    try:
        receipt = verify_authority_receipt(
            token,
            _authority_secret(),
            expected_kind=expected_kind,
        )
    except AuthorityReceiptError as exc:
        raise HTTPException(409, "authority handoff receipt is invalid") from exc
    expected = _project_transfer_receipt(project, expected_kind)
    if receipt != expected:
        raise HTTPException(409, "authority handoff receipt does not match this transfer")
    return receipt


def _require_current_authority_node(node_id: str) -> None:
    current = os.getenv("DDUO_NODE_ID", "").strip()
    if not current:
        raise HTTPException(503, "memory node identity is unavailable")
    if not secrets.compare_digest(current, node_id):
        raise HTTPException(409, "authority request belongs to another memory node")


async def _recover_authority_interrupted_sleep(db: AsyncSession, project_id: str) -> int:
    """Make a worker interrupted by transfer/crash retryable on the claimed node."""
    timestamp = utcnow()
    result = await db.execute(
        update(SleepJob)
        .where(SleepJob.project_id == project_id, SleepJob.status == "running")
        .values(
            status="waiting",
            error_kind="bridge_unavailable",
            last_error="Memory consolidation was interrupted by an authority transition.",
            retry_at=timestamp,
            not_before=timestamp,
        )
    )
    return int(result.rowcount or 0)


async def _recover_authority_interrupted_backups(
    db: AsyncSession,
    project_id: str,
) -> int:
    """Close in-flight rows copied from a previous authority's backup image."""
    result = await db.execute(
        update(BackupRecord)
        .where(
            BackupRecord.project_id == project_id,
            BackupRecord.status.in_(["scheduled", "running"]),
        )
        .values(
            status="failed",
            error="Backup interrupted by an authority transition",
            completed_at=utcnow(),
        )
    )
    return int(result.rowcount or 0)


@app.get("/projects/{project_id}/authority")
async def get_project_authority(
    project_id: str,
    db: AsyncSession = Depends(get_session),
):
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return _authority_payload(project)


@app.post("/projects/{project_id}/authority/status")
async def inspect_host_project_authority(
    project_id: str,
    payload: AuthorityStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Read the host control plane before this restored project has a manager.

    This is deliberately not a bearer-auth fallback on the generic GET route.
    The project-specific authority credential, configured stack identity, and
    current node must all agree. No team membership or writable state is created.
    """
    require_node_authority(request)
    host_project_id = os.getenv("BACKUP_PROJECT_ID", "").strip()
    if not host_project_id:
        raise HTTPException(503, "memory host project identity is unavailable")
    if not secrets.compare_digest(host_project_id, project_id):
        raise HTTPException(404, "project not found")
    _require_current_authority_node(payload.node_id)
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    if (
        project.authority_state == "transfer_pending"
        and project.authority_target_node_id != payload.node_id
    ):
        raise HTTPException(409, "restored project is bound to another target node")
    return _authority_payload(project)


@app.post("/projects/{project_id}/authority/initialize")
async def initialize_project_authority(
    project_id: str,
    payload: AuthorityNodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Bind an unowned project database to its first authoritative remote node."""
    require_node_authority(request)
    _require_current_authority_node(payload.node_id)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if project.authority_generation != payload.expected_generation:
        raise HTTPException(409, "authority generation changed")
    if project.authority_state != "active":
        raise HTTPException(409, "project authority is not active")
    if project.authority_node_id not in {None, payload.node_id}:
        raise HTTPException(409, "project is already owned by another authoritative node")
    changed = project.authority_node_id is None
    if changed:
        project.authority_node_id = payload.node_id
        interrupted_backups = await _recover_authority_interrupted_backups(
            db,
            project_id,
        )
        await mark_project_dirty(db, project_id)
        await activity(
            db,
            project_id,
            "authority.initialized",
            "Authoritative memory node initialized",
            {
                "generation": project.authority_generation,
                "interrupted_backups": interrupted_backups,
            },
        )
        await db.commit()
    return {**_authority_payload(project), "idempotent": not changed}


@app.post("/projects/{project_id}/authority/prepare")
async def prepare_project_transfer(
    project_id: str,
    request: Request,
    expected_generation: int = Query(..., ge=1),
    target_node_id: str = Query(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
    db: AsyncSession = Depends(get_session),
):
    """Freeze mutations before the final full-recovery backup is created."""
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if project.authority_generation != expected_generation:
        raise HTTPException(409, "authority generation changed")
    if (
        project.authority_state == "transfer_pending"
        and project.authority_target_node_id == target_node_id
    ):
        changed = False
        if not project.authority_transfer_nonce:
            project.authority_transfer_nonce = secrets.token_hex(32)
            changed = True
        if not project.authority_node_id:
            project.authority_node_id = os.getenv("DDUO_NODE_ID", "").strip() or None
            changed = True
        if not project.authority_node_id:
            raise HTTPException(503, "source memory node identity is unavailable")
        _require_current_authority_node(project.authority_node_id)
        if changed:
            await db.commit()
        return {**_authority_payload(project), "idempotent": True}
    if project.authority_state == "transfer_pending":
        raise HTTPException(409, "project transfer is already bound to another target node")
    if project.authority_state != "active":
        raise HTTPException(409, "project authority cannot be transferred")
    running_backup = await db.scalar(
        select(BackupRecord.id)
        .where(
            BackupRecord.project_id == project_id,
            BackupRecord.status.in_(["scheduled", "running"]),
        )
        .limit(1)
    )
    if running_backup is not None:
        raise HTTPException(
            409,
            "wait for the current project backup to finish before transferring authority",
        )
    if not project.authority_node_id:
        project.authority_node_id = os.getenv("DDUO_NODE_ID", "").strip() or None
    if not project.authority_node_id:
        raise HTTPException(503, "source memory node identity is unavailable")
    _require_current_authority_node(project.authority_node_id)
    project.authority_state = "transfer_pending"
    project.authority_target_node_id = target_node_id
    project.authority_transfer_nonce = secrets.token_hex(32)
    interrupted_sleep = await _recover_authority_interrupted_sleep(db, project_id)
    await mark_project_dirty(db, project_id)
    await activity(
        db,
        project_id,
        "authority.transfer_prepared",
        "Project memory entered read-only transfer mode",
        {
            "generation": project.authority_generation,
            "target_node_id": target_node_id,
            "interrupted_sleep_jobs": interrupted_sleep,
        },
    )
    await db.commit()
    return {**_authority_payload(project), "idempotent": False}


@app.post("/projects/{project_id}/authority/cancel")
async def cancel_project_transfer(
    project_id: str,
    request: Request,
    expected_generation: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if (
        project.authority_generation != expected_generation
        or project.authority_state != "transfer_pending"
    ):
        raise HTTPException(409, "project transfer is not pending at this generation")
    if not project.authority_node_id:
        raise HTTPException(409, "source memory node identity is unavailable")
    _require_current_authority_node(project.authority_node_id)
    project.authority_state = "active"
    project.authority_target_node_id = None
    project.authority_transfer_nonce = None
    await mark_project_dirty(db, project_id)
    await activity(
        db,
        project_id,
        "authority.transfer_cancelled",
        "Project memory transfer was cancelled",
        {"generation": project.authority_generation},
    )
    await db.commit()
    return _authority_payload(project)


@app.post("/projects/{project_id}/authority/activate")
async def activate_transferred_project(
    project_id: str,
    payload: AuthorityNodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Prove the restored destination is ready while keeping it read-only."""
    require_node_authority(request)
    _require_current_authority_node(payload.node_id)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if (
        project.authority_state != "transfer_pending"
        or project.authority_generation != payload.expected_generation
    ):
        raise HTTPException(409, "restored project is not awaiting this transfer generation")
    if project.authority_target_node_id != payload.node_id:
        raise HTTPException(409, "restored project is bound to another target node")
    receipt = _project_transfer_receipt(project, "destination_ready")
    return {
        **_authority_payload(project),
        "phase": "destination_ready",
        "activation_receipt": issue_authority_receipt(_authority_secret(), receipt),
        "idempotent": True,
    }


@app.post("/projects/{project_id}/authority/complete")
async def complete_transferred_project(
    project_id: str,
    payload: AuthorityCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Become writable only after the source returned a signed finalization proof."""
    require_node_authority(request)
    _require_current_authority_node(payload.node_id)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    try:
        receipt = verify_authority_receipt(
            payload.finalization_receipt,
            _authority_secret(),
            expected_kind="source_finalized",
        )
    except AuthorityReceiptError as exc:
        raise HTTPException(409, "authority handoff receipt is invalid") from exc
    if (
        project.authority_state == "active"
        and project.authority_node_id == payload.node_id
        and project.authority_generation == payload.expected_generation + 1
        and receipt.project_id == project_id
        and receipt.target_node_id == payload.node_id
        and receipt.source_generation == payload.expected_generation
    ):
        return {**_authority_payload(project), "idempotent": True}
    if (
        project.authority_state != "transfer_pending"
        or project.authority_generation != payload.expected_generation
    ):
        raise HTTPException(409, "restored project is not awaiting this transfer generation")
    expected = _project_transfer_receipt(project, "source_finalized")
    if receipt != expected or project.authority_target_node_id != payload.node_id:
        raise HTTPException(409, "authority handoff receipt does not match this transfer")
    project.authority_node_id = payload.node_id
    project.authority_target_node_id = None
    project.authority_transfer_nonce = None
    project.authority_generation += 1
    project.authority_state = "active"
    await _recover_authority_interrupted_sleep(db, project_id)
    interrupted_backups = await _recover_authority_interrupted_backups(db, project_id)
    await mark_project_dirty(db, project_id)
    await activity(
        db,
        project_id,
        "authority.transfer_activated",
        "Restored memory became the authoritative project node",
        {
            "generation": project.authority_generation,
            "interrupted_backups": interrupted_backups,
        },
    )
    await db.commit()
    return {**_authority_payload(project), "idempotent": False}


@app.post("/projects/{project_id}/authority/recover")
async def recover_project_authority(
    project_id: str,
    payload: AuthorityRecoveryRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Take over a verified active backup after explicit loss of the old node.

    Without an external coordinator the server cannot prove that the old VPS is
    gone. Requiring the node secret plus a literal operator attestation makes the
    exceptional boundary explicit and keeps it out of normal startup.
    """
    require_node_authority(request)
    _require_current_authority_node(payload.node_id)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if (
        project.authority_state == "active"
        and project.authority_node_id == payload.node_id
        and project.authority_generation == payload.expected_generation + 1
    ):
        return {**_authority_payload(project), "idempotent": True}
    if project.authority_state != "active":
        raise HTTPException(409, "only an active pre-disaster backup can be recovered")
    if project.authority_generation != payload.expected_generation:
        raise HTTPException(409, "authority generation changed")
    if project.authority_node_id in {None, payload.node_id}:
        raise HTTPException(409, "project does not require disaster recovery")
    project.authority_node_id = payload.node_id
    project.authority_target_node_id = None
    project.authority_transfer_nonce = None
    project.authority_generation += 1
    await _recover_authority_interrupted_sleep(db, project_id)
    interrupted_backups = await _recover_authority_interrupted_backups(db, project_id)
    await mark_project_dirty(db, project_id)
    await activity(
        db,
        project_id,
        "authority.disaster_recovered",
        "Verified backup recovered after the previous node became unreachable",
        {
            "generation": project.authority_generation,
            "interrupted_backups": interrupted_backups,
        },
    )
    await db.commit()
    return {**_authority_payload(project), "idempotent": False}


@app.post("/projects/{project_id}/authority/finalize")
async def finalize_old_project_authority(
    project_id: str,
    payload: AuthorityFinalizeRequest,
    request: Request,
    expected_generation: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_session),
):
    """Permanently retire the old, already-frozen database after new-node verification."""
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    if project.authority_generation != expected_generation or project.authority_state not in {
        "transfer_pending",
        "transferred",
    }:
        raise HTTPException(409, "project transfer is not pending at this generation")
    if not project.authority_node_id:
        raise HTTPException(409, "source memory node identity is unavailable")
    _require_current_authority_node(project.authority_node_id)
    receipt = _verified_transfer_receipt(
        project,
        payload.activation_receipt,
        expected_kind="destination_ready",
    )
    finalization_receipt = issue_authority_receipt(
        _authority_secret(),
        AuthorityReceipt(
            kind="source_finalized",
            project_id=receipt.project_id,
            source_node_id=receipt.source_node_id,
            target_node_id=receipt.target_node_id,
            source_generation=receipt.source_generation,
            nonce=receipt.nonce,
        ),
    )
    if project.authority_state == "transferred":
        return {
            **_authority_payload(project),
            "finalization_receipt": finalization_receipt,
            "idempotent": True,
        }
    project.authority_state = "transferred"
    await activity(
        db,
        project_id,
        "authority.transfer_finalized",
        "Old authoritative memory node retired",
        {"generation": project.authority_generation},
    )
    await db.commit()
    return {
        **_authority_payload(project),
        "finalization_receipt": finalization_receipt,
        "idempotent": False,
    }


@app.post("/projects/{project_id}/team/bootstrap")
async def bootstrap_team(
    project_id: str,
    payload: TeamBootstrap,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Create the first manager from the trusted local control plane only."""
    principal = principal_from_request(request)
    if not principal.trusted_local:
        raise HTTPException(403, "team bootstrap requires the trusted local control plane")
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    token_hash = hash_secret(payload.device_token)
    existing_manager = await db.scalar(
        select(TeamMember).where(
            TeamMember.project_id == project_id,
            TeamMember.capability == INFRASTRUCTURE_MANAGER,
            TeamMember.status == "active",
        )
    )
    if existing_manager:
        existing_token = await db.scalar(
            select(TeamAccessToken).where(
                TeamAccessToken.project_id == project_id,
                TeamAccessToken.member_id == existing_manager.id,
                TeamAccessToken.token_hash == token_hash,
                TeamAccessToken.revoked_at.is_(None),
            )
        )
        if existing_token is None:
            raise HTTPException(409, "the project already has an infrastructure manager")
        requested_device_id = payload.device_id.strip()
        rebound = existing_token.device_id != requested_device_id
        if rebound:
            conflicting_device = await db.scalar(
                select(TeamAccessToken.id).where(
                    TeamAccessToken.member_id == existing_manager.id,
                    TeamAccessToken.device_id == requested_device_id,
                    TeamAccessToken.id != existing_token.id,
                )
            )
            if conflicting_device is not None:
                raise HTTPException(409, "infrastructure device is already registered")
            # A full-recovery restore intentionally preserves the opaque
            # infrastructure token, while the VPS node identity is newly
            # generated and deliberately absent from the archive.  Rebind only
            # that already-authenticated manager token; never create a second
            # manager or accept a different secret.
            existing_token.device_id = requested_device_id
            existing_token.device_label = payload.device_label.strip()
            await activity(
                db,
                project_id,
                "team.infrastructure_device_rebound",
                "Infrastructure control moved to the authoritative memory node",
                {"member_id": existing_manager.id},
                actor_member_id=existing_manager.id,
            )
            await db.commit()
        actual = _member_principal(existing_manager, existing_token)
        return {
            **(await _team_payload(db, project_id, actual)),
            "idempotent": not rebound,
            "device_rebound": rebound,
        }
    if await db.scalar(select(TeamAccessToken.id).where(TeamAccessToken.token_hash == token_hash)):
        raise HTTPException(409, "device token already registered")
    member = TeamMember(
        project_id=project_id,
        display_name=payload.display_name.strip(),
        capability=INFRASTRUCTURE_MANAGER,
    )
    db.add(member)
    await db.flush()
    token = TeamAccessToken(
        project_id=project_id,
        member_id=member.id,
        token_hash=token_hash,
        device_id=payload.device_id.strip(),
        device_label=payload.device_label.strip(),
    )
    db.add(token)
    await db.flush()
    actual = _member_principal(member, token)
    await activity(
        db,
        project_id,
        "team.bootstrapped",
        "Project team access enabled",
        {"member_id": member.id},
        actor_member_id=member.id,
    )
    await db.commit()
    return {
        **(await _team_payload(db, project_id, actual)),
        "idempotent": False,
        "device_rebound": False,
    }


@app.get("/projects/{project_id}/team")
async def get_team(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    return await _team_payload(db, project_id, principal_from_request(request))


@app.post("/projects/{project_id}/team/device-tokens")
async def create_team_device_token(
    project_id: str,
    payload: TeamDeviceTokenCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    if principal.anonymous or principal.trusted_local or principal.member_id is None:
        raise HTTPException(403, "an authenticated team member is required")
    member = await db.scalar(
        select(TeamMember)
        .where(
            TeamMember.id == principal.member_id,
            TeamMember.project_id == project_id,
            TeamMember.status == "active",
        )
        .with_for_update()
    )
    if member is None:
        raise HTTPException(401, "project access was revoked")
    device_id = payload.device_id.strip()
    token_hash = hash_secret(payload.device_token)
    existing = await db.scalar(
        select(TeamAccessToken).where(
            TeamAccessToken.member_id == member.id,
            TeamAccessToken.device_id == device_id,
        )
    )
    if existing is not None:
        if existing.token_hash != token_hash or existing.revoked_at is not None:
            raise HTTPException(409, "device is already registered")
        return team_envelope(
            principal,
            device={
                "id": existing.id,
                "device_id": existing.device_id,
                "device_label": existing.device_label,
                "created_at": existing.created_at,
                "revoked_at": existing.revoked_at,
            },
            idempotent=True,
        )
    if await db.scalar(select(TeamAccessToken.id).where(TeamAccessToken.token_hash == token_hash)):
        raise HTTPException(409, "device token already registered")
    access = TeamAccessToken(
        project_id=project_id,
        member_id=member.id,
        token_hash=token_hash,
        device_id=device_id,
        device_label=payload.device_label.strip(),
    )
    db.add(access)
    await activity(
        db,
        project_id,
        "team.device_added",
        "Team member device added",
        {"member_id": member.id},
    )
    await db.commit()
    await db.refresh(access)
    return team_envelope(
        principal,
        device={
            "id": access.id,
            "device_id": access.device_id,
            "device_label": access.device_label,
            "created_at": access.created_at,
            "revoked_at": access.revoked_at,
        },
        idempotent=False,
    )


@app.post("/projects/{project_id}/team/invites")
async def create_team_invite(
    project_id: str,
    payload: TeamInviteCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    if principal.trusted_local:
        raise HTTPException(
            409,
            "team invitations require a remote project memory; promote this project first",
        )
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    code = invitation_code()
    invite_payload = setup_prompt = None
    if bool(payload.api_url) != bool(payload.dashboard_url):
        raise HTTPException(422, "api_url and dashboard_url must be provided together")
    if payload.api_url and payload.dashboard_url:
        try:
            invite_payload, setup_prompt = create_invitation_bundle(
                project_id=project.id,
                name=project.name,
                api_url=payload.api_url,
                dashboard_url=payload.dashboard_url,
                invitation_code=code,
                language=payload.language,
            )
        except InvitationPayloadError as exc:
            raise HTTPException(422, str(exc)) from exc
    invitation = TeamInvitation(
        project_id=project_id,
        invited_by_member_id=principal.member_id,
        display_name=payload.display_name.strip(),
        token_hash=hash_secret(code),
        expires_at=utcnow() + timedelta(hours=payload.expires_in_hours),
    )
    db.add(invitation)
    await activity(
        db,
        project_id,
        "team.invite_created",
        "Project member invitation created",
        {"expires_in_hours": payload.expires_in_hours},
    )
    await db.commit()
    await db.refresh(invitation)
    serialized_invitation = {
        "id": invitation.id,
        "display_name": invitation.display_name,
        "invitation_code": code,
        "expires_at": invitation.expires_at,
        "consumed_at": invitation.consumed_at,
    }
    if invite_payload and setup_prompt:
        serialized_invitation.update(
            invite_payload=invite_payload,
            setup_prompt=setup_prompt,
            release_version=__version__,
        )
    return team_envelope(principal, invitation=serialized_invitation)


@app.post("/projects/{project_id}/auth/exchange")
async def exchange_team_invite(
    project_id: str,
    payload: TeamInviteExchange,
    db: AsyncSession = Depends(get_session),
):
    invitation = await db.scalar(
        select(TeamInvitation)
        .where(
            TeamInvitation.project_id == project_id,
            TeamInvitation.token_hash == hash_secret(payload.invitation_code),
        )
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(404, "invitation not found")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    supplied_hash = hash_secret(payload.device_token)
    if invitation.consumed_at is not None:
        token = await db.get(TeamAccessToken, invitation.consumed_token_id)
        member = await db.get(TeamMember, invitation.consumed_by_member_id)
        if (
            token
            and member
            and token.token_hash == supplied_hash
            and token.revoked_at is None
            and member.status == "active"
        ):
            actual = _member_principal(member, token)
            return team_envelope(actual, member=serialize_member(member), idempotent=True)
        raise HTTPException(409, "invitation already consumed")
    if expires_at <= utcnow():
        raise HTTPException(410, "invitation expired")
    if await db.scalar(
        select(TeamAccessToken.id).where(TeamAccessToken.token_hash == supplied_hash)
    ):
        raise HTTPException(409, "device token already registered")
    member = TeamMember(
        project_id=project_id,
        display_name=invitation.display_name,
        capability=PROJECT_MEMBER,
    )
    db.add(member)
    await db.flush()
    token = TeamAccessToken(
        project_id=project_id,
        member_id=member.id,
        token_hash=supplied_hash,
        device_id=payload.device_id.strip(),
        device_label=payload.device_label.strip(),
    )
    db.add(token)
    await db.flush()
    invitation.consumed_at = utcnow()
    invitation.consumed_by_member_id = member.id
    invitation.consumed_token_id = token.id
    actual = _member_principal(member, token)
    await activity(
        db,
        project_id,
        "team.invite_exchanged",
        "Project member joined",
        {"member_id": member.id},
        actor_member_id=member.id,
    )
    await db.commit()
    return team_envelope(actual, member=serialize_member(member), idempotent=False)


@app.post("/projects/{project_id}/auth/browser-ticket")
async def create_browser_ticket(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    if (
        principal.trusted_local
        or principal.anonymous
        or principal.access_token_id is None
        or principal.member_id is None
        or principal.browser_session_id is not None
    ):
        raise HTTPException(403, "a project bearer token is required")
    raw_ticket = browser_secret()
    ticket = BrowserAuthCredential(
        project_id=project_id,
        member_id=principal.member_id,
        access_token_id=principal.access_token_id,
        kind="ticket",
        secret_hash=hash_secret(raw_ticket),
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(ticket)
    await db.commit()
    return team_envelope(
        principal,
        ticket=raw_ticket,
        expires_at=ticket.expires_at,
        one_time=True,
    )


@app.post("/projects/{project_id}/auth/browser-session")
async def exchange_browser_ticket(
    project_id: str,
    payload: BrowserSessionExchange,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    ticket = await db.scalar(
        select(BrowserAuthCredential)
        .where(
            BrowserAuthCredential.project_id == project_id,
            BrowserAuthCredential.kind == "ticket",
            BrowserAuthCredential.secret_hash == hash_secret(payload.ticket),
        )
        .with_for_update()
    )
    if ticket is None:
        raise HTTPException(404, "browser ticket not found")
    expires_at = ticket.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if ticket.consumed_at is not None:
        raise HTTPException(409, "browser ticket already consumed")
    if ticket.revoked_at is not None or expires_at <= utcnow():
        raise HTTPException(410, "browser ticket expired")
    token = await db.get(TeamAccessToken, ticket.access_token_id)
    member = await db.get(TeamMember, ticket.member_id)
    if (
        token is None
        or member is None
        or token.revoked_at is not None
        or member.status != "active"
        or token.project_id != project_id
        or member.project_id != project_id
    ):
        raise HTTPException(401, "project access was revoked")
    raw_session = browser_secret()
    raw_csrf = browser_csrf_secret()
    browser_session = BrowserAuthCredential(
        project_id=project_id,
        member_id=member.id,
        access_token_id=token.id,
        kind="session",
        secret_hash=hash_secret(raw_session),
        csrf_secret_hash=hash_secret(raw_csrf),
        expires_at=utcnow() + timedelta(days=7),
    )
    db.add(browser_session)
    ticket.consumed_at = utcnow()
    await db.commit()
    response.set_cookie(
        key=browser_cookie_name(project_id),
        value=raw_session,
        max_age=7 * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    actual = TeamPrincipal(
        project_id=project_id,
        member_id=member.id,
        access_token_id=token.id,
        browser_session_id=browser_session.id,
        display_name=member.display_name,
        capability=member.capability,
    )
    return team_envelope(actual, authenticated=True, csrf_token=raw_csrf)


@app.post("/projects/{project_id}/auth/logout")
async def logout_browser_session(
    project_id: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    if principal.browser_session_id:
        browser_session = await db.get(BrowserAuthCredential, principal.browser_session_id)
        if browser_session and browser_session.project_id == project_id:
            browser_session.revoked_at = utcnow()
            await db.commit()
    response.delete_cookie(
        browser_cookie_name(project_id),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return team_envelope(principal, logged_out=True)


@app.delete("/projects/{project_id}/team/members/{member_id}")
async def revoke_team_member(
    project_id: str,
    member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    if principal.member_id == member_id:
        raise HTTPException(409, "an infrastructure manager cannot revoke its own membership")
    member = await db.scalar(
        select(TeamMember)
        .where(TeamMember.id == member_id, TeamMember.project_id == project_id)
        .with_for_update()
    )
    if member is None:
        raise HTTPException(404, "team member not found")
    if member.status != "revoked":
        now = utcnow()
        member.status = "revoked"
        member.updated_at = now
        await db.execute(
            update(TeamAccessToken)
            .where(
                TeamAccessToken.project_id == project_id,
                TeamAccessToken.member_id == member.id,
                TeamAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.execute(
            update(BrowserAuthCredential)
            .where(
                BrowserAuthCredential.project_id == project_id,
                BrowserAuthCredential.member_id == member.id,
                BrowserAuthCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await activity(
            db,
            project_id,
            "team.member_revoked",
            "Project member access revoked",
            {"member_id": member.id},
        )
        await db.commit()
    return await _team_payload(db, project_id, principal)


@app.get("/projects/{project_id}/team/manual")
async def get_operational_manual(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    manual = await db.get(OperationalManual, project_id)
    return team_envelope(principal_from_request(request), manual=serialize_manual(manual))


@app.patch("/projects/{project_id}/team/manual")
async def patch_operational_manual(
    project_id: str,
    payload: OperationalManualUpdate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise HTTPException(404, "project not found")
    content = payload.content.strip()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    prior = await db.scalar(
        select(OperationalManualRevision).where(
            OperationalManualRevision.project_id == project_id,
            OperationalManualRevision.idempotency_key == payload.idempotency_key,
        )
    )
    manual = await db.get(OperationalManual, project_id)
    if prior is not None:
        if prior.content_sha256 != content_hash:
            raise HTTPException(409, "idempotency key reused with different manual content")
        return team_envelope(principal, manual=serialize_manual(manual), idempotent=True)
    current_version = manual.version if manual else 0
    if payload.expected_version != current_version:
        raise HTTPException(409, "operational manual version conflict")
    if manual is not None and manual.content == content:
        return team_envelope(principal, manual=serialize_manual(manual), idempotent=True)
    next_version = current_version + 1
    if manual is None:
        manual = OperationalManual(
            project_id=project_id,
            content=content,
            version=next_version,
            updated_by_member_id=principal.member_id,
            updated_at=utcnow(),
        )
        db.add(manual)
    else:
        manual.content = content
        manual.version = next_version
        manual.updated_by_member_id = principal.member_id
        manual.updated_at = utcnow()
    db.add(
        OperationalManualRevision(
            project_id=project_id,
            version=next_version,
            content=content,
            content_sha256=content_hash,
            idempotency_key=payload.idempotency_key,
            actor_member_id=principal.member_id,
        )
    )
    await activity(
        db,
        project_id,
        "team.manual_updated",
        "Project operating manual updated",
        {"version": next_version, "characters": len(content)},
    )
    await db.commit()
    return team_envelope(principal, manual=serialize_manual(manual), idempotent=False)


async def _record_manual_compaction_usage(
    db: AsyncSession,
    project_id: str,
    telemetry: dict,
) -> None:
    try:
        client_cost = (
            telemetry.get("client_cost_usd")
            if telemetry.get("provider") == "claude"
            and telemetry.get("cost_source") == "claude_code_client_estimate"
            and isinstance(telemetry.get("client_cost_usd"), Decimal)
            else None
        )
        equivalent = (
            api_equivalent_cost(
                provider=telemetry.get("provider"),
                model=telemetry.get("model"),
                input_tokens=telemetry.get("input_tokens"),
                cached_input_tokens=telemetry.get("cached_input_tokens"),
                cache_write_input_tokens=telemetry.get("cache_write_input_tokens"),
                output_tokens=telemetry.get("output_tokens"),
                reasoning_tokens=telemetry.get("reasoning_tokens"),
            )
            if client_cost is None
            else None
        )
        await record_observation(
            db,
            Observation(
                project_id=project_id,
                idempotency_key=f"manual-compaction:{uuid4()}",
                category="sleep_model",
                operation="sleep.operational_manual_compaction",
                scope="manual",
                status=str(telemetry.get("status") or "unavailable"),
                provider=telemetry.get("provider"),
                model=telemetry.get("model"),
                measurement_source=str(telemetry.get("measurement_source") or "unavailable"),
                input_tokens=telemetry.get("input_tokens"),
                cached_input_tokens=telemetry.get("cached_input_tokens"),
                cache_write_input_tokens=telemetry.get("cache_write_input_tokens"),
                output_tokens=telemetry.get("output_tokens"),
                reasoning_tokens=telemetry.get("reasoning_tokens"),
                reported_total_tokens=telemetry.get("reported_total_tokens"),
                duration_ms=telemetry.get("duration_ms"),
                provider_duration_ms=telemetry.get("duration_ms"),
                request_count=1,
                cost_usd=(
                    client_cost
                    if client_cost is not None
                    else equivalent.cost_usd
                    if equivalent is not None
                    else None
                ),
                unit_price_usd_per_million=(
                    equivalent.unit_price_usd_per_million if equivalent is not None else None
                ),
                pricing_version=(
                    "claude-code-client-estimate-v1"
                    if client_cost is not None
                    else equivalent.pricing_version
                    if equivalent is not None
                    else None
                ),
                details={
                    "cost_kind": "api_equivalent",
                    **(equivalent.numeric_details() if equivalent is not None else {}),
                },
            ),
        )
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()


@app.post("/projects/{project_id}/team/manual/compact-draft")
async def create_operational_manual_compact_draft(
    project_id: str,
    payload: OperationalManualCompactDraft,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    manual = await db.get(OperationalManual, project_id)
    if payload.expected_version != (manual.version if manual else 0):
        raise HTTPException(409, "operational manual version conflict")
    try:
        draft = await compact_manual_draft(project, manual)
    except SleepProviderError as exc:
        if isinstance(exc, SleepGenerationError):
            generation = exc.generation
            await _record_manual_compaction_usage(
                db,
                project_id,
                {
                    "status": "error",
                    "provider": generation.provider,
                    "model": generation.model,
                    "duration_ms": generation.duration_ms,
                    "measurement_source": generation.measurement_source,
                    "input_tokens": generation.input_tokens,
                    "cached_input_tokens": generation.cached_input_tokens,
                    "cache_write_input_tokens": generation.cache_write_input_tokens,
                    "output_tokens": generation.output_tokens,
                    "reasoning_tokens": generation.reasoning_tokens,
                    "reported_total_tokens": generation.reported_total_tokens,
                    "client_cost_usd": generation.client_cost_usd,
                    "cost_source": generation.cost_source,
                },
            )
        status_code = 503 if exc.kind in {"auth_required", "rate_limited"} else 502
        raise HTTPException(status_code, str(exc)) from exc
    telemetry = draft.pop("_telemetry", None)
    if isinstance(telemetry, dict):
        await _record_manual_compaction_usage(db, project_id, telemetry)
    return team_envelope(principal, draft=draft)


@app.get("/projects/{project_id}/briefing")
async def get_briefing(
    project_id: str,
    client: Literal["codex", "claude"] | None = Query(None),
    db: AsyncSession = Depends(get_session),
):
    try:
        return await briefing(db, project_id, client=client)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/projects/{project_id}/setup", status_code=202)
async def open_project_setup(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Ask the authenticated host agent to open Setup without leaking its token."""
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    if not principal.trusted_local:
        raise HTTPException(
            409,
            "Setup is available only from the project's local infrastructure host.",
        )
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    settings = get_settings()
    if not settings.cli_bridge_token or settings.cli_bridge_url.endswith(":0"):
        raise HTTPException(503, "Local Setup is unavailable. Start dDuo from a new project chat.")
    try:
        response = await asyncio.to_thread(
            httpx.post,
            f"{settings.cli_bridge_url.rstrip('/')}/v1/setup/open",
            headers={"Authorization": f"Bearer {settings.cli_bridge_token}"},
            json={"project_root": project.root_path},
            timeout=5,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Local Setup is temporarily unavailable.") from exc
    if response.status_code != 202:
        raise HTTPException(503, "Local Setup is temporarily unavailable.")
    return {"opened": True}


@app.patch("/projects/{project_id}")
async def patch_project(
    project_id: str,
    payload: ProjectUpdate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    require_remote_expected_version(principal_from_request(request), payload.expected_version)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    try:
        await update_profile(db, project, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return serialize(project)


@app.get("/projects/{project_id}/identity")
async def project_identity(project_id: str, db: AsyncSession = Depends(get_session)):
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return {"id": project.id, "name": project.name, "profile_version": project.profile_version}


@app.post("/projects/{project_id}/rename")
async def rename_project(
    project_id: str,
    payload: ProjectRename,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_infrastructure_manager(principal)
    project = await lock_writable_project(db, project_id)
    if project.name == payload.name:
        return serialize(project)
    if project.profile_version != payload.expected_version:
        raise HTTPException(409, "project profile version conflict")
    before = project.name
    project.name = payload.name
    project.profile_version += 1
    project.updated_at = utcnow()
    db.add(
        ProfileRevision(
            project_id=project.id,
            version=project.profile_version,
            snapshot={
                key: getattr(project, key)
                for key in ("name", "cause", "principles", "objectives", "context")
            },
            rationale="Explicit project rename",
            actor_member_id=principal.member_id,
        )
    )
    await activity(
        db, project.id, "project.renamed", "Project renamed",
        {"before": before, "after": project.name},
    )
    await db.commit()
    return serialize(project)


@app.post("/projects/{project_id}/sessions")
async def start_session(
    project_id: str,
    payload: SessionStart,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    existing = await db.scalar(
        select(Session).where(
            Session.project_id == project_id,
            Session.client == payload.client,
            Session.external_id == payload.external_id,
        )
    )
    if existing:
        require_member_session(principal, existing.member_id)
        return serialize(existing)
    agent_session = Session(
        project_id=project_id,
        member_id=principal.member_id,
        access_token_id=principal.access_token_id,
        **payload.model_dump(),
    )
    try:
        async with db.begin_nested():
            db.add(agent_session)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(Session).where(
                Session.project_id == project_id,
                Session.client == payload.client,
                Session.external_id == payload.external_id,
            )
        )
        if existing is None:
            raise HTTPException(409, "session could not be created")
        require_member_session(principal, existing.member_id)
        return serialize(existing)
    if project.sleep_executor_preference is None and payload.client in {"codex", "claude"}:
        # The initial supported chat makes a useful default without asking the
        # user to understand provider terminology.  The host bridge still
        # verifies availability and can use the other subscription later.
        project.sleep_executor_preference = payload.client
    recovered = await resume_project_sleep(db, project_id)
    await mark_project_dirty(db, project_id)
    await db.commit()
    return {**serialize(agent_session), "sleep_recovery_scheduled": len(recovered)}


@app.patch("/projects/{project_id}/sessions/{session_id}/privacy")
async def set_session_privacy(
    project_id: str,
    session_id: str,
    payload: SessionPrivacyUpdate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    session = await db.get(Session, session_id)
    if not session or session.project_id != project_id:
        raise HTTPException(404, "session not found")
    require_member_session(principal_from_request(request), session.member_id)
    session.off_record = payload.off_record
    session.last_activity_at = utcnow()
    excluded_turns = 0
    cancelled_jobs = 0
    if payload.off_record:
        turns = list(
            (
                await db.scalars(
                    select(Turn).where(
                        Turn.session_id == session_id,
                        Turn.sleep_status.in_(["open", "pending"]),
                    )
                )
            ).all()
        )
        for turn in turns:
            turn.off_record = True
            turn.sleep_status = "skipped_off_record"
        excluded_turns = len(turns)
        jobs = list(
            (
                await db.scalars(
                    select(SleepJob).where(
                        SleepJob.session_id == session_id,
                        SleepJob.status.in_(["pending", "waiting", "running"]),
                    )
                )
            ).all()
        )
        for job in jobs:
            job.status = "cancelled"
            job.completed_at = utcnow()
            job.result = {"cancelled_reason": "session_off_record"}
        cancelled_jobs = len(jobs)
    await activity(
        db,
        project_id,
        "session.privacy",
        "Off-the-record enabled" if payload.off_record else "Off-the-record disabled",
        {
            "session_id": session_id,
            "off_record": payload.off_record,
            "excluded_turns": excluded_turns,
            "cancelled_jobs": cancelled_jobs,
        },
    )
    await db.commit()
    return {
        **serialize(session),
        "excluded_turns": excluded_turns,
        "cancelled_jobs": cancelled_jobs,
    }


@app.post("/projects/{project_id}/turns/begin")
async def begin_turn(
    project_id: str,
    payload: TurnBegin,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    agent_session = await db.scalar(
        select(Session).where(Session.id == payload.session_id).with_for_update()
    )
    if not agent_session or agent_session.project_id != project_id:
        raise HTTPException(404, "session not found")
    require_member_session(principal_from_request(request), agent_session.member_id)
    turn = await db.scalar(
        select(Turn).where(
            Turn.session_id == payload.session_id, Turn.external_id == payload.external_id
        )
    )
    prompt_event_id = payload.prompt_event_id or payload.external_id
    prompt_events: list[RawEvent] = []
    prompt_event: RawEvent | None = None
    if not turn:
        segment = await ensure_open_segment(db, agent_session)
        segment.sleep_status = "pending"
        turn = Turn(
            project_id=project_id,
            session_id=payload.session_id,
            external_id=payload.external_id,
            user_prompt=payload.user_prompt,
            segment_id=segment.id,
            off_record=(
                payload.off_record if payload.off_record is not None else agent_session.off_record
            ),
        )
        db.add(turn)
        await db.flush()
    else:
        effective_off_record = (
            payload.off_record if payload.off_record is not None else agent_session.off_record
        )
        # Privacy is monotonic for one interaction: any off-record fragment
        # makes the whole aggregate turn ineligible for sleep and retrieval
        # history, even if the session is switched back before Stop.
        turn.off_record = turn.off_record or effective_off_record
        prompt_events = list(
            (
                await db.scalars(
                    select(RawEvent)
                    .where(
                        RawEvent.turn_id == turn.id,
                        RawEvent.event_type == "user_prompt",
                    )
                    .order_by(RawEvent.created_at, RawEvent.id)
                )
            ).all()
        )

    for index, item in enumerate(prompt_events):
        item_id = item.payload.get("prompt_event_id") if isinstance(item.payload, dict) else None
        # Turns captured before prompt fragments existed have one user_prompt
        # event without an explicit id.  Treat it as the original turn event so
        # old clients retain their idempotent replay behavior.
        if item_id is None and index == 0:
            item_id = turn.external_id
        if item_id == prompt_event_id:
            prompt_event = item
            break

    if prompt_event is not None:
        stored_prompt = str(prompt_event.payload.get("text") or "")
        if stored_prompt != payload.user_prompt:
            raise HTTPException(409, "prompt event conflicts with the existing turn fragment")
    elif turn.committed:
        raise HTTPException(409, "cannot append a prompt to a committed turn")
    else:
        prompt_event = RawEvent(
            project_id=project_id,
            session_id=agent_session.id,
            turn_id=turn.id,
            event_type="user_prompt",
            payload={"text": payload.user_prompt, "prompt_event_id": prompt_event_id},
            actor="user",
            actor_member_id=agent_session.member_id,
            created_at=utcnow(),
        )
        db.add(prompt_event)
        prompt_events.append(prompt_event)
        # One Codex/Claude interaction remains one Turn.  Steering prompts are
        # ordered fragments of that Turn and become one neutral retrieval/sleep
        # input rather than orphaning the original question.
        turn.user_prompt = "\n\n".join(
            str(item.payload.get("text") or "")
            for item in prompt_events
            if isinstance(item.payload, dict)
        )
        await mark_project_dirty(db, project_id)
    # Publish the fragment before provider latency, then serialize only callers
    # for this exact prompt event. PostgreSQL advisory locks avoid duplicate
    # paid retrieval during concurrent retries without blocking other prompts,
    # sessions, or projects.
    await db.flush()
    turn_id = turn.id
    prompt_event_db_id = prompt_event.id
    session_id = agent_session.id
    await db.commit()
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext(:project_id), hashtext(:prompt_event_id))"
            ),
            {"project_id": project_id, "prompt_event_id": prompt_event_id},
        )
    turn = await db.scalar(
        select(Turn)
        .where(Turn.id == turn_id)
        .execution_options(populate_existing=True)
    )
    prompt_event = await db.scalar(
        select(RawEvent)
        .where(RawEvent.id == prompt_event_db_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if turn is None or prompt_event is None:
        raise HTTPException(404, "turn prompt not found")

    event_retrieval_run_id = (
        str(prompt_event.payload.get("retrieval_run_id") or "") if prompt_event is not None else ""
    )
    retrieval = (
        await replay_retrieval(db, event_retrieval_run_id) if event_retrieval_run_id else None
    )
    if retrieval is None:
        retrieval = await retrieve_context(
            db,
            embedding_service(),
            project_id=project_id,
            session_id=session_id,
            turn_id=turn.id,
            current_prompt=bounded_semantic_query(turn.user_prompt),
            requested_limit=payload.limit,
            release_before_provider=False,
        )
        await lock_writable_project(db, project_id)
        turn = await db.scalar(
            select(Turn)
            .where(Turn.id == turn.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if turn is None:
            raise HTTPException(404, "turn not found")
        turn.retrieved_memory_ids = list(
            dict.fromkeys(
                [
                    *(turn.retrieved_memory_ids or []),
                    *retrieval["metadata"]["selected_memory_ids"],
                ]
            )
        )
        if prompt_event is not None:
            latest_prompt_event_id = await db.scalar(
                select(RawEvent.id)
                .where(
                    RawEvent.turn_id == turn.id,
                    RawEvent.event_type == "user_prompt",
                )
                .order_by(desc(RawEvent.created_at), desc(RawEvent.id))
                .limit(1)
            )
            if latest_prompt_event_id == prompt_event.id:
                turn.retrieval_run_id = retrieval["retrieval_run_id"]
            prompt_event.payload = {
                **prompt_event.payload,
                "retrieval_run_id": retrieval["retrieval_run_id"],
            }
        await db.commit()
    result = await briefing(db, project_id, client=agent_session.client)
    result.update(
        {
            "turn": serialize(turn),
            "memories": retrieval["items"],
            "retrieval": retrieval,
            "retrieval_available": retrieval["status"] != "degraded",
            "retrieval_empty": not retrieval["items"],
            "indexing_pending": bool(
                await db.scalar(
                    select(OutboxEvent.id)
                    .where(
                        OutboxEvent.project_id == project_id,
                        OutboxEvent.event_type == "memory.upsert",
                        OutboxEvent.status.in_(["pending", "failed"]),
                    )
                    .limit(1)
                )
            ),
            "task_indexing_pending": bool(
                await db.scalar(pending_task_projection_ids(project_id).limit(1))
            ),
        }
    )
    return result


@app.get("/projects/{project_id}/sessions/{session_id}/capture-status")
async def capture_status(
    project_id: str,
    session_id: UUID,
    request: Request,
    client: Literal["codex", "claude"] = Query(...),
    turn_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
):
    """Return content-free lifecycle evidence for one exact known session."""
    agent_session = await db.get(Session, str(session_id))
    if (
        agent_session is None
        or agent_session.project_id != project_id
        or agent_session.client != client
    ):
        raise HTTPException(404, "session not found")
    require_member_session(principal_from_request(request), agent_session.member_id)

    turn: Turn | None = None
    if turn_id is not None:
        turn = await db.get(Turn, turn_id)
        if (
            turn is None
            or turn.project_id != project_id
            or turn.session_id != agent_session.id
        ):
            raise HTTPException(404, "turn not found")

    def timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "session": {
            "id": agent_session.id,
            "external_id": agent_session.external_id,
            "client": agent_session.client,
            "off_record": agent_session.off_record,
            "started_at": timestamp(agent_session.started_at),
        },
        "turn": None
        if turn is None
        else {
            "id": turn.id,
            "external_id": turn.external_id,
            "status": turn.status,
            "committed": turn.committed,
            "off_record": turn.off_record,
            "committed_at": timestamp(turn.committed_at),
        },
    }


@app.post("/turns/{turn_id}/commit")
async def commit(
    turn_id: str,
    payload: TurnCommit,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    turn = await lock_writable_turn(db, turn_id, principal)
    session = await db.get(Session, turn.session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    require_member_session(principal, session.member_id)
    try:
        return await commit_turn(db, turn, payload)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/projects/{project_id}/memories/search")
async def search_memory(
    project_id: str,
    q: str,
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    started = datetime.now(timezone.utc)
    service = embedding_service()
    observed_result = None
    search_error: Exception | None = None
    hits: list[dict] = []
    try:
        observed = getattr(service, "search_observed", None)
        if callable(observed):
            observed_result = await asyncio.to_thread(observed, project_id, q, limit)
            hits = observed_result.items
        else:
            hits = await asyncio.to_thread(service.search, project_id, q, limit)
    except Exception as exc:
        search_error = exc
    embedding = (
        observed_result.embedding
        if observed_result is not None
        else search_error.embedding
        if isinstance(search_error, VectorOperationError)
        else None
    )
    usage = embedding.usage if embedding else None
    cost, unit_price, pricing_version = embedding_price(
        usage.model if usage else None, usage.input_tokens if usage else None
    )
    await record_observation(
        db,
        Observation(
            project_id=project_id,
            idempotency_key=f"manual-search:{uuid4()}",
            category="embedding",
            operation="embedding.manual_search",
            status=(
                "success"
                if search_error is None
                else "partial_failure"
                if embedding is not None
                else "failed"
            ),
            provider=usage.provider if usage else getattr(search_error, "provider", None),
            model=usage.model if usage else getattr(search_error, "model", None),
            measurement_source=usage.measurement_source if usage else "unavailable",
            input_tokens=usage.input_tokens if usage else None,
            reported_total_tokens=usage.reported_total_tokens if usage else None,
            duration_ms=round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            provider_duration_ms=(
                embedding.provider_duration_ms
                if embedding
                else getattr(search_error, "provider_duration_ms", None)
            ),
            vector_store_duration_ms=(
                observed_result.vector_store_duration_ms
                if observed_result is not None
                else search_error.vector_store_duration_ms
                if isinstance(search_error, VectorOperationError)
                else None
            ),
            request_count=provider_request_count(embedding, search_error),
            item_count=len(hits),
            candidate_count=len(hits),
            cost_usd=cost,
            unit_price_usd_per_million=unit_price,
            pricing_version=pricing_version,
            details={
                "limit": limit,
                "error_code": "vector_operation_failed" if search_error else "",
            },
        ),
    )
    await db.commit()
    if search_error:
        raise search_error
    ids = [str(item["id"]) for item in hits]
    rows = (
        list(
            (
                await db.scalars(
                    select(Memory).where(
                        Memory.id.in_(ids),
                        Memory.project_id == project_id,
                        Memory.status == "active",
                    )
                )
            ).all()
        )
        if ids
        else []
    )
    by_id = {item.id: item for item in rows}
    return {
        "items": [
            {**serialize_memory(by_id[str(hit["id"])]), "score": hit.get("score")}
            for hit in hits
            if str(hit["id"]) in by_id
        ]
    }


@app.get("/projects/{project_id}/memories")
async def list_memories(
    project_id: str,
    status: str | None = Query("active"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    statement = select(Memory).where(Memory.project_id == project_id)
    if status:
        statement = statement.where(Memory.status == status)
    rows = list((await db.scalars(statement.order_by(desc(Memory.updated_at)).limit(limit))).all())
    return {"items": [serialize_memory(item) for item in rows]}


@app.get("/projects/{project_id}/memory-status")
async def get_memory_status(
    project_id: str,
    client: Literal["codex", "claude"] | None = Query(None),
    db: AsyncSession = Depends(get_session),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    memory_counts = dict(
        (
            await db.execute(
                select(Memory.status, func.count(Memory.id))
                .where(Memory.project_id == project_id)
                .group_by(Memory.status)
            )
        ).all()
    )
    # `client` remains accepted for older clients, but execution belongs to the
    # server and is therefore intentionally not scoped to the interactive app.
    latest_statement = select(SleepJob).where(SleepJob.project_id == project_id)
    latest = await db.scalar(
        latest_statement.where(SleepJob.status == "waiting")
        .order_by(*memory_health_job_order())
        .limit(1)
    ) or await db.scalar(
        latest_statement.order_by(desc(SleepJob.created_at), desc(SleepJob.id)).limit(1)
    )
    status = await project_memory_health(
        db,
        project_id,
        provider=None,
        include_providers=True,
    )
    return {
        **status,
        "executor_provider": sleep_executor_provider(project),
        "executor_mode": "automatic",
        "memories": memory_counts,
        "latest_job": serialize_sleep_job(latest) if latest else None,
    }


@app.post("/projects/{project_id}/sleep", status_code=202)
async def request_sleep(
    project_id: str,
    payload: SleepRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    if payload.session_id:
        target_session = await db.get(Session, payload.session_id)
        if not target_session or target_session.project_id != project_id:
            raise HTTPException(404, "session not found")
        require_member_session(principal, target_session.member_id)
    elif not principal.trusted_local and not principal.can_manage_infrastructure:
        raise HTTPException(422, "session_id is required for a project member")
    try:
        jobs = (
            await resume_project_sleep(
                db,
                project_id,
                session_id=payload.session_id,
                provider=payload.provider,
                resume_auth=payload.resume_auth,
            )
            if payload.trigger == "session_start"
            else await schedule_project_sleep(
                db, project_id, trigger=payload.trigger, session_id=payload.session_id
            )
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    await mark_project_dirty(db, project_id)
    await db.commit()
    return {"scheduled": len(jobs), "items": [serialize_sleep_job(item) for item in jobs]}


@app.get("/projects/{project_id}/sleep-jobs")
async def list_sleep_jobs(
    project_id: str, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_session)
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    rows = list(
        (
            await db.scalars(
                select(SleepJob)
                .where(SleepJob.project_id == project_id)
                .order_by(desc(SleepJob.created_at))
                .limit(limit)
            )
        ).all()
    )
    return {"items": [serialize_sleep_job(item) for item in rows]}


@app.post("/projects/{project_id}/sleep-jobs/{job_id}/retry", status_code=202)
async def retry_sleep_job(
    project_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    job = await db.get(SleepJob, job_id)
    if not job or job.project_id != project_id:
        raise HTTPException(404, "sleep job not found")
    principal = principal_from_request(request)
    if not principal.trusted_local and not principal.can_manage_infrastructure:
        require_member_session(principal, job.actor_member_id)
    if job.status in {"completed", "cancelled"}:
        raise HTTPException(409, f"{job.status} sleep jobs cannot be retried")
    if job.status == "running":
        raise HTTPException(409, "running sleep jobs cannot be retried")
    job.status = "pending"
    job.not_before = utcnow()
    job.last_error = None
    job.error_kind = None
    job.retry_at = None
    await db.commit()
    return serialize_sleep_job(job)


@app.get("/projects/{project_id}/memories/{memory_id}/provenance")
async def memory_provenance(
    project_id: str, memory_id: str, db: AsyncSession = Depends(get_session)
):
    try:
        return await explain_memory(db, project_id, memory_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/projects/{project_id}/memories/{memory_id}/revisions")
async def memory_revisions(
    project_id: str, memory_id: str, db: AsyncSession = Depends(get_session)
):
    try:
        provenance = await explain_memory(db, project_id, memory_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"items": provenance["revisions"]}


@app.post("/projects/{project_id}/memories/forget")
async def forget_project_memory(
    project_id: str, payload: MemoryForget, db: AsyncSession = Depends(get_session)
):
    try:
        forgotten = await forget_memory(db, project_id, payload.memory_id, payload.rationale)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    await activity(
        db,
        project_id,
        "memory.forgotten",
        "Memory group removed from active recall",
        {
            "memory_id": payload.memory_id,
            "revisions": len(forgotten),
            "rationale": payload.rationale,
        },
    )
    await db.commit()
    return {"forgotten": len(forgotten)}


@app.post("/projects/{project_id}/memories/reindex")
async def reindex_memories(project_id: str, db: AsyncSession = Depends(get_session)):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    rows = (
        await db.scalars(
            select(Memory).where(Memory.project_id == project_id, Memory.status == "active")
        )
    ).all()
    await db.commit()
    await asyncio.to_thread(embedding_service().reset_collection, project_id)
    await lock_writable_project(db, project_id)
    for memory in rows:
        enqueue_memory_index(db, memory)
    await activity(
        db,
        project_id,
        "memory.reindexed",
        f"Reindexed {len(rows)} memories",
        {"queued": len(rows)},
    )
    await db.commit()
    return {"queued": len(rows), "collection": embedding_service().collection(project_id)}


@app.post("/projects/{project_id}/memories/reconcile")
async def reconcile_memories(project_id: str, db: AsyncSession = Depends(get_session)):
    """Reconcile an accelerated Qdrant restore against authoritative PostgreSQL rows."""
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    rows = (await db.scalars(select(Memory).where(Memory.project_id == project_id))).all()
    await db.commit()
    indexed = await asyncio.to_thread(embedding_service().inventory, project_id)
    by_id = {memory.id: memory for memory in rows}
    extra_ids = sorted(set(indexed) - set(by_id))
    await asyncio.to_thread(embedding_service().delete_points, project_id, extra_ids)
    await lock_writable_project(db, project_id)
    queued = 0
    for memory in rows:
        current = indexed.get(memory.id)
        expected = {
            "node_type": memory.node_type,
            "node_key": memory.node_key,
            "status": memory.status,
            "revision": memory.revision,
            "memory_group_id": memory.memory_group_id,
        }
        if current is None and memory.status != "active":
            continue
        if current is None or any(current.get(key) != value for key, value in expected.items()):
            enqueue_memory_index(db, memory)
            queued += 1
    await activity(
        db,
        project_id,
        "memory.reconciled",
        "Reconciled restored vector index against PostgreSQL",
        {"queued": queued, "deleted": len(extra_ids)},
    )
    await db.commit()
    return {
        "queued": queued,
        "deleted": len(extra_ids),
        "collection": embedding_service().collection(project_id),
    }


@app.post("/projects/{project_id}/events")
async def record_event(
    project_id: str,
    payload: RawEventCreate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    session = None
    if payload.session_id:
        session = await db.get(Session, payload.session_id)
        if not session or session.project_id != project_id:
            raise HTTPException(404, "session not found")
        require_member_session(principal, session.member_id)
    if payload.turn_id:
        turn = await db.get(Turn, payload.turn_id)
        if not turn or turn.project_id != project_id:
            raise HTTPException(404, "turn not found")
        if session is not None and turn.session_id != session.id:
            raise HTTPException(404, "turn does not belong to session")
        if session is None:
            session = await db.get(Session, turn.session_id)
            if session is None or session.project_id != project_id:
                raise HTTPException(404, "session not found")
            require_member_session(principal, session.member_id)
    event = RawEvent(
        project_id=project_id,
        actor_member_id=principal.member_id,
        **payload.model_dump(),
    )
    db.add(event)
    await mark_project_dirty(db, project_id)
    await db.commit()
    return serialize(event)


@app.post("/projects/{project_id}/observability/context-events/batch", status_code=202)
async def record_context_events(
    project_id: str,
    payload: ContextObservationBatch,
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    try:
        accepted = await persist_context_observations(db, project_id, payload.items)
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ContextSnapshotConflictError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return {"accepted": accepted}


@app.post("/projects/{project_id}/observability/client-events/batch", status_code=202)
async def record_client_observability_events(
    project_id: str,
    payload: ClientTelemetryBatch,
    db: AsyncSession = Depends(get_session),
):
    """Persist content-free interactive-agent usage from one client.

    Delivery remains asynchronous so telemetry can never delay project work.
    """
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")

    usage_items = payload.items
    # ClientTelemetryBatch validates but removes retired usage-guard records so
    # older FIFO outboxes can drain without restoring that policy to the domain.
    if not usage_items:
        return {"accepted": 0}
    session_ids = {item.session_id for item in usage_items if item.session_id}
    turn_ids = {item.turn_id for item in usage_items if item.turn_id}
    sessions_by_id = {
        item.id: item
        for item in (
            await db.scalars(select(Session).where(Session.id.in_(session_ids)))
        ).all()
    } if session_ids else {}
    turns_by_id = {
        item.id: item
        for item in (await db.scalars(select(Turn).where(Turn.id.in_(turn_ids)))).all()
    } if turn_ids else {}
    usage_links: dict[str, tuple[str | None, str | None]] = {}
    for item in usage_items:
        linked_session = sessions_by_id.get(item.session_id) if item.session_id else None
        linked_turn = turns_by_id.get(item.turn_id) if item.turn_id else None
        # A local telemetry outbox can legitimately outlive the database rows
        # it once referenced after restore or authority transfer. Missing links
        # therefore degrade to an unlinked project event instead of poisoning
        # the whole FIFO batch forever. Existing foreign links remain a hard
        # project-isolation failure.
        if linked_session is not None and linked_session.project_id != project_id:
            raise HTTPException(404, "telemetry session not found")
        if linked_turn is not None and linked_turn.project_id != project_id:
            raise HTTPException(404, "telemetry turn not found")
        if linked_session is not None and linked_turn is not None:
            if linked_turn.session_id != linked_session.id:
                raise HTTPException(422, "telemetry turn does not belong to session")
        usage_links[item.event_id] = (
            linked_session.id if linked_session is not None else None,
            linked_turn.id if linked_turn is not None else None,
        )

    observations: list[Observation] = []
    for item in payload.items:
        linked_session_id, linked_turn_id = usage_links[item.event_id]
        priced = None
        if item.client_cost_usd is None:
            priced = api_equivalent_cost(
                provider=item.provider,
                model=item.model,
                input_tokens=item.input_tokens,
                cached_input_tokens=item.cached_input_tokens,
                cache_write_input_tokens=item.cache_write_input_tokens,
                output_tokens=item.output_tokens,
                reasoning_tokens=item.reasoning_tokens,
                occurred_at=item.occurred_at,
            )
        observations.append(
            Observation(
                project_id=project_id,
                idempotency_key=f"client:{item.event_id}",
                category="agent_usage",
                operation="agent.interactive",
                scope="interactive",
                status="success",
                provider=item.provider,
                model=item.model,
                measurement_source=item.measurement_source,
                session_id=linked_session_id,
                turn_id=linked_turn_id,
                input_tokens=item.input_tokens,
                cached_input_tokens=item.cached_input_tokens,
                cache_write_input_tokens=item.cache_write_input_tokens,
                output_tokens=item.output_tokens,
                reasoning_tokens=item.reasoning_tokens,
                reported_total_tokens=item.reported_total_tokens,
                request_count=1,
                cost_usd=(
                    item.client_cost_usd
                    if item.client_cost_usd is not None
                    else priced.cost_usd
                    if priced is not None
                    else None
                ),
                unit_price_usd_per_million=(
                    priced.unit_price_usd_per_million if priced is not None else None
                ),
                pricing_version=(
                    "claude-code-client-estimate-v1"
                    if item.client_cost_usd is not None
                    else priced.pricing_version
                    if priced is not None
                    else None
                ),
                details={
                    "cost_kind": "api_equivalent",
                    **(priced.numeric_details() if priced is not None else {}),
                },
                occurred_at=item.occurred_at,
            )
        )

    idempotency_keys = [item.idempotency_key for item in observations]
    existing_keys = set(
        (
            await db.scalars(
                select(ObservabilityEvent.idempotency_key).where(
                    ObservabilityEvent.project_id == project_id,
                    ObservabilityEvent.idempotency_key.in_(idempotency_keys),
                )
            )
        ).all()
    )
    try:
        accepted = await _persist_client_observations_atomic(db, observations, existing_keys)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            503,
            "client telemetry persistence is temporarily unavailable",
        ) from exc
    await db.commit()
    return {"accepted": accepted}


async def _persist_client_observations_atomic(
    db: AsyncSession,
    observations: list[Observation],
    existing_keys: set[str],
) -> int:
    """Stage one idempotent client batch in the request transaction."""
    unique: dict[str, Observation] = {}
    for item in observations:
        unique.setdefault(item.idempotency_key, item)
    pending = [item for key, item in unique.items() if key not in existing_keys]
    if not pending:
        return 0
    db.add_all([item.to_model() for item in pending])
    await db.flush()
    return len(pending)


@app.get("/projects/{project_id}/observability/summary")
async def observability_summary(
    project_id: str,
    range: str = Query("7d"),
    timezone_name: str = Query("UTC", alias="timezone"),
    member_id: str | None = None,
    actor: str | None = None,
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    actor_is_system = actor == "system" and member_id is None
    actor_member_id = member_id or (None if actor_is_system else actor)
    if member_id and actor and member_id != actor:
        raise HTTPException(422, "member_id and actor filters conflict")
    if actor_member_id and not await db.scalar(
        select(TeamMember.id).where(
            TeamMember.id == actor_member_id,
            TeamMember.project_id == project_id,
        )
    ):
        raise HTTPException(404, "team member not found")
    try:
        return await build_summary(
            db,
            project_id,
            range_name=range,
            timezone_name=timezone_name,
            actor_member_id=actor_member_id,
            actor_is_system=actor_is_system,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/projects/{project_id}/observability/events")
async def list_observability_events(
    project_id: str,
    range: str = Query("7d"),
    category: str | None = None,
    operation: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    member_id: str | None = None,
    actor: str | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    try:
        start, end, _, _ = date_window(range, "UTC")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    statement = select(ObservabilityEvent).where(
        ObservabilityEvent.project_id == project_id,
        ObservabilityEvent.occurred_at <= end,
        ObservabilityEvent.category != "usage_guard",
    )
    if start:
        statement = statement.where(ObservabilityEvent.occurred_at >= start)
    if category:
        statement = statement.where(ObservabilityEvent.category == category)
    if operation:
        statement = statement.where(ObservabilityEvent.operation == operation)
    if status:
        statement = statement.where(ObservabilityEvent.status == status)
    if provider:
        statement = statement.where(ObservabilityEvent.provider == provider)
    actor_is_system = actor == "system" and member_id is None
    actor_member_id = member_id or (None if actor_is_system else actor)
    if member_id and actor and member_id != actor:
        raise HTTPException(422, "member_id and actor filters conflict")
    if actor_is_system:
        statement = statement.where(ObservabilityEvent.actor_member_id.is_(None))
    elif actor_member_id:
        if not await db.scalar(
            select(TeamMember.id).where(
                TeamMember.id == actor_member_id,
                TeamMember.project_id == project_id,
            )
        ):
            raise HTTPException(404, "team member not found")
        statement = statement.where(ObservabilityEvent.actor_member_id == actor_member_id)
    if cursor:
        try:
            occurred_at, event_id = cursor.rsplit("|", 1)
            cursor_time = datetime.fromisoformat(occurred_at)
            if cursor_time.tzinfo is None:
                cursor_time = cursor_time.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(422, "cursor is invalid") from exc
        statement = statement.where(
            or_(
                ObservabilityEvent.occurred_at < cursor_time,
                and_(
                    ObservabilityEvent.occurred_at == cursor_time,
                    ObservabilityEvent.id < event_id,
                ),
            )
        )
    rows = list(
        (
            await db.scalars(
                statement.order_by(
                    desc(ObservabilityEvent.occurred_at), desc(ObservabilityEvent.id)
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    actor_ids = {item.actor_member_id for item in items if item.actor_member_id}
    actors = {
        member.id: member
        for member in (
            list((await db.scalars(select(TeamMember).where(TeamMember.id.in_(actor_ids)))).all())
            if actor_ids
            else []
        )
    }
    payload_event_ids = (
        set(
            (
                await db.scalars(
                    select(ContextEventPayload.observability_event_id).where(
                        ContextEventPayload.observability_event_id.in_([item.id for item in items])
                    )
                )
            ).all()
        )
        if items
        else set()
    )
    next_cursor = None
    if has_more and items:
        item = items[-1]
        next_cursor = f"{item.occurred_at.isoformat()}|{item.id}"
    return {
        "items": [
            {
                **serialize_event(item, has_content=item.id in payload_event_ids),
                "actor_member": (
                    serialize_member(actors[item.actor_member_id])
                    if item.actor_member_id in actors
                    else None
                ),
            }
            for item in items
        ],
        "next_cursor": next_cursor,
    }


@app.get("/projects/{project_id}/observability/context-events/{event_id}")
async def get_context_event(
    project_id: str,
    event_id: str,
    db: AsyncSession = Depends(get_session),
):
    event = await db.scalar(
        select(ObservabilityEvent).where(
            ObservabilityEvent.id == event_id,
            ObservabilityEvent.project_id == project_id,
            ObservabilityEvent.category == "context",
        )
    )
    if event is None:
        raise HTTPException(404, "context event not found")
    payload = await db.get(ContextEventPayload, event.id)
    if payload is None:
        raise HTTPException(404, "context payload not available")
    turn = (
        await db.scalar(select(Turn).where(Turn.id == event.turn_id, Turn.project_id == project_id))
        if event.turn_id
        else None
    )
    retrieval = (
        await db.scalar(
            select(RetrievalRun).where(
                RetrievalRun.id == event.retrieval_run_id,
                RetrievalRun.project_id == project_id,
            )
        )
        if event.retrieval_run_id
        else None
    )
    memories = []
    memory_component = next(
        (
            component
            for component in (payload.components or [])
            if isinstance(component, dict) and component.get("name") == "memories"
        ),
        None,
    )
    selection_aware_manifest = payload.render_version in {
        "hook-context-v4",
        "hook-context-v5",
        "hook-context-v6",
    } or any(
        isinstance(component, dict) and "candidate_item_count" in component
        for component in (payload.components or [])
    )
    emitted_memory_ids: list[str] | None = None
    if selection_aware_manifest:
        emitted_memory_ids = [
            reference.removeprefix("memory:")
            for reference in (memory_component or {}).get("references", [])
            if isinstance(reference, str) and reference
        ]
    memory_ids = (
        emitted_memory_ids
        if emitted_memory_ids is not None
        else list(retrieval.selected_memory_ids or [])
        if retrieval
        else []
    )
    if memory_ids:
        rows = list(
            (
                await db.scalars(
                    select(Memory).where(
                        Memory.id.in_(memory_ids),
                        Memory.project_id == project_id,
                    )
                )
            ).all()
        )
        by_id = {item.id: item for item in rows}
        memories = [
            {
                "id": memory.id,
                "node_type": memory.node_type,
                "node_key": memory.node_key,
                "text": memory.text,
                "revision": memory.revision,
                "score": retrieval.selected_scores.get(memory.id) if retrieval else None,
            }
            for memory_id in memory_ids
            for memory in [by_id.get(memory_id)]
            if memory is not None
        ]
    return {
        "event": serialize_event(event, payload),
        "content": payload.content,
        "content_sha256": payload.content_sha256,
        "producer_version": payload.producer_version,
        "render_version": payload.render_version,
        "estimator_version": payload.estimator_version,
        "captured_at": payload.captured_at.isoformat(),
        "components": payload.components,
        "turn": (
            {
                "id": turn.id,
                "user_prompt": turn.user_prompt,
                "assistant_response": turn.assistant_response,
            }
            if turn and not turn.off_record
            else None
        ),
        "turn_off_record": bool(turn and turn.off_record),
        "retrieval": (
            {
                "id": retrieval.id,
                "status": retrieval.status,
                "memories": memories,
            }
            if retrieval
            else None
        ),
        "tool_name": payload.tool_name,
    }


@app.post("/projects/{project_id}/artifacts")
async def create_artifact(
    project_id: str, payload: ArtifactCreate, db: AsyncSession = Depends(get_session)
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    try:
        artifact, created = await register_artifact(db, project_id, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await activity(
        db,
        project_id,
        "artifact.created" if created else "artifact.linked",
        f"Artifact {'saved' if created else 'linked'}: {artifact.filename or artifact.kind}",
        {"artifact_id": artifact.id, "turn_id": payload.turn_id},
    )
    await db.commit()
    return {**serialize_artifact(artifact), "created": created}


@app.get("/projects/{project_id}/artifacts")
async def list_artifacts(
    project_id: str, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_session)
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    rows = list(
        (
            await db.scalars(
                select(Artifact)
                .where(Artifact.project_id == project_id)
                .order_by(desc(Artifact.created_at))
                .limit(limit)
            )
        ).all()
    )
    return {"items": [serialize_artifact(item) for item in rows]}


@app.post("/projects/{project_id}/compactions")
async def record_compaction(
    project_id: str,
    payload: CompactionRecord,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    agent_session = await db.get(Session, payload.session_id)
    if not agent_session or agent_session.project_id != project_id:
        raise HTTPException(404, "session not found")
    require_member_session(principal_from_request(request), agent_session.member_id)
    return await persist_compaction(db, agent_session, payload)


@app.post("/turns/{turn_id}/stop-check")
async def stop_check(
    turn_id: str,
    payload: StopCheck,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    turn = await lock_writable_turn(db, turn_id, principal)
    session = await db.get(Session, turn.session_id)
    if session is None:
        raise HTTPException(404, "turn not found")
    require_member_session(principal, session.member_id)
    if turn.committed:
        await persist_context_observations(
            db,
            turn.project_id,
            payload.context_observations,
            fallback_session_id=turn.session_id,
            fallback_turn_id=turn.id,
            strict=False,
        )
        await db.commit()
        return {"allow": True, "committed": True, "degraded": False}
    retrieved = set(turn.retrieved_memory_ids)
    supplied_memory_ids = list(dict.fromkeys(payload.used_memory_ids))
    used_memory_ids = [item for item in supplied_memory_ids if item in retrieved]
    discarded_memory_ids = [item for item in supplied_memory_ids if item not in retrieved]
    await persist_context_observations(
        db,
        turn.project_id,
        payload.context_observations,
        fallback_session_id=turn.session_id,
        fallback_turn_id=turn.id,
        strict=False,
    )
    result = await commit_turn(
        db,
        turn,
        TurnCommit(
            assistant_response=payload.assistant_response or turn.assistant_response,
            topic_changed=payload.topic_changed,
            segment_summary=payload.segment_summary,
            used_memory_ids=used_memory_ids,
            receipt=payload.receipt,
        ),
    )
    # The normal chat lifecycle is the durable signal that project data changed.
    # Scheduling is cheap and idempotent; actual archive work stays off the response path.
    await _schedule_due_backup(db, turn.project_id, background_tasks)
    return {
        "allow": True,
        "committed": result["committed"],
        "degraded": False,
        "discarded_memory_ids": discarded_memory_ids,
    }


def _backup_available(project_id: str) -> tuple[bool, str]:
    settings = get_settings()
    if settings.backup_project_id and settings.backup_project_id != project_id:
        return False, "Backup configuration belongs to another project."
    if not settings.backup_configured:
        return False, settings.backup_configuration_error or "Backup is not configured."
    return True, ""


def _backup_due(project: Project) -> bool:
    """Back up once initially, then at most daily after project changes."""
    if not project.last_backup_at:
        return True
    if not project.backup_dirty:
        return False
    last_backup = project.last_backup_at
    if last_backup.tzinfo is None:
        last_backup = last_backup.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_backup >= timedelta(hours=24)


def backup_engine() -> BackupEngine:
    return BackupEngine()


async def _run_backup(db: AsyncSession, record: BackupRecord, project: Project) -> BackupRecord:
    record.status = "running"
    await db.commit()
    try:
        result = await asyncio.to_thread(
            backup_engine().create,
            project.id,
            project.name,
            __version__,
        )
    except Exception as exc:
        record.status = "failed"
        record.error = str(exc)[:2_000]
        record.completed_at = datetime.now(timezone.utc)
        await activity(
            db,
            project.id,
            "backup.failed",
            "Encrypted backup failed",
            {"record_id": record.id, "error": record.error},
        )
        await db.commit()
        return record

    await db.refresh(project)
    record.status = "verified"
    record.archive_name = result.archive_name
    record.size_bytes = result.size_bytes
    record.includes_qdrant = result.includes_qdrant
    record.manifest_json = result.manifest
    record.completed_at = datetime.now(timezone.utc)
    record.verified_at = record.completed_at
    project.last_backup_at = record.verified_at
    project.last_backed_up_generation = max(
        project.last_backed_up_generation, record.source_generation
    )
    project.backup_dirty = project.backup_generation > record.source_generation
    if result.pruned_archives:
        pruned = (
            await db.scalars(
                select(BackupRecord).where(
                    BackupRecord.project_id == project.id,
                    BackupRecord.archive_name.in_(result.pruned_archives),
                )
            )
        ).all()
        for previous in pruned:
            previous.retained = False
    await activity(
        db,
        project.id,
        "backup.verified",
        "Encrypted backup created and verified",
        {
            "record_id": record.id,
            "archive_name": record.archive_name,
            "includes_qdrant": record.includes_qdrant,
            "newer_changes_pending": project.backup_dirty,
        },
    )
    await db.commit()
    return record


async def _run_background_backup(record_id: str, project_id: str) -> None:
    async with SessionLocal() as db:
        record = await db.get(BackupRecord, record_id)
        project = await db.get(Project, project_id)
        if record and project:
            await _run_backup(db, record, project)


async def _automatic_backup_loop() -> None:
    """Keep backup protection current even when no later chat turn is submitted."""
    while True:
        try:
            async with SessionLocal() as db:
                project_ids = list(
                    (
                        await db.scalars(
                            select(Project.id).where(Project.authority_state == "active")
                        )
                    ).all()
                )
                for project_id in project_ids:
                    await _schedule_due_backup(db, project_id, None)
        except Exception:
            # A backup target can be disconnected. Its regular status/error remains
            # visible in the dashboard; the scheduler must never take down the API.
            pass
        await asyncio.sleep(get_settings().backup_auto_seconds)


async def _schedule_due_backup(
    db: AsyncSession,
    project_id: str,
    background_tasks: BackgroundTasks | None,
) -> dict:
    """Queue one verified automatic archive when the project's daily copy is due."""
    # Serialize the due/running check with every other scheduler or manual
    # request for this project. Without the row lock, two API requests could
    # both observe an empty queue and publish duplicate full backups.
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if not project:
        raise HTTPException(404, "project not found")
    if not project_authority_writable(project):
        return {"scheduled": False, "reason": "authority_read_only"}
    configured, error = _backup_available(project_id)
    if not configured:
        return {"scheduled": False, "reason": error}
    if not _backup_due(project):
        return {"scheduled": False, "reason": "not_due"}
    running = await db.scalar(
        select(BackupRecord.id)
        .where(
            BackupRecord.project_id == project_id,
            BackupRecord.status.in_(["scheduled", "running"]),
        )
        .limit(1)
    )
    if running:
        return {"scheduled": False, "reason": "already_running"}
    record = BackupRecord(
        project_id=project_id,
        trigger="automatic",
        status="scheduled",
        source_generation=project.backup_generation,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    if background_tasks is not None:
        background_tasks.add_task(_run_background_backup, record.id, project_id)
    else:
        asyncio.create_task(_run_background_backup(record.id, project_id))
    return {"scheduled": True, "record_id": record.id}


def _backup_download_path(record: BackupRecord) -> Path:
    """Return a verified archive path without permitting traversal outside /backups."""
    archive_name = record.archive_name or ""
    if (
        record.status != "verified"
        or not record.retained
        or not archive_name.endswith(".dduobackup")
        or Path(archive_name).name != archive_name
    ):
        raise HTTPException(404, "backup archive not found")
    root = get_settings().backup_directory.resolve()
    archive = (root / archive_name).resolve()
    if archive.parent != root or not archive.is_file():
        raise HTTPException(404, "backup archive not found")
    return archive


async def _backup_status(project_id: str, db: AsyncSession) -> dict:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    rows = (
        await db.scalars(
            select(BackupRecord)
            .where(BackupRecord.project_id == project_id)
            .order_by(desc(BackupRecord.created_at))
            .limit(20)
        )
    ).all()
    configured, error = _backup_available(project_id)
    settings = get_settings()
    return {
        "configured": configured,
        "configuration_error": error,
        "dirty": project.backup_dirty,
        "automatic_due": configured and _backup_due(project),
        "last_backup_at": project.last_backup_at,
        "include_qdrant": settings.backup_include_qdrant,
        "qdrant_collection": embedding_service().collection(project_id),
        "retention": {
            "daily": settings.backup_retention_daily,
            "weekly": settings.backup_retention_weekly,
            "monthly": settings.backup_retention_monthly,
        },
        "latest": serialize(rows[0]) if rows else None,
        "latest_verified": next(
            (serialize(record) for record in rows if record.status == "verified"), None
        ),
        "items": [serialize(record) for record in rows],
    }


@app.get("/projects/{project_id}/backups")
async def get_backups(
    project_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    require_infrastructure_manager(principal_from_request(request))
    return await _backup_status(project_id, db)


@app.get("/projects/{project_id}/backups/{backup_id}/download")
async def download_backup(
    project_id: str,
    backup_id: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Download only a retained, locally verified archive for this project."""
    require_infrastructure_manager(principal_from_request(request))
    record = await db.get(BackupRecord, backup_id)
    if not record or record.project_id != project_id:
        raise HTTPException(404, "backup archive not found")
    archive = _backup_download_path(record)
    return FileResponse(
        archive,
        media_type="application/octet-stream",
        filename=archive.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/projects/{project_id}/backups/register-restore")
async def register_restored_backup(
    project_id: str,
    payload: BackupRestoreRegister,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Record verified restore provenance without claiming later local writes are protected."""
    require_infrastructure_manager(principal_from_request(request))
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    manifest = payload.manifest
    manifest_project = manifest.get("project")
    manifest_qdrant = manifest.get("qdrant")
    if (
        manifest.get("format") != "dduo-solo-founder-backup"
        or manifest.get("schema_version") != 1
        or not isinstance(manifest_project, dict)
        or manifest_project.get("id") != project_id
        or not isinstance(manifest_qdrant, dict)
    ):
        raise HTTPException(422, "backup manifest does not match this project")
    existing = await db.scalar(
        select(BackupRecord).where(
            BackupRecord.project_id == project_id,
            BackupRecord.trigger == "restore",
            BackupRecord.archive_name == payload.archive_name,
        )
    )
    if existing:
        return serialize(existing)
    try:
        archive_created_at = datetime.fromisoformat(manifest["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "backup manifest timestamp is invalid") from exc
    if archive_created_at.tzinfo is None:
        raise HTTPException(422, "backup manifest timestamp is invalid")
    verified_at = datetime.now(timezone.utc)
    record = BackupRecord(
        project_id=project_id,
        trigger="restore",
        status="verified",
        archive_name=payload.archive_name,
        size_bytes=payload.size_bytes,
        includes_qdrant=bool(manifest_qdrant.get("included")),
        source_generation=project.last_backed_up_generation,
        manifest_json=manifest,
        completed_at=verified_at,
        verified_at=verified_at,
    )
    project.last_backup_at = archive_created_at.astimezone(timezone.utc)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return serialize(record)


@app.post("/projects/{project_id}/backups")
async def create_backup(
    project_id: str,
    request: Request,
    trigger: str = Query("manual", pattern="^(manual|update|uninstall)$"),
    db: AsyncSession = Depends(get_session),
):
    require_infrastructure_manager(principal_from_request(request))
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if not project:
        raise HTTPException(404, "project not found")
    configured, error = _backup_available(project_id)
    if not configured:
        raise HTTPException(409, error)
    running = await db.scalar(
        select(BackupRecord.id)
        .where(
            BackupRecord.project_id == project_id,
            BackupRecord.status.in_(["scheduled", "running"]),
        )
        .limit(1)
    )
    if running:
        raise HTTPException(409, "another backup is already running")
    record = BackupRecord(
        project_id=project_id,
        trigger=trigger,
        status="scheduled",
        source_generation=project.backup_generation,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    await _run_backup(db, record, project)
    if record.status == "failed":
        raise HTTPException(500, record.error or "backup failed")
    return serialize(record)


@app.post("/projects/{project_id}/backups/automatic", status_code=202)
async def schedule_automatic_backup(
    project_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    require_infrastructure_manager(principal_from_request(request))
    return await _schedule_due_backup(db, project_id, background_tasks)


@app.get("/projects/{project_id}/plans")
async def list_plans(
    project_id: str,
    detail: Literal["compact", "full"] = Query("full"),
    status: str | None = Query(None),
    q: str | None = Query(None, max_length=1_000),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    statement = select(Plan).where(Plan.project_id == project_id)
    if status:
        statement = statement.where(Plan.status == status)
    if q:
        statement = statement.where(or_(
            Plan.title.icontains(q, autoescape=True), Plan.objective.icontains(q, autoescape=True),
            Plan.content.icontains(q, autoescape=True),
        ))
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    if detail == "compact":
        statement = statement.options(load_only(
            Plan.id, Plan.project_id, Plan.title, Plan.objective, Plan.status, Plan.labels,
            Plan.version, Plan.created_at, Plan.updated_at,
        ))
    rows = list((await db.scalars(
        statement.order_by(desc(Plan.updated_at), Plan.id).limit(limit).offset(offset)
    )).all())
    if detail == "compact":
        links = await plan_work_item_ids(db, [row.id for row in rows])
        items = [{"id": row.id, "project_id": row.project_id, "title": row.title,
                  "objective": row.objective[:360], "status": row.status, "labels": row.labels,
                  "version": row.version, "updated_at": row.updated_at,
                  "work_item_ids": links[row.id], "detail": "compact"} for row in rows]
    else:
        items = await serialize_plans(db, rows)
    return {"items": items, "total": total, "limit": limit, "offset": offset, "detail": detail}


@app.get("/projects/{project_id}/plans/{plan_id}")
async def get_plan(project_id: str, plan_id: str, db: AsyncSession = Depends(get_session)):
    plan = await db.get(Plan, plan_id)
    if plan is None or plan.project_id != project_id:
        raise HTTPException(404, "plan not found")
    return {"plan": (await serialize_plans(db, [plan]))[0]}


@app.post("/projects/{project_id}/plans")
async def create_plan(
    project_id: str, payload: PlanCreate, db: AsyncSession = Depends(get_session)
):
    principal = current_team_principal()
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    try:
        work_item_ids = await validate_plan_work_items(db, project_id, payload.work_item_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    plan = Plan(
        project_id=project_id,
        **payload.model_dump(exclude={"work_item_ids", "rationale"}),
    )
    db.add(plan)
    await db.flush()
    if work_item_ids:
        db.add_all(ordered_plan_work_items(plan.id, work_item_ids))
        await db.flush()
    db.add(
        PlanRevision(
            project_id=project_id,
            plan_id=plan.id,
            version=plan.version,
            snapshot=await plan_snapshot(db, plan),
            actor="agent",
            actor_member_id=principal.member_id if principal else None,
            rationale=payload.rationale,
        )
    )
    await activity(
        db,
        project_id,
        "plan.create",
        f"Plan created: {plan.title}",
        {"id": plan.id, "work_item_ids": work_item_ids},
    )
    await db.commit()
    return (await serialize_plans(db, [plan]))[0]


@app.patch("/projects/{project_id}/plans/{plan_id}")
async def patch_plan(
    project_id: str,
    plan_id: str,
    payload: PlanUpdate,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_remote_expected_version(principal, payload.expected_version)
    plan = await db.scalar(
        select(Plan)
        .where(Plan.id == plan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "plan not found")
    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"expected_version", "rationale", "work_item_ids"},
    )
    changes = {key: value for key, value in changes.items() if getattr(plan, key) != value}
    work_item_ids = None
    links_changed = False
    if "work_item_ids" in payload.model_fields_set:
        try:
            work_item_ids = await validate_plan_work_items(
                db, project_id, payload.work_item_ids or []
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        current_ids = (await plan_work_item_ids(db, [plan.id]))[plan.id]
        links_changed = current_ids != work_item_ids
    if payload.expected_version is not None and payload.expected_version != plan.version:
        if not changes and not links_changed:
            return (await serialize_plans(db, [plan]))[0]
        raise HTTPException(409, "plan version conflict")
    if not changes and not links_changed:
        return (await serialize_plans(db, [plan]))[0]
    for key, value in changes.items():
        setattr(plan, key, value)
    if links_changed:
        await replace_plan_work_items(db, plan, work_item_ids or [])
    plan.version += 1
    plan.updated_at = utcnow()
    await db.flush()
    db.add(
        PlanRevision(
            project_id=project_id,
            plan_id=plan.id,
            version=plan.version,
            snapshot=await plan_snapshot(db, plan),
            actor="agent",
            actor_member_id=principal.member_id,
            rationale=payload.rationale,
        )
    )
    await activity(
        db,
        project_id,
        "plan.update",
        f"Plan updated: {plan.title}",
        {
            "id": plan.id,
            "fields": sorted(changes),
            "work_item_ids": work_item_ids if links_changed else None,
        },
    )
    await db.commit()
    return (await serialize_plans(db, [plan]))[0]


@app.post("/projects/{project_id}/plans/{plan_id}/attachments")
async def create_plan_attachment(
    project_id: str,
    plan_id: str,
    payload: ArtifactCreate,
    db: AsyncSession = Depends(get_session),
):
    plan = await db.get(Plan, plan_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "plan not found")
    try:
        artifact, created, linked = await attach_artifact_to_plan(db, plan, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if linked:
        await activity(
            db,
            project_id,
            "plan.attachment.added",
            f"Attachment added to {plan.title}: {artifact.filename or artifact.kind}",
            {"plan_id": plan.id, "artifact_id": artifact.id},
        )
    await db.commit()
    return {**serialize_artifact(artifact), "created": created, "linked": linked}


@app.get("/projects/{project_id}/plans/{plan_id}/attachments")
async def list_plan_attachments(
    project_id: str,
    plan_id: str,
    db: AsyncSession = Depends(get_session),
):
    plan = await db.get(Plan, plan_id)
    if not plan or plan.project_id != project_id:
        raise HTTPException(404, "plan not found")
    grouped = await plan_artifacts(db, [plan_id])
    return {"items": grouped[plan_id]}


@app.get("/projects/{project_id}/plans/{plan_id}/attachments/{artifact_id}/content")
async def get_plan_attachment_content(
    project_id: str,
    plan_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_session),
):
    plan = await db.get(Plan, plan_id)
    artifact = await db.get(Artifact, artifact_id)
    link = await db.get(PlanArtifact, (plan_id, artifact_id))
    if (
        not plan
        or plan.project_id != project_id
        or not artifact
        or artifact.project_id != project_id
        or not link
    ):
        raise HTTPException(404, "plan attachment not found")
    return artifact_content_response(artifact)


@app.delete("/projects/{project_id}/plans/{plan_id}/attachments/{artifact_id}")
async def delete_plan_attachment(
    project_id: str,
    plan_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_session),
):
    plan = await db.get(Plan, plan_id)
    link = await db.get(PlanArtifact, (plan_id, artifact_id))
    if not plan or plan.project_id != project_id or not link:
        raise HTTPException(404, "plan attachment not found")
    await db.delete(link)
    await activity(
        db,
        project_id,
        "plan.attachment.removed",
        f"Attachment removed from {plan.title}",
        {"plan_id": plan.id, "artifact_id": artifact_id},
    )
    await db.commit()
    return Response(status_code=204)


@app.get("/projects/{project_id}/sprints")
async def list_sprints(
    project_id: str,
    status: Literal["planned", "active", "archived"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    if await db.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    statement = select(Sprint).where(Sprint.project_id == project_id)
    if status is not None:
        statement = statement.where(Sprint.status == status)
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (await db.scalars(
        statement.order_by(desc(Sprint.updated_at), Sprint.id).limit(limit).offset(offset)
    )).all()
    return {"items": [serialize_json(row) for row in rows], "total": total,
            "limit": limit, "offset": offset}


@app.post("/projects/{project_id}/sprints")
async def create_sprint(
    project_id: str, payload: SprintCreate, db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "create", payload)


@app.post("/projects/{project_id}/sprints/history")
async def create_historical_sprint(
    project_id: str, payload: SprintHistoryCreate, db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "history", payload)


@app.get("/projects/{project_id}/sprints/{sprint_id}")
async def get_sprint(
    project_id: str, sprint_id: str, db: AsyncSession = Depends(get_session),
):
    return serialize_json(await find_sprint(db, project_id, sprint_id))


@app.patch("/projects/{project_id}/sprints/{sprint_id}")
async def patch_sprint(
    project_id: str, sprint_id: str, payload: SprintUpdate,
    db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "update", payload, sprint_id)


@app.post("/projects/{project_id}/sprints/{sprint_id}/start")
async def start_sprint(
    project_id: str, sprint_id: str, payload: SprintTransition,
    db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "start", payload, sprint_id)


@app.get("/projects/{project_id}/sprints/{sprint_id}/close-preview")
async def preview_sprint_close(
    project_id: str, sprint_id: str, db: AsyncSession = Depends(get_session),
):
    return await close_preview(db, project_id, sprint_id)


@app.post("/projects/{project_id}/sprints/{sprint_id}/archive")
async def archive_sprint(
    project_id: str, sprint_id: str, payload: SprintArchive,
    db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "archive", payload, sprint_id)


@app.post("/projects/{project_id}/sprints/{sprint_id}/reopen")
async def reopen_sprint(
    project_id: str, sprint_id: str, payload: SprintTransition,
    db: AsyncSession = Depends(get_session),
):
    return await mutate_sprint(db, project_id, "reopen", payload, sprint_id)


@app.get("/projects/{project_id}/sprints/{sprint_id}/tasks")
async def sprint_task_history(
    project_id: str, sprint_id: str,
    closure_version: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=1_000),
    db: AsyncSession = Depends(get_session),
):
    sprint = await find_sprint(db, project_id, sprint_id)
    closure = closure_version if closure_version is not None else sprint.archive_version
    statement = select(SprintTaskSnapshot).where(
        SprintTaskSnapshot.project_id == project_id, SprintTaskSnapshot.sprint_id == sprint_id,
        SprintTaskSnapshot.closure_version == closure,
    )
    if q:
        statement = statement.where(cast(SprintTaskSnapshot.snapshot, String).icontains(q, autoescape=True))
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (await db.scalars(statement.order_by(SprintTaskSnapshot.task_id).limit(limit).offset(offset))).all()
    return {"items": [{**row.snapshot, "outcome": row.outcome,
                       "destination_sprint_id": row.destination_sprint_id,
                       "closure_version": row.closure_version} for row in rows],
            "sprint": serialize_json(sprint), "closure_version": closure, "total": total,
            "limit": limit, "offset": offset}


@app.get("/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    detail: Literal["compact", "full"] = Query("full"),
    scope: Literal["active", "completed", "all"] = Query("all"),
    status: str | None = Query(None),
    kind: str | None = Query(None),
    epic_id: str | None = Query(None),
    sprint_id: str | None = Query(None),
    placement: Literal["all", "current", "backlog", "archive"] = Query("all"),
    label: str | None = Query(None),
    q: str | None = Query(None, max_length=1_000),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    known_snapshot_hash: str | None = Query(None, min_length=64, max_length=64),
    db: AsyncSession = Depends(get_session),
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    statement = select(Task).where(placement_predicate(project_id, placement, sprint_id))
    if status:
        statement = statement.where(Task.status == status)
    elif scope == "active":
        statement = statement.where(Task.status.in_(ACTIVE_TASK_STATUSES))
    elif scope == "completed":
        statement = statement.where(Task.status.in_(COMPLETED_TASK_STATUSES))
    if kind:
        statement = statement.where(Task.kind == kind)
    if epic_id:
        statement = statement.where(Task.epic_id == epic_id)
    if label:
        if db.get_bind().dialect.name == "sqlite":
            labels = func.json_each(Task.labels).table_valued("value")
            statement = statement.where(select(1).select_from(labels).where(labels.c.value == label).exists())
        else:
            from sqlalchemy.dialects.postgresql import JSONB
            statement = statement.where(cast(Task.labels, JSONB).contains([label]))
    if q:
        statement = statement.where(or_(*[
            column.icontains(q, autoescape=True) for column in (
                Task.title, Task.description, Task.objective, Task.next_action, cast(Task.labels, String),
            )
        ]))
    total = await db.scalar(select(func.count()).select_from(statement.subquery()))
    if detail == "compact":
        statement = statement.options(load_only(
            Task.id, Task.kind, Task.epic_id, Task.sprint_id, Task.title, Task.status, Task.priority,
            Task.labels, Task.objective, Task.next_action, Task.due_at, Task.version, Task.updated_at,
        ))
    rows = list((await db.scalars(statement.order_by(desc(Task.updated_at), Task.id).limit(limit).offset(offset))).all())
    items = await serialize_tasks(db, rows, detail)
    epic_ids = [row.id for row in rows if row.kind == "epic"]
    if epic_ids:
        counts = (await db.execute(
            select(Task.epic_id, Task.status, func.count(Task.id))
            .where(Task.project_id == project_id, Task.epic_id.in_(epic_ids))
            .group_by(Task.epic_id, Task.status)
        )).all()
        totals = {epic_id: {"total": 0, "completed": 0, "open": 0} for epic_id in epic_ids}
        for epic_id, task_status, count in counts:
            totals[epic_id]["total"] += count
            totals[epic_id]["completed" if task_status in COMPLETED_TASK_STATUSES else "open"] += count
        for item in items:
            if item["id"] in totals:
                item["task_counts"] = totals[item["id"]]
    return snapshot_response(
        {"items": items, "detail": detail, "scope": scope, "placement": placement,
         "sprint_id": sprint_id, "total": total, "limit": limit, "offset": offset},
        known_snapshot_hash,
    )


@app.post("/projects/{project_id}/tasks/reindex")
async def reindex_tasks(
    project_id: str,
    origin: Literal["bootstrap", "restore"] = Query("bootstrap"),
    reset: bool = Query(False),
    db: AsyncSession = Depends(get_session),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    if reset:
        # Persist invalidation before the external destructive operation. If
        # Qdrant succeeds but the later DB transaction fails, the next search
        # will reconcile instead of trusting an empty collection.
        project.task_index_reconciled = False
        await advance_task_index_generation(db, project_id)
        await db.commit()
    await lock_task_index_project(db, project_id)
    try:
        service = task_index_service()
        collection = await asyncio.to_thread(
            service.reset_collection if reset else service.ensure_collection,
            project_id,
        )
    except Exception as exc:
        raise HTTPException(503, "task semantic index is unavailable") from exc
    await lock_writable_project(db, project_id)
    queued = await enqueue_task_reindex_events(
        db,
        project_id=project_id,
        origin=origin,
        # A reset must leave a fresh, ready event for every authoritative task,
        # even when an older worker was already processing the same version.
        force=reset,
        index_service=service,
    )
    await advance_task_index_generation(db, project_id)
    pending = int(
        await db.scalar(
            select(func.count()).select_from(pending_task_projection_ids(project_id).subquery())
        )
        or 0
    )
    await db.commit()
    return {
        "queued": queued,
        "collection": collection,
        "status": "indexing" if pending else "ready",
    }


@app.post("/projects/{project_id}/tasks/search")
async def search_tasks(
    project_id: str,
    payload: TaskSearch,
    db: AsyncSession = Depends(get_session),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    try:
        offset = int(payload.cursor or "0")
    except ValueError as exc:
        raise HTTPException(422, "invalid task search cursor") from exc
    if offset < 0 or offset > 10_000:
        raise HTTPException(422, "invalid task search cursor")

    started = datetime.now(timezone.utc)
    statuses = task_search_statuses(payload)
    sprint_statuses = dict((await db.execute(
        select(Sprint.id, Sprint.status).where(Sprint.project_id == project_id)
    )).all())
    vector_sprint_ids = task_search_sprint_ids(payload, sprint_statuses)
    direct = await db.get(Task, payload.query)
    if (
        direct
        and direct.project_id == project_id
        and task_matches_search_filters(direct, payload, statuses, sprint_statuses)
    ):
        items = [{**task_view(direct, "compact"), "score": 1.0, "match_type": "exact_id"}]
        await record_task_search_retrieval(
            db,
            project_id=project_id,
            started_at=started,
            match_type="exact_id",
            candidate_count=1,
            selected_count=1,
        )
        await db.commit()
        return {
            "items": items,
            "match_type": "exact_id",
            "degraded": False,
            "next_cursor": None,
        }

    exact_rows = list(
        (
            await db.scalars(
                select(Task)
                .where(
                    Task.project_id == project_id,
                    placement_predicate(project_id, payload.placement, payload.sprint_id),
                    func.lower(Task.title) == payload.query.casefold(),
                )
                .order_by(desc(Task.updated_at), Task.id)
            )
        ).all()
    )
    exact_rows = [row for row in exact_rows if task_matches_search_filters(row, payload, statuses, sprint_statuses)]
    if exact_rows:
        page = exact_rows[offset : offset + payload.limit]
        items = [
            {**task_view(row, "compact"), "score": 1.0, "match_type": "exact_title"} for row in page
        ]
        next_cursor = (
            str(offset + payload.limit) if offset + payload.limit < len(exact_rows) else None
        )
        await record_task_search_retrieval(
            db,
            project_id=project_id,
            started_at=started,
            match_type="exact_title",
            candidate_count=len(exact_rows),
            selected_count=len(items),
        )
        await db.commit()
        return {
            "items": items,
            "match_type": "exact_title",
            "degraded": False,
            "ambiguous": len(exact_rows) > 1,
            "next_cursor": next_cursor,
        }

    authoritative_task_count = int(
        await db.scalar(select(func.count(Task.id)).where(Task.project_id == project_id)) or 0
    )
    if authoritative_task_count == 0:
        await record_task_search_retrieval(
            db,
            project_id=project_id,
            started_at=started,
            match_type="semantic",
            candidate_count=0,
            selected_count=0,
        )
        await db.commit()
        return {
            "items": [],
            "match_type": "semantic",
            "degraded": False,
            "degraded_reason": None,
            "indexing_pending": False,
            "index_status": "ready",
            "stale_hits": 0,
            "next_cursor": None,
        }

    await db.refresh(project)
    pending_repairs = await pending_task_repairs(db, project_id)
    service = None
    observed_result = None
    vector_error: Exception | None = None
    raw_hits: list[dict] = []
    semantic_attempted = False
    semantic_generation: int | None = None
    semantic_epoch: str | None = None
    generation_changed = False
    collection_changed = False
    integrity_verified = False
    semantic_started = datetime.now(timezone.utc)
    requested = max(payload.limit, (offset + payload.limit) * 3)

    # A reconciled local projection is a precondition for paid semantic search.
    # Provider-free identity and exact-cardinality checks detect collection
    # replacement and count-changing point loss; current outbox rows and a monotonic
    # generation protect the vector call without holding a database lock
    # throughout OpenAI/Qdrant latency.
    if not pending_repairs:
        try:
            service = task_index_service()
            if project.task_index_reconciled:
                integrity_matches = await task_projection_integrity_matches(
                    service,
                    project_id,
                    project.task_index_epoch,
                    authoritative_task_count,
                )
                if not integrity_matches:
                    project.task_index_reconciled = False
                    collection_changed = True
                    await db.commit()
                    await db.refresh(project)
                else:
                    integrity_verified = True
            if not project.task_index_reconciled:
                project = await lock_writable_project(db, project_id)
                await lock_task_index_project(db, project_id)
                await db.refresh(project)
                if not project.task_index_reconciled:
                    await enqueue_task_reindex_events(
                        db,
                        project_id=project_id,
                        origin="bootstrap",
                        force=False,
                        index_service=service,
                        strict_inventory=True,
                    )
                # Release the reconciliation lock before any paid/remote call.
                await db.commit()
                await db.refresh(project)
                pending_repairs = await pending_task_repairs(db, project_id)
                if project.task_index_reconciled:
                    collection_changed = False
                    integrity_verified = False
            if not pending_repairs and project.task_index_reconciled and not integrity_verified:
                integrity_verified = await task_projection_integrity_matches(
                    service,
                    project_id,
                    project.task_index_epoch,
                    authoritative_task_count,
                )
                if not integrity_verified:
                    project = await lock_writable_project(db, project_id)
                    project.task_index_reconciled = False
                    collection_changed = True
                    await db.commit()
                    await db.refresh(project)
            if not pending_repairs and project.task_index_reconciled and integrity_verified:
                expected_state = (
                    True,
                    int(project.task_index_generation),
                    project.task_index_epoch,
                )
                first_state = await current_task_index_state(db, project_id)
                pending_repairs = await pending_task_repairs(db, project_id)
                sprint_statuses = dict((await db.execute(
                    select(Sprint.id, Sprint.status).where(Sprint.project_id == project_id)
                )).all())
                vector_sprint_ids = task_search_sprint_ids(payload, sprint_statuses)
                confirmed_state = await current_task_index_state(db, project_id)
                generation_changed = (
                    first_state != expected_state
                    or first_state != confirmed_state
                    or not confirmed_state[0]
                    or confirmed_state[2] != project.task_index_epoch
                )
                semantic_generation = confirmed_state[1]
                semantic_epoch = confirmed_state[2]
            if (
                not pending_repairs
                and project.task_index_reconciled
                and integrity_verified
                and not generation_changed
            ):
                semantic_attempted = True
                semantic_started = datetime.now(timezone.utc)
                observed_result = await asyncio.to_thread(
                    service.search_observed,
                    project_id,
                    payload.query,
                    limit=payload.limit,
                    overfetch=requested,
                    statuses=statuses,
                    **({"placement": payload.placement, "sprint_ids": vector_sprint_ids,
                        "include_unsprinted": payload.sprint_id is None}
                       if payload.placement != "all" or payload.sprint_id is not None else {}),
                    kinds=[payload.kind] if payload.kind else None,
                    epic_id=payload.epic_id,
                    label=payload.label,
                )
                raw_hits = observed_result.items
                project = await lock_writable_project(db, project_id)
                # The pending read precedes the final generation read: a
                # mutation between them is still detected by the generation.
                pending_repairs.update(await pending_task_repairs(db, project_id))
                collection_changed = not await task_projection_integrity_matches(
                    service,
                    project_id,
                    semantic_epoch,
                    authoritative_task_count,
                )
                final_state = await current_task_index_state(db, project_id)
                generation_changed = generation_changed or final_state != (
                    True,
                    semantic_generation,
                    semantic_epoch,
                )
                if collection_changed:
                    project.task_index_reconciled = False
                if generation_changed or collection_changed:
                    raw_hits = []
        except Exception as exc:
            vector_error = exc

    # Every semantic branch can release the request transaction before an
    # external call.  Fence the final observations, repair events and readiness
    # flags against an authority transfer that completed in that interval.
    project = await lock_writable_project(db, project_id)
    # Provider failures do not invalidate Qdrant. Every other failure in the
    # block means the local projection could not be verified or queried and
    # must be reconciled before another paid attempt.
    if vector_error is not None and not isinstance(vector_error, EmbeddingProviderError):
        project.task_index_reconciled = False

    if semantic_attempted:
        embedding = (
            observed_result.embedding
            if observed_result is not None
            else vector_error.embedding
            if isinstance(vector_error, VectorOperationError)
            else None
        )
        usage = embedding.usage if embedding else None
        cost, unit_price, pricing_version = embedding_price(
            usage.model if usage else None, usage.input_tokens if usage else None
        )
        await record_observation(
            db,
            Observation(
                project_id=project_id,
                idempotency_key=f"task-search-embedding:{uuid4()}",
                category="embedding",
                operation="embedding.task_search",
                status=(
                    "success"
                    if vector_error is None
                    else "partial_failure"
                    if embedding is not None
                    else "failed"
                ),
                provider=usage.provider if usage else getattr(vector_error, "provider", None),
                model=usage.model if usage else getattr(vector_error, "model", None),
                measurement_source=usage.measurement_source if usage else "unavailable",
                input_tokens=usage.input_tokens if usage else None,
                reported_total_tokens=usage.reported_total_tokens if usage else None,
                duration_ms=round(
                    (datetime.now(timezone.utc) - semantic_started).total_seconds() * 1000
                ),
                provider_duration_ms=(
                    embedding.provider_duration_ms
                    if embedding
                    else getattr(vector_error, "provider_duration_ms", None)
                ),
                vector_store_duration_ms=(
                    observed_result.vector_store_duration_ms
                    if observed_result is not None
                    else vector_error.vector_store_duration_ms
                    if isinstance(vector_error, VectorOperationError)
                    else None
                ),
                request_count=provider_request_count(embedding, vector_error),
                item_count=len(raw_hits),
                candidate_count=len(raw_hits),
                cost_usd=cost,
                unit_price_usd_per_million=unit_price,
                pricing_version=pricing_version,
                details={
                    "limit": payload.limit,
                    "error_code": "task_index_unavailable" if vector_error else "",
                },
            ),
        )

    sprint_statuses = dict((await db.execute(
        select(Sprint.id, Sprint.status).where(Sprint.project_id == project_id)
    )).all())
    ids = list(dict.fromkeys(str(hit.get("task_id") or hit.get("id")) for hit in raw_hits))
    rows = (
        list(
            (
                await db.scalars(
                    select(Task).where(Task.project_id == project_id, Task.id.in_(ids))
                )
            ).all()
        )
        if ids
        else []
    )
    by_id = {row.id: row for row in rows}
    stale_count = 0
    orphan_ids: list[str] = []
    candidates: list[dict] = []
    threshold = float(getattr(get_settings(), "task_retrieval_similarity_threshold", 0.35))
    for hit in raw_hits:
        task_id = str(hit.get("task_id") or hit.get("id"))
        row = by_id.get(task_id)
        document = service.render(row) if row is not None and service is not None else None
        current = bool(
            row is not None
            and hit.get("project_id") == project_id
            and hit.get("source_version") == row.version
            and hit.get("sprint_id") == row.sprint_id
            and hit.get("semantic_hash") == document.semantic_hash
            and hit.get("index_render_version") == document.render_version
        )
        if not current:
            stale_count += 1
            if row is not None and row.id not in pending_repairs:
                enqueue_task_projection(db, row, origin="bootstrap")
                pending_repairs.add(row.id)
            elif row is None:
                orphan_ids.append(task_id)
            continue
        if not task_matches_search_filters(row, payload, statuses, sprint_statuses):
            continue
        score = float(hit.get("score") or 0.0)
        if score < threshold:
            continue
        candidates.append({**task_view(row, "compact"), "score": score, "match_type": "semantic"})

    orphan_cleanup_failed = False
    if orphan_ids and service is not None:
        try:
            await asyncio.to_thread(service.delete_points, project_id, orphan_ids)
        except Exception:
            # Hydration still prevents content leakage; the fallback avoids an
            # incomplete result if orphan hits crowded out current tasks.
            orphan_cleanup_failed = True
            project.task_index_reconciled = False
    task_indexing_pending = bool(pending_repairs)
    runtime_incomplete = bool(orphan_ids)
    readiness_unverified = not project.task_index_reconciled
    degraded = (
        vector_error is not None
        or task_indexing_pending
        or runtime_incomplete
        or generation_changed
        or collection_changed
        or readiness_unverified
    )
    match_type = "semantic"
    if degraded:
        fallback_rows = list(
            (
                await db.scalars(
                    select(Task)
                    .where(placement_predicate(project_id, payload.placement, payload.sprint_id))
                    .order_by(desc(Task.updated_at), Task.id)
                )
            ).all()
        )
        ranked = [
            (lexical_task_score(row, payload.query), row)
            for row in fallback_rows
            if task_matches_search_filters(row, payload, statuses, sprint_statuses)
        ]
        ranked = [item for item in ranked if item[0] > 0]
        ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        candidates = [
            {
                **task_view(row, "compact"),
                "score": round(score, 6),
                "match_type": "lexical_fallback",
            }
            for score, row in ranked
        ]
        match_type = "lexical_fallback"

    page = candidates[offset : offset + payload.limit]
    next_cursor = str(offset + payload.limit) if offset + payload.limit < len(candidates) else None
    await record_task_search_retrieval(
        db,
        project_id=project_id,
        started_at=started,
        match_type=match_type,
        candidate_count=len(raw_hits) if match_type == "semantic" else len(candidates),
        selected_count=len(page),
        stale_count=stale_count,
        degraded=degraded,
    )
    await db.commit()
    return {
        "items": page,
        "match_type": match_type,
        "degraded": degraded,
        "degraded_reason": (
            "task_index_unavailable"
            if vector_error is not None
            else "task_indexing"
            if task_indexing_pending
            else "task_index_incomplete"
            if runtime_incomplete
            else "task_index_changed"
            if generation_changed or collection_changed
            else "task_index_unverified"
            if readiness_unverified
            else None
        ),
        "indexing_pending": task_indexing_pending,
        "index_status": (
            "degraded"
            if (
                vector_error is not None
                or runtime_incomplete
                or generation_changed
                or collection_changed
                or orphan_cleanup_failed
                or readiness_unverified
            )
            else "indexing"
            if task_indexing_pending
            else "ready"
        ),
        "stale_hits": stale_count,
        "next_cursor": next_cursor,
    }


@app.get("/projects/{project_id}/tasks/{task_id}")
async def get_task(
    project_id: str,
    task_id: str,
    detail: TaskDetail = Query("working"),
    known_snapshot_hash: str | None = Query(None, min_length=64, max_length=64),
    db: AsyncSession = Depends(get_session),
):
    task = await db.get(Task, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(404, "task not found")
    item = (await serialize_tasks(db, [task], detail))[0]
    return snapshot_response({"task": item, "detail": detail}, known_snapshot_hash)


@app.post("/projects/{project_id}/tasks")
async def create_task(
    project_id: str,
    payload: TaskCreate,
    detail: TaskDetail = Query("full"),
    db: AsyncSession = Depends(get_session),
):
    principal = current_team_principal()
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    await lock_task_index_project(db, project_id)
    try:
        await validate_task_sprint(db, project_id, values=payload.model_dump())
        await validate_task_hierarchy(
            db,
            project_id,
            kind=payload.kind,
            epic_id=payload.epic_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    task = Task(project_id=project_id, **payload.model_dump())
    db.add(task)
    await db.flush()
    await touch_task_sprints(db, project_id, task.sprint_id)
    await advance_task_index_generation(db, project_id)
    db.add(
        TaskRevision(
            project_id=project_id,
            task_id=task.id,
            version=task.version,
            snapshot=serialize_json(task),
            actor="agent",
            actor_member_id=principal.member_id if principal else None,
            rationale=task.rationale or "",
        )
    )
    enqueue_task_projection(db, task)
    await activity(db, project_id, "task.create", f"Task created: {task.title}", {"id": task.id})
    await db.commit()
    return (await serialize_tasks(db, [task], detail))[0]


@app.patch("/projects/{project_id}/tasks/{task_id}")
async def patch_task(
    project_id: str,
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    detail: TaskDetail = Query("full"),
    db: AsyncSession = Depends(get_session),
):
    principal = principal_from_request(request)
    require_remote_expected_version(principal, payload.expected_version)
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    await lock_task_index_project(db, project_id)
    task = await db.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not task or task.project_id != project_id:
        raise HTTPException(404, "task not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    requested_is_current = all(getattr(task, key) == value for key, value in changes.items())
    if payload.expected_version is not None and payload.expected_version != task.version:
        if requested_is_current:
            return (await serialize_tasks(db, [task], detail))[0]
        raise HTTPException(409, "task version conflict")
    next_kind = changes.get("kind", task.kind)
    next_epic_id = changes.get("epic_id", task.epic_id)
    try:
        await validate_task_sprint(db, project_id, task=task, values=changes)
        await validate_task_hierarchy(
            db,
            project_id,
            kind=next_kind,
            epic_id=next_epic_id,
            task_id=task.id,
            converting_epic=task.kind == "epic" and next_kind == "task",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous_sprint_id = task.sprint_id
    changed = False
    for key, value in changes.items():
        if getattr(task, key) != value:
            setattr(task, key, value)
            changed = True
    if not changed:
        await db.commit()
        return (await serialize_tasks(db, [task], detail))[0]
    await advance_task_index_generation(db, project_id)
    await touch_task_sprints(db, project_id, previous_sprint_id, task.sprint_id)
    task.version += 1
    task.updated_at = utcnow()
    await db.flush()
    db.add(
        TaskRevision(
            project_id=project_id,
            task_id=task.id,
            version=task.version,
            snapshot=serialize_json(task),
            actor="agent",
            actor_member_id=principal.member_id,
            rationale=payload.rationale or "",
        )
    )
    enqueue_task_projection(db, task)
    await activity(db, project_id, "task.update", f"Task updated: {task.title}", {"id": task.id})
    await db.commit()
    return (await serialize_tasks(db, [task], detail))[0]


@app.post("/projects/{project_id}/tasks/{task_id}/attachments")
async def create_task_attachment(
    project_id: str,
    task_id: str,
    payload: ArtifactCreate,
    db: AsyncSession = Depends(get_session),
):
    task = await db.get(Task, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(404, "task not found")
    try:
        artifact, created, linked = await attach_artifact_to_task(db, task, payload)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if linked:
        await activity(
            db,
            project_id,
            "task.attachment.added",
            f"Attachment added to {task.title}: {artifact.filename or artifact.kind}",
            {"task_id": task.id, "artifact_id": artifact.id},
        )
    await db.commit()
    return {**serialize_artifact(artifact), "created": created, "linked": linked}


@app.get("/projects/{project_id}/tasks/{task_id}/attachments")
async def list_task_attachments(
    project_id: str,
    task_id: str,
    db: AsyncSession = Depends(get_session),
):
    task = await db.get(Task, task_id)
    if not task or task.project_id != project_id:
        raise HTTPException(404, "task not found")
    grouped = await task_artifacts(db, [task_id])
    return {"items": grouped[task_id]}


@app.get("/projects/{project_id}/tasks/{task_id}/attachments/{artifact_id}/content")
async def get_task_attachment_content(
    project_id: str,
    task_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_session),
):
    task = await db.get(Task, task_id)
    artifact = await db.get(Artifact, artifact_id)
    link = await db.get(TaskArtifact, (task_id, artifact_id))
    if (
        not task
        or task.project_id != project_id
        or not artifact
        or artifact.project_id != project_id
        or not link
    ):
        raise HTTPException(404, "task attachment not found")
    return artifact_content_response(artifact)


@app.delete("/projects/{project_id}/tasks/{task_id}/attachments/{artifact_id}")
async def delete_task_attachment(
    project_id: str,
    task_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_session),
):
    task = await db.get(Task, task_id)
    link = await db.get(TaskArtifact, (task_id, artifact_id))
    if not task or task.project_id != project_id or not link:
        raise HTTPException(404, "task attachment not found")
    await db.delete(link)
    await activity(
        db,
        project_id,
        "task.attachment.removed",
        f"Attachment removed from {task.title}",
        {"task_id": task.id, "artifact_id": artifact_id},
    )
    await db.commit()
    return Response(status_code=204)


@app.get("/projects/{project_id}/activity")
async def get_activity(
    project_id: str, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_session)
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    rows = (
        await db.scalars(
            select(Activity)
            .where(Activity.project_id == project_id)
            .order_by(desc(Activity.created_at))
            .limit(limit)
        )
    ).all()
    return {"items": [serialize(row) for row in rows]}
