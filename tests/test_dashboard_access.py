from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest

from dduo_solo_founder import dashboard_access, mcp_server
from dduo_solo_founder.client_binding import ProjectBinding
from dduo_solo_founder.main import app
from dduo_solo_founder.team import browser_cookie_name
from test_mcp_server import _sdk_session
from test_team_api import _remote_client, _seed_team


TOKEN = "dduo_link_" + "a" * 43
BASE = "https://memory.test:24443"


def _payload():
    return {
        "token": TOKEN,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "reusable": True,
    }


def _runtime(tmp_path, *, project_id="p1", base=BASE, bearer="synthetic-api-bearer"):
    binding = ProjectBinding(
        project_id=project_id, name="Artificial", root_path=tmp_path, kind="remote",
        api_url=base, dashboard_url=base, binding_id="a" * 64, bearer_token=bearer,
    )
    return mcp_server.MCPToolRuntime(
        client="codex", project_root=tmp_path, project_id=project_id,
        api=mcp_server.ApiEndpoint(base), dashboard_url=binding.dashboard_link("tasks"),
        plans_dashboard_url=binding.dashboard_link("tasks"), binding=binding,
    )


@pytest.mark.parametrize("payload", [None, [], "invalid", {}, {
    "token": TOKEN, "expires_at": "2999-01-01T00:00:00", "reusable": True,
}, {"token": TOKEN, "expires_at": "invalid", "reusable": True}, {
    "token": TOKEN, "expires_at": "2000-01-01T00:00:00+00:00", "reusable": True,
}, {"token": TOKEN, "expires_at": "2999-01-01T00:00:00Z", "reusable": False}, {
    "token": "dduo_dev_" + "a" * 43, "expires_at": "2999-01-01T00:00:00Z", "reusable": True,
}, {"token": "dduo_link_short", "expires_at": "2999-01-01T00:00:00Z", "reusable": True}])
def test_invalid_link_payload_fails_closed(payload):
    with pytest.raises(ValueError):
        dashboard_access.validate_browser_link(payload)


def test_link_validation_and_replacement_preserve_destination():
    payload = _payload()
    assert dashboard_access.validate_browser_link(payload) == (TOKEN, payload["expires_at"])
    url = BASE + "/?project=p1&tab=tasks&work=w1&ticket=old&access_token=older#details"
    linked = dashboard_access.with_dashboard_access_token(url, TOKEN)
    parsed = urlsplit(linked)
    assert parsed.fragment == "details"
    assert parse_qs(parsed.query) == {
        "project": ["p1"], "tab": ["tasks"], "work": ["w1"], "access_token": [TOKEN],
    }
    with pytest.raises(ValueError):
        dashboard_access.with_dashboard_access_token(url, "dduo_dev_" + "a" * 43)


@pytest.mark.parametrize("url", [
    None, 9, "http://memory.test:24443/?project=p1&tab=tasks",
    "https://other.test:24443/?project=p1&tab=tasks",
    "https://memory.test:24444/?project=p1&tab=tasks",
    "https://memory.test:24443/other?project=p1&tab=tasks",
    "https://memory.test:24443/../?project=p1&tab=tasks",
    "https://memory.test:24443/%2e/?project=p1&tab=tasks",
    "https://user@memory.test:24443/?project=p1&tab=tasks",
    "https://user:password@memory.test:24443/?project=p1&tab=tasks",
    "https://memory.test:24443/?project=other&tab=tasks",
    "https://memory.test:24443/?project=p1&project=p1&tab=tasks",
    "https://memory.test:24443/?project=p1&tab=tasks&tab=team",
    "https://memory.test:24443/?project=p1&tab=secrets",
    "https://memory.test:24443/?project=p1", "https://[invalid",
])
def test_only_the_exact_project_dashboard_origin_and_route_receive_access(url):
    assert not dashboard_access.is_project_dashboard_url(url, BASE, "p1")


@pytest.mark.parametrize("reason", ["no-binding", "local", "no-dashboard", "no-project", "no-links"])
def test_nonremote_or_nonlink_results_never_mint_credentials(monkeypatch, tmp_path, reason):
    runtime = _runtime(tmp_path)
    value = {"url": BASE + "/?project=p1&tab=tasks"}
    if reason == "no-binding":
        runtime = replace(runtime, binding=None)
    elif reason == "local":
        runtime = replace(runtime, binding=replace(runtime.binding, kind="local"))
    elif reason == "no-dashboard":
        runtime = replace(runtime, binding=replace(runtime.binding, dashboard_url=None))
    elif reason == "no-project":
        runtime = replace(runtime, project_id=None)
    else:
        value = {"url": "https://other.test/?project=p1&tab=tasks", "text": value["url"]}
    monkeypatch.setattr(mcp_server, "request", lambda *_a, **_k: pytest.fail("unexpected mint"))
    assert mcp_server._attach_remote_dashboard_access(value, runtime) is value


