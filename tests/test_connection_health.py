from types import SimpleNamespace

import httpx
import pytest

from dduo_solo_founder import mcp_server
from dduo_solo_founder.connection_health import combine_connection_checks, sleep_connection_notice


@pytest.mark.parametrize("state,choice,reason", [
    ("updated", False, None), ("updating", False, None),
    ("connection_required", True, "sleep_auth_required"),
    ("limited", False, "sleep_rate_limited"), ("waiting", False, "sleep_waiting"),
    ("unknown", True, "memory_status_unavailable"),
])
def test_notices_distinguish_capture_from_consolidation_and_transient_wait(state, choice, reason):
    health = {"state": state, "provider": "codex", "issue_id": "safe", "last_error": "private diagnostic", "result": "private content"}
    notice = sleep_connection_notice(health)
    if reason is None:
        assert notice is None
        return
    assert notice["requires_choice"] is choice
    assert notice["reason"] == reason
    assert "private" not in str(notice)
    assert len(notice["warning_id"]) == 32
    if state in {"limited", "waiting"}:
        health["requires_action"] = True
        assert sleep_connection_notice(health)["requires_choice"] is True


def test_multiple_failures_keep_both_actions_and_binding_scope():
    native = {"ready": False, "requires_choice": True, "message": "Approve hooks.", "next_action": "Open Settings.", "response_instruction": "Ask first.", "warning_id": "native"}
    health = {"state": "connection_required", "provider": "codex", "issue_id": "auth"}
    combined = combine_connection_checks(native, health, binding_id="a", remote=False)
    assert "Approve hooks" in combined["message"] and "consolidation" in combined["message"]
    assert "Open Settings" in combined["next_action"] and "Reconnect" in combined["next_action"]
    assert combined["capture_ready"] is False
    assert combine_connection_checks(native, health, binding_id="b", remote=False)["warning_id"] != combined["warning_id"]
    assert "infrastructure manager" in sleep_connection_notice({"state": "unknown"}, remote=True)["next_action"]


@pytest.mark.parametrize("payload", [{"state": "updated"}, [], {}, None])
def test_read_health_is_bound_read_only_and_does_not_probe_a_model(monkeypatch, payload):
    calls = []
    monkeypatch.setattr(mcp_server, "_raw_response", lambda *a, **k: calls.append((a, k)) or httpx.Response(200, json=payload, request=httpx.Request("GET", "http://test")))
    runtime = SimpleNamespace(api="http://test", project_id="project-a")
    result = mcp_server._read_connection_health(runtime)
    assert result["state"] == ("updated" if payload == {"state": "updated"} else "unavailable")
    assert calls == [(("http://test", "GET", "/projects/project-a/memory-status"), {"timeout": 5})]


def test_read_health_outage_and_missing_setup_health_never_claim_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_raw_response", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("private error")))
    result = mcp_server._read_connection_health(SimpleNamespace(api="http://test", project_id="a"))
    assert result == {"state": "unavailable", "available": False}
    monkeypatch.setattr(mcp_server, "read_setup_status", lambda root: {"project": {"ready": True}, "docker": {"ready": True}, "embeddings": {"ready": True}})
    checked = mcp_server.check_setup(tmp_path, None)
    assert checked["ready"] is False
    assert checked["next_action"]["id"] == "memory_status_unavailable"
