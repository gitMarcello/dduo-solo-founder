from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from conftest import project_payload
from dduo_solo_founder import __version__
from dduo_solo_founder import main
from dduo_solo_founder.backup import BackupError
from dduo_solo_founder.backup_service import BackupResult
from dduo_solo_founder.embeddings import VectorOperationError
from dduo_solo_founder.models import (
    Activity,
    BackupRecord,
    Memory,
    ObservabilityEvent,
    OutboxEvent,
    Plan,
    PlanRevision,
    PlanWorkItem,
    Project,
    RawEvent,
    SleepJob,
    Task,
    TaskRevision,
    Turn,
)
from dduo_solo_founder.task_index import (
    TASK_INDEX_RENDER_VERSION,
    render_task_document,
    task_index_payload,
)


_FAKE_TASK_MARKERS: dict[str, str] = {}
_FAKE_TASK_COUNTS: dict[str, int] = {}


class TaskMarkerMixin:
    def write_marker(self, project_id: str, epoch: str, task_count: int) -> None:
        _FAKE_TASK_MARKERS[project_id] = epoch
        _FAKE_TASK_COUNTS[project_id] = task_count

    def marker_matches(self, project_id: str, epoch: str | None) -> bool:
        return bool(epoch) and _FAKE_TASK_MARKERS.get(project_id) == epoch

    def projection_count(self, project_id: str) -> int | None:
        return _FAKE_TASK_COUNTS.get(project_id)

    def integrity_snapshot(self, project_id: str) -> tuple[str, int] | None:
        epoch = _FAKE_TASK_MARKERS.get(project_id)
        count = _FAKE_TASK_COUNTS.get(project_id)
        return (epoch, count) if epoch is not None and count is not None else None


async def create_project(client, **overrides):
    payload = project_payload(**overrides)
    response = await client.post("/projects", json=payload)
    assert response.status_code == 200
    return payload, response.json()


async def create_turn(client, project_id: str, external_id: str = "turn-1"):
    session = (
        await client.post(
            f"/projects/{project_id}/sessions",
            json={"client": "codex", "external_id": "session-1"},
        )
    ).json()
    response = await client.post(
        f"/projects/{project_id}/turns/begin",
        json={"session_id": session["id"], "external_id": external_id, "user_prompt": "Ship?"},
    )
    assert response.status_code == 200
    return session, response.json()


async def test_current_task_index_state_reads_one_authoritative_snapshot(db_factory):
    async with db_factory() as db:
        db.add(
            Project(
                id="state-project",
                name="State",
                root_path="/tmp/state-project",
                task_index_reconciled=True,
                task_index_generation=7,
                task_index_epoch="11111111-1111-4111-8111-111111111111",
            )
        )
        await db.commit()
        assert await main.current_task_index_state(db, "state-project") == (
            True,
            7,
            "11111111-1111-4111-8111-111111111111",
        )
        with pytest.raises(LookupError, match="project not found"):
            await main.current_task_index_state(db, "missing")


async def test_health_project_profile_and_briefing(api_client):
    client, _ = api_client
    health = (await client.get("/health")).json()
    assert health["status"] == "ok" and health["embedding_provider"] == "openai"
    assert health["version"] == __version__
    assert health["task_embedding_index_version"] == "v1"
    assert health["task_index_max_utf8_bytes"] == 7_500
    assert (await client.get(f"/projects/{uuid.uuid4()}/briefing")).status_code == 404

    payload, project = await create_project(client)
    duplicate = (await client.post("/projects", json=payload)).json()
    assert duplicate["id"] == project["id"]
    briefing = (await client.get(f"/projects/{project['id']}/briefing")).json()
    assert briefing["project"]["cause"] == payload["cause"]

    assert (
        await client.patch(f"/projects/{uuid.uuid4()}", json={"cause": "none"})
    ).status_code == 404
    assert (
        await client.patch(
            f"/projects/{project['id']}", json={"cause": "new", "expected_version": 99}
        )
    ).status_code == 409
    updated = (
        await client.patch(
            f"/projects/{project['id']}",
            json={"cause": "new", "rationale": "Sharper", "expected_version": 1},
        )
    ).json()
    assert updated["cause"] == "new" and updated["profile_version"] == 2


async def test_sessions_turn_retrieval_idempotency_and_failure(api_client, db_factory):
    client, embeddings = api_client
    missing = str(uuid.uuid4())
    assert (
        await client.post(
            f"/projects/{missing}/sessions", json={"client": "codex", "external_id": "s"}
        )
    ).status_code == 404
    payload, _ = await create_project(client)
    session, context = await create_turn(client, payload["id"])
    assert context["retrieval_available"] and context["retrieval_empty"]
    repeated, repeated_context = await create_turn(client, payload["id"])
    assert repeated["id"] == session["id"]
    assert repeated_context["turn"]["id"] == context["turn"]["id"]

    wrong_session = str(uuid.uuid4())
    bad = await client.post(
        f"/projects/{payload['id']}/turns/begin",
        json={"session_id": wrong_session, "external_id": "x", "user_prompt": "x"},
    )
    assert bad.status_code == 404
    async with db_factory() as db:
        memory = Memory(
            project_id=payload["id"], node_type="reusable_fact", node_key="release", text="Relevant"
        )
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
    embeddings.search_results = [{"id": memory.id, "score": 0.9}]
    _, found = await create_turn(client, payload["id"], "turn-2")
    assert found["memories"][0]["text"] == "Relevant" and not found["retrieval_empty"]
    embeddings.search_error = RuntimeError("offline")
    _, degraded = await create_turn(client, payload["id"], "turn-3")
    assert degraded["retrieval_available"] is False


async def test_prompt_fragments_are_ordered_idempotent_and_extend_one_turn(
    api_client, db_factory
):
    client, embeddings = api_client
    payload, _ = await create_project(client)
    session = (
        await client.post(
            f"/projects/{payload['id']}/sessions",
            json={"client": "codex", "external_id": "steered-session"},
        )
    ).json()
    async with db_factory() as db:
        first_memory = Memory(
            project_id=payload["id"],
            node_type="reusable_fact",
            node_key="first-fragment",
            text="The first prompt needs this decision.",
        )
        second_memory = Memory(
            project_id=payload["id"],
            node_type="heuristic",
            node_key="second-fragment",
            text="The steering prompt adds this constraint.",
        )
        db.add_all([first_memory, second_memory])
        await db.commit()
        await db.refresh(first_memory)
        await db.refresh(second_memory)

    queries: list[str] = []
    original_search = embeddings.search

    def tracked_search(project_id, query, limit):
        queries.append(query)
        return original_search(project_id, query, limit)

    embeddings.search = tracked_search
    embeddings.search_results = [{"id": first_memory.id, "score": 0.92}]
    first_body = {
        "session_id": session["id"],
        "external_id": "codex-turn",
        "prompt_event_id": "message-one",
        "user_prompt": "Prepare the release",
    }
    first = await client.post(f"/projects/{payload['id']}/turns/begin", json=first_body)
    assert first.status_code == 200
    first_context = first.json()

    replay = await client.post(f"/projects/{payload['id']}/turns/begin", json=first_body)
    assert replay.status_code == 200
    assert replay.json()["retrieval"]["metadata"]["replayed"] is True
    assert queries == ["Prepare the release"]

    conflict = await client.post(
        f"/projects/{payload['id']}/turns/begin",
        json={**first_body, "user_prompt": "A conflicting replay"},
    )
    assert conflict.status_code == 409
    assert queries == ["Prepare the release"]

    embeddings.search_results = [{"id": second_memory.id, "score": 0.94}]
    second_body = {
        **first_body,
        "prompt_event_id": "message-two",
        "user_prompt": "Also preserve the production database",
    }
    second = await client.post(f"/projects/{payload['id']}/turns/begin", json=second_body)
    assert second.status_code == 200
    second_context = second.json()
    assert second_context["turn"]["id"] == first_context["turn"]["id"]
    assert second_context["turn"]["user_prompt"] == (
        "Prepare the release\n\nAlso preserve the production database"
    )
    assert second_context["turn"]["retrieved_memory_ids"] == [
        first_memory.id,
        second_memory.id,
    ]
    assert queries[-1] == "Prepare the release\n\nAlso preserve the production database"

    second_replay = await client.post(
        f"/projects/{payload['id']}/turns/begin", json=second_body
    )
    assert second_replay.status_code == 200
    assert len(queries) == 2

    committed = await client.post(
        f"/turns/{first_context['turn']['id']}/commit",
        json={
            "assistant_response": "Done",
            "used_memory_ids": [first_memory.id, second_memory.id],
        },
    )
    assert committed.status_code == 200
    committed_replay = await client.post(
        f"/projects/{payload['id']}/turns/begin", json=second_body
    )
    assert committed_replay.status_code == 200
    assert len(queries) == 2
    late = await client.post(
        f"/projects/{payload['id']}/turns/begin",
        json={
            **first_body,
            "prompt_event_id": "message-three",
            "user_prompt": "Too late",
        },
    )
    assert late.status_code == 409

    async with db_factory() as db:
        events = list(
            (
                await db.scalars(
                    select(RawEvent)
                    .where(
                        RawEvent.turn_id == first_context["turn"]["id"],
                        RawEvent.event_type == "user_prompt",
                    )
                    .order_by(RawEvent.created_at, RawEvent.id)
                )
            ).all()
        )
        assert [event.payload["prompt_event_id"] for event in events] == [
            "message-one",
            "message-two",
        ]
        assert all(event.payload.get("retrieval_run_id") for event in events)


