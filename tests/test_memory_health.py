from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from dduo_solo_founder.models import Project, Session, SleepJob
from dduo_solo_founder.service import memory_health, project_memory_health, utcnow


async def seed_project(db):
    project = Project(id=str(uuid.uuid4()), name="Example", root_path="/tmp/example")
    session = Session(
        project_id=project.id,
        client="codex",
        external_id=str(uuid.uuid4()),
    )
    db.add_all([project, session])
    await db.flush()
    return project, session


def job_for(project, session, **overrides):
    return SleepJob(
        **{
            "project_id": project.id,
            "session_id": session.id,
            "provider": "codex",
            "trigger": "manual",
            "status": "waiting",
            "dedupe_key": str(uuid.uuid4()),
            "attempts": 1,
            "error_kind": "auth_required",
            "created_at": utcnow(),
            **overrides,
        }
    )


def test_health_issue_tracks_failure_episode_without_private_content():
    job = SimpleNamespace(
        id=str(uuid.uuid4()),
        attempts=1,
        error_kind="auth_required",
        provider="codex",
        retry_at=utcnow() + timedelta(minutes=5),
        last_error="private diagnostic with credentials",
        result={"private": "private user conversation"},
    )
    first = memory_health({"waiting": 1}, job)
    assert first["state"] == "connection_required"
    assert first["requires_action"] is True
    assert first["next_action"] == "reconnect"
    assert first["retry_at"] is None
    assert "do not retry automatically" in first["summary"]
    assert first["issue_id"].startswith("memory:")
    assert job.id not in first["issue_id"]
    assert "private" not in json.dumps(first)
    assert memory_health({"waiting": 1}, job)["issue_id"] == first["issue_id"]

    job.last_error = "a different private diagnostic"
    job.retry_at = None
    assert memory_health({"waiting": 2, "pending": 1}, job)["issue_id"] == first["issue_id"]
    job.attempts += 1
    next_attempt = memory_health({"waiting": 1}, job)["issue_id"]
    assert next_attempt != first["issue_id"]
    job.id = str(uuid.uuid4())
    next_job = memory_health({"waiting": 1}, job)["issue_id"]
    assert next_job != next_attempt
    job.error_kind = "dependency_unavailable"
    assert memory_health({"waiting": 1}, job)["issue_id"] != next_job


@pytest.mark.parametrize("kind", ["rate_limited", "bridge_unavailable", "invalid_model_output"])
def test_scheduled_failures_do_not_require_reconnection(kind):
    retry_at = utcnow() + timedelta(minutes=5)
    job = SimpleNamespace(
        id=str(uuid.uuid4()), attempts=1, error_kind=kind, provider="codex", retry_at=retry_at
    )
    health = memory_health({"waiting": 1}, job)
    assert health["state"] == ("limited" if kind == "rate_limited" else "waiting")
    assert health["requires_action"] is False
    assert health["next_action"] is None
    assert health["retry_at"] == retry_at
    assert "retry automatically" in health["summary"]
    assert health["issue_id"]


@pytest.mark.parametrize("kind", ["rate_limited", "bridge_unavailable", None])
def test_unscheduled_failures_require_a_service_check_without_promising_retry(kind):
    job = SimpleNamespace(
        id=str(uuid.uuid4()), attempts=1, error_kind=kind, provider="codex", retry_at=None
    )
    health = memory_health({"waiting": 1}, job)
    assert health["requires_action"] is True
    assert health["next_action"] == "check_service"
    assert "automatically" not in health["summary"]


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_retried_auth_job_stays_updating_until_consolidation_finishes(db_factory, status):
    async with db_factory() as db:
        project, session = await seed_project(db)
        job = job_for(project, session)
        db.add(job)
        await db.commit()
        blocked = await project_memory_health(db, project.id)
        job.status = status
        # Even if an older worker retained these fields, they are not active failures.
        job.retry_at = utcnow() + timedelta(minutes=5)
        await db.commit()
        updating = await project_memory_health(db, project.id)
        assert updating["state"] == "updating"
        assert updating["error_kind"] is None
        assert updating["issue_id"] is None
        assert updating["retry_at"] is None
        assert updating["requires_action"] is False
        assert updating["next_action"] is None
        assert "up to date" not in updating["summary"]

        job.status = "waiting"
        job.attempts += 1
        await db.commit()
        recurrence = await project_memory_health(db, project.id)
        assert recurrence["state"] == "connection_required"
        assert recurrence["issue_id"] != blocked["issue_id"]

        job.status = "completed"
        await db.commit()
        completed = await project_memory_health(db, project.id)
        assert completed["state"] == "updated"
        assert completed["issue_id"] is None
        assert completed["error_kind"] is None


