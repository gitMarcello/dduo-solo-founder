from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from conftest import project_payload
from dduo_solo_founder import main
from dduo_solo_founder.models import BackupRecord, Memory, OutboxEvent, Project, SleepJob, Turn
from dduo_solo_founder.retrieval import build_retrieval_query
from dduo_solo_founder.service import utcnow


async def create_committed_turn(client, project_id: str):
    session = (
        await client.post(
            f"/projects/{project_id}/sessions",
            json={"client": "codex", "external_id": "memory-session"},
        )
    ).json()
    context = (
        await client.post(
            f"/projects/{project_id}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "memory-turn",
                "user_prompt": "Remember the Android release state",
            },
        )
    ).json()
    response = await client.post(
        f"/turns/{context['turn']['id']}/commit",
        json={"assistant_response": "Captured", "used_memory_ids": []},
    )
    assert response.status_code == 200
    return session, context["turn"]


async def test_sleep_status_request_list_retry_and_privacy(api_client, db_factory):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    session, turn = await create_committed_turn(client, project["id"])
    scheduled = (
        await client.post(
            f"/projects/{project['id']}/sleep",
            json={"session_id": session["id"], "trigger": "manual"},
        )
    ).json()
    assert scheduled["scheduled"] == 1
    job_id = scheduled["items"][0]["id"]
    jobs = (await client.get(f"/projects/{project['id']}/sleep-jobs")).json()["items"]
    assert jobs[0]["input_turn_ids"] == [turn["id"]]
    status = (await client.get(f"/projects/{project['id']}/memory-status")).json()
    assert status["jobs"]["pending"] == 1
    assert (
        await client.post(f"/projects/{project['id']}/sleep-jobs/{job_id}/retry")
    ).status_code == 202
    privacy = await client.patch(
        f"/projects/{project['id']}/sessions/{session['id']}/privacy",
        json={"off_record": True},
    )
    assert privacy.json()["off_record"] is True
    assert privacy.json()["excluded_turns"] == 1
    assert privacy.json()["cancelled_jobs"] == 1
    assert (
        await client.post(f"/projects/{project['id']}/sleep-jobs/{job_id}/retry")
    ).status_code == 409


async def test_open_secret_turn_is_excluded_and_privacy_reset_applies_only_to_future_turns(
    api_client,
    db_factory,
):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "infrastructure-session"},
        )
    ).json()
    secret_prompt = "VPS_PASSWORD=must-never-reappear"
    opened = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "secret-turn",
                "user_prompt": secret_prompt,
            },
        )
    ).json()["turn"]

    enabled = await client.patch(
        f"/projects/{project['id']}/sessions/{session['id']}/privacy",
        json={"off_record": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["excluded_turns"] == 1
    async with db_factory() as db:
        secret_turn = await db.get(Turn, opened["id"])
        assert secret_turn.off_record is True
        assert secret_turn.sleep_status == "skipped_off_record"

    disabled = await client.patch(
        f"/projects/{project['id']}/sessions/{session['id']}/privacy",
        json={"off_record": False},
    )
    assert disabled.status_code == 200
    assert (
        await client.post(
            f"/turns/{opened['id']}/commit",
            json={"assistant_response": "Infrastructure configured", "used_memory_ids": []},
        )
    ).status_code == 200
    future = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "ordinary-turn",
                "user_prompt": "Continue ordinary project work",
            },
        )
    ).json()["turn"]
    assert future["off_record"] is False

    async with db_factory() as db:
        secret_turn = await db.get(Turn, opened["id"])
        assert secret_turn.off_record is True
        assert secret_turn.sleep_status == "skipped_off_record"
        query = await build_retrieval_query(
            db,
            session_id=session["id"],
            current_prompt="What is next?",
        )
    assert secret_prompt not in query


async def test_replayed_off_record_turn_stays_excluded_after_session_privacy_reset(
    api_client,
    db_factory,
):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "offline-secret-session"},
        )
    ).json()
    assert (
        await client.patch(
            f"/projects/{project['id']}/sessions/{session['id']}/privacy",
            json={"off_record": True},
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/projects/{project['id']}/sessions/{session['id']}/privacy",
            json={"off_record": False},
        )
    ).status_code == 200

    secret_prompt = "VPS_PASSWORD=spooled-secret"
    replayed = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "offline-secret-turn",
                "user_prompt": secret_prompt,
                "off_record": True,
            },
        )
    ).json()["turn"]
    assert replayed["off_record"] is True
    assert (
        await client.post(
            f"/turns/{replayed['id']}/commit",
            json={"assistant_response": "Configured", "used_memory_ids": []},
        )
    ).status_code == 200

    async with db_factory() as db:
        stored = await db.get(Turn, replayed["id"])
        assert stored.off_record is True
        assert stored.sleep_status == "skipped_off_record"
        query = await build_retrieval_query(
            db,
            session_id=session["id"],
            current_prompt="Continue ordinary work",
        )
    assert secret_prompt not in query

    scheduled = await client.post(
        f"/projects/{project['id']}/sleep",
        json={"session_id": session["id"], "trigger": "manual"},
    )
    assert scheduled.status_code == 202
    assert scheduled.json()["scheduled"] == 0