async def test_new_session_resumes_recoverable_sleep_jobs(api_client, db_factory):
    client, _ = api_client
    project, _ = await create_project(client)
    session, context = await create_turn(client, project["id"])
    async with db_factory() as db:
        turn = await db.get(Turn, context["turn"]["id"])
        turn.committed = True
        turn.sleep_status = "pending"
        waiting = SleepJob(
            project_id=project["id"],
            session_id=session["id"],
            provider="codex",
            trigger="idle",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[turn.id],
            last_error="Codex CLI unavailable",
        )
        db.add(waiting)
        await db.commit()
        job_id = waiting.id

    started = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "claude", "external_id": "new-chat"},
        )
    ).json()
    assert started["sleep_recovery_scheduled"] == 1
    async with db_factory() as db:
        recovered = await db.get(SleepJob, job_id)
        assert recovered.status == "pending"
        assert recovered.last_error is None


async def test_commit_stop_protocol_and_events(api_client, db_factory):
    client, _ = api_client
    project, _ = await create_project(client)
    session, context = await create_turn(client, project["id"])
    turn_id = context["turn"]["id"]
    assert (
        await client.post(f"/turns/{uuid.uuid4()}/commit", json={"assistant_response": "x"})
    ).status_code == 404
    assert (await client.post(f"/turns/{uuid.uuid4()}/stop-check", json={})).status_code == 404
    first = (
        await client.post(
            f"/turns/{turn_id}/stop-check",
            json={"assistant_response": "Answer", "used_memory_ids": ["invented-memory"]},
        )
    ).json()
    assert first == {
        "allow": True,
        "committed": True,
        "degraded": False,
        "discarded_memory_ids": ["invented-memory"],
    }
    second = (await client.post(f"/turns/{turn_id}/stop-check", json={})).json()
    assert second == {"allow": True, "committed": True, "degraded": False}

    _, new_context = await create_turn(client, project["id"], "commit-turn")
    committed = (
        await client.post(
            f"/turns/{new_context['turn']['id']}/commit",
            json={
                "assistant_response": "Done",
                "receipt": "r1",
                "task_actions": [{"action": "create", "title": "Release", "priority": "high"}],
            },
        )
    ).json()
    assert committed["committed"] and committed["receipt"] == "r1"
    again = await client.post(
        f"/turns/{new_context['turn']['id']}/commit", json={"assistant_response": "ignored"}
    )
    assert again.json()["idempotent"] is True
    assert (await client.post(f"/turns/{new_context['turn']['id']}/stop-check", json={})).json()[
        "committed"
    ]

    event = await client.post(
        f"/projects/{project['id']}/events",
        json={"session_id": session["id"], "event_type": "tool_result", "payload": {"ok": True}},
    )
    assert event.status_code == 200
    for body in (
        {"session_id": str(uuid.uuid4()), "event_type": "system"},
        {"turn_id": str(uuid.uuid4()), "event_type": "system"},
    ):
        assert (
            await client.post(f"/projects/{project['id']}/events", json=body)
        ).status_code == 404
    assert (
        await client.post(f"/projects/{uuid.uuid4()}/events", json={"event_type": "system"})
    ).status_code == 404

    async with db_factory() as db:
        assert await db.scalar(select(func.count(RawEvent.id))) >= 5


async def test_compaction_checkpoint_is_durable_searchable_and_idempotent(api_client, db_factory):
    client, _ = api_client
    project, _ = await create_project(client)
    session, context = await create_turn(client, project["id"])
    await client.post(
        f"/turns/{context['turn']['id']}/commit", json={"assistant_response": "Captured"}
    )
    endpoint = f"/projects/{project['id']}/compactions"

    pre = (
        await client.post(
            endpoint,
            json={"session_id": session["id"], "phase": "pre", "trigger": "auto"},
        )
    ).json()
    assert pre["recorded"] and not pre["summary_saved"]

    payload = {
        "session_id": session["id"],
        "phase": "post",
        "trigger": "auto",
        "summary": "Objective: test Android. Next: run the release flow as a real user.",
    }
    first = (await client.post(endpoint, json=payload)).json()
    repeated = (await client.post(endpoint, json=payload)).json()
    assert first["summary_saved"] and not first["idempotent"]
    assert repeated["idempotent"] and repeated["sleep_job_id"] == first["sleep_job_id"]

    briefing = (await client.get(f"/projects/{project['id']}/briefing")).json()
    assert briefing["recent_handoffs"][0]["summary"] == payload["summary"]
    assert (
        await client.post(
            endpoint,
            json={"session_id": str(uuid.uuid4()), "phase": "post", "summary": "x"},
        )
    ).status_code == 404

    async with db_factory() as db:
        assert await db.scalar(select(func.count(Memory.id))) == 0
        assert await db.scalar(select(func.count(OutboxEvent.id))) == 0
        assert await db.scalar(select(func.count(SleepJob.id))) == 1
        assert (
            await db.scalar(
                select(func.count(RawEvent.id)).where(
                    RawEvent.event_type.in_(["compaction_pre", "compaction_post"])
                )
            )
            == 2
        )


async def test_tasks_activity_search_and_reindex(api_client, db_factory):
    client, embeddings = api_client
    missing = str(uuid.uuid4())
    assert (await client.get(f"/projects/{missing}/tasks")).status_code == 404
    assert (await client.get(f"/projects/{missing}/activity")).status_code == 404
    assert (await client.get(f"/projects/{missing}/memories/search?q=x")).status_code == 404
    assert (await client.post(f"/projects/{missing}/tasks", json={"title": "x"})).status_code == 404
    assert (await client.post(f"/projects/{missing}/memories/reindex")).status_code == 404

    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Android release", "priority": "critical", "labels": ["mobile"]},
        )
    ).json()
    listed = (await client.get(f"/projects/{project['id']}/tasks")).json()["items"]
    assert listed[0]["id"] == task["id"]
    assert (
        await client.patch(
            f"/projects/{project['id']}/tasks/{task['id']}",
            json={"status": "done", "expected_version": 99},
        )
    ).status_code == 409
    updated = (
        await client.patch(
            f"/projects/{project['id']}/tasks/{task['id']}",
            json={"status": "done", "completion_evidence": "Store ready", "expected_version": 1},
        )
    ).json()
    assert updated["status"] == "done" and updated["version"] == 2
    assert (
        await client.patch(
            f"/projects/{project['id']}/tasks/{uuid.uuid4()}", json={"status": "done"}
        )
    ).status_code == 404
    assert (await client.get(f"/projects/{project['id']}/activity")).json()["items"]

    async with db_factory() as db:
        active_memory = Memory(
            project_id=project["id"], node_type="episode", node_key="a", text="A"
        )
        db.add(active_memory)
        db.add(
            Memory(
                project_id=project["id"],
                node_type="episode",
                node_key="b",
                text="B",
                status="inactive",
            )
        )
        await db.commit()
        await db.refresh(active_memory)
    embeddings.search_results = [{"id": active_memory.id, "score": 0.9}]
    assert (await client.get(f"/projects/{project['id']}/memories/search?q=release")).json()[
        "items"
    ]
    reindex = (await client.post(f"/projects/{project['id']}/memories/reindex")).json()
    assert reindex["queued"] == 1 and reindex["collection"].startswith("test_")
    async with db_factory() as db:
        assert (
            await db.scalar(
                select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "memory.upsert")
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "task.upsert")
            )
            == 2
        )
        assert await db.scalar(select(func.count(Task.id))) == 1
        assert await db.scalar(select(func.count(Activity.id))) >= 3


