from __future__ import annotations

import json

import httpx
import pytest

from dduo_solo_founder import bridge_probe


ENV = {"CLI_BRIDGE_URL": "https://bridge.example.test:1234", "CLI_BRIDGE_TOKEN": "synthetic-secret"}
READY = {"status": "ok", "bridge_protocol_version": 3, "project_id": "project-a"}


def transport(monkeypatch, handler):
    original_client = httpx.Client
    captured = []

    def client(**kwargs):
        captured.append(kwargs)
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bridge_probe.httpx, "Client", client)
    return captured


def test_probe_uses_exact_scoped_auth_real_environment_and_safe_http(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=READY)

    clients = transport(monkeypatch, handler)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HTTPS_PROXY", "http://not-used.example.test")
    assert bridge_probe.probe("project-a") == {"ready": True, "reason": "ready"}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://bridge.example.test:1234/health?project_id=project-a"
    assert seen[0].headers["Authorization"] == "Bearer synthetic-secret"
    assert clients[0]["trust_env"] is False
    assert clients[0]["follow_redirects"] is False
    assert clients[0]["verify"] is True
    assert clients[0]["timeout"].read == bridge_probe.PROBE_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("project", "environment"),
    [
        ("", ENV),
        ("x" * 161, ENV),
        ("project\na", ENV),
        ("project a", ENV),
        ("project-a", {}),
        ("project-a", {**ENV, "CLI_BRIDGE_TOKEN": ""}),
        ("project-a", {**ENV, "CLI_BRIDGE_TOKEN": "secret\r\nX: y"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host:0"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "file:///tmp/secret"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://user:secret@host"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host/?token=secret"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host/#secret"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host/private-path"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host:invalid"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://[broken"}),
        ("project-a", {**ENV, "CLI_BRIDGE_URL": "http://host/\n"}),
    ],
)
def test_probe_invalid_configuration_never_attempts_network(monkeypatch, project, environment):
    monkeypatch.setattr(bridge_probe.httpx, "Client", lambda **_: pytest.fail("network attempted"))
    assert bridge_probe.probe(project, environ=environment) == {"ready": False, "reason": "configuration"}


@pytest.mark.parametrize("status,reason", [(401, "unauthorized"), (403, "unauthorized"), (302, "invalid_response"), (404, "invalid_response"), (503, "invalid_response")])
def test_probe_http_errors_are_content_free(monkeypatch, status, reason):
    transport(monkeypatch, lambda _: httpx.Response(status, text="synthetic-secret", headers={"Location": "https://secret.example.test"}))
    assert bridge_probe.probe("project-a", environ=ENV) == {"ready": False, "reason": reason}


@pytest.mark.parametrize(
    "payload,reason",
    [
        ([], "invalid_response"),
        ({}, "invalid_response"),
        ({**READY, "project_id": "project-b"}, "invalid_response"),
        ({**READY, "status": "failed"}, "invalid_response"),
        ({**READY, "bridge_protocol_version": 2}, "protocol_mismatch"),
        ({**READY, "bridge_protocol_version": "3"}, "protocol_mismatch"),
        ({**READY, "bridge_protocol_version": True}, "protocol_mismatch"),
        ({"status": "ok", "project_id": "project-a"}, "protocol_mismatch"),
    ],
)
def test_probe_requires_exact_identity_status_and_protocol(monkeypatch, payload, reason):
    transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    assert bridge_probe.probe("project-a", environ=ENV) == {"ready": False, "reason": reason}


@pytest.mark.parametrize("body", [b"not-json synthetic-secret", b"\xff", b"x" * (bridge_probe.MAX_RESPONSE_BYTES + 1)])
def test_probe_rejects_invalid_or_oversized_body_without_echo(monkeypatch, body):
    transport(monkeypatch, lambda _: httpx.Response(200, content=body))
    assert bridge_probe.probe("project-a", environ=ENV) == {"ready": False, "reason": "invalid_response"}


def test_probe_network_exception_and_slow_response_are_redacted(monkeypatch):
    def unavailable(request):
        raise httpx.ConnectError("synthetic-secret in request", request=request)

    transport(monkeypatch, unavailable)
    assert bridge_probe.probe("project-a", environ=ENV) == {"ready": False, "reason": "unreachable"}


def test_probe_enforces_response_deadline(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(200, json=READY))
    ticks = iter([0.0, 6.0])
    monkeypatch.setattr(bridge_probe.time, "monotonic", lambda: next(ticks))
    assert bridge_probe.probe("project-a", environ=ENV) == {"ready": False, "reason": "unreachable"}


@pytest.mark.parametrize("arguments", [[], ["project-a", "unexpected"]])
def test_main_invalid_argv_is_sanitized(monkeypatch, capsys, arguments):
    monkeypatch.setattr(bridge_probe, "probe", lambda _: pytest.fail("must not probe"))
    assert bridge_probe.main(arguments) == 1
    assert json.loads(capsys.readouterr().out) == {"ready": False, "reason": "configuration"}


def test_main_returns_exit_code_and_never_prints_exception(monkeypatch, capsys):
    monkeypatch.setattr(bridge_probe, "probe", lambda _: {"ready": True, "reason": "ready"})
    monkeypatch.setattr(bridge_probe.sys, "argv", ["bridge_probe", "project-a"])
    assert bridge_probe.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ready": True, "reason": "ready"}

    def fail(_):
        raise RuntimeError("synthetic-secret and URL")

    monkeypatch.setattr(bridge_probe, "probe", fail)
    assert bridge_probe.main(["project-a"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ready": False, "reason": "invalid_response"}
    assert captured.err == ""
