from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from dduo_solo_founder.models import Task


TaskDetail = Literal["compact", "working", "full"]
COMPACT_TEXT_LIMIT = 360
COMPACT_TEXT_BYTES = 720
COMPACT_LABEL_LIMIT = 12
COMPACT_LABEL_CHARACTERS = 60
COMPACT_LABEL_BYTES = 120
WORKING_DESCRIPTION_LIMIT = 12_000
WORKING_DESCRIPTION_BYTES = 8_000
WORKING_RATIONALE_LIMIT = 3_000
WORKING_RATIONALE_BYTES = 2_000
WORKING_TEXT_LIMIT = 2_000
WORKING_TEXT_BYTES = 2_000
WORKING_DEPENDENCY_LIMIT = 20
WORKING_DEPENDENCY_BYTES = 256
WORKING_ATTACHMENT_LIMIT = 10


def _columns(task: Task) -> dict:
    return {
        attribute.columns[0].name: getattr(task, attribute.key)
        for attribute in task.__mapper__.column_attrs
    }


def _excerpt(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None or len(value) <= limit:
        return value, False
    if limit <= 1:
        return "…"[:limit], True
    head = max(1, (limit - 1) * 2 // 3)
    tail = max(0, limit - head - 1)
    return f"{value[:head]}…{value[-tail:] if tail else ''}", True


def _utf8_excerpt(value: str | None, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    marker = "…"
    marker_bytes = len(marker.encode("utf-8"))
    remaining = max(0, limit - marker_bytes)
    head_bytes = remaining * 2 // 3
    tail_bytes = remaining - head_bytes
    head = encoded[:head_bytes].decode("utf-8", errors="ignore")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore") if tail_bytes else ""
    return f"{head}{marker if limit >= marker_bytes else ''}{tail}", True


def _bounded_excerpt(
    value: str | None,
    *,
    characters: int,
    utf8_bytes: int,
) -> tuple[str | None, bool]:
    character_bounded, character_truncated = _excerpt(value, characters)
    byte_bounded, byte_truncated = _utf8_excerpt(character_bounded, utf8_bytes)
    return byte_bounded, character_truncated or byte_truncated


def _bounded_labels(labels: list | None) -> tuple[list[str], bool]:
    source = [str(item) for item in labels or []]
    values: list[str] = []
    truncated = len(source) > COMPACT_LABEL_LIMIT
    for item in source[:COMPACT_LABEL_LIMIT]:
        excerpt, shortened = _bounded_excerpt(
            item,
            characters=COMPACT_LABEL_CHARACTERS,
            utf8_bytes=COMPACT_LABEL_BYTES,
        )
        values.append(excerpt or "")
        truncated = truncated or shortened
    return values, truncated


def compact_task(task: Task) -> dict:
    """Return the deterministic, bounded task card used by agents and briefings."""
    objective, objective_truncated = _bounded_excerpt(
        task.objective,
        characters=COMPACT_TEXT_LIMIT,
        utf8_bytes=COMPACT_TEXT_BYTES,
    )
    next_action, next_action_truncated = _bounded_excerpt(
        task.next_action,
        characters=COMPACT_TEXT_LIMIT,
        utf8_bytes=COMPACT_TEXT_BYTES,
    )
    labels, labels_truncated = _bounded_labels(task.labels)
    truncated_fields = [
        name
        for name, truncated in (
            ("objective", objective_truncated),
            ("next_action", next_action_truncated),
            ("labels", labels_truncated),
        )
        if truncated
    ]
    result = {
        "id": task.id,
        "kind": task.kind,
        "epic_id": task.epic_id,
        "sprint_id": task.sprint_id,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "labels": labels,
        "objective": objective,
        "next_action": next_action,
        "due_at": task.due_at,
        "version": task.version,
        "updated_at": task.updated_at,
    }
    if labels_truncated:
        result["labels_total"] = len(task.labels or [])
    if truncated_fields:
        result["truncated_fields"] = truncated_fields
    return result


def compact_attachment(attachment: dict) -> dict:
    """Expose a useful reference without delivering extracted attachment text."""
    filename, filename_truncated = _bounded_excerpt(
        attachment.get("filename"), characters=200, utf8_bytes=200
    )
    source_uri, source_uri_truncated = _bounded_excerpt(
        attachment.get("source_uri"), characters=300, utf8_bytes=300
    )
    summary, summary_truncated = _bounded_excerpt(
        attachment.get("summary"), characters=250, utf8_bytes=250
    )
    result = {
        "id": attachment.get("id"),
        "kind": attachment.get("kind"),
        "filename": filename,
        "mime_type": attachment.get("mime_type"),
        "size_bytes": attachment.get("size_bytes"),
        "source_uri": source_uri,
        "summary": summary,
        "status": attachment.get("status"),
        "has_extracted_text": bool(
            attachment.get("has_extracted_text") or attachment.get("extracted_text")
        ),
        "created_at": attachment.get("created_at"),
    }
    truncated_fields = set(attachment.get("truncated_fields") or [])
    truncated_fields.update(
        name
        for name, truncated in (
            ("filename", filename_truncated),
            ("source_uri", source_uri_truncated),
            ("summary", summary_truncated),
        )
        if truncated
    )
    if truncated_fields:
        result["truncated_fields"] = sorted(truncated_fields)
    return result


def task_view(
    task: Task,
    detail: TaskDetail,
    attachments: list[dict] | None = None,
) -> dict:
    if detail == "compact":
        return compact_task(task)
    if detail == "working":
        description, description_truncated = _bounded_excerpt(
            task.description,
            characters=WORKING_DESCRIPTION_LIMIT,
            utf8_bytes=WORKING_DESCRIPTION_BYTES,
        )
        rationale, rationale_truncated = _bounded_excerpt(
            task.rationale,
            characters=WORKING_RATIONALE_LIMIT,
            utf8_bytes=WORKING_RATIONALE_BYTES,
        )
        objective, objective_truncated = _bounded_excerpt(
            task.objective,
            characters=WORKING_TEXT_LIMIT,
            utf8_bytes=WORKING_TEXT_BYTES,
        )
        next_action, next_action_truncated = _bounded_excerpt(
            task.next_action,
            characters=WORKING_TEXT_LIMIT,
            utf8_bytes=WORKING_TEXT_BYTES,
        )
        dependencies = [str(item) for item in (task.dependencies or [])]
        dependency_excerpts = [
            _bounded_excerpt(
                item,
                characters=200,
                utf8_bytes=WORKING_DEPENDENCY_BYTES,
            )
            for item in dependencies[:WORKING_DEPENDENCY_LIMIT]
        ]
        bounded_dependencies = [value or "" for value, _ in dependency_excerpts]
        attachment_total = int(getattr(attachments, "total", len(attachments or [])))
        bounded_attachments = list(attachments or [])[:WORKING_ATTACHMENT_LIMIT]
        compact = compact_task(task)
        truncated_fields = set(compact.get("truncated_fields", []))
        # The working representation replaces these compact excerpts with
        # larger values, so only the working limits determine whether they are
        # actually truncated in this response.
        truncated_fields.difference_update({"objective", "next_action"})
        compact.pop("truncated_fields", None)
        truncated_fields.update(
            name
            for name, truncated in (
                ("description", description_truncated),
                ("rationale", rationale_truncated),
                ("objective", objective_truncated),
                ("next_action", next_action_truncated),
                (
                    "dependencies",
                    len(dependencies) > WORKING_DEPENDENCY_LIMIT
                    or any(truncated for _, truncated in dependency_excerpts),
                ),
                ("attachments", attachment_total > WORKING_ATTACHMENT_LIMIT),
            )
            if truncated
        )
        result = {
            **compact,
            "objective": objective,
            "next_action": next_action,
            "description": description,
            "dependencies": bounded_dependencies,
            "rationale": rationale,
            "completion_evidence_available": bool(task.completion_evidence),
            "attachments": [compact_attachment(item) for item in bounded_attachments],
        }
        if len(dependencies) > WORKING_DEPENDENCY_LIMIT:
            result["dependencies_total"] = len(dependencies)
        if attachment_total > WORKING_ATTACHMENT_LIMIT:
            result["attachments_total"] = attachment_total
        if truncated_fields:
            result["truncated_fields"] = sorted(truncated_fields)
        return result
    if detail == "full":
        return {**_columns(task), "attachments": list(attachments or [])}
    raise ValueError(f"unsupported task detail: {detail}")


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")


def snapshot_hash(payload: object) -> str:
    """Hash the delivered representation, not merely Task.version.

    Attachment links can change without changing the task row. Hashing the exact
    response keeps the conditional MCP contract stateless and correct.
    """
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_response(payload: dict, known_snapshot_hash: str | None = None) -> dict:
    current = snapshot_hash(payload)
    if known_snapshot_hash and known_snapshot_hash == current:
        return {"unchanged": True, "snapshot_hash": current}
    return {**payload, "unchanged": False, "snapshot_hash": current}