async def test_task_list_views_preserve_full_default_without_an_implicit_limit_and_filter(
    api_client, db_factory
):
    client, _ = api_client
    project, _ = await create_project(client)
    async with db_factory() as db:
        epic = Task(
            project_id=project["id"],
            kind="epic",
            title="Programma release",
            description="Descrizione completa dell'epica",
            completion_evidence="Evidenza completa",
            status="in_progress",
            priority="critical",
            labels=["release"],
        )
        db.add(epic)
        await db.flush()
        rows = [epic]
        for index in range(24):
            rows.append(
                Task(
                    project_id=project["id"],
                    kind="task",
                    epic_id=epic.id if index == 0 else None,
                    title=f"Task {index:02d}",
                    description=f"Descrizione {index}",
                    completion_evidence=f"Evidenza {index}",
                    status="done" if index == 1 else "todo",
                    priority="high" if index == 0 else "medium",
                    labels=["needle"] if index == 0 else ["ordinary"],
                )
            )
        db.add_all(rows[1:])
        await db.commit()
        epic_id = epic.id
        child_id = rows[1].id

    full_response = (await client.get(f"/projects/{project['id']}/tasks")).json()
    assert full_response["detail"] == "full"
    assert full_response["scope"] == "all"
    assert full_response["total"] == 25
    assert len(full_response["items"]) == 25
    assert full_response["unchanged"] is False
    full_epic = next(item for item in full_response["items"] if item["id"] == epic_id)
    assert full_epic["description"] == "Descrizione completa dell'epica"
    assert full_epic["completion_evidence"] == "Evidenza completa"
    assert full_epic["attachments"] == []

    compact_response = (await client.get(f"/projects/{project['id']}/tasks?detail=compact")).json()
    assert compact_response["detail"] == "compact"
    assert compact_response["total"] == 25
    assert len(compact_response["items"]) == 25
    for item in compact_response["items"]:
        assert "description" not in item
        assert "completion_evidence" not in item
        assert "attachments" not in item
        assert "dependencies" not in item
        assert "rationale" not in item

    filtered = (
        await client.get(
            f"/projects/{project['id']}/tasks",
            params={"detail": "compact", "status": "todo", "kind": "task", "label": "needle"},
        )
    ).json()
    assert [item["id"] for item in filtered["items"]] == [child_id]
    by_epic = (
        await client.get(
            f"/projects/{project['id']}/tasks",
            params={"detail": "compact", "epic_id": epic_id},
        )
    ).json()
    assert [item["id"] for item in by_epic["items"]] == [child_id]
    assert (await client.get(f"/projects/{project['id']}/tasks", params={"kind": "epic"})).json()[
        "total"
    ] == 1
    assert (await client.get(f"/projects/{project['id']}/tasks", params={"status": "done"})).json()[
        "total"
    ] == 1
    assert (
        await client.get(f"/projects/{project['id']}/tasks", params={"scope": "active"})
    ).json()["total"] == 24
    assert (
        await client.get(f"/projects/{project['id']}/tasks", params={"scope": "completed"})
    ).json()["total"] == 1
    assert (
        await client.get(
            f"/projects/{project['id']}/tasks",
            params={"scope": "active", "status": "done"},
        )
    ).json()["total"] == 1


async def test_get_task_working_full_and_stateless_snapshot_contract(api_client):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Preparare la release",
                "description": "Sequenza operativa completa",
                "dependencies": ["approvazione"],
                "rationale": "Ridurre il rischio",
            },
        )
    ).json()
    await client.patch(
        f"/projects/{project['id']}/tasks/{task['id']}",
        json={"completion_evidence": "Build firmata", "expected_version": 1},
    )

    working = (await client.get(f"/projects/{project['id']}/tasks/{task['id']}")).json()
    assert working["detail"] == "working"
    assert working["task"]["description"] == "Sequenza operativa completa"
    assert working["task"]["dependencies"] == ["approvazione"]
    assert working["task"]["rationale"] == "Ridurre il rischio"
    assert working["task"]["completion_evidence_available"] is True
    assert "completion_evidence" not in working["task"]
    assert working["task"]["attachments"] == []
    assert len(working["snapshot_hash"]) == 64 and working["unchanged"] is False

    unchanged = (
        await client.get(
            f"/projects/{project['id']}/tasks/{task['id']}",
            params={"known_snapshot_hash": working["snapshot_hash"]},
        )
    ).json()
    assert unchanged == {"unchanged": True, "snapshot_hash": working["snapshot_hash"]}

    full = (
        await client.get(f"/projects/{project['id']}/tasks/{task['id']}", params={"detail": "full"})
    ).json()
    assert full["detail"] == "full"
    assert full["task"]["completion_evidence"] == "Build firmata"
    assert full["snapshot_hash"] != working["snapshot_hash"]

    listed = (
        await client.get(f"/projects/{project['id']}/tasks", params={"detail": "compact"})
    ).json()
    repeated_list = (
        await client.get(
            f"/projects/{project['id']}/tasks",
            params={"detail": "compact", "known_snapshot_hash": listed["snapshot_hash"]},
        )
    ).json()
    assert repeated_list == {"unchanged": True, "snapshot_hash": listed["snapshot_hash"]}


async def test_task_views_are_bounded_and_compact_mutations_do_not_echo_full_content(api_client):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            params={"detail": "compact"},
            json={
                "title": "Contesto task limitato",
                "objective": "漢" * 5_000,
                "next_action": "🚀" * 5_000,
                "description": "d" * 30_000,
                "rationale": "r" * 10_000,
                "labels": [f"label-{index}-" + "x" * 100 for index in range(30)],
                "dependencies": ["dependency-" + "y" * 500 for _ in range(60)],
                "completion_evidence": "e" * 20_000,
            },
        )
    ).json()
    assert "description" not in created
    assert "completion_evidence" not in created
    assert "project_id" not in created
    assert "created_at" not in created and "updated_at" in created
    assert len(created["objective"]) <= 360
    assert len(created["next_action"]) <= 360
    assert len(created["objective"].encode("utf-8")) <= 720
    assert len(created["next_action"].encode("utf-8")) <= 720
    assert len(created["labels"]) == 12
    assert set(created["truncated_fields"]) == {"objective", "next_action", "labels"}

    working = (await client.get(f"/projects/{project['id']}/tasks/{created['id']}")).json()[
        "task"
    ]
    assert len(working["description"]) <= 12_000
    assert len(working["description"].encode("utf-8")) <= 8_000
    assert len(working["rationale"]) <= 3_000
    assert len(working["rationale"].encode("utf-8")) <= 2_000
    assert len(working["dependencies"]) == 20
    assert all(len(item) <= 200 for item in working["dependencies"])
    assert working["dependencies_total"] == 60
    assert {"description", "rationale", "dependencies"}.issubset(
        working["truncated_fields"]
    )
    assert "completion_evidence" not in working


async def test_task_mutations_enqueue_versions_and_noop_patch_is_truly_noop(api_client, db_factory):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Indicizzare i task", "priority": "high"},
        )
    ).json()

    async with db_factory() as db:
        assert await db.scalar(select(func.count(TaskRevision.id))) == 1
        events = list(
            (
                await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "task.upsert"))
            ).all()
        )
        assert len(events) == 1
        assert events[0].aggregate_id == created["id"]
        assert events[0].payload == {"task_version": 1, "origin": "live"}

    no_op = (
        await client.patch(
            f"/projects/{project['id']}/tasks/{created['id']}",
            json={"title": "Indicizzare i task", "expected_version": 1},
        )
    ).json()
    assert no_op["version"] == 1
    async with db_factory() as db:
        assert await db.scalar(select(func.count(TaskRevision.id))) == 1
        assert (
            await db.scalar(
                select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "task.upsert")
            )
            == 1
        )

    changed = (
        await client.patch(
            f"/projects/{project['id']}/tasks/{created['id']}",
            json={"next_action": "Eseguire il bootstrap", "expected_version": 1},
        )
    ).json()
    assert changed["version"] == 2
    conflict = await client.patch(
        f"/projects/{project['id']}/tasks/{created['id']}",
        json={"status": "done", "expected_version": 1},
    )
    assert conflict.status_code == 409
    async with db_factory() as db:
        persisted = await db.get(Task, created["id"])
        assert persisted.version == 2 and persisted.status == "todo"
        assert await db.scalar(select(func.count(TaskRevision.id))) == 2
        events = list(
            (
                await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "task.upsert"))
            ).all()
        )
        assert sorted(event.payload["task_version"] for event in events) == [1, 2]


async def test_empty_task_search_never_initializes_or_calls_embedding(api_client, monkeypatch):
    client, _ = api_client
    project, _ = await create_project(client)

    def embeddings_are_forbidden():
        raise AssertionError("an empty project must not initialize the semantic index")

    monkeypatch.setattr(main, "task_index_service", embeddings_are_forbidden)
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "qualunque task"},
        )
    ).json()
    assert result == {
        "items": [],
        "match_type": "semantic",
        "degraded": False,
        "degraded_reason": None,
        "indexing_pending": False,
        "index_status": "ready",
        "stale_hits": 0,
        "next_cursor": None,
    }


async def test_task_search_exact_id_and_duplicate_exact_titles_never_embed(api_client, monkeypatch):
    client, _ = api_client
    project, _ = await create_project(client)
    first = (
        await client.post(f"/projects/{project['id']}/tasks", json={"title": "Release Android"})
    ).json()
    second = (
        await client.post(f"/projects/{project['id']}/tasks", json={"title": "Release Android"})
    ).json()

    def embeddings_are_forbidden():
        raise AssertionError("an exact task lookup must not initialize the semantic index")

    monkeypatch.setattr(main, "task_index_service", embeddings_are_forbidden)
    by_id = (
        await client.post(f"/projects/{project['id']}/tasks/search", json={"query": first["id"]})
    ).json()
    assert by_id["match_type"] == "exact_id"
    assert [item["id"] for item in by_id["items"]] == [first["id"]]
    assert by_id["items"][0]["match_type"] == "exact_id"

    await client.patch(
        f"/projects/{project['id']}/tasks/{first['id']}",
        json={"status": "done", "expected_version": 1},
    )
    filtered_id = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": first["id"], "scope": "all", "status": "todo"},
        )
    ).json()
    assert filtered_id["items"] == []
    assert filtered_id["match_type"] == "lexical_fallback"

    by_title = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "  Release   Android  ", "scope": "all"},
        )
    ).json()
    assert by_title["match_type"] == "exact_title"
    assert by_title["ambiguous"] is True
    assert {item["id"] for item in by_title["items"]} == {first["id"], second["id"]}
    assert all(item["match_type"] == "exact_title" for item in by_title["items"])


