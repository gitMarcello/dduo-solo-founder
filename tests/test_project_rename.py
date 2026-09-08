from uuid import uuid4

import pytest
from sqlalchemy import select

from conftest import project_payload
from dduo_solo_founder.models import Activity, ProfileRevision, Project
from dduo_solo_founder.schemas import ProjectUpdate
from dduo_solo_founder import mcp_server


def test_mcp_rename_is_explicit_and_returns_human_confirmation(monkeypatch):
    calls = []
    def request(api, method, path, body):
        calls.append((method, path, body))
        return {"name": "Orchard", "profile_version": 2, "root_path": "/private/not-for-output"}
    monkeypatch.setattr(mcp_server, "request", request)
    result = mcp_server.call("rename_project", {"name": "Orchard", "expected_version": 1}, "p1", "http://api")
    assert calls == [("POST", "/projects/p1/rename", {"name": "Orchard", "expected_version": 1})]
    assert result["name"] == "Orchard"
    assert "response_instruction" in result
    assert "root_path" not in result


async def test_rename_is_explicit_versioned_and_survives_bootstrap(api_client, db_factory):
    client, _ = api_client
    original = project_payload(name="Demo")
    project = (await client.post("/projects", json=original)).json()
    path = f"/projects/{project['id']}"
    saved = await client.post(path + "/rename", json={"name": "  Caffè 🌱  ", "expected_version": 1})
    assert saved.status_code == 200
    assert saved.json()["name"] == "Caffè 🌱"
    assert saved.json()["profile_version"] == 2
    for key in ("id", "root_path", "cause", "principles", "objectives"):
        assert saved.json()[key] == project[key]
    assert (await client.post("/projects", json=original)).json()["name"] == "Caffè 🌱"
    assert (await client.get(path + "/identity")).json() == {
        "id": project["id"], "name": "Caffè 🌱", "profile_version": 2,
    }
    replay = await client.post(path + "/rename", json={"name": "Caffè 🌱", "expected_version": 1})
    assert replay.status_code == 200
    assert replay.json()["profile_version"] == 2
    stale = await client.post(path + "/rename", json={"name": "Other", "expected_version": 1})
    assert stale.status_code == 409
    async with db_factory() as db:
        revisions = list(await db.scalars(select(ProfileRevision).order_by(ProfileRevision.version)))
        assert [r.snapshot["name"] for r in revisions] == ["Demo", "Caffè 🌱"]
        events = list(await db.scalars(select(Activity).where(Activity.kind == "project.renamed")))
        assert len(events) == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, "bad\nname", "bad\x00", "bad\u202ename", 42, None])
async def test_invalid_names_never_mutate(api_client, name):
    client, _ = api_client
    original = project_payload()
    await client.post("/projects", json=original)
    response = await client.post(f"/projects/{original['id']}/rename", json={"name": name, "expected_version": 1})
    assert response.status_code == 422
    assert (await client.get(f"/projects/{original['id']}/identity")).json()["name"] == original["name"]


async def test_rename_rejects_unknown_fields_and_missing_version(api_client):
    client, _ = api_client
    original = project_payload()
    await client.post("/projects", json=original)
    path = f"/projects/{original['id']}/rename"
    for payload in ({"name": "New"}, {"name": "New", "expected_version": 1, "id": str(uuid4())}):
        assert (await client.post(path, json=payload)).status_code == 422
    assert "name" not in ProjectUpdate.model_fields
    missing = f"/projects/{uuid4()}"
    assert (await client.get(missing + "/identity")).status_code == 404
    assert (await client.post(missing + "/rename", json={"name": "New", "expected_version": 1})).status_code == 404


async def test_rename_requires_writable_authority(api_client, db_factory):
    client, _ = api_client
    original = project_payload()
    await client.post("/projects", json=original)
    async with db_factory() as db:
        project = await db.get(Project, original["id"])
        project.authority_state = "transferred"
        await db.commit()
    response = await client.post(f"/projects/{original['id']}/rename", json={"name": "New", "expected_version": 1})
    assert response.status_code == 409