async def test_memory_status_hides_cli_output_and_opens_setup_without_exposing_token(
    api_client, db_factory, monkeypatch
):
    client, _ = api_client
    project = project_payload(root_path="/Users/example/ExampleApp")
    await client.post("/projects", json=project)
    session, _ = await create_committed_turn(client, project["id"])
    async with db_factory() as db:
        job = SleepJob(
            project_id=project["id"],
            session_id=session["id"],
            provider="claude",
            trigger="manual",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="auth_required",
            last_error="Claude CLI exited with 1: secret output",
        )
        db.add(job)
        await db.commit()
    status = (await client.get(f"/projects/{project['id']}/memory-status")).json()
    jobs = (await client.get(f"/projects/{project['id']}/sleep-jobs")).json()["items"]
    assert status["state"] == "connection_required"
    assert "Connect Claude" in status["summary"]
    assert "secret output" not in jobs[0].get("message", "")
    assert "last_error" not in jobs[0]
    class SetupResponse:
        status_code = 202

    calls = []
    monkeypatch.setattr(main, "get_settings", lambda: type("Settings", (), {
        "cli_bridge_url": "http://host.docker.internal:45123",
        "cli_bridge_token": "private-setup-token",
    })())
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SetupResponse(),
    )
    setup = await client.post(f"/projects/{project['id']}/setup")
    assert setup.status_code == 202
    assert setup.json() == {"opened": True}
    assert "private-setup-token" not in setup.text
    assert calls[0][0][0].endswith("/v1/setup/open")
    assert calls[0][1]["json"] == {"project_root": "/Users/example/ExampleApp"}
    assert (
        await client.patch(
            f"/projects/{project['id']}/sessions/{uuid.uuid4()}/privacy",
            json={"off_record": True},
        )
    ).status_code == 404


async def test_memory_status_is_server_scoped_for_every_interactive_client(
    api_client, db_factory
):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    session, _ = await create_committed_turn(client, project["id"])
    async with db_factory() as db:
        blocked = SleepJob(
            project_id=project["id"],
            session_id=session["id"],
            provider="claude",
            trigger="idle",
            status="waiting",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            error_kind="auth_required",
            created_at=utcnow() - timedelta(minutes=2),
        )
        completed = SleepJob(
            project_id=project["id"],
            session_id=session["id"],
            provider="codex",
            trigger="manual",
            status="completed",
            dedupe_key=str(uuid.uuid4()),
            input_turn_ids=[],
            created_at=utcnow(),
        )
        db.add_all([blocked, completed])
        await db.commit()
    status = (await client.get(f"/projects/{project['id']}/memory-status")).json()
    assert status["state"] == "connection_required"
    assert status["provider"] == "claude"
    assert status["providers"]["claude"]["state"] == "connection_required"
    assert status["providers"]["codex"]["state"] == "updated"
    scoped_status = (
        await client.get(f"/projects/{project['id']}/memory-status", params={"client": "codex"})
    ).json()
    assert scoped_status["state"] == "connection_required"
    assert scoped_status["latest_job"]["provider"] == "claude"
    assert scoped_status["providers"] == status["providers"]
    assert (
        await client.get(f"/projects/{project['id']}/memory-status", params={"client": "other"})
    ).status_code == 422

    global_briefing = (await client.get(f"/projects/{project['id']}/briefing")).json()
    codex_briefing = (
        await client.get(f"/projects/{project['id']}/briefing", params={"client": "codex"})
    ).json()
    claude_briefing = (
        await client.get(f"/projects/{project['id']}/briefing", params={"client": "claude"})
    ).json()
    assert global_briefing["memory_status"]["state"] == "connection_required"
    assert codex_briefing["memory_status"]["state"] == "connection_required"
    assert claude_briefing["memory_status"]["state"] == "connection_required"
    assert (
        await client.get(f"/projects/{project['id']}/briefing", params={"client": "other"})
    ).status_code == 422

    turn_context = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "provider-scoped-turn",
                "user_prompt": "Continue from Codex",
            },
        )
    ).json()
    assert turn_context["memory_status"]["state"] == "connection_required"