async def test_task_search_falls_back_lexically_when_semantic_index_fails(api_client, monkeypatch):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Affinare il riconoscimento",
                "objective": "Calibrazione immagini giapponesi",
                "description": "Misurare precisione e richiamo",
            },
        )
    ).json()
    await client.post(f"/projects/{project['id']}/tasks", json={"title": "Preparare la fattura"})

    def broken_index():
        raise RuntimeError("qdrant configuration unavailable")

    monkeypatch.setattr(main, "task_index_service", broken_index)
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "calibrazione immagini", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is True
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == target["id"]
    assert result["items"][0]["match_type"] == "lexical_fallback"
    assert "description" not in result["items"][0]


async def test_task_search_discards_a_stale_hit_and_queues_exactly_one_repair(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Riparare indice task", "objective": "Ricerca affidabile"},
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, task["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        await db.execute(
            delete(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.event_type == "task.upsert",
            )
        )
        await db.commit()

    class StaleIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            return {task["id"]: current_payload}

        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(
                items=[
                    {
                        "task_id": task["id"],
                        "project_id": project["id"],
                        "source_version": task["version"],
                        "semantic_hash": "obsolete",
                        "index_render_version": TASK_INDEX_RENDER_VERSION,
                        "score": 0.99,
                    }
                ],
                embedding=None,
                vector_store_duration_ms=2,
            )

        def render(self, row):
            return render_task_document(row)

    monkeypatch.setattr(main, "task_index_service", lambda: StaleIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "riparare indice", "scope": "all"},
        )
    ).json()
    assert result["stale_hits"] == 1
    assert result["indexing_pending"] is True
    assert result["degraded"] is True
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == task["id"]
    async with db_factory() as db:
        repairs = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project["id"],
                        OutboxEvent.event_type == "task.upsert",
                    )
                )
            ).all()
        )
        assert len(repairs) == 1
        assert repairs[0].aggregate_id == task["id"]
        assert repairs[0].payload == {"task_version": 1, "origin": "bootstrap"}


async def test_task_search_pushes_epic_and_label_filters_into_qdrant(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    epic = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"kind": "epic", "title": "Mobile"},
        )
    ).json()
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Affinare OCR",
                "objective": "Migliorare il riconoscimento immagini",
                "epic_id": epic["id"],
                "labels": ["mobile"],
            },
        )
    ).json()
    async with db_factory() as db:
        await db.execute(
            delete(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.event_type == "task.upsert",
            )
        )
        await db.commit()
        task_rows = list(
            (
                await db.scalars(
                    select(Task).where(Task.project_id == project["id"])
                )
            ).all()
        )
        inventory = {
            row.id: task_index_payload(row, render_task_document(row)) for row in task_rows
        }
        target_row = next(row for row in task_rows if row.id == target["id"])
        rendered = render_task_document(target_row)

    calls = []
    inventory_calls = []

    class FilteredIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            inventory_calls.append(project_id)
            return inventory

        def search_observed(self, *args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                items=[
                    {
                        "task_id": target["id"],
                        "project_id": project["id"],
                        "source_version": target["version"],
                        "semantic_hash": rendered.semantic_hash,
                        "index_render_version": rendered.render_version,
                        "score": 0.91,
                    }
                ],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def render(self, row):
            return render_task_document(row)

    monkeypatch.setattr(main, "task_index_service", lambda: FilteredIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={
                "query": "riconoscimento visivo",
                "scope": "all",
                "epic_id": epic["id"],
                "label": "mobile",
            },
        )
    ).json()
    assert [item["id"] for item in result["items"]] == [target["id"]]
    assert result["match_type"] == "semantic"
    assert calls[0]["epic_id"] == epic["id"]
    assert calls[0]["label"] == "mobile"
    repeated = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={
                "query": "lettura fotografie",
                "scope": "all",
                "epic_id": epic["id"],
                "label": "mobile",
            },
        )
    ).json()
    assert repeated["match_type"] == "semantic"
    assert inventory_calls == [project["id"]]
    assert len(calls) == 2


async def test_task_search_treats_below_threshold_hits_as_a_normal_no_match(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Correggere duplicazione lista",
                "objective": "Eliminare elementi duplicati",
            },
        )
    ).json()
    async with db_factory() as db:
        await db.execute(
            delete(OutboxEvent).where(OutboxEvent.project_id == project["id"])
        )
        await db.commit()
        rendered = render_task_document(await db.get(Task, target["id"]))

    class BelowThresholdIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            return {target["id"]: task_index_payload_row}

        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(
                items=[
                    {
                        "task_id": target["id"],
                        "project_id": project["id"],
                        "source_version": target["version"],
                        "semantic_hash": rendered.semantic_hash,
                        "index_render_version": rendered.render_version,
                        "score": 0.10,
                    }
                ],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def render(self, row):
            return render_task_document(row)

    task_index_payload_row = None
    async with db_factory() as db:
        row = await db.get(Task, target["id"])
        task_index_payload_row = task_index_payload(row, render_task_document(row))

    monkeypatch.setattr(main, "task_index_service", lambda: BelowThresholdIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "duplicazione lista", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is False
    assert result["degraded_reason"] is None
    assert result["index_status"] == "ready"
    assert result["match_type"] == "semantic"
    assert result["items"] == []


async def test_empty_task_collection_enqueues_repair_and_uses_lexical_result(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Riconciliare collection vuota"},
        )
    ).json()
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        await db.commit()

    class EmptyIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            return {}

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(items=[], embedding=None, vector_store_duration_ms=1)

    monkeypatch.setattr(main, "task_index_service", lambda: EmptyIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "collection vuota", "scope": "all"},
        )
    ).json()
    assert result["indexing_pending"] is True
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == target["id"]


async def test_missing_collection_marker_forces_reconciliation_before_provider(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Ricreare marker collection"},
        )
    ).json()
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = str(uuid.uuid4())
        await db.commit()
    _FAKE_TASK_MARKERS.pop(project["id"], None)
    inventories = []

    class RecreatedCollection(TaskMarkerMixin):
        def inventory(self, project_id):
            inventories.append(project_id)
            return {}

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *args, **kwargs):
            raise AssertionError("provider must wait for marker reconciliation")

    monkeypatch.setattr(main, "task_index_service", lambda: RecreatedCollection())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "marker collection", "scope": "all"},
        )
    ).json()
    assert inventories == [project["id"]]
    assert result["degraded_reason"] == "task_indexing"
    assert result["indexing_pending"] is True
    assert result["items"][0]["id"] == target["id"]
    async with db_factory() as db:
        repair = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.event_type == "task.upsert",
            )
        )
        assert repair.aggregate_id == target["id"]
        assert repair.payload["origin"] == "bootstrap"


async def test_partial_point_loss_forces_reconciliation_before_provider(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Riparare punto task mancante"},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    _FAKE_TASK_COUNTS[project["id"]] = 0
    inventories = []

    class MissingPointIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            inventories.append(project_id)
            return {}

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *args, **kwargs):
            raise AssertionError("provider must wait for cardinality reconciliation")

    monkeypatch.setattr(main, "task_index_service", lambda: MissingPointIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "punto task mancante", "scope": "all"},
        )
    ).json()

    assert inventories == [project["id"]]
    assert result["degraded_reason"] == "task_indexing"
    assert result["indexing_pending"] is True
    assert result["items"][0]["id"] == target["id"]
    async with db_factory() as db:
        repairs = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project["id"],
                        OutboxEvent.event_type == "task.upsert",
                    )
                )
            ).all()
        )
        assert [(event.aggregate_id, event.payload["origin"]) for event in repairs] == [
            (target["id"], "bootstrap")
        ]


async def test_partial_task_inventory_is_repaired_before_semantic_search(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    indexed = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Preparare la fattura"},
        )
    ).json()
    missing = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Calibrare il retrieval semantico"},
        )
    ).json()
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        await db.commit()
        indexed_row = await db.get(Task, indexed["id"])
        inventory = {
            indexed["id"]: task_index_payload(
                indexed_row, render_task_document(indexed_row)
            )
        }

    class PartialIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            return inventory

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *args, **kwargs):
            raise AssertionError("semantic search must wait for a complete projection")

    monkeypatch.setattr(main, "task_index_service", lambda: PartialIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "calibrare retrieval", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_indexing"
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == missing["id"]
    async with db_factory() as db:
        repairs = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project["id"],
                        OutboxEvent.event_type == "task.upsert",
                    )
                )
            ).all()
        )
        assert [event.aggregate_id for event in repairs] == [missing["id"]]
        assert (
            await db.scalar(
                select(func.count(ObservabilityEvent.id)).where(
                    ObservabilityEvent.project_id == project["id"],
                    ObservabilityEvent.operation == "embedding.task_search",
                )
            )
            == 0
        )


