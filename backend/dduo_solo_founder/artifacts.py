from __future__ import annotations

import base64
import binascii
import hashlib

from sqlalchemy import case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from dduo_solo_founder.config import get_settings
from dduo_solo_founder.link_privacy import contains_dashboard_access_tokens
from dduo_solo_founder.models import (
    Artifact,
    Plan,
    PlanArtifact,
    Task,
    TaskArtifact,
    Turn,
    TurnArtifact,
)
from dduo_solo_founder.schemas import ArtifactCreate


class AttachmentRows(list[dict]):
    """A bounded attachment page with its exact authoritative total."""

    def __init__(self, values=(), *, total: int = 0):
        super().__init__(values)
        self.total = total


def _sql_middle_excerpt(column, limit: int):
    head = max(1, (limit - 1) * 2 // 3)
    tail = max(0, limit - head - 1)
    shortened = func.substr(column, 1, head) + literal("…")
    if tail:
        # Negative starts differ between SQLite and PostgreSQL. A positive,
        # length-derived start preserves the same middle excerpt on both.
        shortened += func.substr(column, func.length(column) - tail + 1, tail)
    return case((func.length(column) > limit, shortened), else_=column)


def _decode_content(payload: ArtifactCreate) -> bytes | None:
    if payload.content_base64 is None:
        return None
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("artifact content is not valid base64") from exc
    if len(content) > get_settings().artifact_max_bytes:
        raise ValueError("artifact exceeds the configured size limit")
    return content


def _content_hash(payload: ArtifactCreate, content: bytes | None) -> str:
    text_parts = [payload.source_uri, payload.filename, payload.extracted_text, payload.summary]
    if content is None and not any(text_parts):
        raise ValueError("artifact requires content, a source URI, extracted text, or a summary")
    material = content or "\0".join(text_parts).encode()
    return hashlib.sha256(material).hexdigest()


async def register_artifact(
    db: AsyncSession, project_id: str, payload: ArtifactCreate
) -> tuple[Artifact, bool]:
    content = _decode_content(payload)
    textual_fields = {
        "filename": payload.filename,
        "source_uri": payload.source_uri,
        "extracted_text": payload.extracted_text,
        "summary": payload.summary,
        "metadata": payload.metadata,
    }
    # Inspect the textual copy without altering original attachment bytes or
    # their content hash. This is not a claim to sanitize images/binary formats.
    content_text = ""
    if content is not None:
        encoding = (
            "utf-32" if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"))
            else "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff"))
            else "utf-8"
        )
        try:
            content_text = content.decode(encoding)
        except UnicodeDecodeError:
            pass
    if contains_dashboard_access_tokens(textual_fields) or contains_dashboard_access_tokens(content_text):
        raise ValueError(
            "artifact contains a private dashboard access link; remove its access_token "
            "and register the ordinary project/task link instead"
        )
    digest = _content_hash(payload, content)
    artifact = await db.scalar(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.content_hash == digest,
        )
    )
    created = artifact is None
    if artifact is None:
        artifact = Artifact(
            project_id=project_id,
            content_hash=digest,
            kind=payload.kind,
            filename=payload.filename,
            mime_type=payload.mime_type,
            size_bytes=len(content or b""),
            source_uri=payload.source_uri,
            content=content,
            extracted_text=payload.extracted_text,
            summary=payload.summary,
            metadata_json=payload.metadata,
        )
        db.add(artifact)
        await db.flush()
    if payload.turn_id:
        turn = await db.get(Turn, payload.turn_id)
        if not turn or turn.project_id != project_id:
            raise LookupError("turn not found")
        existing = await db.get(TurnArtifact, (turn.id, artifact.id))
        if not existing:
            db.add(TurnArtifact(turn_id=turn.id, artifact_id=artifact.id))
    return artifact, created


async def attach_artifact_to_task(
    db: AsyncSession,
    task: Task,
    payload: ArtifactCreate,
) -> tuple[Artifact, bool, bool]:
    """Register content once and idempotently link it to a project task."""
    artifact, created = await register_artifact(db, task.project_id, payload)
    link = await db.get(TaskArtifact, (task.id, artifact.id))
    linked = link is None
    if linked:
        db.add(TaskArtifact(task_id=task.id, artifact_id=artifact.id))
    return artifact, created, linked


async def attach_artifact_to_plan(
    db: AsyncSession,
    plan: Plan,
    payload: ArtifactCreate,
) -> tuple[Artifact, bool, bool]:
    """Register content once and idempotently link it to a project plan."""
    artifact, created = await register_artifact(db, plan.project_id, payload)
    link = await db.get(PlanArtifact, (plan.id, artifact.id))
    linked = link is None
    if linked:
        db.add(PlanArtifact(plan_id=plan.id, artifact_id=artifact.id))
    return artifact, created, linked


