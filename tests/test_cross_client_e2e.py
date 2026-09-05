from __future__ import annotations

from sqlalchemy import select

from dduo_solo_founder.models import Memory, SleepJob
from dduo_solo_founder.sleep_engine import process_one_sleep_job
from conftest import project_payload


class CrossClientSleepProvider:
    def __init__(self):
        self.source_turn_id = ""
        self.source_message_id = ""

    async def generate(self, **kwargs):
        payload = kwargs["payload"]
        turn = payload["conversation_turns"][0]
        self.source_turn_id = turn["turn_id"]
        self.source_message_id = turn["user_message"]["message_id"]
        return {
            "topics": [
                {
                    "topic_id": "production-release",
                    "label": "Production release invariant",
                    "query_text": "Production database release safety invariant",
                    "language": "en",
                    "source_turn_ids": [self.source_turn_id],
                    "source_message_ids": [self.source_message_id],
                    "memory_actions": [
                        {
                            "action": "create",
                            "target_node_type": "heuristic",
                            "target_node_key": "production-release-safety",
                            "text": "Release work must preserve production database safety.",
                            "status": "active",
                            "source_message_ids": [self.source_message_id],
                            "source_node_ids": [],
                            "artifact_ids": [],
                            "metadata": {},
                        }
                    ],
                }
            ]
        }


async def test_claude_sleep_codex_recall_and_shared_task_round_trip(api_client, db_factory):
    client, embeddings = api_client
    project = project_payload(
        principles=["Production database connections must always be released"],
        context="The product is in production.",
    )
    assert (await client.post("/projects", json=project)).status_code == 200

    claude = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "claude", "external_id": "claude-initial"},
        )
    ).json()
    first_turn = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": claude["id"],
                "external_id": "claude-turn-1",
                "user_prompt": "Prepare the production release safely.",
            },
        )
    ).json()["turn"]
    task = (
        await client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Prepare production release",
                "status": "in_progress",
                "priority": "high",
                "next_action": "Verify database connection handling",
                "labels": ["release"],
            },
        )
    ).json()
    committed = await client.post(
        f"/turns/{first_turn['id']}/commit",
        json={
            "assistant_response": "The release task is active with the production invariant.",
            "receipt": "claude-commit",
        },
    )
    assert committed.json()["committed"] is True
    scheduled = await client.post(
        f"/projects/{project['id']}/sleep",
        json={"session_id": claude["id"], "trigger": "manual"},
    )
    assert scheduled.json()["scheduled"] == 1

    async with db_factory() as db:
        assert await process_one_sleep_job(db, embeddings, CrossClientSleepProvider()) is True
        memory = await db.scalar(select(Memory).where(Memory.project_id == project["id"]))
        job = await db.scalar(select(SleepJob).where(SleepJob.project_id == project["id"]))
        assert memory is not None
        assert memory.metadata_json["language"] == "en"
        assert job is not None and job.provider == "codex" and job.status == "completed"
        assert job.source_client == "claude"
        embeddings.search_results = [{"id": memory.id, "score": 0.94}]

    codex = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "codex-follow-up"},
        )
    ).json()
    codex_turn = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": codex["id"],
                "external_id": "codex-turn-1",
                "user_prompt": "What protects the production release?",
            },
        )
    ).json()
    assert codex_turn["memories"][0]["node_key"] == "production-release-safety"
    assert codex_turn["project"]["principles"] == project["principles"]

    completed = await client.patch(
        f"/projects/{project['id']}/tasks/{task['id']}",
        json={
            "status": "done",
            "completion_evidence": "Connection handling verified",
            "expected_version": task["version"],
        },
    )
    assert completed.json()["status"] == "done"

    claude_return = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "claude", "external_id": "claude-return"},
        )
    ).json()
    briefing = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": claude_return["id"],
                "external_id": "claude-turn-2",
                "user_prompt": "Show the current release state.",
            },
        )
    ).json()
    assert briefing["project"]["id"] == project["id"]
    all_tasks = (await client.get(f"/projects/{project['id']}/tasks")).json()["items"]
    shared_task = next(item for item in all_tasks if item["id"] == task["id"])
    assert shared_task["status"] == "done"
    assert shared_task["completion_evidence"] == "Connection handling verified"