async def test_reconciled_inventory_is_reverified_before_provider(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    target = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Riverificare indice riconciliato"},
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, target["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        await db.commit()

    class LostAfterInventory(TaskMarkerMixin):
        def inventory(self, project_id):
            return {target["id"]: current_payload}

        def render(self, row):
            return render_task_document(row)

        def integrity_snapshot(self, project_id):
            return None

        def search_observed(self, *args, **kwargs):
            raise AssertionError("provider must wait for post-reconciliation verification")

    monkeypatch.setattr(main, "task_index_service", lambda: LostAfterInventory())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "indice riconciliato", "scope": "all"},
        )
    ).json()

    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_index_changed"
    assert result["indexing_pending"] is False
    assert result["items"][0]["id"] == target["id"]
    async with db_factory() as db:
        project_row = await db.get(Project, project["id"])
        assert project_row.task_index_reconciled is False
        assert (
            await db.scalar(
                select(func.count(ObservabilityEvent.id)).where(
                    ObservabilityEvent.project_id == project["id"],
                    ObservabilityEvent.operation == "embedding.task_search",
                )
            )
            == 0
        )


async def test_task_search_discards_results_if_generation_changes_during_vector_call(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Proteggere generazione ricerca"},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        row = await db.get(Task, task["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        generation = project_row.task_index_generation
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    _FAKE_TASK_COUNTS[project["id"]] = 1

    class RacingGenerationIndex(TaskMarkerMixin):
        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(
                items=[{**current_payload, "score": 0.98}],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def render(self, row):
            return render_task_document(row)

    states = iter(
        [
            (True, generation, epoch),
            (True, generation, epoch),
            (True, generation + 1, epoch),
        ]
    )

    async def state_read(*args, **kwargs):
        return next(states)

    monkeypatch.setattr(main, "task_index_service", lambda: RacingGenerationIndex())
    monkeypatch.setattr(main, "current_task_index_state", state_read)
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "generazione ricerca", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_index_changed"
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == task["id"]


async def test_task_search_rechecks_persisted_readiness_before_provider(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Rileggere readiness persistita"},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        generation = project_row.task_index_generation
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    _FAKE_TASK_COUNTS[project["id"]] = 1

    class InvalidatedIndex(TaskMarkerMixin):
        def search_observed(self, *args, **kwargs):
            raise AssertionError("provider must wait for current persisted readiness")

    async def invalidated_state(*args, **kwargs):
        return False, generation, epoch

    monkeypatch.setattr(main, "task_index_service", lambda: InvalidatedIndex())
    monkeypatch.setattr(main, "current_task_index_state", invalidated_state)
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "readiness persistita", "scope": "all"},
        )
    ).json()

    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_index_changed"
    assert result["items"][0]["id"] == task["id"]
    async with db_factory() as db:
        assert (
            await db.scalar(
                select(func.count(ObservabilityEvent.id)).where(
                    ObservabilityEvent.project_id == project["id"],
                    ObservabilityEvent.operation == "embedding.task_search",
                )
            )
            == 0
        )


async def test_task_search_discards_results_if_a_point_disappears_during_vector_call(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Rilevare perdita durante ricerca"},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        row = await db.get(Task, task["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    counts = iter([1, 0])
    search_calls = []

    class PointLossIndex(TaskMarkerMixin):
        def integrity_snapshot(self, project_id):
            return epoch, next(counts)

        def search_observed(self, *args, **kwargs):
            search_calls.append((args, kwargs))
            return SimpleNamespace(
                items=[{**current_payload, "score": 0.98}],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def render(self, row):
            return render_task_document(row)

    monkeypatch.setattr(main, "task_index_service", lambda: PointLossIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "perdita durante ricerca", "scope": "all"},
        )
    ).json()

    assert len(search_calls) == 1
    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_index_changed"
    assert result["match_type"] == "lexical_fallback"
    assert result["items"][0]["id"] == task["id"]
    async with db_factory() as db:
        project_row = await db.get(Project, project["id"])
        assert project_row.task_index_reconciled is False


async def test_failed_old_task_version_does_not_block_current_semantic_index(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Versionare indice", "objective": "Ricerca corrente"},
        )
    ).json()
    task = (
        await client.patch(
            f"/projects/{project['id']}/tasks/{task['id']}",
            json={"next_action": "Confermare la versione due", "expected_version": 1},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        events = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == task["id"])
                )
            ).all()
        )
        for event in events:
            if event.payload["task_version"] == 1:
                event.status = "failed"
                event.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            else:
                event.status = "processed"
                event.processed_at = datetime.now(timezone.utc)
        row = await db.get(Task, task["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    _FAKE_TASK_COUNTS[project["id"]] = 1

    class CurrentVersionIndex(TaskMarkerMixin):
        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(
                items=[{**current_payload, "score": 0.94}],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def render(self, row):
            return render_task_document(row)

    monkeypatch.setattr(main, "task_index_service", lambda: CurrentVersionIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "ricerca della versione corrente", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is False
    assert result["match_type"] == "semantic"
    assert result["items"][0]["id"] == task["id"]
    briefing = (await client.get(f"/projects/{project['id']}/briefing")).json()
    assert briefing["task_indexing_pending"] is False


async def test_qdrant_search_failure_invalidates_readiness_after_paid_embedding(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Riprovare indice locale"},
        )
    ).json()
    epoch = str(uuid.uuid4())
    async with db_factory() as db:
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        project_row = await db.get(Project, project["id"])
        project_row.task_index_reconciled = True
        project_row.task_index_epoch = epoch
        await db.commit()
    _FAKE_TASK_MARKERS[project["id"]] = epoch
    _FAKE_TASK_COUNTS[project["id"]] = 1
    embedding = SimpleNamespace(
        usage=SimpleNamespace(
            provider="openai",
            model="text-embedding-3-large",
            measurement_source="provider_reported",
            input_tokens=7,
            reported_total_tokens=7,
        ),
        provider_duration_ms=4,
        request_count=1,
    )

    class FailingQueryIndex(TaskMarkerMixin):
        def search_observed(self, *args, **kwargs):
            raise VectorOperationError(
                "qdrant offline",
                embedding=embedding,
                vector_store_duration_ms=2,
            )

        def render(self, row):
            return render_task_document(row)

    monkeypatch.setattr(main, "task_index_service", lambda: FailingQueryIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "indice locale", "scope": "all"},
        )
    ).json()
    assert result["degraded_reason"] == "task_index_unavailable"
    assert result["items"][0]["id"] == task["id"]
    async with db_factory() as db:
        project_row = await db.get(Project, project["id"])
        assert project_row.task_index_reconciled is False
        event = await db.scalar(
            select(ObservabilityEvent).where(
                ObservabilityEvent.project_id == project["id"],
                ObservabilityEvent.operation == "embedding.task_search",
            )
        )
        assert event.status == "partial_failure"
        assert event.request_count == 1


async def test_task_restore_reindex_preserves_current_vectors_and_queues_every_task(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    async with db_factory() as db:
        db.add_all(
            [
                Task(project_id=project["id"], title="Primo"),
                Task(project_id=project["id"], title="Secondo"),
                Task(project_id=project["id"], title="Terzo"),
            ]
        )
        await db.commit()

    ensures = []

    class FakeTaskIndex(TaskMarkerMixin):
        def ensure_collection(self, project_id):
            ensures.append(project_id)
            return f"task_test_{project_id}"

        def reset_collection(self, project_id):
            raise AssertionError("restore reindex must preserve already-current task vectors")

        def inventory(self, project_id):
            return {}

        def render(self, task):
            return render_task_document(task)

    monkeypatch.setattr(main, "task_index_service", lambda: FakeTaskIndex())
    result = (
        await client.post(f"/projects/{project['id']}/tasks/reindex", params={"origin": "restore"})
    ).json()
    assert result == {
        "queued": 3,
        "collection": f"task_test_{project['id']}",
        "status": "indexing",
    }
    assert ensures == [project["id"]]
    async with db_factory() as db:
        events = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project["id"],
                        OutboxEvent.event_type == "task.upsert",
                    )
                )
            ).all()
        )
        assert len(events) == 3
        assert {event.aggregate_id for event in events} == {
            row.id for row in (await db.scalars(select(Task))).all()
        }
        assert all(event.payload["origin"] == "restore" for event in events)


async def test_task_bootstrap_uses_inventory_and_rearms_only_proven_stale_points(
    api_client, db_factory
):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Verify task projection"},
        )
    ).json()

    async with db_factory() as db:
        row = await db.get(Task, created["id"])
        event = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.event_type == "task.upsert",
            )
        )
        await db.commit()
        event_id = event.id
        rendered = render_task_document(row)
        current_payload = task_index_payload(row, rendered)

    class InventoryIndex(TaskMarkerMixin):
        def __init__(self, inventory):
            self.current_inventory = inventory

        def inventory(self, project_id):
            return self.current_inventory

        def render(self, task):
            return render_task_document(task)

    current_index = InventoryIndex({created["id"]: current_payload})
    async with db_factory() as db:
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=current_index,
            )
            == 0
        )
        await db.commit()
        event = await db.get(OutboxEvent, event_id)
        assert event.status == "processed"

    missing_index = InventoryIndex({})
    async with db_factory() as db:
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=missing_index,
            )
            == 1
        )
        await db.commit()
        event = await db.get(OutboxEvent, event_id)
        assert event.status == "processed"
        pending = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.status == "pending",
            )
        )
        assert pending.payload == {"task_version": 1, "origin": "bootstrap"}
        assert (
            await db.scalar(
                select(func.count(OutboxEvent.id)).where(
                    OutboxEvent.event_type == "task.upsert"
                )
            )
            == 2
        )