def test_nested_dashboard_links_share_one_token_without_mutating_original(monkeypatch, tmp_path):
    runtime = _runtime(tmp_path)
    task = BASE + "/?project=p1&tab=tasks&work=w1"
    plan = BASE + "/?project=p1&tab=tasks&plan=p2"
    value = {"task": {"url": task, "description": task}, "items": [
        {"item_url": task + "&ticket=old"}, {"plans_dashboard_url": plan},
        {"url": "https://other.test/?project=p1&tab=tasks"},
    ], "response_instruction": "Task created."}
    original = deepcopy(value)
    calls = []
    payload = _payload()
    monkeypatch.setattr(mcp_server, "request", lambda *args: calls.append(args) or payload)
    result = mcp_server._attach_remote_dashboard_access(value, runtime)
    assert calls == [(runtime.api, "POST", "/projects/p1/auth/browser-link")]
    assert value == original
    assert result["task"]["description"] == task
    assert result["items"][2] == original["items"][2]
    for linked in (result["task"]["url"], result["items"][0]["item_url"],
                   result["items"][1]["plans_dashboard_url"]):
        query = parse_qs(urlsplit(linked).query)
        assert query["access_token"] == [TOKEN] and "ticket" not in query
    assert result["dashboard_access"] == {
        "ready": True, "expires_at": payload["expires_at"], "reusable": True, "private": True,
    }
    assert "Task created." in result["response_instruction"]


@pytest.mark.parametrize("error", [RuntimeError("issuer unavailable"), ValueError("invalid issuer result")])
def test_access_failure_preserves_completed_mutation_and_warns_without_retry(monkeypatch, tmp_path, error):
    runtime = _runtime(tmp_path)
    value = {"task": {"id": "w1", "title": "Completed mutation"},
             "url": BASE + "/?project=p1&tab=tasks&work=w1"}
    calls = []

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(mcp_server, "request", fail)
    result = mcp_server._attach_remote_dashboard_access(value, runtime)
    assert len(calls) == 1
    assert all(result[key] == field for key, field in value.items())
    assert result["dashboard_access"] == {"ready": False}
    assert "already completed; do not repeat it" in result["response_instruction"]
    assert "Do not claim these plain links sign the browser in" in result["response_instruction"]


@pytest.mark.asyncio
async def test_real_mcp_dashboard_link_opens_two_authenticated_browsers(db_factory, monkeypatch, tmp_path):
    project_id, _, manager, _, bearer = await _seed_team(db_factory)
    runtime = _runtime(tmp_path, project_id=project_id, base="https://memory.test", bearer=bearer)
    issuer = await _remote_client(db_factory, monkeypatch)
    first = await _remote_client(db_factory, monkeypatch)
    second = await _remote_client(db_factory, monkeypatch)
    calls, observations = [], []

    async def request_asgi(method, url, kwargs):
        calls.append((method, url))
        return await issuer.request(method, url, headers=kwargs.get("headers"), json=kwargs.get("json"))

    def transport(method, url, **kwargs):
        return anyio.from_thread.run(request_asgi, method, url, kwargs)

    api = mcp_server.ApiEndpoint(runtime.binding.api_url, mcp_server.ProjectHttpClient(
        runtime.binding, component="mcp", request_function=transport,
    ), runtime.binding.binding_id)
    runtime = replace(runtime, api=api)
    monkeypatch.setattr(mcp_server, "_resolve_tool_runtime", lambda *_args: runtime)
    monkeypatch.setattr(mcp_server, "flush_observability_queue",
                        lambda _api, _id, pending=None, **_kw: observations.extend(pending or []))

    async def operation(session, _initialized):
        return await session.call_tool("get_dashboard_link", {"tab": "team"})

    try:
        response = await _sdk_session("codex-mcp-client", operation)
        assert response.is_error is False
        result = response.structured_content
        assert result["dashboard_access"]["ready"] is True
        raw = parse_qs(urlsplit(result["dashboard_url"]).query)["access_token"][0]
        assert raw.startswith("dduo_link_") and len(raw) == 53
        assert calls == [("POST", f"https://memory.test/projects/{project_id}/auth/browser-link")]
        cookies, csrf = [], []
        for browser in (first, second):
            exchanged = await browser.post(
                f"/projects/{project_id}/auth/browser-session", json={"ticket": raw},
            )
            assert exchanged.status_code == 200
            cookies.append(browser.cookies.get(browser_cookie_name(project_id)))
            csrf.append(exchanged.json()["csrf_token"])
            team = await browser.get(f"/projects/{project_id}/team")
            assert team.status_code == 200 and team.json()["current_member"]["id"] == manager.id
        assert cookies[0] != cookies[1] and csrf[0] != csrf[1]
        assert observations and raw not in json.dumps(observations)
        observed = observations[0]["content"]
        assert observations[0]["content_sha256"] == hashlib.sha256(observed.encode()).hexdigest()
        assert raw in response.content[0].text
    finally:
        await issuer.aclose()
        await first.aclose()
        await second.aclose()
        app.dependency_overrides.clear()