async def test_memory_listing_provenance_revisions_and_forget(api_client, db_factory):
    client, embeddings = api_client
    project = project_payload()
    other_project = project_payload(name="Other project")
    await client.post("/projects", json=project)
    await client.post("/projects", json=other_project)
    _, turn = await create_committed_turn(client, project["id"])
    group = str(uuid.uuid4())
    async with db_factory() as db:
        first = Memory(
            project_id=project["id"],
            node_type="reusable_fact",
            node_key="release-date",
            text="Release Thursday",
            status="superseded",
            memory_group_id=group,
            revision=1,
            source_turn_ids=[turn["id"]],
        )
        current = Memory(
            project_id=project["id"],
            node_type="reusable_fact",
            node_key="release-date",
            text="Release Friday",
            memory_group_id=group,
            revision=2,
            source_turn_ids=[turn["id"]],
        )
        unrelated = Memory(
            project_id=other_project["id"],
            node_type="reusable_fact",
            node_key="private-fact",
            text="Belongs to another project",
        )
        db.add_all([first, current, unrelated])
        await db.commit()
        await db.refresh(current)
        embeddings.search_results = [
            {"id": first.id, "score": 0.99},
            {"id": unrelated.id, "score": 0.98},
            {"id": current.id, "score": 0.97},
        ]
    listed = (await client.get(f"/projects/{project['id']}/memories")).json()["items"]
    assert [item["text"] for item in listed] == ["Release Friday"]
    searched = (
        await client.get(f"/projects/{project['id']}/memories/search", params={"q": "release"})
    ).json()["items"]
    assert [item["id"] for item in searched] == [current.id]
    provenance = (
        await client.get(f"/projects/{project['id']}/memories/{current.id}/provenance")
    ).json()
    assert len(provenance["revisions"]) == 2
    revisions = (
        await client.get(f"/projects/{project['id']}/memories/{current.id}/revisions")
    ).json()
    assert len(revisions["items"]) == 2
    forgotten = await client.post(
        f"/projects/{project['id']}/memories/forget",
        json={"memory_id": current.id, "rationale": "User requested removal"},
    )
    assert forgotten.json()["forgotten"] == 2
    async with db_factory() as db:
        assert len((await db.scalars(select(OutboxEvent))).all()) == 2


async def test_artifact_api_deduplicates_binary_sources(api_client):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    _, turn = await create_committed_turn(client, project["id"])
    payload = {
        "turn_id": turn["id"],
        "kind": "document",
        "filename": "release.md",
        "mime_type": "text/markdown",
        "content_base64": base64.b64encode(b"Friday release").decode(),
        "extracted_text": "Friday release",
    }
    first = (await client.post(f"/projects/{project['id']}/artifacts", json=payload)).json()
    second = (await client.post(f"/projects/{project['id']}/artifacts", json=payload)).json()
    assert first["created"] is True and second["created"] is False
    items = (await client.get(f"/projects/{project['id']}/artifacts")).json()["items"]
    assert items[0]["filename"] == "release.md"