async def test_task_reset_forces_fresh_events_even_when_versions_are_already_pending(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    for title in ("First", "Second"):
        await client.post(f"/projects/{project['id']}/tasks", json={"title": title})

    resets = []

    class ResetIndex(TaskMarkerMixin):
        def reset_collection(self, project_id):
            resets.append(project_id)
            return f"task_test_{project_id}"

    monkeypatch.setattr(main, "task_index_service", lambda: ResetIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/reindex",
            params={"origin": "bootstrap", "reset": True},
        )
    ).json()
    assert result == {
        "queued": 2,
        "collection": f"task_test_{project['id']}",
        "status": "indexing",
    }
    assert resets == [project["id"]]
    async with db_factory() as db:
        events = list(
            (
                await db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project["id"],
                        OutboxEvent.event_type == "task.upsert",
                    )
                )
            ).all()
        )
        assert len(events) == 4
        assert sum(event.status == "pending" for event in events) == 4


async def test_task_inventory_failure_never_blocks_startup_and_restore_keeps_repair_queued(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    await client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Repair after inventory outage"},
    )

    class UnavailableInventory:
        def inventory(self, project_id):
            raise RuntimeError("qdrant unavailable")

    async with db_factory() as db:
        with pytest.raises(RuntimeError, match="qdrant unavailable"):
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=UnavailableInventory(),
                strict_inventory=True,
            )

    async with db_factory() as db:
        event = await db.scalar(
            select(OutboxEvent).where(OutboxEvent.project_id == project["id"])
        )
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        await db.commit()

        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=UnavailableInventory(),
            )
            == 1
        )
        await db.refresh(event)
        assert event.status == "processed"
        pending = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.status == "pending",
            )
        )
        assert pending is not None

        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="restore",
                force=False,
                index_service=UnavailableInventory(),
            )
            == 0
        )
        assert pending.status == "pending"

    monkeypatch.setattr(
        main,
        "task_index_service",
        lambda: (_ for _ in ()).throw(RuntimeError("invalid Qdrant URL")),
    )
    async with db_factory() as db:
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
            )
            == 1
        )

        with pytest.raises(RuntimeError, match="invalid Qdrant URL"):
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                strict_inventory=True,
            )


async def test_task_reconciliation_deletes_points_absent_from_postgresql(
    api_client, db_factory
):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks", json={"title": "Authoritative task"}
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, created["id"])
        current_payload = task_index_payload(row, render_task_document(row))

    deleted = []

    class InventoryWithOrphan(TaskMarkerMixin):
        def inventory(self, project_id):
            return {created["id"]: current_payload, "orphan-task": {}}

        def delete_points(self, project_id, task_ids):
            deleted.append((project_id, task_ids))

        def render(self, task):
            return render_task_document(task)

    async with db_factory() as db:
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=InventoryWithOrphan(),
                strict_inventory=True,
            )
            == 0
        )
        await db.commit()
    assert deleted == [(project["id"], ["orphan-task"])]


async def test_task_reconciliation_generation_fence_never_publishes_stale_readiness(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks", json={"title": "Fence inventory"}
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, created["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        project_row = await db.get(Project, project["id"])
        changed_generation = project_row.task_index_generation + 1

    marker_writes = []

    class FencedInventory(TaskMarkerMixin):
        def inventory(self, project_id):
            return {created["id"]: current_payload}

        def render(self, row):
            return render_task_document(row)

        def write_marker(self, project_id, epoch, task_count):
            marker_writes.append((project_id, epoch, task_count))

    async def changed(*args, **kwargs):
        return changed_generation

    monkeypatch.setattr(main, "current_task_index_generation", changed)
    async with db_factory() as db:
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=FencedInventory(),
                strict_inventory=True,
            )
            == 0
        )
        await db.commit()
        project_row = await db.get(Project, project["id"])
        assert project_row.task_index_reconciled is False
    assert marker_writes == []


async def test_task_reconciliation_rearms_projection_when_orphan_cleanup_fails(
    api_client, db_factory
):
    client, _ = api_client
    project, _ = await create_project(client)
    created = (
        await client.post(
            f"/projects/{project['id']}/tasks", json={"title": "Repair after cleanup"}
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, created["id"])
        payload = task_index_payload(row, render_task_document(row))
        event = await db.scalar(
            select(OutboxEvent).where(OutboxEvent.project_id == project["id"])
        )
        event.status = "processed"
        await db.commit()

        class BrokenCleanup:
            def inventory(self, project_id):
                return {created["id"]: payload, "orphan-task": {}}

            def delete_points(self, project_id, task_ids):
                raise RuntimeError("delete unavailable")

            def render(self, task):
                return render_task_document(task)

        with pytest.raises(RuntimeError, match="delete unavailable"):
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=BrokenCleanup(),
                strict_inventory=True,
            )
        assert (
            await main.enqueue_task_reindex_events(
                db,
                project_id=project["id"],
                origin="bootstrap",
                force=False,
                index_service=BrokenCleanup(),
            )
            == 1
        )
        await db.refresh(event)
        assert event.status == "processed"
        pending = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project["id"],
                OutboxEvent.status == "pending",
            )
        )
        assert pending is not None


async def test_task_search_degrades_if_an_orphan_appears_after_inventory(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project, _ = await create_project(client)
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Runtime orphan fallback"},
        )
    ).json()
    async with db_factory() as db:
        row = await db.get(Task, task["id"])
        current_payload = task_index_payload(row, render_task_document(row))
        await db.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project["id"]))
        await db.commit()

    delete_calls = []

    class RacingIndex(TaskMarkerMixin):
        def inventory(self, project_id):
            return {task["id"]: current_payload}

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *args, **kwargs):
            return SimpleNamespace(
                items=[
                    {
                        "task_id": "orphan-after-inventory",
                        "project_id": project["id"],
                        "source_version": 1,
                        "semantic_hash": "orphan",
                        "index_render_version": TASK_INDEX_RENDER_VERSION,
                        "score": 0.99,
                    }
                ],
                embedding=None,
                vector_store_duration_ms=1,
            )

        def delete_points(self, project_id, task_ids):
            delete_calls.append((project_id, task_ids))
            raise RuntimeError("raced cleanup failure")

    monkeypatch.setattr(main, "task_index_service", lambda: RacingIndex())
    result = (
        await client.post(
            f"/projects/{project['id']}/tasks/search",
            json={"query": "runtime orphan", "scope": "all"},
        )
    ).json()
    assert result["degraded"] is True
    assert result["degraded_reason"] == "task_index_incomplete"
    assert result["stale_hits"] == 1
    assert result["items"][0]["id"] == task["id"]
    assert delete_calls == [(project["id"], ["orphan-after-inventory"])]


