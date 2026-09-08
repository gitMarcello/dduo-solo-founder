"""Independent regression cases found during the Beta sprint review."""

from urllib.parse import parse_qs, urlsplit

import pytest

from dduo_solo_founder import mcp_server


def test_default_mcp_plan_list_preserves_compact_projection(monkeypatch):
    requests = []

    def fake_request(api, method, path, *args, **kwargs):
        requests.append((method, path))
        return {"items": [], "total": 0}

    monkeypatch.setattr(mcp_server, "request", fake_request)
    validated = mcp_server.PlanListInput.model_validate({})
    # Match the SDK dispatch: only the user's explicit arguments are retained.
    arguments = validated.model_dump(mode="json", exclude_unset=True)
    mcp_server.call("list_plans", arguments, "review-project", "http://unused.invalid")

    assert requests[0][0] == "GET"
    query = parse_qs(urlsplit(requests[0][1]).query)
    assert query.get("detail") == ["compact"]


@pytest.mark.parametrize("tool", ["list_tasks", "search_tasks"])
@pytest.mark.parametrize("explicit_scope", [None, "active", "completed", "all"])
def test_mcp_archive_includes_terminal_work_unless_scope_is_explicit(
    monkeypatch, tool, explicit_scope,
):
    requests = []

    def fake_request(api, method, path, *args, **kwargs):
        requests.append((method, path, args))
        return {"items": [], "total": 0}

    monkeypatch.setattr(mcp_server, "request", fake_request)
    raw = {"placement": "archive"}
    if tool == "search_tasks":
        raw["query"] = "Delivered authentication"
    if explicit_scope is not None:
        raw["scope"] = explicit_scope
    validated = mcp_server.TOOL_MODELS[tool][1].model_validate(raw)
    arguments = validated.model_dump(mode="json", exclude_unset=True)
    mcp_server.call(tool, arguments, "review-project", "http://unused.invalid")

    method, path, bodies = requests[0]
    if method == "GET":
        delivered_scope = parse_qs(urlsplit(path).query)["scope"][0]
    else:
        delivered_scope = bodies[0]["scope"]
    assert delivered_scope == (explicit_scope or "all")