async def test_api_recovery_and_error_paths_keep_work_and_memory_readable(api_client, db_factory):
    """Exercise the non-blocking endpoints used by hooks and the local Work UI."""
    client, embeddings = api_client
    missing = str(uuid.uuid4())
    assert (await client.get("/health")).json()["status"] == "ok"
    assert (
        await client.post(
            f"/projects/{missing}/sessions", json={"client": "codex", "external_id": "missing"}
        )
    ).status_code == 404

    project = project_payload()
    assert (await client.post("/projects", json=project)).status_code == 200
    moved = dict(project, root_path="/tmp/moved-project")
    assert (await client.post("/projects", json=moved)).json()["root_path"] == "/tmp/moved-project"
    assert (await client.patch(f"/projects/{missing}", json={"cause": "x"})).status_code == 404
    assert (await client.get(f"/projects/{missing}/memories")).status_code == 404
    assert (await client.get(f"/projects/{project['id']}/memories", params={"status": ""})).status_code == 200

    session = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "stable-session"},
        )
    ).json()
    repeated = (
        await client.post(
            f"/projects/{project['id']}/sessions",
            json={"client": "codex", "external_id": "stable-session"},
        )
    ).json()
    assert repeated["id"] == session["id"]
    assert (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={"session_id": missing, "external_id": "bad", "user_prompt": "bad"},
        )
    ).status_code == 404
    begun = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "stable-turn",
                "user_prompt": "Keep this release constraint",
            },
        )
    ).json()
    replayed = (
        await client.post(
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": "stable-turn",
                "user_prompt": "Keep this release constraint",
            },
        )
    ).json()
    assert replayed["turn"]["id"] == begun["turn"]["id"]
    assert (await client.post(f"/turns/{missing}/commit", json={"assistant_response": "x"})).status_code == 404
    assert (await client.post(f"/turns/{missing}/stop-check", json={"assistant_response": "x"})).status_code == 404
    committed = await client.post(
        f"/turns/{begun['turn']['id']}/stop-check",
        json={"assistant_response": "Saved", "used_memory_ids": ["not-retrieved"]},
    )
    assert committed.json()["discarded_memory_ids"] == ["not-retrieved"]
    assert (
        await client.post(
            f"/projects/{project['id']}/sleep", json={"session_id": missing, "trigger": "manual"}
        )
    ).status_code == 404
    assert (await client.post(f"/projects/{project['id']}/sleep-jobs/{missing}/retry")).status_code == 404
    assert (await client.get(f"/projects/{project['id']}/memories/{missing}/provenance")).status_code == 404
    assert (
        await client.post(
            f"/projects/{project['id']}/memories/forget",
            json={"memory_id": missing, "rationale": "not present"},
        )
    ).status_code == 404

    async with db_factory() as db:
        active = Memory(
            project_id=project["id"],
            node_type="heuristic",
            node_key="release-constraint",
            text="Keep rollback ready",
        )
        inactive = Memory(
            project_id=project["id"],
            node_type="episode",
            node_key="old-note",
            text="Old note",
            status="superseded",
        )
        db.add_all([active, inactive])
        await db.commit()
        await db.refresh(active)
        embeddings.inventory_points = {"stale-vector": {}, active.id: {"revision": 0}}

    reconciled = await client.post(f"/projects/{project['id']}/memories/reconcile")
    assert reconciled.json()["queued"] == 1
    assert embeddings.deleted_points == ["stale-vector"]
    reindexed = await client.post(f"/projects/{project['id']}/memories/reindex")
    assert reindexed.json()["queued"] == 1
    assert embeddings.reset_projects == [project["id"]]
    assert (
        await client.post(
            f"/projects/{project['id']}/events",
            json={"event_type": "system", "session_id": missing},
        )
    ).status_code == 404
    event = await client.post(
        f"/projects/{project['id']}/events",
        json={"event_type": "system", "session_id": session["id"], "payload": {"ok": True}},
    )
    assert event.status_code == 200
    assert (
        await client.post(
            f"/projects/{project['id']}/artifacts", json={"turn_id": missing, "filename": "missing.txt"}
        )
    ).status_code == 404
    assert (await client.get(f"/projects/{missing}/artifacts")).status_code == 404
    assert (
        await client.post(
            f"/projects/{project['id']}/compactions",
            json={"session_id": missing, "phase": "pre"},
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/projects/{project['id']}/tasks/{missing}/attachments",
            json={"filename": "missing.txt"},
        )
    ).status_code == 404


async def test_lifespan_marks_interrupted_backups_failed(db_factory, monkeypatch):
    project = project_payload()
    async with db_factory() as db:
        db.add(Project(**project))
        db.add_all(
            [
                BackupRecord(project_id=project["id"], trigger="manual", status="scheduled"),
                BackupRecord(project_id=project["id"], trigger="manual", status="running"),
            ]
        )
        await db.commit()

    async def no_init():
        return None

    monkeypatch.setattr(main, "SessionLocal", db_factory)
    monkeypatch.setattr(main, "init_db", no_init)
    async with main.app.router.lifespan_context(main.app):
        pass
    async with db_factory() as db:
        statuses = list((await db.scalars(select(BackupRecord.status))).all())
    assert statuses == ["failed", "failed"]


async def test_restore_registration_validates_provenance_and_is_idempotent(api_client):
    client, _ = api_client
    project = project_payload()
    await client.post("/projects", json=project)
    manifest = {
        "format": "dduo-solo-founder-backup",
        "schema_version": 1,
        "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc).isoformat(),
        "project": {"id": project["id"], "name": project["name"]},
        "qdrant": {"included": True, "collection": "memory"},
    }
    payload = {
        "archive_name": "restored-project.dduobackup",
        "size_bytes": 123,
        "manifest": manifest,
    }
    restored = await client.post(
        f"/projects/{project['id']}/backups/register-restore",
        json=payload,
    )
    assert restored.status_code == 200
    assert restored.json()["trigger"] == "restore"
    assert restored.json()["status"] == "verified"
    assert restored.json()["includes_qdrant"] is True

    replay = await client.post(
        f"/projects/{project['id']}/backups/register-restore",
        json=payload,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == restored.json()["id"]

    wrong_project = {
        **payload,
        "archive_name": "wrong-project.dduobackup",
        "manifest": {**manifest, "project": {"id": "another", "name": "Other"}},
    }
    assert (
        await client.post(
            f"/projects/{project['id']}/backups/register-restore",
            json=wrong_project,
        )
    ).status_code == 422

    naive_time = {
        **payload,
        "archive_name": "naive-time.dduobackup",
        "manifest": {**manifest, "created_at": "2026-08-28T00:00:00"},
    }
    assert (
        await client.post(
            f"/projects/{project['id']}/backups/register-restore",
            json=naive_time,
        )
    ).status_code == 422
