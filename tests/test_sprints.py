from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from conftest import project_payload
from dduo_solo_founder.models import (
    Base,
    Memory,
    OutboxEvent,
    Plan,
    Project,
    Sprint,
    SprintMutation,
    SprintTaskSnapshot,
    Task,
    TaskRevision,
)
from dduo_solo_founder.schemas import SprintArchive, SprintCreate, SprintHistoryCreate, SprintUpdate
from dduo_solo_founder.service import apply_task_action, briefing
from dduo_solo_founder.schemas import TaskAction
from dduo_solo_founder.task_index import render_task_document, task_index_payload
from dduo_solo_founder.task_views import compact_task, snapshot_hash


async def setup_project(client):
    project = project_payload()
    response = await client.post("/projects", json=project)
    assert response.status_code == 200, response.text
    return f"/projects/{project['id']}", project["id"]


async def sprint(client, base, title="A sprint"):
    result = await client.post(
        base + "/sprints", json={"title": title, "idempotency_key": str(uuid.uuid4())}
    )
    assert result.status_code == 200, result.text
    return result.json()


async def task(client, base, **values):
    result = await client.post(base + "/tasks", json={"title": "Work", **values})
    assert result.status_code == 200, result.text
    return result.json()


async def transition(client, base, current, action, **fields):
    latest = (await client.get(base + f"/sprints/{current['id']}")).json()
    result = await client.post(
        base + f"/sprints/{current['id']}/{action}",
        json={
            "expected_version": latest["version"],
            "idempotency_key": str(uuid.uuid4()),
            **fields,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


async def test_close_preserves_outcomes_memory_links_revisions_and_retries(api_client, db_factory):
    client, _ = api_client
    base, project_id = await setup_project(client)
    current = await sprint(client, base)
    next_sprint = await sprint(client, base, "Next")
    await transition(client, base, current, "start")
    epic = await task(client, base, title="Parent", kind="epic")
    finished = await task(
        client, base, title="Delivered", status="done", sprint_id=current["id"], epic_id=epic["id"]
    )
    unfinished = await task(
        client, base, title="Carry", status="blocked", sprint_id=current["id"], epic_id=epic["id"]
    )
    plan = (
        await client.post(
            base + "/plans",
            json={"title": "Independent", "work_item_ids": [unfinished["id"], epic["id"]]},
        )
    ).json()
    async with db_factory() as db:
        db.add(
            Memory(project_id=project_id, node_type="fact", node_key="fact", text="Durable memory")
        )
        await db.commit()
        before_dirty = (await db.get(Project, project_id)).backup_generation
    preview = (await client.get(base + f"/sprints/{current['id']}/close-preview")).json()
    assert preview["total"] == 2 and preview["unfinished_count"] == 1
    body = {
        "expected_version": preview["sprint"]["version"],
        "idempotency_key": "close-retry-1",
        "unfinished_destination": "sprint",
        "destination_sprint_id": next_sprint["id"],
    }
    response = await client.post(base + f"/sprints/{current['id']}/archive", json=body)
    assert response.status_code == 200, response.text
    archived = response.json()
    repeated = await client.post(base + f"/sprints/{current['id']}/archive", json=body)
    assert repeated.json() == archived
    history = (await client.get(base + f"/sprints/{current['id']}/tasks")).json()
    outcomes = {row["id"]: row for row in history["items"]}
    assert outcomes[unfinished["id"]]["outcome"] == "blocked"
    assert outcomes[unfinished["id"]]["sprint_id"] == current["id"]
    assert outcomes[unfinished["id"]]["destination_sprint_id"] == next_sprint["id"]
    assert outcomes[finished["id"]]["outcome"] == "done"
    assert "description" not in outcomes[finished["id"]]
    carried = (await client.get(base + f"/tasks/{unfinished['id']}?detail=full")).json()["task"]
    assert carried["sprint_id"] == next_sprint["id"] and carried["status"] == "blocked"
    assert carried["epic_id"] == epic["id"]
    assert (await client.get(base + f"/plans/{plan['id']}")).json()["plan"]["work_item_ids"] == [
        unfinished["id"],
        epic["id"],
    ]
    async with db_factory() as db:
        assert await db.scalar(sa.select(sa.func.count(Memory.id))) == 1
        assert (
            await db.scalar(
                sa.select(sa.func.count(TaskRevision.id)).where(
                    TaskRevision.task_id == unfinished["id"]
                )
            )
            == 2
        )
        assert await db.scalar(sa.select(sa.func.count(SprintTaskSnapshot.task_id))) == 2
        assert (await db.get(Project, project_id)).backup_generation > before_dirty
        assert (
            await db.scalar(
                sa.select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == unfinished["id"],
                    OutboxEvent.payload["task_version"].as_integer() == carried["version"],
                )
            )
            is not None
        )
    # Later work, task reopening and sprint reopening never rewrite this story.
    assert (
        await client.patch(
            base + f"/tasks/{unfinished['id']}",
            json={
                "status": "done",
                "expected_version": carried["version"],
            },
        )
    ).status_code == 200
    reopened = await transition(client, base, archived, "reopen")
    assert reopened["status"] == "planned"
    assert (await client.get(base + f"/sprints/{current['id']}/tasks")).json()["items"] == history[
        "items"
    ]
    closed_again = await transition(
        client, base, reopened, "archive", unfinished_destination="backlog"
    )
    old = (
        await client.get(
            base + f"/sprints/{current['id']}/tasks?closure_version={archived['archive_version']}"
        )
    ).json()
    assert old["items"] == history["items"]
    assert closed_again["archive_version"] != archived["archive_version"]


async def test_preview_conflict_and_archive_validation_are_atomic(api_client, db_factory):
    client, _ = api_client
    base, project_id = await setup_project(client)
    other, _ = await setup_project(client)
    current = await sprint(client, base)
    foreign = await sprint(client, other)
    work = await task(client, base, sprint_id=current["id"])
    preview = (await client.get(base + f"/sprints/{current['id']}/close-preview")).json()["sprint"]
    edit = await client.patch(
        base + f"/tasks/{work['id']}",
        json={"status": "blocked", "expected_version": work["version"]},
    )
    assert edit.status_code == 200
    body = {
        "expected_version": preview["version"],
        "idempotency_key": "stale-close",
        "unfinished_destination": "backlog",
    }
    assert (
        await client.post(base + f"/sprints/{current['id']}/archive", json=body)
    ).status_code == 409
    latest = (await client.get(base + f"/sprints/{current['id']}")).json()
    for destination, code in [(foreign["id"], 404), (current["id"], 422), ("missing", 404)]:
        response = await client.post(
            base + f"/sprints/{current['id']}/archive",
            json={
                **body,
                "expected_version": latest["version"],
                "unfinished_destination": "sprint",
                "destination_sprint_id": destination,
            },
        )
        assert response.status_code == code, response.text
    assert (await client.get(base + f"/sprints/{current['id']}")).json()["version"] == latest[
        "version"
    ]
    async with db_factory() as db:
        assert await db.scalar(sa.select(sa.func.count(SprintTaskSnapshot.task_id))) == 0
        assert (await db.get(Task, work["id"])).sprint_id == current["id"]
    closed = await transition(client, base, current, "archive", unfinished_destination="backlog")
    assert closed["status"] == "archived"
    backlog = (await client.get(base + "/tasks?placement=backlog&detail=compact")).json()
    assert backlog["items"][0]["status"] == "blocked"
    assert backlog["items"][0]["sprint_id"] is None
    assert (await client.get(other + f"/sprints/{current['id']}")).status_code == 404
    assert (await client.get(other + f"/sprints/{current['id']}/tasks")).status_code == 404
    # The database, not just HTTP prevalidation, owns the active-sprint invariant.
    async with db_factory() as db:
        db.add_all(
            [
                Sprint(project_id=project_id, title="one", status="active"),
                Sprint(project_id=project_id, title="two", status="active"),
            ]
        )
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_task_placement_ownership_reopen_and_live_version_invalidation(api_client):
    client, _ = api_client
    base, _ = await setup_project(client)
    other, _ = await setup_project(client)
    current = await sprint(client, base)
    foreign = await sprint(client, other)
    for values in [
        {"sprint_id": foreign["id"]},
        {"sprint_id": "missing"},
        {"kind": "epic", "sprint_id": current["id"]},
    ]:
        assert (
            await client.post(base + "/tasks", json={"title": "invalid", **values})
        ).status_code == 422
    done = await task(client, base, status="done", sprint_id=current["id"])
    closed = await transition(client, base, current, "archive", unfinished_destination="backlog")
    assert (
        await client.post(base + "/tasks", json={"title": "invalid", "sprint_id": current["id"]})
    ).status_code == 422
    assert (
        await client.patch(
            base + f"/tasks/{done['id']}",
            json={"status": "todo", "expected_version": done["version"]},
        )
    ).status_code == 422
    assert (
        await client.patch(
            base + f"/tasks/{done['id']}",
            json={"sprint_id": current["id"], "expected_version": done["version"]},
        )
    ).status_code == 422
    reopened = await client.patch(
        base + f"/tasks/{done['id']}",
        json={
            "sprint_id": None,
            "status": "in_progress",
            "expected_version": done["version"],
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert (await client.get(base + f"/sprints/{closed['id']}/tasks")).json()["items"][0][
        "status"
    ] == "done"
    assert (await client.get(base + "/tasks?placement=backlog")).json()["items"][0][
        "status"
    ] == "in_progress"


async def test_paginated_inventory_over_100_and_exact_links_and_epic_counts(api_client, db_factory):
    client, _ = api_client
    base, project_id = await setup_project(client)
    current = await sprint(client, base)
    await transition(client, base, current, "start")
    async with db_factory() as db:
        epic = Task(project_id=project_id, title="Epic", kind="epic")
        db.add(epic)
        await db.flush()
        rows = [
            Task(
                project_id=project_id,
                title=f"Archive {i:03}",
                description="deep matching text " * 500,
                status="done",
                labels=["exact"],
                epic_id=epic.id,
            )
            for i in range(135)
        ]
        db.add_all(rows)
        db.add(Task(project_id=project_id, title="Current", sprint_id=current["id"]))
        db.add(Task(project_id=project_id, title="Backlog", status="blocked"))
        db.add_all(
            [
                Plan(project_id=project_id, title=f"Plan {i:03}", content="long plan " * 500)
                for i in range(115)
            ]
        )
        await db.commit()
        last_id = rows[-1].id
    first = (
        await client.get(
            base + "/tasks?placement=archive&detail=compact&limit=100&q=deep&label=exact"
        )
    ).json()
    second = (
        await client.get(
            base + "/tasks?placement=archive&detail=compact&limit=100&offset=100&q=deep&label=exact"
        )
    ).json()
    assert first["total"] == second["total"] == 135
    assert len(first["items"]) == 100 and len(second["items"]) == 35
    assert len({row["id"] for row in first["items"] + second["items"]}) == 135
    assert all("description" not in row for row in first["items"])
    assert (
        (await client.get(base + f"/tasks/{last_id}?detail=full"))
        .json()["task"]["description"]
        .startswith("deep matching")
    )
    active = (await client.get(base + "/tasks?placement=current&scope=all")).json()
    assert [row["title"] for row in active["items"]] == ["Current"]
    backlog = (await client.get(base + "/tasks?placement=backlog")).json()
    assert [row["title"] for row in backlog["items"]] == ["Backlog"]
    epics = (await client.get(base + "/tasks?kind=epic&detail=compact")).json()
    assert epics["items"][0]["task_counts"] == {"total": 135, "completed": 135, "open": 0}
    plans = (await client.get(base + "/plans?detail=compact&limit=100&offset=100&q=long")).json()
    assert plans["total"] == 115 and len(plans["items"]) == 15
    assert "content" not in plans["items"][0]
    fetched = (await client.get(base + f"/plans/{plans['items'][0]['id']}")).json()["plan"]
    assert fetched["content"].startswith("long plan")
    assert (await client.get(base + "/plans/missing")).status_code == 404
    assert (await client.get(base + "/tasks?limit=201")).status_code == 422
    historical = (
        await client.post(
            base + "/sprints/history",
            json={
                "title": "Verified historical selection",
                "idempotency_key": "bulk-history-135",
                "task_ids": [row["id"] for row in first["items"] + second["items"]],
            },
        )
    ).json()
    archived_page = (
        await client.get(base + f"/sprints/{historical['id']}/tasks?limit=100&offset=100")
    ).json()
    assert archived_page["total"] == 135 and len(archived_page["items"]) == 35
    assert all(
        row["outcome"] == "done" and "description" not in row for row in archived_page["items"]
    )


async def test_explicit_historical_sprint_is_bounded_and_never_fabricated(api_client, db_factory):
    client, _ = api_client
    base, project_id = await setup_project(client)
    other, _ = await setup_project(client)
    terminal = await task(client, base, status="cancelled", description="Original history")
    open_task = await task(client, base)
    foreign = await task(client, other, status="done")
    for ids in [[open_task["id"]], [foreign["id"]], ["missing"]]:
        result = await client.post(
            base + "/sprints/history",
            json={
                "title": "Invalid",
                "task_ids": ids,
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert result.status_code == 422, result.text
    assert (await client.get(base + "/sprints")).json()["total"] == 0
    body = {
        "title": "Prior delivery",
        "task_ids": [terminal["id"]],
        "idempotency_key": "historical-1",
    }
    created = await client.post(base + "/sprints/history", json=body)
    assert created.status_code == 200, created.text
    historical = created.json()
    assert historical["status"] == "archived"
    assert (await client.post(base + "/sprints/history", json=body)).json() == historical
    assert (
        await client.post(base + "/sprints/history", json={**body, "title": "Different"})
    ).status_code == 409
    assert (await client.get(base + f"/sprints/{historical['id']}/tasks?q=Work&limit=1")).json()[
        "items"
    ][0]["outcome"] == "cancelled"
    assert (await client.get(base + "/sprints?status=archived&limit=1")).json()["total"] == 1
    async with db_factory() as db:
        assert (await db.get(Task, terminal["id"])).description == "Original history"
        assert (
            await db.scalar(
                sa.select(sa.func.count(SprintMutation.project_id)).where(
                    SprintMutation.project_id == project_id
                )
            )
            == 1
        )


async def test_lifecycle_conflicts_update_and_no_cross_project_mutations(api_client):
    client, _ = api_client
    base, _ = await setup_project(client)
    current = await sprint(client, base)
    next_sprint = await sprint(client, base, "Next")
    changed = await client.patch(
        base + f"/sprints/{current['id']}",
        json={
            "title": "Renamed",
            "objective": "Ship",
            "expected_version": current["version"],
            "idempotency_key": "rename-123",
        },
    )
    assert changed.status_code == 200 and changed.json()["title"] == "Renamed"
    active = await transition(client, base, current, "start")
    for item, action in [(next_sprint, "start"), (active, "start"), (active, "reopen")]:
        result = await client.post(
            base + f"/sprints/{item['id']}/{action}",
            json={
                "expected_version": item["version"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert result.status_code == 409, result.text
    closed = await transition(client, base, current, "archive", unfinished_destination="backlog")
    for action, extra in [("archive", {"unfinished_destination": "backlog"}), ("start", {})]:
        assert (
            await client.post(
                base + f"/sprints/{current['id']}/{action}",
                json={
                    "expected_version": closed["version"],
                    "idempotency_key": str(uuid.uuid4()),
                    **extra,
                },
            )
        ).status_code == 409
    assert (
        await client.patch(
            base + f"/sprints/{current['id']}",
            json={
                "title": "No rewrite",
                "expected_version": closed["version"],
                "idempotency_key": "edit-archive",
            },
        )
    ).status_code == 409
    assert (await client.get("/projects/missing/sprints")).status_code == 404
    assert (
        await client.post(
            "/projects/missing/sprints",
            json={"title": "Missing", "idempotency_key": "missing-project"},
        )
    ).status_code == 404


async def test_brief_and_turn_actions_respect_live_placement(api_client, db_factory):
    client, _ = api_client
    base, project_id = await setup_project(client)
    current = await sprint(client, base)
    future = await sprint(client, base, "Future")
    await task(client, base, title="Backlog")
    work = await task(client, base, title="Current", sprint_id=current["id"])
    await task(client, base, title="Future", sprint_id=future["id"])
    await transition(client, base, current, "start")
    async with db_factory() as db:
        db.add(Plan(project_id=project_id, title="Finished plan", status="completed"))
        await db.commit()
        result = await briefing(db, project_id)
        assert [item["title"] for item in result["tasks"]] == ["Current"]
        assert result["plans"] == []
        latest = await db.get(Sprint, current["id"])
        previous_version = latest.version
        await apply_task_action(
            db,
            project_id,
            TaskAction(
                action="update",
                task_id=work["id"],
                status="in_progress",
                expected_version=work["version"],
            ),
        )
        assert latest.version == previous_version + 1
        await db.commit()
    await transition(client, base, current, "archive", unfinished_destination="backlog")
    async with db_factory() as db:
        result = await briefing(db, project_id)
        assert {item["title"] for item in result["tasks"]} == {"Current", "Backlog"}


async def test_semantic_placement_is_filtered_in_qdrant_and_again_after_sql_hydration(
    api_client, db_factory, monkeypatch
):
    from dduo_solo_founder import main
    from test_api import TaskMarkerMixin

    client, _ = api_client
    base, project_id = await setup_project(client)
    current = await sprint(client, base)
    historical = await sprint(client, base, "Historical")
    await transition(client, base, current, "start")
    target = await task(client, base, title="Improve OCR current", sprint_id=current["id"])
    backlog = await task(client, base, title="Improve OCR backlog")
    closed = await task(
        client, base, title="Improve OCR archived", status="done", sprint_id=historical["id"]
    )
    await transition(client, base, historical, "archive", unfinished_destination="backlog")
    async with db_factory() as db:
        await db.execute(sa.delete(OutboxEvent).where(OutboxEvent.project_id == project_id))
        rows = (await db.scalars(sa.select(Task).where(Task.project_id == project_id))).all()
        inventory = {row.id: task_index_payload(row, render_task_document(row)) for row in rows}
        await db.commit()
    calls = []

    class Index(TaskMarkerMixin):
        stale = False

        def inventory(self, _project_id):
            return inventory

        def render(self, row):
            return render_task_document(row)

        def search_observed(self, *_args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                items=[
                    {**record, "score": 0.9, **({"sprint_id": "obsolete"} if self.stale else {})}
                    for record in inventory.values()
                ],
                embedding=None,
                vector_store_duration_ms=1,
            )

    index = Index()
    monkeypatch.setattr(main, "task_index_service", lambda: index)
    for placement, expected in [("current", target), ("backlog", backlog), ("archive", closed)]:
        result = await client.post(
            base + "/tasks/search",
            json={"query": "recognition quality", "scope": "all", "placement": placement},
        )
        assert result.status_code == 200, result.text
        assert [item["id"] for item in result.json()["items"]] == [expected["id"]]
        assert result.json()["match_type"] == "semantic"
        assert calls[-1]["placement"] == placement
    assert calls[0]["sprint_ids"] == [current["id"]]
    explicit = (
        await client.post(
            base + "/tasks/search",
            json={
                "query": "recognition quality",
                "scope": "all",
                "placement": "archive",
                "sprint_id": historical["id"],
            },
        )
    ).json()
    assert [row["id"] for row in explicit["items"]] == [closed["id"]]
    assert calls[-1]["include_unsprinted"] is False
    index.stale = True
    repaired = (
        await client.post(
            base + "/tasks/search",
            json={
                "query": "Improve OCR",
                "scope": "all",
                "placement": "current",
            },
        )
    ).json()
    assert repaired["stale_hits"] == 3 and repaired["degraded"] is True
    assert [row["id"] for row in repaired["items"]] == [target["id"]]
    # Exact-ID and title routes obey the same placement independently of legacy scope.
    exact = (
        await client.post(
            base + "/tasks/search",
            json={
                "query": target["id"],
                "placement": "current",
                "sprint_id": current["id"],
            },
        )
    ).json()
    assert exact["match_type"] == "exact_id"
    exact_title = (
        await client.post(
            base + "/tasks/search",
            json={
                "query": target["title"],
                "placement": "current",
            },
        )
    ).json()
    assert exact_title["match_type"] == "exact_title"
    wrong_placement = (
        await client.post(
            base + "/tasks/search",
            json={
                "query": target["id"],
                "placement": "archive",
                "scope": "all",
                "sprint_id": current["id"],
            },
        )
    ).json()
    assert wrong_placement["items"] == []


@pytest.mark.parametrize(
    "schema,body",
    [
        (SprintCreate, {"title": " ", "idempotency_key": "12345678"}),
        (SprintUpdate, {"title": None, "expected_version": 1, "idempotency_key": "12345678"}),
        (SprintUpdate, {"title": " ", "expected_version": 1, "idempotency_key": "12345678"}),
        (
            SprintArchive,
            {
                "expected_version": 1,
                "idempotency_key": "12345678",
                "unfinished_destination": "sprint",
            },
        ),
        (
            SprintArchive,
            {
                "expected_version": 1,
                "idempotency_key": "12345678",
                "unfinished_destination": "backlog",
                "destination_sprint_id": "other",
            },
        ),
        (
            SprintHistoryCreate,
            {"title": "Old", "task_ids": ["same", "same"], "idempotency_key": "12345678"},
        ),
        (SprintHistoryCreate, {"title": "Old", "task_ids": [""], "idempotency_key": "12345678"}),
    ],
)
def test_sprint_schema_rejects_ambiguous_mutations(schema, body):
    with pytest.raises(ValueError):
        schema.model_validate(body)


def test_sprint_changes_compact_snapshot_and_projection_without_embedding_content_change():
    row = Task(id="task", project_id="project", title="Same task", version=1)
    before = snapshot_hash(compact_task(row))
    semantic = render_task_document(row)
    row.sprint_id = "sprint"
    assert snapshot_hash(compact_task(row)) != before
    assert render_task_document(row).semantic_hash == semantic.semantic_hash
    assert task_index_payload(row, semantic)["sprint_id"] == "sprint"


def test_sprint_migration_preserves_preexisting_history_and_enforces_ownership():
    source = Path(__file__).parents[1] / "migrations/versions/e72b1d4c9a60_add_project_sprints.py"
    spec = importlib.util.spec_from_file_location("sprint_migration", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ScriptDirectory(str(source.parents[1])).get_current_head() == module.revision
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql(
            "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, task_index_reconciled BOOLEAN)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), kind VARCHAR(20), status VARCHAR(30), description TEXT, version INTEGER)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE task_revisions (id VARCHAR(36) PRIMARY KEY, snapshot JSON)"
        )
        connection.exec_driver_sql("INSERT INTO projects VALUES ('p1', 1), ('p2', 1)")
        connection.exec_driver_sql(
            "INSERT INTO tasks VALUES ('old', 'p1', 'task', 'done', 'original description', 7)"
        )
        connection.exec_driver_sql(
            "INSERT INTO task_revisions VALUES ('history', '{\"title\":\"original\"}')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert connection.exec_driver_sql(
            "SELECT sprint_id, description, version FROM tasks"
        ).one() == (None, "original description", 7)
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM sprints").scalar() == 0
        assert (
            connection.exec_driver_sql("SELECT snapshot FROM task_revisions").scalar()
            == '{"title":"original"}'
        )
        connection.exec_driver_sql(
            "INSERT INTO sprints (id,project_id,title,status,created_at,updated_at) VALUES ('s1','p1','one','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO sprints (id,project_id,title,status,created_at,updated_at) VALUES ('s2','p1','two','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO tasks (id,project_id,kind,status,sprint_id) VALUES ('foreign','p2','task','todo','s1')"
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO tasks (id,project_id,kind,status,sprint_id) VALUES ('epic','p1','epic','todo','s1')"
            )
    engine.dispose()
    assert {"sprints", "sprint_task_snapshots", "sprint_mutations"} <= set(Base.metadata.tables)
