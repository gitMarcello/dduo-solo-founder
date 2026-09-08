#!/usr/bin/env python3
"""Measure dDuo task-delivery size without writing to a project service.

The input is a JSON response previously obtained from GET /projects/{id}/tasks.
Only aggregate measurements and a corpus hash are emitted; task content is not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from dduo_solo_founder.models import Task
from dduo_solo_founder.service import compact_task_briefing
from dduo_solo_founder.task_index import render_task_document
from dduo_solo_founder.task_views import task_view


def encoded_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


def hydrate(item: dict) -> Task:
    columns = {attribute.columns[0].name for attribute in Task.__mapper__.column_attrs}
    return Task(**{key: value for key, value in item.items() if key in columns})


def legacy_task_view(task: Task) -> dict:
    """Reproduce the pre-alpha.44 task serialization used by automatic briefing."""
    return {
        attribute.columns[0].name: getattr(task, attribute.key)
        for attribute in Task.__mapper__.column_attrs
    }


def briefing_order(task: Task) -> tuple:
    status_rank = {"in_progress": 0, "blocked": 1}.get(task.status, 2)
    priority_rank = {"critical": 0, "high": 1, "medium": 2}.get(task.priority, 3)
    updated = task.updated_at
    if isinstance(updated, str):
        try:
            updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            updated = None
    timestamp = updated.timestamp() if isinstance(updated, datetime) else 0.0
    return status_rank, priority_rank, -timestamp, task.id


def measure(source: bytes) -> dict:
    payload = json.loads(source)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("input must be a task-list JSON object with an items array")
    tasks = [hydrate(item) for item in items]
    compact_items = [task_view(task, "compact") for task in tasks]
    working_items = [
        task_view(task, "working", list(item.get("attachments") or []))
        for task, item in zip(tasks, items, strict=True)
    ]
    active = [task for task in tasks if task.status not in {"done", "cancelled"}]
    selected_briefing_tasks = sorted(active, key=briefing_order)[:12]
    legacy_briefing_items = [legacy_task_view(task) for task in selected_briefing_tasks]
    briefing_items = compact_task_briefing(selected_briefing_tasks)
    default_list_items = [
        task_view(task, "compact")
        for task in active
    ]

    legacy_default_list_bytes = encoded_size({"items": items})
    default_list_bytes = encoded_size({"items": default_list_items})
    compact_bytes = encoded_size({"items": compact_items})
    working_bytes = encoded_size({"items": working_items})
    legacy_briefing_bytes = encoded_size({"items": legacy_briefing_items})
    briefing_bytes = encoded_size({"items": briefing_items})
    card_sizes = [encoded_size(item) for item in compact_items]
    compact_all_reduction = (
        1 - (compact_bytes / legacy_default_list_bytes)
        if legacy_default_list_bytes
        else 0.0
    )
    default_list_reduction = (
        1 - (default_list_bytes / legacy_default_list_bytes)
        if legacy_default_list_bytes
        else 0.0
    )
    briefing_reduction = (
        1 - (briefing_bytes / legacy_briefing_bytes)
        if legacy_briefing_bytes
        else 0.0
    )
    return {
        "schema_version": "task-context-benchmark-v2",
        "corpus_sha256": hashlib.sha256(source).hexdigest(),
        "task_count": len(items),
        "active_task_count": len(active),
        "legacy_default_list": {
            "selected_tasks": len(items),
            "utf8_bytes": legacy_default_list_bytes,
            "estimated_tokens": math.ceil(legacy_default_list_bytes / 4),
        },
        "default_list": {
            "selected_tasks": len(default_list_items),
            "utf8_bytes": default_list_bytes,
            "estimated_tokens": math.ceil(default_list_bytes / 4),
            "reduction_fraction": round(default_list_reduction, 6),
            "reduction_percent": round(default_list_reduction * 100, 3),
        },
        "compact_all": {
            "utf8_bytes": compact_bytes,
            "estimated_tokens": math.ceil(compact_bytes / 4),
            "reduction_fraction": round(compact_all_reduction, 6),
            "reduction_percent": round(compact_all_reduction * 100, 3),
            "card_p50_bytes": percentile(card_sizes, 0.50),
            "card_p95_bytes": percentile(card_sizes, 0.95),
            "card_max_bytes": max(card_sizes, default=0),
        },
        "working_all": {
            "utf8_bytes": working_bytes,
            "estimated_tokens": math.ceil(working_bytes / 4),
        },
        "legacy_automatic_briefing": {
            "selected_tasks": len(legacy_briefing_items),
            "utf8_bytes": legacy_briefing_bytes,
            "estimated_tokens": math.ceil(legacy_briefing_bytes / 4),
        },
        "automatic_briefing": {
            "selected_tasks": len(briefing_items),
            "utf8_bytes": briefing_bytes,
            "estimated_tokens": math.ceil(briefing_bytes / 4),
            "reduction_fraction": round(briefing_reduction, 6),
            "reduction_percent": round(briefing_reduction * 100, 3),
        },
    }


def semantic_documents(source: bytes) -> dict:
    """Prepare a private, disposable corpus for the semantic recall runner."""
    payload = json.loads(source)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("input must be a task-list JSON object with an items array")
    documents = []
    for item in items:
        task = hydrate(item)
        document = render_task_document(task)
        documents.append(
            {
                "task_id": task.id,
                "status": task.status,
                "text": document.text,
                "semantic_hash": document.semantic_hash,
            }
        )
    return {"schema_version": "task-semantic-corpus-v1", "documents": documents}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--semantic-documents-output", type=Path)
    parser.add_argument("--require-reduction", type=float, default=None)
    args = parser.parse_args()
    source = args.dataset.read_bytes()
    result = measure(source)
    if (
        args.require_reduction is not None
        and result["automatic_briefing"]["reduction_fraction"] < args.require_reduction
    ):
        raise SystemExit(
            "compact reduction below gate: "
            f"{result['automatic_briefing']['reduction_fraction']:.3f} "
            f"< {args.require_reduction:.3f}"
        )
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if args.semantic_documents_output:
        args.semantic_documents_output.write_text(
            json.dumps(semantic_documents(source), ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
