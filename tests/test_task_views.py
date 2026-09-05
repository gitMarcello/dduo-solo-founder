from __future__ import annotations

import pytest

from dduo_solo_founder.models import Task
from dduo_solo_founder.task_views import (
    _excerpt,
    _utf8_excerpt,
    compact_attachment,
    snapshot_hash,
    task_view,
)


def current_task() -> Task:
    return Task(
        id="11111111-1111-4111-8111-111111111111",
        project_id="p1",
        kind="task",
        title="Bounded working context",
        description="Description",
        status="in_progress",
        priority="high",
        labels=[],
        dependencies=[],
        version=1,
    )


def test_compact_attachment_bounds_references_and_never_returns_extracted_text():
    value = compact_attachment(
        {
            "id": "a1",
            "kind": "file",
            "filename": "f" * 500,
            "mime_type": "text/plain",
            "size_bytes": 123,
            "source_uri": "🚀" * 500,
            "summary": "s" * 500,
            "status": "ready",
            "extracted_text": "PROJECT_CONTENT_MUST_NOT_LEAK",
            "created_at": "2026-08-25T10:00:00+00:00",
        }
    )
    assert value["has_extracted_text"] is True
    assert "extracted_text" not in value
    assert len(value["filename"].encode("utf-8")) <= 200
    assert len(value["source_uri"].encode("utf-8")) <= 300
    assert len(value["summary"].encode("utf-8")) <= 250
    assert set(value["truncated_fields"]) == {"filename", "source_uri", "summary"}


def test_working_attachment_count_full_view_and_invalid_detail_are_explicit():
    task = current_task()
    attachments = [
        {"id": f"a{index}", "filename": f"file-{index}", "extracted_text": "private"}
        for index in range(11)
    ]
    working = task_view(task, "working", attachments)
    assert len(working["attachments"]) == 10
    assert working["attachments_total"] == 11
    assert "attachments" in working["truncated_fields"]
    assert all("extracted_text" not in item for item in working["attachments"])

    full = task_view(task, "full", attachments)
    assert full["attachments"] == attachments
    with pytest.raises(ValueError, match="unsupported task detail"):
        task_view(task, "unknown")


def test_working_truncation_flags_describe_the_delivered_working_values():
    task = current_task()
    task.objective = "o" * 500
    task.next_action = "n" * 500

    working = task_view(task, "working")
    assert working["objective"] == task.objective
    assert working["next_action"] == task.next_action
    assert "truncated_fields" not in working

    task.objective = "o" * 3_000
    truncated = task_view(task, "working")
    assert "objective" in truncated["truncated_fields"]
    assert len(truncated["objective"].encode("utf-8")) <= 2_000


def test_excerpt_edge_cases_and_snapshot_reject_unsupported_values():
    assert _excerpt("abc", 1) == ("…", True)
    assert _utf8_excerpt(None, 10) == (None, False)
    assert _utf8_excerpt("è", 10) == ("è", False)
    assert _utf8_excerpt("🚀", 2) == ("", True)
    with pytest.raises(TypeError, match="unsupported snapshot value"):
        snapshot_hash({"unsupported": object()})