async def test_epics_and_task_attachments_are_project_scoped(api_client):
    client, _ = api_client
    first, _ = await create_project(client, name="First")
    second, _ = await create_project(client, name="Second")
    epic = (
        await client.post(
            f"/projects/{first['id']}/tasks",
            json={"kind": "epic", "title": "Android launch", "labels": ["release"]},
        )
    ).json()
    task = (
        await client.post(
            f"/projects/{first['id']}/tasks",
            json={"title": "Run device tests", "epic_id": epic["id"], "labels": ["android"]},
        )
    ).json()
    assert task["kind"] == "task" and task["epic_id"] == epic["id"]
    assert task["attachments"] == []
    invalid_null = await client.patch(
        f"/projects/{first['id']}/tasks/{task['id']}",
        json={"status": None},
    )
    assert invalid_null.status_code == 422

    ordinary = (
        await client.post(f"/projects/{first['id']}/tasks", json={"title": "Not an epic"})
    ).json()
    invalid_parent = await client.post(
        f"/projects/{first['id']}/tasks",
        json={"title": "Child", "epic_id": ordinary["id"]},
    )
    assert invalid_parent.status_code == 422
    cross_project = await client.post(
        f"/projects/{second['id']}/tasks",
        json={"title": "Wrong project", "epic_id": epic["id"]},
    )
    assert cross_project.status_code == 422
    nested = await client.post(
        f"/projects/{first['id']}/tasks",
        json={"kind": "epic", "title": "Nested", "epic_id": epic["id"]},
    )
    assert nested.status_code == 422
    conversion = await client.patch(
        f"/projects/{first['id']}/tasks/{epic['id']}",
        json={"kind": "task", "expected_version": 1},
    )
    assert conversion.status_code == 422

    content = b"fake image bytes"
    payload = {
        "kind": "image",
        "filename": "campaign.png",
        "mime_type": "image/png",
        "content_base64": base64.b64encode(content).decode(),
    }
    uploaded = await client.post(
        f"/projects/{first['id']}/tasks/{task['id']}/attachments", json=payload
    )
    assert uploaded.status_code == 200
    attachment = uploaded.json()
    assert attachment["created"] is True and attachment["linked"] is True
    duplicate = (
        await client.post(f"/projects/{first['id']}/tasks/{task['id']}/attachments", json=payload)
    ).json()
    assert duplicate["created"] is False and duplicate["linked"] is False

    listed_tasks = (await client.get(f"/projects/{first['id']}/tasks")).json()["items"]
    listed_task = next(item for item in listed_tasks if item["id"] == task["id"])
    assert [item["filename"] for item in listed_task["attachments"]] == ["campaign.png"]
    listed_attachments = (
        await client.get(f"/projects/{first['id']}/tasks/{task['id']}/attachments")
    ).json()["items"]
    assert listed_attachments[0]["content_hash"] == attachment["content_hash"]

    content_url = (
        f"/projects/{first['id']}/tasks/{task['id']}/attachments/{attachment['id']}/content"
    )
    downloaded = await client.get(content_url)
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["content-disposition"].startswith("inline;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "campaign.png" in downloaded.headers["content-disposition"]
    wrong_project_url = (
        f"/projects/{second['id']}/tasks/{task['id']}/attachments/{attachment['id']}/content"
    )
    assert (await client.get(wrong_project_url)).status_code == 404

    removed = await client.delete(
        f"/projects/{first['id']}/tasks/{task['id']}/attachments/{attachment['id']}"
    )
    assert removed.status_code == 204
    assert (await client.get(f"/projects/{first['id']}/tasks/{task['id']}/attachments")).json() == {
        "items": []
    }
    assert (await client.delete(content_url.removesuffix("/content"))).status_code == 404

    unsafe = (
        await client.post(
            f"/projects/{first['id']}/tasks/{task['id']}/attachments",
            json={
                "kind": "document",
                "filename": "report.html",
                "mime_type": "text/html",
                "content_base64": base64.b64encode(b"<script>alert(1)</script>").decode(),
            },
        )
    ).json()
    unsafe_download = await client.get(
        f"/projects/{first['id']}/tasks/{task['id']}/attachments/{unsafe['id']}/content"
    )
    assert unsafe_download.headers["content-type"] == "application/octet-stream"
    assert unsafe_download.headers["content-disposition"].startswith("attachment;")
    assert unsafe_download.headers["x-content-type-options"] == "nosniff"

    missing_task = str(uuid.uuid4())
    assert (
        await client.post(f"/projects/{first['id']}/tasks/{missing_task}/attachments", json=payload)
    ).status_code == 404
    assert (
        await client.get(f"/projects/{first['id']}/tasks/{missing_task}/attachments")
    ).status_code == 404

    reference = (
        await client.post(
            f"/projects/{first['id']}/tasks/{task['id']}/attachments",
            json={"kind": "url", "source_uri": "https://example.com/brief"},
        )
    ).json()
    reference_content = (
        f"/projects/{first['id']}/tasks/{task['id']}/attachments/{reference['id']}/content"
    )
    assert (await client.get(reference_content)).status_code == 404


async def test_plans_are_versioned_linked_to_work_and_keep_attachments_scoped(
    api_client, db_factory
):
    client, _ = api_client
    first, _ = await create_project(client, name="First")
    second, _ = await create_project(client, name="Second")
    epic = (
        await client.post(
            f"/projects/{first['id']}/tasks",
            json={"kind": "epic", "title": "Android release"},
        )
    ).json()
    task = (
        await client.post(
            f"/projects/{first['id']}/tasks",
            json={"title": "Run real-user test", "epic_id": epic["id"]},
        )
    ).json()
    foreign_task = (
        await client.post(
            f"/projects/{second['id']}/tasks",
            json={"title": "Do not link this"},
        )
    ).json()
    work_item_ids = sorted([epic["id"], task["id"]], reverse=True)
    payload = {
        "title": "Android launch design",
        "objective": "Decide the release path before execution.",
        "content": "Compare the beta gate, review findings, and release requirements.",
        "status": "decided",
        "labels": ["android", "release"],
        "work_item_ids": work_item_ids,
        "rationale": "Capture the agreed release approach",
    }
    created = await client.post(f"/projects/{first['id']}/plans", json=payload)
    assert created.status_code == 200
    plan = created.json()
    assert plan["version"] == 1
    assert plan["work_item_ids"] == work_item_ids
    assert plan["attachments"] == []

    invalid_link = await client.post(
        f"/projects/{first['id']}/plans",
        json={"title": "Wrong link", "work_item_ids": [foreign_task["id"]]},
    )
    assert invalid_link.status_code == 422
    assert (await client.get(f"/projects/{uuid.uuid4()}/plans")).status_code == 404
    assert (
        await client.post(f"/projects/{uuid.uuid4()}/plans", json={"title": "Missing project"})
    ).status_code == 404
    assert (
        await client.patch(f"/projects/{first['id']}/plans/{uuid.uuid4()}", json={"title": "Missing"})
    ).status_code == 404

    briefing = (await client.get(f"/projects/{first['id']}/briefing")).json()
    assert briefing["plans"][0]["title"] == "Android launch design"
    assert briefing["plans"][0]["content_excerpt"].startswith("Compare the beta")
    assert briefing["plans"][0]["work_item_ids"] == work_item_ids
    assert "broad strategy, design, or decisions" in briefing["instruction"]

    invalid_null = await client.patch(
        f"/projects/{first['id']}/plans/{plan['id']}", json={"status": None}
    )
    assert invalid_null.status_code == 422
    stale = await client.patch(
        f"/projects/{first['id']}/plans/{plan['id']}",
        json={"status": "executing", "expected_version": 9},
    )
    assert stale.status_code == 409
    invalid_update_link = await client.patch(
        f"/projects/{first['id']}/plans/{plan['id']}",
        json={"work_item_ids": [foreign_task["id"]], "expected_version": 1},
    )
    assert invalid_update_link.status_code == 422
    updated_work_item_ids = list(reversed(work_item_ids))
    updated = (
        await client.patch(
            f"/projects/{first['id']}/plans/{plan['id']}",
            json={
                "content": "The release path is approved. Start the real-user test.",
                "status": "executing",
                "work_item_ids": updated_work_item_ids,
                "expected_version": 1,
            },
        )
    ).json()
    assert updated["version"] == 2
    assert updated["work_item_ids"] == updated_work_item_ids
    unchanged = (
        await client.patch(
            f"/projects/{first['id']}/plans/{plan['id']}",
            json={"expected_version": 2},
        )
    ).json()
    assert unchanged["version"] == 2

    content = b"design image"
    invalid_content = await client.post(
        f"/projects/{first['id']}/plans/{plan['id']}/attachments",
        json={"kind": "image", "filename": "broken.png", "content_base64": "not-base64"},
    )
    assert invalid_content.status_code == 422
    attachment = (
        await client.post(
            f"/projects/{first['id']}/plans/{plan['id']}/attachments",
            json={
                "kind": "image",
                "filename": "release-flow.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
    ).json()
    duplicate = (
        await client.post(
            f"/projects/{first['id']}/plans/{plan['id']}/attachments",
            json={
                "kind": "image",
                "filename": "release-flow.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
    ).json()
    assert attachment["created"] and attachment["linked"]
    assert not duplicate["created"] and not duplicate["linked"]
    listed = (await client.get(f"/projects/{first['id']}/plans")).json()["items"]
    assert listed[0]["attachments"][0]["filename"] == "release-flow.png"
    content_url = (
        f"/projects/{first['id']}/plans/{plan['id']}/attachments/{attachment['id']}/content"
    )
    downloaded = await client.get(content_url)
    assert downloaded.content == content
    assert downloaded.headers["content-disposition"].startswith("inline;")
    assert (
        await client.get(
            f"/projects/{second['id']}/plans/{plan['id']}/attachments/{attachment['id']}/content"
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/projects/{first['id']}/plans/{plan['id']}/attachments/{attachment['id']}"
        )
    ).status_code == 204
    assert (
        await client.get(f"/projects/{first['id']}/plans/{plan['id']}/attachments")
    ).json() == {"items": []}
    assert (await client.get(content_url)).status_code == 404
    assert (
        await client.delete(
            f"/projects/{first['id']}/plans/{plan['id']}/attachments/{attachment['id']}"
        )
    ).status_code == 404
    assert (
        await client.post(f"/projects/{first['id']}/plans/{uuid.uuid4()}/attachments", json={})
    ).status_code == 404

    async with db_factory() as db:
        assert await db.scalar(select(func.count(Plan.id))) == 1
        assert await db.scalar(select(func.count(PlanRevision.id))) == 2
        links = list(
            (
                await db.scalars(
                    select(PlanWorkItem)
                    .where(PlanWorkItem.plan_id == plan["id"])
                    .order_by(PlanWorkItem.position)
                )
            ).all()
        )
        assert [(link.task_id, link.position) for link in links] == list(
            zip(updated_work_item_ids, range(len(updated_work_item_ids)), strict=True)
        )


async def test_reconcile_restored_qdrant_snapshot(api_client, db_factory):
    client, embeddings = api_client
    missing = str(uuid.uuid4())
    assert (await client.post(f"/projects/{missing}/memories/reconcile")).status_code == 404
    project, _ = await create_project(client)
    async with db_factory() as db:
        active = Memory(
            project_id=project["id"],
            node_type="heuristic",
            node_key="active",
            text="Active",
        )
        inactive = Memory(
            project_id=project["id"],
            node_type="episode",
            node_key="inactive",
            text="Inactive",
            status="inactive",
        )
        db.add_all([active, inactive])
        await db.commit()
        await db.refresh(active)
        await db.refresh(inactive)
    embeddings.inventory_points = {
        active.id: {
            "node_type": active.node_type,
            "node_key": active.node_key,
            "status": "inactive",
            "revision": active.revision,
        },
        "ghost": {"status": "active"},
    }
    result = (await client.post(f"/projects/{project['id']}/memories/reconcile")).json()
    assert result["queued"] == 1 and result["deleted"] == 1
    assert embeddings.deleted_points == ["ghost"]
    async with db_factory() as db:
        assert await db.scalar(select(func.count(OutboxEvent.id))) == 1


def backup_settings(**overrides):
    values = {
        "backup_project_id": "",
        "backup_configured": True,
        "backup_configuration_error": "",
        "backup_include_qdrant": True,
        "backup_retention_daily": 7,
        "backup_retention_weekly": 4,
        "backup_retention_monthly": 6,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class SuccessfulBackup:
    def create(self, project_id: str, project_name: str, version: str):
        return BackupResult(
            archive_name="dduo-solo-founder-example-20260712T000000Z-12345678.dduobackup",
            size_bytes=1234,
            includes_qdrant=True,
            manifest={"project": {"id": project_id, "name": project_name}, "version": version},
            pruned_archives=("old.dduobackup",),
        )


async def test_backup_status_manual_success_failure_and_generation_race(
    api_client, db_factory, monkeypatch, tmp_path
):
    client, _ = api_client
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: backup_settings(backup_directory=Path(tmp_path)),
    )
    monkeypatch.setattr(main, "backup_engine", lambda: SuccessfulBackup())
    missing = str(uuid.uuid4())
    assert (await client.get(f"/projects/{missing}/backups")).status_code == 404
    assert (await client.post(f"/projects/{missing}/backups")).status_code == 404

    payload, project = await create_project(client)
    initial = (await client.get(f"/projects/{project['id']}/backups")).json()
    assert initial["configured"] and initial["dirty"] and initial["automatic_due"]
    created = (await client.post(f"/projects/{project['id']}/backups?trigger=update")).json()
    assert created["status"] == "verified" and created["trigger"] == "update"
    archive = tmp_path / created["archive_name"]
    archive.write_bytes(b"encrypted backup")
    download = await client.get(
        f"/projects/{project['id']}/backups/{created['id']}/download"
    )
    assert download.status_code == 200 and download.content == b"encrypted backup"
    assert "attachment" in download.headers["content-disposition"]
    assert (
        await client.get(f"/projects/{project['id']}/backups/{uuid.uuid4()}/download")
    ).status_code == 404
    status = (await client.get(f"/projects/{project['id']}/backups")).json()
    assert not status["dirty"] and status["latest_verified"]["size_bytes"] == 1234

    async with db_factory() as db:
        row = await db.get(Project, project["id"])
        row.backup_dirty = True
        row.backup_generation += 1
        stale = BackupRecord(
            project_id=row.id,
            source_generation=row.backup_generation - 1,
            status="scheduled",
        )
        db.add(stale)
        await db.commit()
        await db.refresh(stale)
        await main._run_backup(db, stale, row)
        assert row.backup_dirty

    class FailedBackup:
        def create(self, *args):
            raise BackupError("destination disconnected")

    monkeypatch.setattr(main, "backup_engine", lambda: FailedBackup())
    failed_payload, _ = await create_project(client)
    failed = await client.post(f"/projects/{failed_payload['id']}/backups")
    assert failed.status_code == 500 and "disconnected" in failed.json()["detail"]
    failed_status = (await client.get(f"/projects/{failed_payload['id']}/backups")).json()
    assert failed_status["latest"]["status"] == "failed"

    relocated = {**payload, "name": "Moved", "root_path": "/new/root"}
    moved = (await client.post("/projects", json=relocated)).json()
    # Registering a moved checkout cannot overwrite the server's display name.
    assert moved["name"] == payload["name"] and moved["root_path"] == "/new/root"


async def test_stop_check_queues_a_due_backup_without_blocking_the_chat(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    monkeypatch.setattr(main, "get_settings", lambda: backup_settings())
    started = []

    async def background(record_id, project_id):
        started.append((record_id, project_id))

    monkeypatch.setattr(main, "_run_background_backup", background)
    project, _ = await create_project(client)
    _, context = await create_turn(client, project["id"])
    response = await client.post(
        f"/turns/{context['turn']['id']}/stop-check",
        json={"assistant_response": "Committed"},
    )
    assert response.status_code == 200 and started[0][1] == project["id"]
    async with db_factory() as db:
        record = await db.scalar(
            select(BackupRecord).where(BackupRecord.project_id == project["id"])
        )
        assert record and record.status == "scheduled" and record.trigger == "automatic"


async def test_automatic_backup_loop_scans_projects_and_queues_work(api_client, db_factory, monkeypatch):
    client, _ = api_client
    project, _ = await create_project(client)
    monkeypatch.setattr(main, "SessionLocal", db_factory)
    monkeypatch.setattr(main, "get_settings", lambda: backup_settings(backup_auto_seconds=1))
    scheduled = []

    async def queue(db, project_id, background_tasks):
        scheduled.append((project_id, background_tasks))
        return {"scheduled": False, "reason": "not_due"}

    async def stop_after_first_cycle(_: float):
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "_schedule_due_backup", queue)
    monkeypatch.setattr(main.asyncio, "sleep", stop_after_first_cycle)
    with pytest.raises(asyncio.CancelledError):
        await main._automatic_backup_loop()
    assert scheduled == [(project["id"], None)]

    async def fail_queue(*_):
        raise RuntimeError("temporary backup destination failure")

    monkeypatch.setattr(main, "_schedule_due_backup", fail_queue)
    with pytest.raises(asyncio.CancelledError):
        await main._automatic_backup_loop()


async def test_backup_configuration_boundaries_and_automatic_scheduling(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    payload, _ = await create_project(client)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: backup_settings(backup_configured=False, backup_configuration_error="No folder"),
    )
    status = (await client.get(f"/projects/{payload['id']}/backups")).json()
    assert not status["configured"] and status["configuration_error"] == "No folder"
    assert (await client.post(f"/projects/{payload['id']}/backups")).status_code == 409
    automatic = (await client.post(f"/projects/{payload['id']}/backups/automatic")).json()
    assert not automatic["scheduled"] and automatic["reason"] == "No folder"

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: backup_settings(backup_project_id="another-project"),
    )
    mismatch = (await client.get(f"/projects/{payload['id']}/backups")).json()
    assert "another project" in mismatch["configuration_error"]

    monkeypatch.setattr(main, "get_settings", lambda: backup_settings())
    called = []

    async def background(record_id, project_id):
        called.append((record_id, project_id))

    monkeypatch.setattr(main, "_run_background_backup", background)
    scheduled = (await client.post(f"/projects/{payload['id']}/backups/automatic")).json()
    assert scheduled["scheduled"] and called[0][1] == payload["id"]
    repeated = (await client.post(f"/projects/{payload['id']}/backups/automatic")).json()
    assert repeated["reason"] == "already_running"
    assert (await client.post(f"/projects/{uuid.uuid4()}/backups/automatic")).status_code == 404

    async with db_factory() as db:
        records = (
            await db.scalars(select(BackupRecord).where(BackupRecord.project_id == payload["id"]))
        ).all()
        for record in records:
            await db.delete(record)
        project = await db.get(Project, payload["id"])
        project.backup_dirty = False
        project.last_backup_at = datetime.now(timezone.utc)
        await db.commit()
    not_due = (await client.post(f"/projects/{payload['id']}/backups/automatic")).json()
    assert not_due == {"scheduled": False, "reason": "not_due"}


async def test_registers_verified_restore_provenance_without_clearing_dirty_state(api_client):
    client, _ = api_client
    missing = str(uuid.uuid4())
    body = {
        "archive_name": "project.dduobackup",
        "size_bytes": 100,
        "manifest": {},
    }
    assert (
        await client.post(f"/projects/{missing}/backups/register-restore", json=body)
    ).status_code == 404
    project, _ = await create_project(client)
    assert (
        await client.post(f"/projects/{project['id']}/backups/register-restore", json=body)
    ).status_code == 422
    body["manifest"] = {
        "format": "dduo-solo-founder-backup",
        "schema_version": 1,
        "project": {"id": project["id"]},
        "qdrant": {"included": False},
        "created_at": "not-a-date",
    }
    assert (
        await client.post(f"/projects/{project['id']}/backups/register-restore", json=body)
    ).status_code == 422
    body["manifest"]["created_at"] = "2026-07-01T12:00:00+00:00"
    first = (
        await client.post(f"/projects/{project['id']}/backups/register-restore", json=body)
    ).json()
    repeated = (
        await client.post(f"/projects/{project['id']}/backups/register-restore", json=body)
    ).json()
    assert first["id"] == repeated["id"] and first["trigger"] == "restore"
    status = (await client.get(f"/projects/{project['id']}/backups")).json()
    assert status["dirty"] and status["latest_verified"]["archive_name"] == "project.dduobackup"
    body["archive_name"] = "../unsafe.dduobackup"
    assert (
        await client.post(f"/projects/{project['id']}/backups/register-restore", json=body)
    ).status_code == 422
