from types import SimpleNamespace

import httpx
import pytest

from dduo_solo_founder import cli_bridge


@pytest.fixture
def setup_case(monkeypatch, tmp_path):
    root = tmp_path / "project"
    config = root / ".dduo-solo-founder" / "project.toml"
    config.parent.mkdir(parents=True)
    config.write_text('id = "p1"\napi_port = 18001\nweb_port = 20001\n', encoding="utf-8")
    monkeypatch.setattr(cli_bridge, "validate_project_registration", lambda *a, **k: {})
    setup = cli_bridge.SetupService()
    health = {"state": "connection_required", "provider": "codex", "executor_provider": "codex"}
    monkeypatch.setattr(setup, "_memory_status", lambda root: health)
    auth = SimpleNamespace(ready=True, as_dict=lambda: {"ready": True, "reason": "authenticated"})
    monkeypatch.setattr(setup, "_subscription_status", lambda *a, **k: auth)
    monkeypatch.setattr(setup, "_codex_hook_status", lambda root: SimpleNamespace(as_dict=lambda: {"ready": True}))
    monkeypatch.setattr(setup, "_docker_status", lambda: {"ready": True})
    monkeypatch.setattr(setup, "_embeddings_ready", lambda p: True)
    monkeypatch.setattr(setup, "_project_status", lambda root: {"ready": True})
    monkeypatch.setattr(setup, "_backup_status", lambda root: {})
    monkeypatch.setattr(setup, "claude_telemetry_status", lambda root: {})
    monkeypatch.setattr(cli_bridge, "resolve_codex_executable", lambda: "test-codex")
    monkeypatch.setattr(cli_bridge, "client_command", lambda executable, args, **k: [executable, *args])
    homes = []
    monkeypatch.setattr(cli_bridge, "ensure_project_codex_home", lambda project_id, **k: homes.append((project_id, k)) or tmp_path / "sleep-home")
    process = SimpleNamespace(returncode=None)
    process.poll = lambda: process.returncode
    launched = []
    monkeypatch.setattr(cli_bridge.subprocess, "Popen", lambda command, **kwargs: launched.append((command, kwargs)) or process)
    posted = []
    monkeypatch.setattr(cli_bridge.httpx, "post", lambda url, **kwargs: posted.append((url, kwargs)) or httpx.Response(202, json={"scheduled": 1}, request=httpx.Request("POST", url)))
    return SimpleNamespace(setup=setup, root=root, health=health, auth=auth, homes=homes, process=process, launched=launched, posted=posted)


def test_stored_login_does_not_override_failed_sleep_or_resume_on_setup_open(setup_case):
    c = setup_case
    for _ in range(2):
        state = c.setup.status(str(c.root))
        assert state["clients"]["codex"]["ready"] is False
        assert state["clients"]["codex"]["reason"] == "auth_required"
        assert state["ready_for_client"] == {"codex": False, "claude": False}
        assert state["clients"]["codex"]["resume_ready"] is False
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is False
    assert c.launched == c.posted == c.homes == []


def test_explicit_reconnect_uses_project_home_without_copying_host_login(setup_case):
    c = setup_case
    assert c.setup.start_auth("codex", str(c.root))["status"] == "waiting"
    assert c.setup.start_auth("codex", str(c.root))["status"] == "waiting"
    assert len(c.launched) == 1
    assert c.homes == [("p1", {"import_global_auth": False})]
    assert c.launched[0][0] == ["test-codex", "login"]
    assert c.launched[0][1]["env"]["CODEX_HOME"].endswith("sleep-home")
    assert c.setup.status(str(c.root))["clients"]["codex"]["resume_ready"] is False
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is False
    c.process.returncode = 0
    state = c.setup.status(str(c.root))
    assert state["clients"]["codex"]["resume_ready"] is True
    assert state["clients"]["codex"]["ready"] is False  # actual retry not yet successful
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is True
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is False
    assert len(c.posted) == 1
    c.health.update(state="updating", provider=None)
    assert c.setup.status(str(c.root))["memory_status"]["state"] == "updating"


@pytest.mark.parametrize("exit_code,ready", [(1, True), (0, False)])
def test_failed_or_unverified_login_never_resumes(setup_case, exit_code, ready):
    c = setup_case
    c.setup.start_auth("codex", str(c.root))
    c.process.returncode = exit_code
    c.auth.as_dict = lambda: {"ready": ready}
    assert c.setup.status(str(c.root))["clients"]["codex"]["setup_state"] == "failed"
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is False
    assert c.posted == []


def test_resume_is_executor_scoped_and_offline_retry_requires_explicit_action(setup_case, monkeypatch):
    c = setup_case
    for provider in ("claude", "codex"):
        c.setup._auth[f"{provider}:p1"] = cli_bridge.AuthAttempt(provider, verified=True)
    assert c.setup.resume_memory(str(c.root), "claude")["resumed"] is False
    original_post = cli_bridge.httpx.post
    monkeypatch.setattr(cli_bridge.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert c.setup.resume_memory(str(c.root), "codex")["error"] == "resume_failed"
    for _ in range(2):
        state = c.setup.status(str(c.root))["clients"]["codex"]
        assert state["resume_ready"] is False
        assert state["resume_failed"] is True
    monkeypatch.setattr(cli_bridge.httpx, "post", original_post)
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is True
    assert len(c.posted) == 1


def test_remote_setup_cannot_reconnect_or_resume_local_executor(setup_case, monkeypatch):
    c = setup_case
    monkeypatch.setattr(c.setup, "_project_config", lambda root: (c.root, {"id": "p1", "binding": "remote"}))
    with pytest.raises(ValueError, match="remote service"):
        c.setup.start_auth("codex", str(c.root))
    monkeypatch.setattr(cli_bridge.SetupService, "_project_config", lambda root: (c.root, {"id": "p1", "binding": "remote"}))
    assert c.setup.resume_memory(str(c.root), "codex")["resumed"] is False
    assert c.launched == c.posted == []


def test_health_read_never_initializes_auth_and_rejects_unknown_responses(tmp_path, monkeypatch):
    root = tmp_path / "project"
    project = {"id": "p1", "api_port": 18001, "web_port": 20001}
    monkeypatch.setattr(cli_bridge.SetupService, "_project_config", lambda root: (root, project))
    for payload in ({"state": "updated"}, {"state": "made-up"}, [], None):
        monkeypatch.setattr(cli_bridge.httpx, "get", lambda url, **k: httpx.Response(200, json=payload, request=httpx.Request("GET", url)))
        result = cli_bridge.SetupService._memory_status(root)
        assert result["state"] == ("updated" if payload == {"state": "updated"} else "unknown")
    monkeypatch.setattr(cli_bridge, "project_codex_home", lambda p: tmp_path / "missing")
    monkeypatch.setattr(cli_bridge, "ensure_project_codex_home", lambda *a, **k: pytest.fail("must not create or copy auth on read"))
    assert cli_bridge.SetupService()._subscription_status("codex", project_id="p1").ready is False


def test_setup_page_keeps_explicit_resume_across_page_reload():
    page = cli_bridge._setup_html()
    assert "if (client.resume_ready)" in page
    assert "pendingLogins" not in page
    assert "client.resume_failed ? resumeMemory(provider) : connect(provider)" in page
    assert "Ricollega" in page and "Reconnect" in page