async def task_artifacts(
    db: AsyncSession,
    task_ids: list[str],
    *,
    include_extracted_text: bool = True,
    limit_per_task: int | None = None,
) -> dict[str, list[dict]]:
    """Return attachment metadata grouped by task without loading binary content."""
    grouped = {task_id: AttachmentRows() for task_id in task_ids}
    if not task_ids:
        return grouped
    if not include_extracted_text:
        limit = max(1, limit_per_task or 10)
        for task_id in task_ids:
            rows = (
                await db.execute(
                    select(
                        Artifact.id.label("id"),
                        Artifact.kind.label("kind"),
                        _sql_middle_excerpt(Artifact.filename, 200).label("filename"),
                        Artifact.mime_type.label("mime_type"),
                        Artifact.size_bytes.label("size_bytes"),
                        _sql_middle_excerpt(Artifact.source_uri, 300).label("source_uri"),
                        _sql_middle_excerpt(Artifact.summary, 250).label("summary"),
                        Artifact.status.label("status"),
                        (Artifact.extracted_text != "").label("has_extracted_text"),
                        Artifact.created_at.label("created_at"),
                        (func.length(Artifact.filename) > 200).label(
                            "filename_truncated"
                        ),
                        (func.length(Artifact.source_uri) > 300).label(
                            "source_uri_truncated"
                        ),
                        (func.length(Artifact.summary) > 250).label("summary_truncated"),
                        func.count().over().label("attachment_total"),
                    )
                    .join(TaskArtifact, TaskArtifact.artifact_id == Artifact.id)
                    .where(TaskArtifact.task_id == task_id)
                    .order_by(TaskArtifact.created_at, TaskArtifact.artifact_id)
                    .limit(limit)
                )
            ).mappings().all()
            target = grouped[task_id]
            target.total = int(rows[0]["attachment_total"]) if rows else 0
            for row in rows:
                truncated_fields = [
                    name
                    for name in ("filename", "source_uri", "summary")
                    if row[f"{name}_truncated"]
                ]
                target.append(
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "kind",
                            "filename",
                            "mime_type",
                            "size_bytes",
                            "source_uri",
                            "summary",
                            "status",
                            "has_extracted_text",
                            "created_at",
                        )
                    }
                )
                if truncated_fields:
                    target[-1]["truncated_fields"] = truncated_fields
        return grouped
    metadata_columns = [
        Artifact.id,
        Artifact.project_id,
        Artifact.content_hash,
        Artifact.kind,
        Artifact.filename,
        Artifact.mime_type,
        Artifact.size_bytes,
        Artifact.source_uri,
        Artifact.summary,
        Artifact.status,
        Artifact.metadata_json,
        Artifact.created_at,
    ]
    metadata_columns.append(Artifact.extracted_text)
    rows = (
        await db.execute(
            select(
                TaskArtifact.task_id,
                Artifact,
                (Artifact.extracted_text != "").label("has_extracted_text"),
            )
            .options(load_only(*metadata_columns))
            .join(Artifact, Artifact.id == TaskArtifact.artifact_id)
            .where(TaskArtifact.task_id.in_(task_ids))
            .order_by(TaskArtifact.created_at, TaskArtifact.artifact_id)
        )
    ).all()
    for task_id, artifact, has_extracted_text in rows:
        grouped[task_id].append(
            serialize_artifact(
                artifact,
                include_extracted_text=True,
                has_extracted_text=bool(has_extracted_text),
            )
        )
    return grouped


async def plan_artifacts(
    db: AsyncSession,
    plan_ids: list[str],
) -> dict[str, list[dict]]:
    """Return plan attachment metadata without loading stored binary content."""
    grouped = {plan_id: [] for plan_id in plan_ids}
    if not plan_ids:
        return grouped
    rows = (
        await db.execute(
            select(PlanArtifact.plan_id, Artifact)
            .options(
                load_only(
                    Artifact.id,
                    Artifact.project_id,
                    Artifact.content_hash,
                    Artifact.kind,
                    Artifact.filename,
                    Artifact.mime_type,
                    Artifact.size_bytes,
                    Artifact.source_uri,
                    Artifact.extracted_text,
                    Artifact.summary,
                    Artifact.status,
                    Artifact.metadata_json,
                    Artifact.created_at,
                )
            )
            .join(Artifact, Artifact.id == PlanArtifact.artifact_id)
            .where(PlanArtifact.plan_id.in_(plan_ids))
            .order_by(PlanArtifact.created_at, PlanArtifact.artifact_id)
        )
    ).all()
    for plan_id, artifact in rows:
        grouped[plan_id].append(serialize_artifact(artifact))
    return grouped


def serialize_artifact(
    artifact: Artifact,
    *,
    include_extracted_text: bool = True,
    has_extracted_text: bool | None = None,
) -> dict:
    result = {
        "id": artifact.id,
        "project_id": artifact.project_id,
        "content_hash": artifact.content_hash,
        "kind": artifact.kind,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "source_uri": artifact.source_uri,
        "summary": artifact.summary,
        "status": artifact.status,
        "metadata": artifact.metadata_json,
        "created_at": artifact.created_at,
    }
    if include_extracted_text:
        result["extracted_text"] = artifact.extracted_text
    else:
        result["has_extracted_text"] = bool(has_extracted_text)
    return result
