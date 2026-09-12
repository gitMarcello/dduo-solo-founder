"""Transfer a never-shared local project with the application's real auth layer."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dduo_solo_founder.authority_receipts import (
    AuthorityReceipt,
    issue_authority_receipt,
)
from dduo_solo_founder.db import get_session
from dduo_solo_founder.main import app
from dduo_solo_founder.models import Base, Project, TeamAccessToken, TeamMember


pytestmark = pytest.mark.asyncio
SECRET = "isolated-transfer-authority-credential"
MANAGER_TOKEN = "dduo_dev_" + "m" * 43


@pytest_asyncio.fixture
async def nodes(db_factory, monkeypatch):
    """Separate restored/source databases, never a mocked auth dependency."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    destination_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = str(uuid.uuid4())
    factories = {"source": db_factory, "destination": destination_factory}
    async with db_factory() as db:
        db.add(Project(id=project_id, name="Artificial transfer", root_path="/tmp/artificial"))
        await db.commit()

    @asynccontextmanager
    async def client(node):
        async def session():
            async with factories[node]() as db:
                yield db

        previous = app.dependency_overrides.get(get_session)
        app.dependency_overrides[get_session] = session
        with monkeypatch.context() as context:
            context.setenv("DDUO_AUTH_REQUIRED", "true" if node == "destination" else "false")
            context.setenv("DDUO_NODE_ID", node)
            context.setenv("BACKUP_PROJECT_ID", project_id)
            context.setenv("DDUO_NODE_AUTHORITY_SECRET", SECRET)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as api:
                    yield api
            finally:
                if previous is None:
                    app.dependency_overrides.pop(get_session, None)
                else:
                    app.dependency_overrides[get_session] = previous

    async def prepare_and_restore():
        async with client("source") as api:
            response = await api.post(
                f"/projects/{project_id}/authority/prepare",
                params={"expected_generation": 1, "target_node_id": "destination"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["writable"] is False
        async with db_factory() as source, destination_factory() as destination:
            project = await source.get(Project, project_id)
            # Restore exactly the durable authority data, without team bootstrap.
            destination.add(Project(**{
                column.key: getattr(project, column.key)
                for column in Project.__table__.columns
            }))
            await destination.commit()
        return response.json()

    yield project_id, factories, client, prepare_and_restore
    await engine.dispose()


async def test_first_remote_claim_reproduces_bearer_deadlock_and_uses_scoped_status(nodes):
    project_id, factories, client, prepare_and_restore = nodes
    await prepare_and_restore()
    async with client("destination") as api:
        headers = {"Authorization": f"Bearer {MANAGER_TOKEN}"}
        # This is beta.1's failing first remote-host request. An infrastructure
        # token in the private file is not yet a TeamAccessToken in this restore.
        original = await api.get(f"/projects/{project_id}/authority", headers=headers)
        assert original.status_code == 401
        assert original.json()["detail"] == "invalid or revoked project token"
        status = await api.post(
            f"/projects/{project_id}/authority/status",
            headers={**headers, "X-DDUO-Authority": SECRET},
            json={"node_id": "destination"},
        )
        assert status.status_code == 200, status.text
        assert status.json() == {
            "project_id": project_id, "node_id": "source",
            "target_node_id": "destination", "generation": 1,
            "state": "transfer_pending", "writable": False,
        }
        for method, path, payload in (
            ("GET", "/authority", None), ("GET", "/team", None),
            ("POST", "/tasks", {"title": "Forbidden"}),
        ):
            rejected = await api.request(
                method, f"/projects/{project_id}{path}",
                headers={"X-DDUO-Authority": SECRET}, json=payload,
            )
            assert rejected.status_code == 401
        bootstrap = await api.post(
            f"/projects/{project_id}/team/bootstrap",
            headers={"X-DDUO-Authority": SECRET},
            json={"display_name": "Manager", "device_id": "host", "device_token": MANAGER_TOKEN},
        )
        assert bootstrap.status_code == 409
    async with factories["destination"]() as db:
        assert (await db.scalars(select(TeamAccessToken))).all() == []
        assert (await db.scalars(select(TeamMember))).all() == []


@pytest.mark.parametrize("fault,expected", [
    ("missing_secret", 401), ("wrong_secret", 401), ("wrong_node", 409),
    ("wrong_project", 404), ("missing_project_scope", 503),
    ("missing_node", 503), ("wrong_target", 409), ("absent_project", 404),
])
async def test_authority_status_is_not_a_generic_auth_bypass(nodes, monkeypatch, fault, expected):
    project_id, factories, client, prepare_and_restore = nodes
    await prepare_and_restore()
    requested_project = project_id
    if fault == "wrong_project":
        requested_project = str(uuid.uuid4())
        async with factories["destination"]() as db:
            db.add(Project(id=requested_project, name="Other project", root_path="/tmp/other"))
            await db.commit()
    if fault == "wrong_target":
        async with factories["destination"]() as db:
            project = await db.get(Project, project_id)
            project.authority_target_node_id = "another-destination"
            await db.commit()
    async with client("destination") as api:
        if fault == "absent_project":
            requested_project = str(uuid.uuid4())
            monkeypatch.setenv("BACKUP_PROJECT_ID", requested_project)
        if fault == "missing_project_scope":
            monkeypatch.delenv("BACKUP_PROJECT_ID")
        if fault == "missing_node":
            monkeypatch.delenv("DDUO_NODE_ID")
        headers = {} if fault == "missing_secret" else {
            "X-DDUO-Authority": "wrong" if fault == "wrong_secret" else SECRET,
        }
        response = await api.post(
            f"/projects/{requested_project}/authority/status", headers=headers,
            json={"node_id": "wrong" if fault == "wrong_node" else "destination"},
        )
        assert response.status_code == expected, response.text


@pytest.mark.parametrize("headers,payload,expected", [
    ({}, {"node_id": "destination", "expected_generation": 1}, 401),
    ({"X-DDUO-Authority": "wrong"}, {"node_id": "destination", "expected_generation": 1}, 401),
    ({"X-DDUO-Authority": SECRET}, {"node_id": "source", "expected_generation": 1}, 409),
    ({"X-DDUO-Authority": SECRET}, {"node_id": "destination", "expected_generation": 2}, 409),
])
async def test_destination_activation_rejects_wrong_auth_node_or_generation(nodes, headers, payload, expected):
    project_id, factories, client, prepare_and_restore = nodes
    await prepare_and_restore()
    async with client("destination") as api:
        result = await api.post(
            f"/projects/{project_id}/authority/activate", headers=headers, json=payload,
        )
        assert result.status_code == expected
    async with factories["destination"]() as db:
        project = await db.get(Project, project_id)
        assert project.authority_state == "transfer_pending"
        assert project.authority_generation == 1


async def test_authority_status_can_inspect_first_unowned_remote_project(nodes):
    project_id, factories, client, _ = nodes
    async with factories["destination"]() as db:
        db.add(Project(id=project_id, name="Fresh host", root_path="/tmp/fresh-host"))
        await db.commit()
    async with client("destination") as api:
        result = await api.post(
            f"/projects/{project_id}/authority/status", headers={"X-DDUO-Authority": SECRET},
            json={"node_id": "destination"},
        )
        assert result.status_code == 200
        assert result.json()["state"] == "active"
        assert result.json()["node_id"] is None
        invalid = await api.post(
            f"/projects/{project_id}/authority/status", headers={"X-DDUO-Authority": SECRET},
            json={"node_id": "destination", "unrecognized": True},
        )
        assert invalid.status_code == 422


async def test_first_transfer_finishes_before_manager_bootstrap_without_two_writers(nodes):
    project_id, factories, client, prepare_and_restore = nodes
    await prepare_and_restore()
    headers = {"X-DDUO-Authority": SECRET}
    async with client("destination") as api:
        activated = await api.post(
            f"/projects/{project_id}/authority/activate", headers=headers,
            json={"node_id": "destination", "expected_generation": 1},
        )
        assert activated.status_code == 200
        assert activated.json()["writable"] is False
        assert activated.json()["phase"] == "destination_ready"
    async with client("source") as api:
        denied = await api.post(f"/projects/{project_id}/tasks", json={"title": "Frozen"})
        assert denied.status_code == 409
        retired = await api.post(
            f"/projects/{project_id}/authority/finalize", params={"expected_generation": 1},
            json={"activation_receipt": activated.json()["activation_receipt"]},
        )
        assert retired.status_code == 200
        assert retired.json()["writable"] is False
    async with client("destination") as api:
        complete = await api.post(
            f"/projects/{project_id}/authority/complete", headers=headers,
            json={"node_id": "destination", "expected_generation": 1,
                  "finalization_receipt": retired.json()["finalization_receipt"]},
        )
        assert complete.status_code == 200
        assert complete.json()["writable"] is True
        assert complete.json()["generation"] == 2
        bootstrap = await api.post(
            f"/projects/{project_id}/team/bootstrap", headers=headers,
            json={"display_name": "Manager", "device_id": "host", "device_token": MANAGER_TOKEN},
        )
        assert bootstrap.status_code == 200
        saved = await api.post(
            f"/projects/{project_id}/tasks", headers={"Authorization": f"Bearer {MANAGER_TOKEN}"},
            json={"title": "Destination only"},
        )
        assert saved.status_code == 200
    async with client("source") as api:
        assert (await api.post(
            f"/projects/{project_id}/tasks", json={"title": "Must remain retired"},
        )).status_code == 409
    for node in factories:
        async with factories[node]() as db:
            project = await db.get(Project, project_id)
            assert project.authority_state == ("transferred" if node == "source" else "active")


@pytest.mark.parametrize("fault", ["altered", "project", "source", "target", "generation", "nonce"])
async def test_unauthentic_or_mismatched_finalization_cannot_enable_destination(nodes, fault):
    project_id, factories, client, prepare_and_restore = nodes
    await prepare_and_restore()
    async with factories["destination"]() as db:
        project = await db.get(Project, project_id)
        receipt = AuthorityReceipt(
            kind="source_finalized", project_id=project_id, source_node_id="source",
            target_node_id="destination", source_generation=1,
            nonce=project.authority_transfer_nonce,
        )
    changes = {
        "project": {"project_id": str(uuid.uuid4())}, "source": {"source_node_id": "other"},
        "target": {"target_node_id": "other"}, "generation": {"source_generation": 2},
        "nonce": {"nonce": "b" * 64},
    }
    token = issue_authority_receipt(SECRET, replace(receipt, **changes.get(fault, {})))
    if fault == "altered":
        token = token[:-10] + ("a" if token[-10] != "a" else "b") + token[-9:]
    async with client("destination") as api:
        response = await api.post(
            f"/projects/{project_id}/authority/complete", headers={"X-DDUO-Authority": SECRET},
            json={"node_id": "destination", "expected_generation": 1, "finalization_receipt": token},
        )
        assert response.status_code == 409
    async with factories["destination"]() as db:
        project = await db.get(Project, project_id)
        assert project.authority_state == "transfer_pending"
        assert project.authority_generation == 1


async def test_cancel_restores_source_writes_but_not_the_pending_destination(nodes):
    project_id, _, client, prepare_and_restore = nodes
    await prepare_and_restore()
    async with client("source") as api:
        cancel = await api.post(
            f"/projects/{project_id}/authority/cancel", params={"expected_generation": 1},
        )
        assert cancel.status_code == 200
        assert cancel.json()["writable"] is True
        assert (await api.post(
            f"/projects/{project_id}/tasks", json={"title": "Local work resumes"},
        )).status_code == 200
    async with client("destination") as api:
        status = await api.post(
            f"/projects/{project_id}/authority/status", headers={"X-DDUO-Authority": SECRET},
            json={"node_id": "destination"},
        )
        assert status.status_code == 200
        assert status.json()["writable"] is False