async def test_global_health_prioritizes_auth_and_keeps_provider_and_project_scope(db_factory):
    async with db_factory() as db:
        project, session = await seed_project(db)
        other_project, other_session = await seed_project(db)
        auth = job_for(project, session, provider="claude", created_at=utcnow() - timedelta(days=1))
        rate = job_for(
            project, session, error_kind="rate_limited", retry_at=utcnow() + timedelta(minutes=5)
        )
        other = job_for(other_project, other_session)
        db.add_all([auth, rate, other])
        await db.commit()

        health = await project_memory_health(db, project.id, include_providers=True)
        assert health["state"] == "connection_required"
        assert health["provider"] == "claude"
        assert health["jobs"] == {"waiting": 2}
        assert health["issue_id"] == health["providers"]["claude"]["issue_id"]
        assert health["providers"]["codex"]["state"] == "limited"
        assert health["providers"]["codex"]["requires_action"] is False
        scoped = await project_memory_health(db, project.id, provider="codex")
        assert scoped == health["providers"]["codex"]
        other_health = await project_memory_health(db, other_project.id)
        assert other_health["jobs"] == {"waiting": 1}
        assert other_health["provider"] == "codex"
        assert other_health["issue_id"] != health["issue_id"]


async def test_unscheduled_wait_precedes_newer_transient_wait(db_factory):
    async with db_factory() as db:
        project, session = await seed_project(db)
        db.add_all([
            job_for(
                project, session, provider="claude", error_kind="bridge_unavailable",
                created_at=utcnow() - timedelta(hours=1),
            ),
            job_for(
                project, session, error_kind="rate_limited",
                retry_at=utcnow() + timedelta(minutes=5),
            ),
        ])
        await db.commit()
        health = await project_memory_health(db, project.id)
        assert health["provider"] == "claude"
        assert health["next_action"] == "check_service"


async def test_memory_status_api_and_briefing_share_actionable_failure_for_all_clients(
    api_client, db_factory
):
    client, _ = api_client
    async with db_factory() as db:
        project, session = await seed_project(db)
        blocked = job_for(
            project, session, provider="claude", created_at=utcnow() - timedelta(minutes=2),
            last_error="private provider diagnostic with credentials",
        )
        db.add_all([
            blocked,
            job_for(
                project, session, error_kind="rate_limited",
                retry_at=utcnow() + timedelta(minutes=5),
            ),
        ])
        await db.commit()
        project_id, blocked_id = project.id, blocked.id

    expected_issue = None
    for interactive_client in (None, "codex", "claude"):
        params = {"client": interactive_client} if interactive_client else {}
        response = await client.get(f"/projects/{project_id}/memory-status", params=params)
        assert response.status_code == 200
        assert "private provider diagnostic" not in response.text
        assert "last_error" not in response.text
        health = response.json()
        assert health["provider"] == health["latest_job"]["provider"] == "claude"
        assert health["latest_job"]["id"] == blocked_id
        assert health["requires_action"] is True
        assert health["next_action"] == "reconnect"
        expected_issue = expected_issue or health["issue_id"]
        assert health["issue_id"] == expected_issue

        briefing = await client.get(f"/projects/{project_id}/briefing", params=params)
        assert briefing.status_code == 200
        memory_status = briefing.json()["memory_status"]
        assert memory_status["issue_id"] == health["issue_id"]
        assert memory_status["providers"] == health["providers"]
        assert memory_status["requires_action"] is True
