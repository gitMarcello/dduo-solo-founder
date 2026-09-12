from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anyio
import httpx
import mcp.types as mcp_types
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from dduo_solo_founder import mcp_server
from dduo_solo_founder import hooks


@pytest.fixture(autouse=True)
def authorized_native_hooks(monkeypatch):
    # Domain contract tests never inspect the real workstation's native trust.
    monkeypatch.setattr(
        mcp_server._MEMORY_CONNECTION_CHECKS, "check",
        lambda *_args, **_kwargs: {"ready": True, "requires_choice": False},
    )
    monkeypatch.setattr(mcp_server, "_read_connection_health", lambda _runtime: {"state": "updated"})


async def _sdk_session(client_name: str, operation):
    server = mcp_server.create_mcp_server()
    initialization = server.create_initialization_options()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                server.run,
                server_streams[0],
                server_streams[1],
                initialization,
            )
            async with ClientSession(
                client_streams[0],
                client_streams[1],
                client_info=mcp_types.Implementation(name=client_name, version="test"),
            ) as session:
                initialized = await session.initialize()
                value = await operation(session, initialized)
            task_group.cancel_scope.cancel()
    return value


def test_mcp_contract_contains_complete_protocol_and_real_schemas():
    names = set(mcp_server.TOOL_MODELS)
    assert {
        "health",
        "decline_setup",
        "open_setup",
        "check_setup",
        "check_memory_connection",
        "get_dashboard_link",
        "get_project_briefing",
        "get_project_manual",
        "update_project_manual",
        "search_memory",
        "get_memory_status",
        "request_sleep",
        "list_sleep_jobs",
        "retry_sleep_job",
        "explain_memory",
        "list_memory_revisions",
        "forget_memory",
        "set_off_record",
        "rebuild_vector_index",
        "register_artifact",
        "list_artifacts",
        "list_tasks",
        "get_task",
        "search_tasks",
        "list_plans",
        "get_plan",
        "list_sprints",
        "get_sprint",
        "create_sprint",
        "update_sprint",
        "start_sprint",
        "preview_sprint_close",
        "archive_sprint",
        "reopen_sprint",
        "list_sprint_history",
        "create_historical_sprint",
        "activate_task_context",
        "activate_plan_context",
        "create_task",
        "update_task",
        "create_plan",
        "update_plan",
        "get_activity",
        "update_project_profile",
        "rename_project",
    } == names
    assert "start_session" not in names
    assert "begin_turn" not in names
    assert "commit_turn" not in names
    for tool in ("update_task", "update_plan", "update_project_profile", "update_project_manual"):
        schema = mcp_server.TOOL_MODELS[tool][1].model_json_schema()
        assert "expected_version" in schema["required"]


def test_task_tool_schemas_are_explicit_and_keep_full_retrieval_available():
    list_schema = mcp_server.TOOL_MODELS["list_tasks"][1].model_json_schema()
    assert list_schema["properties"]["detail"]["default"] == "compact"
    assert set(list_schema["properties"]["detail"]["enum"]) == {"compact", "full"}
    assert list_schema["properties"]["scope"]["default"] == "active"
    assert set(list_schema["properties"]["scope"]["enum"]) == {
        "active",
        "completed",
        "all",
    }
    assert list_schema["properties"]["limit"]["maximum"] == 200
    assert "cursor" not in list_schema["properties"]
    assert "known_snapshot_hash" in list_schema["properties"]

    get_schema = mcp_server.TOOL_MODELS["get_task"][1].model_json_schema()
    assert get_schema["required"] == ["task_id"]
    assert get_schema["properties"]["detail"]["default"] == "working"
    assert set(get_schema["properties"]["detail"]["enum"]) == {
        "compact",
        "working",
        "full",
    }

    search_schema = mcp_server.TOOL_MODELS["search_tasks"][1].model_json_schema()
    assert search_schema["required"] == ["query"]
    assert search_schema["properties"]["scope"]["default"] == "active"
    assert set(search_schema["properties"]["scope"]["enum"]) == {
        "active",
        "completed",
        "all",
    }
    assert search_schema["properties"]["limit"]["default"] == 8
    assert search_schema["properties"]["limit"]["maximum"] == 100
    assert "cursor" in search_schema["properties"]

    activate_schema = mcp_server.TOOL_MODELS["activate_task_context"][1].model_json_schema()
    assert activate_schema["required"] == ["task_id"]
    assert "known_snapshot_hash" in activate_schema["properties"]


def test_read_tool_descriptions_discourage_duplicate_context_fetches():
    briefing = mcp_server.TOOL_MODELS["get_project_briefing"][0]
    task = mcp_server.TOOL_MODELS["get_task"][0]
    search = mcp_server.TOOL_MODELS["search_tasks"][0]

    assert "automatic Founder Brief" in briefing and "duplicates" in briefing
    assert "reuse an in-context result" in task and "snapshot hash" in task
    assert "do not repeat the same search" in search and "call get_task directly" in search


def test_portable_tool_schema_requires_a_root_only_without_a_native_adapter(monkeypatch):
    monkeypatch.delenv(mcp_server.MCP_PROJECT_ROOT_ENV, raising=False)
    portable = mcp_server._mcp_input_schema(mcp_server.EmptyInput)
    assert portable["properties"][mcp_server.MCP_WORKSPACE_ARGUMENT]["type"] == "string"
    assert mcp_server.MCP_WORKSPACE_ARGUMENT in portable["required"]

    monkeypatch.setenv(mcp_server.MCP_PROJECT_ROOT_ENV, "/project/from-adapter")
    native = mcp_server._mcp_input_schema(mcp_server.EmptyInput)
    assert mcp_server.MCP_WORKSPACE_ARGUMENT not in native.get("required", [])


def test_project_root_arguments_must_be_absolute_exact_and_consistent(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.delenv(mcp_server.MCP_PROJECT_ROOT_ENV, raising=False)
    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda root: Path(root))

    with pytest.raises(mcp_server.MCPContextError, match="absolute"):
        mcp_server._request_project_root({mcp_server.MCP_WORKSPACE_ARGUMENT: "relative"})

    monkeypatch.setenv(mcp_server.MCP_PROJECT_ROOT_ENV, str(first))
    with pytest.raises(mcp_server.MCPContextError, match="does not match"):
        mcp_server._request_project_root(
            {mcp_server.MCP_WORKSPACE_ARGUMENT: str(second)}
        )


def test_capture_lifecycle_uses_exact_content_free_local_hook_evidence(
    monkeypatch, tmp_path: Path
):
    """A server turn never lets diagnostics guess another local conversation."""
    binding = SimpleNamespace(binding_id="b" * 64)
    runtime = SimpleNamespace(
        project_id="p1",
        client="codex",
        binding=binding,
        api=object(),
    )
    session_id = "7e56a0c3-4c2d-4946-8e34-eefb2d2ef5f1"
    turn_id = "0d07b0d1-31c8-4ba6-bfe9-36e35e92d408"
    external = "native-session"
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    hooks.write_state(
        hooks.state_path("p1", "codex", external, binding.binding_id),
        {
            "project_id": "p1",
            "client": "codex",
            "binding_id": binding.binding_id,
            "session_id": session_id,
            "lifecycle_evidence": {
                "version": 1,
                "session_start": {"observed_at": "now", "context_emitted_at": "now"},
                "prompt": {
                    "observed_at": "now",
                    "turn_id": turn_id,
                    "context_emitted_at": "now",
                },
            },
        },
    )
    monkeypatch.setattr(
        mcp_server,
        "_raw_response",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "session": {"id": session_id, "external_id": external, "client": "codex"},
                "turn": {"id": turn_id, "status": "open", "committed": False, "off_record": False},
            },
        ),
    )

    value = mcp_server._capture_lifecycle(runtime, session_id, turn_id)

    assert value == {
        "correlation": "exact",
        "session_start_observed": True,
        "prompt_observed": True,
        "context_emitted": True,
        "completion": "not_yet_completed",
    }


def test_project_root_validation_rejects_every_ambiguous_adapter_input(monkeypatch, tmp_path):
    root = tmp_path / "root"
    nested = root / "nested"
    root.mkdir()
    nested.mkdir()
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("x")

    monkeypatch.delenv(mcp_server.MCP_PROJECT_ROOT_ENV, raising=False)
    with pytest.raises(mcp_server.MCPContextError, match="non-empty"):
        mcp_server._canonical_adapter_root(None, source="workspace_root")
    with pytest.raises(mcp_server.MCPContextError, match="invalid"):
        mcp_server._canonical_adapter_root(f"{root}\x00escape", source="workspace_root")
    with pytest.raises(mcp_server.MCPContextError, match="available"):
        mcp_server._canonical_adapter_root(str(tmp_path / "missing"), source="workspace_root")
    with pytest.raises(mcp_server.MCPContextError, match="directory"):
        mcp_server._canonical_adapter_root(str(regular_file), source="workspace_root")

    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda _candidate: root)
    with pytest.raises(mcp_server.MCPContextError, match="project root"):
        mcp_server._canonical_adapter_root(str(nested), source="workspace_root")

    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda candidate: Path(candidate))
    same = {mcp_server.MCP_WORKSPACE_ARGUMENT: str(root)}
    monkeypatch.setenv(mcp_server.MCP_PROJECT_ROOT_ENV, str(root))
    assert mcp_server._request_project_root(same) == root
    assert same == {}

    monkeypatch.delenv(mcp_server.MCP_PROJECT_ROOT_ENV)
    with pytest.raises(mcp_server.MCPContextError, match="workspace_root is required"):
        mcp_server._request_project_root({})


def test_runtime_rejects_a_binding_resolved_for_a_different_root(monkeypatch, tmp_path):
    root = tmp_path / "selected"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda candidate: Path(candidate))
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda _root: {"id": "p1", "binding": "remote"},
    )
    monkeypatch.setattr(
        mcp_server,
        "binding_from_project",
        lambda *_args, **_kwargs: mcp_server.ProjectBinding(
            project_id="p1",
            name="Wrong root",
            root_path=other,
            kind="remote",
            api_url="https://memory.example.test/api",
            dashboard_url=None,
            binding_id="a" * 64,
            bearer_token="token",
        ),
    )
    with pytest.raises(mcp_server.MCPContextError, match="does not match"):
        mcp_server._resolve_tool_runtime(
            "codex", {mcp_server.MCP_WORKSPACE_ARGUMENT: str(root)}
        )


def test_copied_local_project_config_fails_before_any_memory_request(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda _root: {"id": "copied", "api_port": 18001, "web_port": 20001},
    )
    monkeypatch.setattr(
        mcp_server,
        "validate_project_registration",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("claimed by another checkout")),
    )
    arguments = {mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
    with pytest.raises(RuntimeError, match="claimed by another checkout"):
        mcp_server._resolve_tool_runtime("codex", arguments)


def test_call_requires_configuration_except_health_or_setup(monkeypatch, tmp_path):
    assert mcp_server.call("health", {}, None, "http://api")["status"] == "unconfigured"
    monkeypatch.setattr(
        mcp_server, "start_local_setup", lambda root, **kwargs: {"opened": True}
    )
    setup = mcp_server.call("open_setup", {}, None, "http://api", tmp_path)
    assert setup["opened"] is True and "Do not show commands" in setup["response_instruction"]
    assert "setup_url" not in setup and "127.0.0.1" not in str(setup)
    with pytest.raises(ValueError, match="not configured"):
        mcp_server.call("list_tasks", {}, None, "http://api")


def test_open_setup_proposes_key_choice_without_claiming_page_or_success(monkeypatch, tmp_path):
    choice = {"project_id": "92f89fdc-304f-4185-a743-34f1eed8e620", "name": "Example"}
    monkeypatch.setattr(mcp_server, "start_local_setup", lambda root, **kwargs: {
        "opened": False, "requires_key_choice": True, "key_choices": [choice],
    })
    result = mcp_server.call("open_setup", {}, None, "", tmp_path)
    assert result["requires_key_choice"] is True and result["opened"] is False
    assert result["key_choices"] == [choice]
    assert "Wait for the answer" in result["response_instruction"]
    assert "ready" not in result


@pytest.mark.parametrize("arguments", [
    {"reuse_key_from_project": "92f89fdc-304f-4185-a743-34f1eed8e620"},
    {"use_new_key": True},
])
def test_open_setup_forwards_only_explicit_key_choice(monkeypatch, tmp_path, arguments):
    calls = []
    monkeypatch.setattr(mcp_server, "start_local_setup", lambda root, **kwargs: (
        calls.append((root, kwargs)) or {"opened": True}
    ))
    result = mcp_server.call("open_setup", arguments, None, "", tmp_path)
    assert result["opened"] is True
    assert calls == [(tmp_path, {
        "provider": mcp_server.current_setup_provider(),
        "reuse_key_from_project": arguments.get("reuse_key_from_project"),
        "use_new_key": arguments.get("use_new_key", False),
    })]


@pytest.mark.parametrize("ready", [True, False])
def test_open_setup_browserless_activation_uses_real_readiness(monkeypatch, tmp_path, ready):
    state = {
        "memory_status": {"state": "updated"},
        "docker": {"ready": True}, "embeddings": {"ready": True},
        "clients": {"codex": {"ready": True}}, "codex_hooks": {"ready": True},
        "project": {"ready": ready},
    }
    monkeypatch.setattr(mcp_server, "start_local_setup", lambda root, **kwargs: {
        "opened": False, "status": state,
    })
    result = mcp_server.call("open_setup", {}, None, "", tmp_path)
    assert result["opened"] is False and result["ready"] is ready


def test_check_setup_returns_one_safe_next_action_and_verifies_completion(monkeypatch, tmp_path):
    state = {
        "memory_status": {"state": "updated"},
        "docker": {"ready": False},
        "embeddings": {"ready": False},
        "clients": {"codex": {"ready": False, "setup_state": "waiting"}},
        "codex_hooks": {"ready": False},
        "project": {"ready": False},
    }
    monkeypatch.setattr(mcp_server, "read_setup_status", lambda root: state)
    incomplete = mcp_server.call("check_setup", {"provider": "codex"}, None, "http://api", tmp_path)
    assert incomplete["ready"] is False
    assert incomplete["next_action"]["id"] == "docker"
    assert [action["id"] for action in incomplete["remaining_actions"]] == [
        "docker",
        "embeddings",
        "codex_login",
        "codex_hooks",
        "project",
    ]
    assert "already open" in incomplete["remaining_actions"][2]["detail"]
    assert incomplete["remaining_actions"][-1]["id"] == "project"
    assert "Do not claim Setup is complete" in incomplete["response_instruction"]

    state["docker"]["ready"] = True
    state["embeddings"]["ready"] = True
    state["clients"]["codex"] = {"ready": True, "setup_state": "idle"}
    state["codex_hooks"] = {"ready": False, "reason": "hooks_incomplete"}
    state["project"]["ready"] = True
    repair = mcp_server.call(
        "check_setup", {"provider": "codex"}, None, "http://api", tmp_path
    )
    assert repair["next_action"]["title"] == "Repair the Codex plugin"
    assert "fully restart Codex" in repair["next_action"]["detail"]

    state["clients"]["codex"] = {"ready": False, "setup_state": "idle"}
    state["codex_hooks"] = {"ready": False, "reason": "authorization_required"}
    reconnect = mcp_server.call("check_setup", {"provider": "codex"}, None, "http://api", tmp_path)
    assert reconnect["next_action"]["id"] == "codex_login"
    assert "Select Connect" in reconnect["next_action"]["detail"]

    state["codex_hooks"]["ready"] = True
    state["project"]["ready"] = True
    state["clients"]["codex"] = {"ready": True, "setup_state": "idle"}
    complete = mcp_server.call("check_setup", {"provider": "codex"}, None, "http://api", tmp_path)
    assert complete["ready"] is True
    assert complete["next_action"] is None
    assert "Setup is verified" in complete["response_instruction"]

    monkeypatch.setattr(
        mcp_server,
        "read_setup_status",
        lambda root: (_ for _ in ()).throw(RuntimeError("private failure")),
    )
    unavailable = mcp_server.call(
        "check_setup", {"provider": "claude"}, None, "http://api", tmp_path
    )
    assert unavailable["next_action"]["id"] == "setup_unavailable"
    assert "private failure" not in str(unavailable)


def test_check_setup_infers_the_current_plugin_client(monkeypatch, tmp_path):
    state = {
        "docker": {"ready": True},
        "embeddings": {"ready": True},
        "clients": {"codex": {"ready": True, "setup_state": "idle"}},
        "codex_hooks": {"ready": True},
        "project": {"ready": True},
    }
    monkeypatch.setattr(mcp_server, "read_setup_status", lambda root: state)
    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "codex")
    assert mcp_server.call("check_setup", {}, None, "http://api", tmp_path)["provider"] == "codex"
    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "claude")
    assert mcp_server.current_setup_provider() == "claude"
    monkeypatch.delenv(mcp_server.MCP_CLIENT_ENV)
    assert mcp_server.current_setup_provider() is None

    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "cursor")
    assert mcp_server.current_setup_provider() is None


def test_setup_requires_an_explicit_root_and_skips_client_actions_without_a_provider(
    monkeypatch, tmp_path
):
    state = {
        "memory_status": {"state": "updated"},
        "docker": {"ready": True},
        "embeddings": {"ready": True},
        "clients": {},
        "codex_hooks": {"ready": False},
        "project": {"ready": True},
    }
    monkeypatch.setattr(mcp_server, "read_setup_status", lambda _root: state)
    checked = mcp_server.check_setup(tmp_path, None)
    assert checked["ready"] is True
    assert checked["remaining_actions"] == []

    with pytest.raises(ValueError, match="did not supply"):
        mcp_server.check_setup(None, "codex")
    with pytest.raises(ValueError, match="did not supply"):
        mcp_server.call("open_setup", {}, None, "http://api", None)


def test_remote_setup_tools_never_open_local_setup_or_require_docker(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote project",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url="https://203.0.113.10",
        binding_id="a" * 64,
        bearer_token="project-token",
    )
    monkeypatch.setattr(
        mcp_server,
        "start_local_setup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local Setup must stay closed")),
    )
    monkeypatch.setattr(
        mcp_server,
        "read_setup_status",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local Setup must not be checked")),
    )
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((api, method, path, body))
        or ({"state": "updated"} if path.endswith("memory-status") else {"current_member": {"id": "member-1"}}),
    )

    opened = mcp_server.call(
        "open_setup",
        {},
        "p1",
        "https://203.0.113.10/api",
        tmp_path,
        binding=binding,
    )
    assert opened["opened"] is False
    assert opened["mode"] == "remote"
    assert opened["binding_ready"] is True
    assert opened["docker_required"] is False
    assert opened["local_setup_required"] is False
    assert opened["dashboard_url"].startswith("https://203.0.113.10/")
    assert "Do not open local Setup or start Docker" in opened["response_instruction"]

    checked = mcp_server.call(
        "check_setup",
        {"provider": "codex"},
        "p1",
        "https://203.0.113.10/api",
        tmp_path,
        binding=binding,
    )
    assert checked["ready"] is True
    assert checked["mode"] == "remote"
    assert checked["provider"] == "codex"
    assert checked["next_action"] is None
    assert checked["docker_required"] is False
    assert calls == [
        ("https://203.0.113.10/api", "GET", "/projects/p1/team", None),
        ("https://203.0.113.10/api", "GET", "/projects/p1/memory-status", None),
    ]

    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private outage")),
    )
    unavailable = mcp_server.call(
        "check_setup",
        {},
        "p1",
        "https://203.0.113.10/api",
        tmp_path,
        binding=binding,
    )
    assert unavailable["ready"] is False
    assert unavailable["next_action"]["id"] == "remote_memory_unavailable"
    assert "private outage" not in str(unavailable)
    assert "Do not open local Setup or start Docker" in unavailable["response_instruction"]


def test_declared_remote_binding_failure_never_falls_back_to_local_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda _root: {"id": "p1", "name": "Remote", "binding": "remote"},
    )
    monkeypatch.setattr(
        mcp_server,
        "start_local_setup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local Setup must stay closed")),
    )
    monkeypatch.setattr(
        mcp_server,
        "read_setup_status",
        lambda *_args: (_ for _ in ()).throw(AssertionError("local Setup must not be checked")),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("binding is incomplete")),
    )

    health = mcp_server.call("health", {}, None, "http://127.0.0.1:8765", tmp_path)
    assert health["mode"] == "remote"
    assert health["binding_ready"] is False
    assert health["next_action"]["id"] == "remote_binding_required"

    opened = mcp_server.call("open_setup", {}, None, "http://127.0.0.1:8765", tmp_path)
    assert opened["mode"] == "remote"
    assert opened["binding_ready"] is False
    assert opened["next_action"]["id"] == "remote_binding_required"
    assert opened["docker_required"] is False

    checked = mcp_server.call("check_setup", {}, None, "http://127.0.0.1:8765", tmp_path)
    assert checked["ready"] is False
    assert checked["mode"] == "remote"
    assert checked["next_action"]["id"] == "remote_binding_required"
    assert checked["local_setup_required"] is False


def test_runtime_scopes_briefing_and_memory_status_to_the_active_client(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((method, path)) or {},
    )

    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "codex")
    mcp_server.call("get_project_briefing", {}, "p1", "http://api")
    mcp_server.call("get_memory_status", {}, "p1", "http://api")
    assert calls == [
        ("GET", "/projects/p1/briefing?client=codex"),
        ("GET", "/projects/p1/memory-status?client=codex"),
    ]

    calls.clear()
    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "claude")
    mcp_server.call("get_project_briefing", {}, "p1", "http://api")
    mcp_server.call("get_memory_status", {}, "p1", "http://api")
    assert calls == [
        ("GET", "/projects/p1/briefing?client=claude"),
        ("GET", "/projects/p1/memory-status?client=claude"),
    ]


@pytest.mark.parametrize(
    ("name", "args", "method", "path"),
    [
        ("health", {}, "GET", "/health"),
        ("get_project_briefing", {}, "GET", "/projects/p1/briefing"),
        ("get_memory_status", {}, "GET", "/projects/p1/memory-status"),
        ("request_sleep", {"trigger": "topic_boundary"}, "POST", "/projects/p1/sleep"),
        ("list_sleep_jobs", {"limit": 7}, "GET", "/projects/p1/sleep-jobs?limit=7"),
        ("retry_sleep_job", {"job_id": "j1"}, "POST", "/projects/p1/sleep-jobs/j1/retry"),
        ("explain_memory", {"memory_id": "m1"}, "GET", "/projects/p1/memories/m1/provenance"),
        ("list_memory_revisions", {"memory_id": "m1"}, "GET", "/projects/p1/memories/m1/revisions"),
        (
            "forget_memory",
            {"memory_id": "m1", "rationale": "requested"},
            "POST",
            "/projects/p1/memories/forget",
        ),
        (
            "set_off_record",
            {"session_id": "s1", "off_record": True},
            "PATCH",
            "/projects/p1/sessions/s1/privacy",
        ),
        ("rebuild_vector_index", {}, "POST", "/projects/p1/memories/reindex"),
        ("list_artifacts", {"limit": 7}, "GET", "/projects/p1/artifacts?limit=7"),
        ("list_tasks", {}, "GET", "/projects/p1/tasks?detail=compact&scope=active"),
        ("get_task", {"task_id": "t1"}, "GET", "/projects/p1/tasks/t1?detail=working"),
        ("search_tasks", {"query": "android"}, "POST", "/projects/p1/tasks/search"),
        ("list_plans", {}, "GET", "/projects/p1/plans?detail=compact"),
        ("create_task", {"title": "x"}, "POST", "/projects/p1/tasks?detail=compact"),
        (
            "update_task",
            {"task_id": "x"},
            "PATCH",
            "/projects/p1/tasks/x?detail=compact",
        ),
        ("create_plan", {"title": "x"}, "POST", "/projects/p1/plans"),
        ("update_plan", {"plan_id": "x"}, "PATCH", "/projects/p1/plans/x"),
        ("get_activity", {"limit": 7}, "GET", "/projects/p1/activity?limit=7"),
        ("update_project_profile", {"cause": "x"}, "PATCH", "/projects/p1"),
    ],
)
def test_call_routes_tools_without_mutating_input(monkeypatch, name, args, method, path):
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, verb, route, body=None: calls.append((api, verb, route, body)) or {"ok": True},
    )
    original = dict(args)
    result = mcp_server.call(name, args, "p1", "http://api")
    if name in {
        "get_project_briefing",
        "list_tasks",
        "get_task",
        "search_tasks",
        "list_plans",
        "create_task",
        "update_task",
        "create_plan",
        "update_plan",
    }:
        assert "response_instruction" in result
    else:
        assert result == {"ok": True}
    expected_body = original if method in {"POST", "PATCH"} else None
    if name in {"update_task", "update_plan"}:
        identifier = "task_id" if name == "update_task" else "plan_id"
        expected_body = {key: value for key, value in original.items() if key != identifier}
    if name == "set_off_record":
        expected_body = {key: value for key, value in original.items() if key != "session_id"}
    if name in {"retry_sleep_job", "rebuild_vector_index"}:
        expected_body = None
    if name == "search_tasks":
        expected_body = {"query": "android", "scope": "active", "limit": 8}
    assert calls == [("http://api", method, path, expected_body)]
    assert args == original


def test_call_search_and_unknown(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": [1]}

    monkeypatch.setattr(mcp_server.httpx, "get", lambda *args, **kwargs: Response())
    assert mcp_server.call("search_memory", {"query": "why", "limit": 3}, "p1", "x") == {
        "items": [1]
    }
    with pytest.raises(ValueError, match="unknown tool"):
        mcp_server.call("missing", {}, "p1", "x")


def test_mcp_result_captures_the_exact_project_scoped_unicode_result(monkeypatch, tmp_path):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    monkeypatch.setattr(
        mcp_server.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs["json"])) or Response(),
    )
    response = mcp_server.result(
        {"memory": "valore privato già verificato 🚀"},
        api="http://api",
        project_id="p1",
        tool_name="search_memory",
    )
    text = response["content"][0]["text"]
    assert json.loads(text)["memory"] == "valore privato già verificato 🚀"
    url, body = calls[0]
    assert url.endswith("/projects/p1/observability/context-events/batch")
    item = body["items"][0]
    assert item["operation"] == "context.mcp_tool_result"
    assert item["component_bytes"] == {"result": item["utf8_bytes"]}
    assert item["content"] == text
    assert item["content_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert item["producer_version"] == mcp_server.__version__
    assert item["render_version"] == mcp_server.MCP_CONTEXT_RENDER_VERSION
    assert item["components"] == [
        {
            "name": "result",
            "utf8_bytes": len(text.encode("utf-8")),
            "estimated_tokens": (len(text.encode("utf-8")) + 3) // 4,
            "item_count": 1,
            "references": [],
        }
    ]
    assert datetime.fromisoformat(item["occurred_at"]).utcoffset() is not None
    assert item.get("session_id") is None
    assert item.get("turn_id") is None
    assert item.get("retrieval_run_id") is None


@pytest.mark.parametrize("expected", ["codex", "claude"])
def test_mcp_result_attributes_the_active_plugin_client(
    monkeypatch, tmp_path, expected
):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, expected)
    monkeypatch.setattr(
        mcp_server.httpx,
        "post",
        lambda url, **kwargs: calls.append(kwargs["json"]) or Response(),
    )

    mcp_server.result(
        {"status": "ok"},
        api="http://api",
        project_id="p1",
        tool_name="health",
    )

    assert calls[0]["items"][0]["client"] == expected


def test_remote_mcp_spool_never_crosses_device_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    binding_id = "c" * 64

    def endpoint(token, request_function):
        binding = mcp_server.ProjectBinding(
            project_id="p1",
            name="Remote",
            root_path=tmp_path,
            kind="remote",
            api_url="https://203.0.113.10/api",
            dashboard_url=None,
            binding_id=binding_id,
            bearer_token=token,
        )
        client = mcp_server.ProjectHttpClient(
            binding,
            component="mcp",
            request_function=request_function,
        )
        return mcp_server.ApiEndpoint(binding.api_url, client, binding_id)

    def offline(method, url, **_kwargs):
        raise httpx.ConnectError("offline", request=httpx.Request(method, url))

    old_api = endpoint("old-device-token", offline)
    mcp_server.result(
        {"memory": "queued by the old member"},
        api=old_api,
        project_id="p1",
        tool_name="search_memory",
    )
    queue_path = mcp_server._observability_queue_path("p1", binding_id)
    queued = json.loads(queue_path.read_text())
    assert queued[0]["_credential_scope"] == hashlib.sha256(
        b"old-device-token"
    ).hexdigest()

    new_calls = []

    def online(method, url, **kwargs):
        new_calls.append(kwargs["json"])
        return httpx.Response(202, request=httpx.Request(method, url))

    new_api = endpoint("new-device-token", online)
    mcp_server.flush_observability_queue(new_api, "p1", binding_id=binding_id)

    assert new_calls == []
    assert not queue_path.exists()

    new_offline_api = endpoint("new-device-token", offline)
    mcp_server.result(
        {"memory": "queued by the new member"},
        api=new_offline_api,
        project_id="p1",
        tool_name="search_memory",
    )
    mcp_server.flush_observability_queue(new_api, "p1", binding_id=binding_id)

    assert len(new_calls) == 1
    assert new_calls[0]["items"][0]["content"].find("queued by the new member") >= 0
    assert "_credential_scope" not in new_calls[0]["items"][0]
    assert not queue_path.exists()


def test_remote_observability_without_a_credential_gets_an_isolated_scope(tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="c" * 64,
        bearer_token="",
    )
    api = mcp_server.ApiEndpoint(
        binding.api_url,
        mcp_server.ProjectHttpClient(binding, component="mcp"),
        binding.binding_id,
    )
    assert mcp_server._observability_credential_scope(api) == "remote-credential-unavailable"


def test_observability_queue_cleanup_failure_never_changes_the_saved_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    original_unlink = type(tmp_path).unlink

    def fail_temporary_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError("cleanup unavailable")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "unlink", fail_temporary_cleanup)
    mcp_server._write_observability_queue("p1", [{"idempotency_key": "one"}])
    assert json.loads((tmp_path / "p1.json").read_text()) == [{"idempotency_key": "one"}]


def test_mcp_task_result_records_task_versions_and_delivered_detail(monkeypatch, tmp_path):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    monkeypatch.setattr(
        mcp_server.httpx,
        "post",
        lambda url, **kwargs: calls.append(kwargs["json"]) or Response(),
    )
    value = {
        "items": [
            {"id": "task-1", "version": 4, "title": "Ship Android"},
            {"id": "task-2", "version": 7, "title": "Verify release"},
        ],
        "detail": "compact",
    }
    mcp_server.result(
        value,
        api="http://api",
        project_id="p1",
        tool_name="list_tasks",
    )
    item = calls[0]["items"][0]
    assert item["render_version"] == "mcp-result-v3"
    assert item["components"][0]["references"] == [
        "task:task-1@v4:compact",
        "task:task-2@v7:compact",
    ]
    assert item["components"][0]["item_count"] == 2


def test_task_observability_references_normalize_all_supported_payload_shapes():
    assert mcp_server._task_observability_references(
        {"task": {"id": "t1", "version": 2}, "detail": "invalid"},
        "get_task",
    ) == ["task:t1@v2:working"]
    assert mcp_server._task_observability_references(
        {"id": "t2", "version": 3},
        "create_task",
    ) == ["task:t2@v3:compact"]
    assert mcp_server._task_observability_references(
        {
            "items": [
                None,
                {"id": "", "version": 1},
                {"id": "wrong-version", "version": "1"},
                {"id": "t3", "version": 4},
                {"id": "t3", "version": 4},
                {"id": "x" * 220, "version": 5},
            ],
            "detail": "full",
        },
        "search_tasks",
    ) == ["task:t3@v4:full"]
    assert mcp_server._task_observability_references(
        {"items": "not-a-list"}, "list_tasks"
    ) == []


def test_mcp_non_task_result_does_not_invent_task_references(monkeypatch, tmp_path):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(mcp_server, "MCP_OBSERVABILITY_DIR", tmp_path)
    monkeypatch.setattr(
        mcp_server.httpx,
        "post",
        lambda url, **kwargs: calls.append(kwargs["json"]) or Response(),
    )
    mcp_server.result(
        {"items": [{"id": "memory-1", "version": 9}]},
        api="http://api",
        project_id="p1",
        tool_name="search_memory",
    )
    assert calls[0]["items"][0]["components"][0]["references"] == []


def test_mcp_telemetry_failures_never_change_tool_result(monkeypatch):
    value = {"items": [{"text": "the exact tool result must survive"}]}
    expected = mcp_server.result(value)

    monkeypatch.setattr(
        mcp_server,
        "_read_observability_queue",
        lambda project_id: (_ for _ in ()).throw(PermissionError("queue unreadable")),
    )
    monkeypatch.setattr(
        mcp_server.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("api offline")),
    )
    monkeypatch.setattr(
        mcp_server,
        "_write_observability_queue",
        lambda project_id, items: (_ for _ in ()).throw(PermissionError("queue unwritable")),
    )

    assert (
        mcp_server.result(
            value,
            api="http://api",
            project_id="p1",
            tool_name="search_memory",
        )
        == expected
    )


def test_mcp_result_survives_estimator_failure(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "estimated_tokens_for_text",
        lambda value: (_ for _ in ()).throw(RuntimeError("estimator unavailable")),
    )
    value = {"status": "ok"}
    assert mcp_server.result(
        value,
        api="http://api",
        project_id="p1",
        tool_name="health",
    ) == mcp_server.result(value)


def test_activate_task_context_loads_one_task_directly_as_a_compatible_alias(monkeypatch):
    calls = []
    task = {"id": "t1", "title": "Ship Android", "status": "in_progress"}
    snapshot_hash = "b" * 64
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((method, path, body))
        or {"task": task, "detail": "working", "unchanged": False, "snapshot_hash": snapshot_hash},
    )
    dashboard_url = "http://127.0.0.1:20417/?project=p1&tab=tasks"
    selected = mcp_server.call(
        "activate_task_context",
        {"task_id": "t1"},
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
    )
    assert selected["task"] == {
        **task,
        "url": f"{dashboard_url}&work=t1",
    }
    assert selected["activated"] is True
    assert selected["snapshot_hash"] == snapshot_hash
    assert "Never expose an internal ID" in selected["response_instruction"]
    assert calls == [("GET", "/projects/p1/tasks/t1?detail=working", None)]

    selected = mcp_server.call(
        "activate_task_context",
        {"task_id": "t1", "known_snapshot_hash": snapshot_hash},
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
    )
    assert selected["task"]["title"] == task["title"]
    assert calls[-1] == (
        "GET",
        f"/projects/p1/tasks/t1?detail=working&known_snapshot_hash={snapshot_hash}",
        None,
    )

    unchanged = {"task": None, "detail": "working", "unchanged": True, "snapshot_hash": snapshot_hash}
    monkeypatch.setattr(mcp_server, "request", lambda *args, **kwargs: unchanged)
    unchanged_result = mcp_server.call(
        "activate_task_context",
        {"task_id": "t1"},
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
    )
    assert unchanged_result["unchanged"] is True
    assert unchanged_result["item_url"].endswith("&work=t1")


def test_task_reads_forward_filters_snapshot_hash_and_dashboard_without_caching(monkeypatch):
    snapshot_hash = "a" * 64
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((method, path, body))
        or {"items": []},
    )
    dashboard_url = "http://127.0.0.1:20417/?project=p1&tab=tasks"
    list_args = {
        "detail": "full",
        "status": "in_progress",
        "kind": "task",
        "epic_id": "epic/one",
        "label": "mobile app",
        "known_snapshot_hash": snapshot_hash,
    }
    first = mcp_server.call(
        "list_tasks", list_args, "p1", "http://api", dashboard_url=dashboard_url
    )
    second = mcp_server.call(
        "list_tasks", list_args, "p1", "http://api", dashboard_url=dashboard_url
    )
    task = mcp_server.call(
        "get_task",
        {
            "task_id": "t1",
            "detail": "full",
            "known_snapshot_hash": snapshot_hash,
        },
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
    )
    expected_list_path = (
        "/projects/p1/tasks?detail=full&scope=active&status=in_progress&kind=task&epic_id=epic%2Fone"
        f"&label=mobile+app&known_snapshot_hash={snapshot_hash}"
    )
    assert calls == [
        ("GET", expected_list_path, None),
        ("GET", expected_list_path, None),
        (
            "GET",
            f"/projects/p1/tasks/t1?detail=full&known_snapshot_hash={snapshot_hash}",
            None,
        ),
    ]
    assert first["dashboard_url"] == dashboard_url
    assert second["dashboard_url"] == dashboard_url
    assert task["dashboard_url"] == dashboard_url
    assert list_args["known_snapshot_hash"] == snapshot_hash


def test_single_task_and_briefing_reads_expose_exact_human_urls(monkeypatch):
    dashboard_url = "http://127.0.0.1:20417/?project=p1&tab=tasks"
    plans_url = f"{dashboard_url}&view=plans"
    responses = iter(
        [
            {"task": {"id": "task/one", "title": "Human task"}},
            {
                "tasks": [{"id": "task/one", "title": "Human task"}],
                "plans": [{"id": "plan/one", "title": "Human plan"}],
            },
        ]
    )
    monkeypatch.setattr(mcp_server, "request", lambda *args, **kwargs: next(responses))

    task = mcp_server.call(
        "get_task",
        {"task_id": "task/one"},
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
    )
    assert task["task"]["url"].endswith("&work=task%2Fone")
    assert "internal ID or UUID" in task["response_instruction"]

    briefing = mcp_server.call(
        "get_project_briefing",
        {},
        "p1",
        "http://api",
        dashboard_url=dashboard_url,
        plans_dashboard_url=plans_url,
    )
    assert briefing["tasks"][0]["url"].endswith("&work=task%2Fone")
    assert briefing["plans"][0]["url"].endswith("&plan=plan%2Fone")


def test_search_tasks_posts_scope_cursor_and_simple_filters_without_mutating_input(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((method, path, body))
        or {"items": [{"id": "t1", "score": 0.91}]},
    )
    args = {
        "query": "traduzioni giapponesi",
        "scope": "all",
        "limit": 25,
        "cursor": "next-page",
        "status": "done",
        "kind": "task",
        "epic_id": "e1",
        "label": "translation",
    }
    original = dict(args)
    response = mcp_server.call(
        "search_tasks",
        args,
        "p1",
        "http://api",
        dashboard_url="http://127.0.0.1:20417/?project=p1&tab=tasks",
    )
    assert response["items"][0]["id"] == "t1"
    assert response["items"][0]["url"].endswith("&work=t1")
    assert response["dashboard_url"].endswith("project=p1&tab=tasks")
    assert calls == [("POST", "/projects/p1/tasks/search", original)]
    assert args == original


@pytest.mark.parametrize("name,args,method,path", [
    ("list_sprints", {"status": "archived", "offset": 100}, "GET", "/projects/p1/sprints?status=archived&offset=100"),
    ("get_sprint", {"sprint_id": "s1"}, "GET", "/projects/p1/sprints/s1"),
    ("preview_sprint_close", {"sprint_id": "s1"}, "GET", "/projects/p1/sprints/s1/close-preview"),
    ("list_sprint_history", {"sprint_id": "s1", "closure_version": 3}, "GET", "/projects/p1/sprints/s1/tasks?closure_version=3"),
    ("create_sprint", {"title": "One", "idempotency_key": "12345678"}, "POST", "/projects/p1/sprints"),
    ("create_historical_sprint", {"title": "Old", "task_ids": ["t1"], "idempotency_key": "12345678"}, "POST", "/projects/p1/sprints/history"),
    ("update_sprint", {"sprint_id": "s1", "title": "Next", "expected_version": 1, "idempotency_key": "12345678"}, "PATCH", "/projects/p1/sprints/s1"),
    ("start_sprint", {"sprint_id": "s1", "expected_version": 1, "idempotency_key": "12345678"}, "POST", "/projects/p1/sprints/s1/start"),
    ("archive_sprint", {"sprint_id": "s1", "expected_version": 1, "idempotency_key": "12345678", "unfinished_destination": "backlog"}, "POST", "/projects/p1/sprints/s1/archive"),
    ("reopen_sprint", {"sprint_id": "s1", "expected_version": 1, "idempotency_key": "12345678"}, "POST", "/projects/p1/sprints/s1/reopen"),
])
def test_sprint_tools_preserve_project_paths_versions_and_idempotency(monkeypatch, name, args, method, path):
    calls = []
    monkeypatch.setattr(mcp_server, "request", lambda *values: calls.append(values) or {"id": "s1"})
    original = dict(args)
    assert mcp_server.call(name, args, "p1", "http://api")["id"] == "s1"
    assert calls[0][1:3] == (method, path)
    if method != "GET":
        assert calls[0][3] == {key: value for key, value in args.items() if key != "sprint_id"}
    assert args == original


def test_decline_setup_requires_explicit_unconfigured_root(monkeypatch, tmp_path):
    from dduo_solo_founder import project_activation
    calls = []
    monkeypatch.setattr(project_activation, "decline_setup", calls.append)
    with pytest.raises(ValueError, match="project root"):
        mcp_server.call("decline_setup", {}, None, "http://api")
    with pytest.raises(ValueError, match="unconfigured"):
        mcp_server.call("decline_setup", {}, "p1", "http://api", project_root=tmp_path)
    assert mcp_server.call("decline_setup", {}, None, "http://api", project_root=tmp_path)["status"] == "declined"
    assert calls == [tmp_path]


def test_health_respects_decline_but_explicit_setup_remains_available(monkeypatch, tmp_path):
    from dduo_solo_founder import project_activation

    monkeypatch.setattr(project_activation, "ACTIVATION_DIR", tmp_path / "private-preferences")
    opened = []
    monkeypatch.setattr(mcp_server, "start_local_setup", lambda root, **kwargs: opened.append(root) or {"opened": True})
    assert mcp_server.call("health", {}, None, "", project_root=tmp_path)["status"] == "unconfigured"
    mcp_server.call("decline_setup", {}, None, "", project_root=tmp_path)
    assert mcp_server.call("health", {}, None, "", project_root=tmp_path)["status"] == "declined"
    runtime = SimpleNamespace(project_id=None, project_root=tmp_path)
    checked = mcp_server._connection_notice("check_memory_connection", runtime, None)
    assert checked["reason"] == "declined" and checked["requires_choice"] is False
    assert opened == []
    assert mcp_server.call("open_setup", {}, None, "", project_root=tmp_path)["opened"] is True
    assert opened == [tmp_path]


def test_connection_check_uses_only_the_supplied_session_and_turn(monkeypatch, tmp_path):
    calls = []
    runtime = mcp_server.MCPToolRuntime(
        client="codex",
        project_root=tmp_path,
        project_id="project-1",
        api=mcp_server.ApiEndpoint("https://memory.example/api"),
        dashboard_url=None,
        plans_dashboard_url=None,
        binding=None,
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "session": {"id": "session-1", "client": "codex"},
                "turn": {"id": "turn-1", "committed": True, "off_record": False},
            }

    monkeypatch.setattr(
        mcp_server,
        "_raw_response",
        lambda _api, method, path, **kwargs: calls.append((method, path, kwargs["params"])) or Response(),
    )
    lifecycle = mcp_server._capture_lifecycle(runtime, "session-1", "turn-1")

    assert calls == [
        (
            "GET",
            "/projects/project-1/sessions/session-1/capture-status",
            {"client": "codex", "turn_id": "turn-1"},
        )
    ]
    assert lifecycle["correlation"] == "exact"
    assert lifecycle["completion"] == "persisted"


@pytest.mark.parametrize("stop,expected", [
    (None, "not_yet_completed"),
    ({"response_available": False}, "incomplete"),
    ({"response_available": True, "queued_at": "now"}, "queued"),
])
def test_capture_open_server_turn_preserves_exact_local_completion(monkeypatch, stop, expected):
    runtime = SimpleNamespace(project_id="p1", client="codex", api=object())
    state = {"turn_id": "t1", "lifecycle_evidence": {"version": 1}}
    if stop is not None:
        state["lifecycle_evidence"]["stop"] = {"turn_id": "t1", **stop}
    monkeypatch.setattr(mcp_server, "_local_lifecycle_state", lambda *args: ("exact", state))
    monkeypatch.setattr(mcp_server, "_raw_response", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "session": {"id": "s1", "client": "codex", "external_id": "native-s1"},
            "turn": {"id": "t1", "status": "open", "committed": False, "off_record": False},
        },
    ))
    value = mcp_server._capture_lifecycle(runtime, "s1", "t1")
    assert value["completion"] == expected
    assert value["session_start_observed"] is None


def test_local_capture_does_not_substitute_a_previous_turn():
    state = {
        "turn_id": "current", "lifecycle_evidence": {"version": 1, "stop": {
            "turn_id": "old", "response_available": True, "server_committed_at": "now",
        }},
    }
    assert mcp_server._lifecycle_from_state(state, None)["completion"] == "unknown"
    assert mcp_server._lifecycle_from_state(state, "current")["completion"] == "not_yet_completed"
    assert mcp_server._lifecycle_from_state(state, "old")["completion"] == "persisted"


def test_server_turn_does_not_certify_a_local_hook(monkeypatch):
    runtime = SimpleNamespace(project_id="p1", client="codex", api="unused")
    monkeypatch.setattr(mcp_server, "_local_lifecycle_state", lambda *_: ("unknown", None))
    monkeypatch.setattr(mcp_server, "_raw_response", lambda *_args, **_kwargs: httpx.Response(
        200, request=httpx.Request("GET", "http://test/capture"), json={
            "session": {"id": "s1", "external_id": "native", "client": "codex"},
            "turn": {"id": "t1", "status": "open", "committed": False},
        },
    ))
    result = mcp_server._capture_lifecycle(runtime, "s1", "t1")
    assert result["completion"] == "not_yet_completed"
    assert result["prompt_observed"] is None
    assert result["context_emitted"] is None
    assert mcp_server._lifecycle_from_state({}, "current")["session_start_observed"] is None


def test_offline_lifecycle_preserves_exact_turn_privacy():
    state = {"session_off_record": False, "lifecycle_evidence": {"version": 1, "stop": {
        "turn_id": "t1", "off_record": True, "response_available": True, "queued_at": "now",
    }}}
    assert mcp_server._lifecycle_from_state(state, "t1")["completion"] == "off_record"
    assert mcp_server._lifecycle_from_state(state, "other")["completion"] == "unknown"
    state = {"turn_id": "t2", "turn_off_record": False, "session_off_record": True}
    assert mcp_server._lifecycle_from_state(state, "t2")["completion"] == "not_yet_completed"


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_capture_unsupported_or_denied_endpoint_does_not_use_local_evidence(monkeypatch, status):
    runtime = SimpleNamespace(project_id="p1", client="codex", api=object())
    def response(*args, **kwargs):
        return httpx.Response(status, request=httpx.Request("GET", "https://memory.example/capture-status"))
    monkeypatch.setattr(mcp_server, "_raw_response", response)
    monkeypatch.setattr(mcp_server, "_local_lifecycle_state", lambda *args: pytest.fail("server denied identity"))
    assert mcp_server._capture_lifecycle(runtime, "s1", "t1") == mcp_server._empty_lifecycle()


def test_capture_offline_uses_only_exact_local_identity(monkeypatch):
    runtime = SimpleNamespace(project_id="p1", client="codex", api=object())
    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")
    calls = []
    monkeypatch.setattr(mcp_server, "_raw_response", offline)
    monkeypatch.setattr(mcp_server, "_local_lifecycle_state", lambda *args: calls.append(args[1:]) or ("ambiguous", None))
    assert mcp_server._capture_lifecycle(runtime, "s1", "t1")["correlation"] == "ambiguous"
    assert calls == [("s1",)]


def test_connection_diagnostic_keeps_integration_separate_from_sleep(monkeypatch, tmp_path):
    runtime = mcp_server.MCPToolRuntime("codex", tmp_path, "p1", object(), None, None, None)
    native = {"ready": True, "registered": True, "requires_choice": False, "hooks": {"ready": True}}
    monkeypatch.setattr(mcp_server._MEMORY_CONNECTION_CHECKS, "check", lambda *args, **kwargs: native)
    monkeypatch.setattr(mcp_server, "_read_connection_health", lambda *args: {
        "state": "limited", "provider": "codex", "error_kind": "rate_limited",
    })
    value = mcp_server._connection_notice("check_memory_connection", runtime, None)
    assert value["ready"] is False
    assert value["integration"] == {"registered": True, "hooks": {"ready": True}}
    assert value["sleep"] == {"state": "limited", "provider": "codex"}
    assert value["lifecycle"] == mcp_server._empty_lifecycle()


def test_setup_rejected_login_offers_reconnect_without_opening_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(mcp_server, "start_local_setup", lambda root, **kwargs: opened.append(root) or {"opened": True})
    monkeypatch.setattr(mcp_server, "read_setup_status", lambda root: {
        "docker": {"ready": True}, "embeddings": {"ready": True},
        "project": {"ready": True}, "codex_hooks": {"ready": True},
        "clients": {"codex": {"ready": False, "reason": "auth_required"}},
        "memory_status": {"state": "connection_required", "provider": "codex"},
    })
    checked = mcp_server.check_setup(tmp_path, "codex")
    assert checked["ready"] is False and checked["requires_choice"] is True
    assert checked["next_action"]["id"] == "codex_login"
    assert checked["next_action"]["title"] == "Reconnect Codex"
    assert mcp_server.CONFIGURATION_CHOICE_INSTRUCTION in checked["response_instruction"]
    assert opened == []


def test_claude_chat_repairs_project_sleep_executor_not_its_interactive_client():
    state = {
        "docker": {"ready": True}, "embeddings": {"ready": True}, "project": {"ready": True},
        "clients": {"claude": {"ready": False}, "codex": {"ready": False, "reason": "auth_required"}},
        "memory_status": {"state": "connection_required", "provider": "codex", "executor_provider": "codex"},
    }
    checked = mcp_server._setup_check(state, "claude")
    assert checked["next_action"]["id"] == "codex_login"
    assert not any(item["id"] == "claude_login" for item in checked["remaining_actions"])
    state["clients"]["codex"] = {"ready": True}
    state["memory_status"] = {"state": "updated", "executor_provider": "codex"}
    assert mcp_server._setup_check(state, "claude")["ready"] is True


def test_activate_plan_context_loads_any_existing_plan(monkeypatch):
    paths = []

    def get_plan(_api, _method, path):
        paths.append(path)
        if path.endswith("/missing"):
            raise ValueError("plan not found")
        return {"plan": {"id": "p1", "title": "Launch approach", "status": "draft"}}

    monkeypatch.setattr(
        mcp_server,
        "request",
        get_plan,
    )
    plans_url = "http://127.0.0.1:20417/?project=project&tab=tasks&view=plans"
    selected = mcp_server.call(
        "activate_plan_context",
        {"plan_id": "p1"},
        "project",
        "http://api",
        plans_dashboard_url=plans_url,
    )
    assert selected["plan"]["title"] == "Launch approach"
    assert selected["plan"]["url"].endswith("&plan=p1")
    assert selected["activated"] is True
    assert paths == ["/projects/project/plans/p1"]
    with pytest.raises(ValueError, match="plan not found"):
        mcp_server.call("activate_plan_context", {"plan_id": "missing"}, "project", "http://api")


@pytest.mark.parametrize("name", ["create_task", "update_task"])
def test_task_mutations_return_project_dashboard_handoff(monkeypatch, name):
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: {
            "id": "t1",
            "title": "Ship Android",
            "status": "in_progress",
            "priority": "high",
        },
    )
    arguments = {"title": "Ship Android"} if name == "create_task" else {"task_id": "t1"}
    response = mcp_server.call(
        name,
        arguments,
        "p1",
        "http://api",
        dashboard_url="http://127.0.0.1:20417/?project=p1&tab=tasks",
    )
    assert response["task"]["id"] == "t1"
    assert response["task"]["url"].endswith("&work=t1")
    assert response["item_url"].endswith("&work=t1")
    assert response["dashboard_url"] == ("http://127.0.0.1:20417/?project=p1&tab=tasks")
    assert "human title" in response["response_instruction"]
    assert "Never expose an internal ID" in response["response_instruction"]


def test_task_mutation_ignores_a_non_work_dashboard_for_the_exact_item_link():
    dashboard_url = "http://127.0.0.1:20417/?project=p1&tab=memory"

    response = mcp_server.task_mutation_result(
        {"id": "t1", "title": "Ship Android", "status": "in_progress"},
        dashboard_url,
    )

    assert response["task"] == {
        "id": "t1",
        "title": "Ship Android",
        "status": "in_progress",
    }
    assert "item_url" not in response
    assert response["dashboard_url"] == dashboard_url


@pytest.mark.parametrize("name", ["create_plan", "update_plan"])
def test_plan_mutations_return_plans_dashboard_handoff(monkeypatch, name):
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: {
            "id": "p1",
            "title": "Launch approach",
            "status": "decided",
            "work_item_ids": ["t1"],
        },
    )
    arguments = {"title": "Launch approach"} if name == "create_plan" else {"plan_id": "p1"}
    response = mcp_server.call(
        name,
        arguments,
        "project",
        "http://api",
        plans_dashboard_url="http://127.0.0.1:20417/?project=project&tab=tasks&view=plans",
    )
    assert response["plan"]["id"] == "p1"
    assert response["plan"]["url"].endswith("&plan=p1")
    assert response["item_url"].endswith("&plan=p1")
    assert response["dashboard_url"].endswith("tab=tasks&view=plans")
    assert "human title" in response["response_instruction"]
    assert "Never expose an internal ID" in response["response_instruction"]


@pytest.mark.parametrize("name", ["get_project_briefing", "list_tasks"])
def test_task_read_tools_include_project_dashboard_url(monkeypatch, name):
    monkeypatch.setattr(mcp_server, "request", lambda *args, **kwargs: {"items": []})
    response = mcp_server.call(
        name,
        {},
        "p1",
        "http://api",
        dashboard_url="http://127.0.0.1:20417/?project=p1&tab=tasks",
    )
    assert response["dashboard_url"] == ("http://127.0.0.1:20417/?project=p1&tab=tasks")


def test_plan_reads_include_the_exact_plans_dashboard_url(monkeypatch):
    monkeypatch.setattr(mcp_server, "request", lambda *args, **kwargs: {"items": []})
    response = mcp_server.call(
        "list_plans",
        {},
        "p1",
        "http://api",
        plans_dashboard_url="http://127.0.0.1:20417/?project=p1&tab=tasks&view=plans",
    )
    assert response["dashboard_url"].endswith("tab=tasks&view=plans")


def test_artifact_tool_reads_only_safe_project_files(monkeypatch, tmp_path):
    note = tmp_path / "notes.md"
    note.write_text("Release on Friday")
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((path, body)) or {"ok": True},
    )
    assert mcp_server.call(
        "register_artifact",
        {"path": "notes.md", "turn_id": "t1"},
        "p1",
        "http://api",
        tmp_path,
    ) == {"ok": True}
    body = calls[0][1]
    assert body["filename"] == "notes.md"
    assert body["extracted_text"] == "Release on Friday"
    assert body["content_base64"]
    mcp_server.call(
        "register_artifact",
        {"path": "notes.md", "task_id": "task-1"},
        "p1",
        "http://api",
        tmp_path,
    )
    assert calls[1][0] == "/projects/p1/tasks/task-1/attachments"
    assert "task_id" not in calls[1][1]

    mcp_server.call(
        "register_artifact",
        {"path": "notes.md", "plan_id": "plan-1"},
        "p1",
        "http://api",
        tmp_path,
    )
    assert calls[2][0] == "/projects/p1/plans/plan-1/attachments"
    assert "plan_id" not in calls[2][1]

    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret")
    with pytest.raises(ValueError, match="credential"):
        mcp_server.call("register_artifact", {"path": ".env"}, "p1", "http://api", tmp_path)
    with pytest.raises(ValueError, match="inside the project"):
        mcp_server.call(
            "register_artifact",
            {"path": str(tmp_path.parent / "outside.txt")},
            "p1",
            "http://api",
            tmp_path,
        )
    with pytest.raises(ValueError, match="either task_id or plan_id"):
        mcp_server.call(
            "register_artifact",
            {"path": "notes.md", "task_id": "task-1", "plan_id": "plan-1"},
            "p1",
            "http://api",
            tmp_path,
        )


def test_artifact_tool_covers_inline_binary_and_size_boundaries(monkeypatch, tmp_path):
    inline = mcp_server._artifact_body(
        {
            "filename": "brief.md",
            "mime_type": "text/markdown",
            "summary": "Project brief",
            "extracted_text": "Already supplied",
        },
        tmp_path,
    )
    assert inline["filename"] == "brief.md"

    binary = tmp_path / "image.bin"
    binary.write_bytes(b"\x00\x01")
    extracted = mcp_server._artifact_body({"path": "image.bin"}, tmp_path)
    assert extracted["extracted_text"] == ""

    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="larger than 10 MiB"):
        mcp_server._artifact_body({"path": "oversized.bin"}, tmp_path)

    with pytest.raises(ValueError, match="requires session_id"):
        mcp_server.call("set_off_record", {"off_record": True}, "p1", "http://api")
    with pytest.raises(ValueError, match="project root"):
        mcp_server.call("register_artifact", {}, "p1", "http://api")


def test_request_raises_and_preserves_http_contract(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            calls.append("raised")

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(
        mcp_server.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(),
    )
    assert mcp_server.request("http://api", "POST", "/work", {"title": "Ship"}) == {"ok": True}
    assert calls[0][0] == ("POST", "http://api/work")
    assert calls[0][1]["json"] == {"title": "Ship"}
    assert calls[1] == "raised"


def test_request_supports_generic_http_methods_and_authenticated_binding_transport(
    monkeypatch, tmp_path
):
    generic_calls = []
    response = httpx.Response(200, json={"ok": True}, request=httpx.Request("TRACE", "http://api"))
    monkeypatch.setattr(
        mcp_server.httpx,
        "request",
        lambda *args, **kwargs: generic_calls.append((args, kwargs)) or response,
    )
    assert mcp_server._raw_response("http://api", "TRACE", "/debug", timeout=4) is response
    assert generic_calls == [(('TRACE', 'http://api/debug'), {"timeout": 4})]

    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="a" * 64,
        bearer_token="project-token",
    )
    transport_calls = []

    def transport(method, url, **kwargs):
        transport_calls.append((method, url, kwargs))
        return httpx.Response(200, json={"transport": "project"}, request=httpx.Request(method, url))

    api = mcp_server.ApiEndpoint(
        binding.api_url,
        mcp_server.ProjectHttpClient(binding, component="mcp", request_function=transport),
        binding.binding_id,
    )
    assert mcp_server.request(api, "POST", "/work", {"title": "Ship"}) == {
        "transport": "project"
    }
    assert transport_calls[0][0:2] == ("POST", "https://203.0.113.10/api/work")
    assert transport_calls[0][2]["json"] == {"title": "Ship"}


@pytest.mark.asyncio
async def test_official_mcp_sdk_negotiates_lists_tools_and_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda _: (_ for _ in ()).throw(FileNotFoundError()),
    )

    async def supported(session, initialized):
        tools = await session.list_tools()
        response = await session.call_tool(
            "health", {mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
        )
        return initialized, tools, response

    initialized, tools, response = await _sdk_session("codex-mcp-client", supported)
    assert initialized.protocol_version
    assert initialized.server_info.version == mcp_server.__version__
    assert len(tools.tools) == len(mcp_server.TOOL_MODELS)
    assert response.structured_content["status"] == "unconfigured"
    assert response.is_error is False

    async def unsupported(session, _initialized):
        return await session.list_tools()

    hidden = await _sdk_session("cursor", unsupported)
    assert hidden.tools == []

    claude_tools = await _sdk_session("claude-ai", unsupported)
    assert len(claude_tools.tools) == len(mcp_server.TOOL_MODELS)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_name", ["Codex Desktop", "Claude Code"])
async def test_sleep_contract_exposes_scope_effects_and_conservative_hints(client_name, monkeypatch):
    monkeypatch.delenv(mcp_server.MCP_CLIENT_ENV, raising=False)

    async def inspect(session, _initialized):
        return await session.list_tools()

    listed = await _sdk_session(client_name, inspect)
    tools = {tool.name: tool for tool in listed.tools}
    for name in ("request_sleep", "retry_sleep_job"):
        tool = tools[name]
        assert tool.annotations.model_dump(by_alias=True, exclude_none=True) == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
        assert "off-record" in tool.description
        assert "Codex/Claude" in tool.description
        assert "configured embeddings (OpenAI by default)" in tool.description
        assert "Processing may consume subscription usage and API credits where applicable" in tool.description
        assert "new chat transcript" in tool.description
        assert "client approval" in tool.description
    assert "all project sessions" in tools["request_sleep"].description
    assert "control fields only" in tools["request_sleep"].description
    session_field = tools["request_sleep"].input_schema["properties"]["session_id"]
    assert "all project sessions" in session_field["description"]
    assert "never invent" in session_field["description"]
    assert "Existing job ID" in tools["retry_sleep_job"].input_schema["properties"]["job_id"]["description"]
    assert tools["get_memory_status"].annotations is None


@pytest.mark.asyncio
@pytest.mark.parametrize("client_name", ["Codex Desktop", "Claude Code"])
@pytest.mark.parametrize(
    ("name", "arguments", "path", "body"),
    [
        ("request_sleep", {}, "/projects/p1/sleep", {}),
        (
            "request_sleep", {"session_id": "s1", "trigger": "topic_boundary"},
            "/projects/p1/sleep", {"session_id": "s1", "trigger": "topic_boundary"},
        ),
        (
            "request_sleep", {"session_id": None, "trigger": "manual"},
            "/projects/p1/sleep", {"session_id": None, "trigger": "manual"},
        ),
        (
            "request_sleep",
            {"session_id": "s1", "trigger": "session_start", "provider": "claude", "resume_auth": True},
            "/projects/p1/sleep",
            {"session_id": "s1", "trigger": "session_start", "provider": "claude", "resume_auth": True},
        ),
        ("retry_sleep_job", {"job_id": "j1"}, "/projects/p1/sleep-jobs/j1/retry", None),
    ],
)
async def test_sleep_metadata_keeps_real_mcp_dispatch_unchanged(
    client_name, name, arguments, path, body, monkeypatch, tmp_path,
):
    monkeypatch.delenv(mcp_server.MCP_CLIENT_ENV, raising=False)
    monkeypatch.setattr(mcp_server, "_resolve_tool_runtime", lambda client, _arguments: mcp_server.MCPToolRuntime(
        client=client, project_root=tmp_path, project_id="p1", api=mcp_server.ApiEndpoint("https://memory.example"),
        dashboard_url=None, plans_dashboard_url=None, binding=None,
    ))
    calls = []
    monkeypatch.setattr(
        mcp_server, "request",
        lambda api, method, route, payload=None: calls.append((api, method, route, payload)) or {"scheduled": 1},
    )
    # Result observability remains a separate, existing operation. Do not
    # misrepresent the scheduling body's scope as a ban on all other traffic.
    observations = []
    monkeypatch.setattr(
        mcp_server, "flush_observability_queue",
        lambda *args, **_kwargs: observations.append(args),
    )
    provided = {**arguments, mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
    original = dict(provided)

    async def invoke(session, _initialized):
        return await session.call_tool(name, provided)

    response = await _sdk_session(client_name, invoke)
    assert response.is_error is False
    assert response.structured_content == {"scheduled": 1}
    assert calls == [(mcp_server.ApiEndpoint("https://memory.example"), "POST", path, body)]
    assert len(observations) == 1
    assert provided == original


@pytest.mark.asyncio
async def test_codex_and_claude_receive_the_same_human_work_contract(monkeypatch, tmp_path):
    monkeypatch.delenv(mcp_server.MCP_CLIENT_ENV, raising=False)
    monkeypatch.setattr(
        mcp_server,
        "_resolve_tool_runtime",
        lambda client, _arguments: mcp_server.MCPToolRuntime(
            client=client,
            project_root=tmp_path,
            project_id="p1",
            api=mcp_server.ApiEndpoint("http://api"),
            dashboard_url="http://127.0.0.1:20003/?project=p1&tab=tasks",
            plans_dashboard_url=(
                "http://127.0.0.1:20003/?project=p1&tab=tasks&view=plans"
            ),
            binding=None,
        ),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *_args, **_kwargs: {
            "items": [{"id": "task-one", "title": "Human task", "version": 1}]
        },
    )
    monkeypatch.setattr(mcp_server, "flush_observability_queue", lambda *_args, **_kwargs: None)

    async def read_work(session, _initialized):
        return await session.call_tool(
            "list_tasks",
            {mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)},
        )

    codex = await _sdk_session("Codex Desktop", read_work)
    claude = await _sdk_session("Claude Code", read_work)

    assert codex.structured_content == claude.structured_content
    assert codex.structured_content["items"][0]["url"].endswith("&work=task-one")
    assert "human title" in codex.structured_content["response_instruction"]
    assert "unless the user asks" in codex.structured_content["response_instruction"]


@pytest.mark.asyncio
async def test_native_adapter_identity_must_match_the_real_mcp_client(monkeypatch):
    monkeypatch.setenv(mcp_server.MCP_CLIENT_ENV, "claude")

    async def inspect(session, _initialized):
        listed = await session.list_tools()
        called = await session.call_tool("health", {})
        return listed, called

    listed, called = await _sdk_session("codex-mcp-client", inspect)
    assert listed.tools == []
    assert called.is_error is True
    assert "does not match" in called.content[0].text


@pytest.mark.asyncio
async def test_mcp_preserves_explicit_null_backlog_placement(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_resolve_tool_runtime", lambda client, _arguments: mcp_server.MCPToolRuntime(
        client=client, project_root=tmp_path, project_id="p1", api=mcp_server.ApiEndpoint("http://api"),
        dashboard_url=None, plans_dashboard_url=None, binding=None,
    ))
    calls = []
    monkeypatch.setattr(mcp_server, "request", lambda _api, method, path, body: calls.append(body) or {
        "id": "t1", "title": "Backlog", "status": "todo", "version": 2, "sprint_id": None,
    })
    monkeypatch.setattr(mcp_server, "flush_observability_queue", lambda *_args, **_kwargs: None)

    async def move_to_backlog(session, _initialized):
        return await session.call_tool("update_task", {
            "task_id": "t1", "sprint_id": None, "expected_version": 1,
            mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path),
        })

    result = await _sdk_session("Codex Desktop", move_to_backlog)
    assert result.is_error is False
    assert calls == [{"sprint_id": None, "expected_version": 1}]


@pytest.mark.asyncio
async def test_mcp_routes_each_call_to_its_explicit_root_without_sticky_state(
    monkeypatch, tmp_path
):
    roots = [tmp_path / name for name in ("a", "b")]
    for root in roots:
        root.mkdir()
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda root: {
            "id": Path(root).name,
            "api_port": 18000 if Path(root).name == "a" else 18001,
            "web_port": 20000 if Path(root).name == "a" else 20001,
        },
    )
    monkeypatch.setattr(mcp_server, "validate_project_registration", lambda *_args: {})
    monkeypatch.setattr(
        mcp_server,
        "call",
        lambda name, args, project_id, api, project_root, *_rest: calls.append(
            (name, project_id, Path(project_root), str(api))
        )
        or {"project_id": project_id},
    )

    async def sequence(session, _initialized):
        results = []
        for root in (roots[0], roots[1], roots[0]):
            results.append(
                await session.call_tool(
                    "health", {mcp_server.MCP_WORKSPACE_ARGUMENT: str(root)}
                )
            )
        return results

    results = await _sdk_session("Claude Code", sequence)
    assert [result.structured_content["project_id"] for result in results] == ["a", "b", "a"]
    assert [(project_id, root.name) for _, project_id, root, _ in calls] == [
        ("a", "a"),
        ("b", "b"),
        ("a", "a"),
    ]


def test_get_project_manual_is_read_only_and_returns_the_complete_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((api, method, path, body))
        or {"manual": {"version": 4, "content": "complete manual"}},
    )
    assert mcp_server.call("get_project_manual", {}, "p1", "http://api") == {
        "manual": {"version": 4, "content": "complete manual"}
    }
    assert calls == [("http://api", "GET", "/projects/p1/team/manual", None)]


def test_update_project_manual_uses_versioned_deterministic_idempotency_and_human_handoff(
    monkeypatch, tmp_path
):
    calls = []
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Solo",
        root_path=tmp_path,
        kind="local",
        api_url="http://127.0.0.1:18003",
        dashboard_url="http://127.0.0.1:20003",
        binding_id="a" * 64,
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((method, path, body))
        or {"manual": {"version": 3, "content": body["content"]}},
    )
    arguments = {"content": "# Rules\n\n- Test before release.", "expected_version": 2}
    first = mcp_server.call(
        "update_project_manual", arguments, "p1", "http://api", binding=binding
    )
    second = mcp_server.call(
        "update_project_manual", arguments, "p1", "http://api", binding=binding
    )

    assert first["manual"]["version"] == 3
    assert first["dashboard_url"].endswith("project=p1&tab=team")
    assert "durable project rules changed" in first["response_instruction"]
    assert calls[0][0:2] == ("PATCH", "/projects/p1/team/manual")
    assert calls[0][2]["expected_version"] == 2
    assert calls[0][2]["idempotency_key"] == calls[1][2]["idempotency_key"]
    assert calls[0][2]["idempotency_key"].startswith("mcp-manual-v2-")
    assert arguments == {"content": "# Rules\n\n- Test before release.", "expected_version": 2}
    assert second["manual"] == first["manual"]


def test_manual_replay_never_claims_that_superseded_text_was_published(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Solo",
        root_path=tmp_path,
        kind="local",
        api_url="http://127.0.0.1:18003",
        dashboard_url="http://127.0.0.1:20003",
        binding_id="b" * 64,
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *_args, **_kwargs: {
            "manual": {"version": 4, "content": "# Current\n\nA later procedure."},
            "idempotent": True,
        },
    )

    response = mcp_server.call(
        "update_project_manual",
        {"content": "# Old request", "expected_version": 2},
        "p1",
        "http://api",
        binding=binding,
    )

    assert response["manual"]["version"] == 4
    assert "No manual revision was created" in response["response_instruction"]
    assert "do not claim that the requested text was published" in response[
        "response_instruction"
    ]
    assert "superseded" in response["response_instruction"]


def test_remote_manual_update_succeeds_when_verified_cache_write_fails(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="e" * 64,
        bearer_token="project-token",
    )
    calls = []
    cache_attempts = []
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda api, method, path, body=None: calls.append((api, method, path, body))
        or {"manual": {"version": 3, "content": body["content"]}},
    )

    def fail_cache_write(selected, manual):
        cache_attempts.append((selected, manual))
        raise OSError("cache read-only")

    monkeypatch.setattr(mcp_server, "store_verified_manual", fail_cache_write)

    response = mcp_server.call(
        "update_project_manual",
        {"content": "# Rules\n\n- Test before release.", "expected_version": 2},
        "p1",
        "https://203.0.113.10/api",
        binding=binding,
    )

    assert response["manual"] == {
        "version": 3,
        "content": "# Rules\n\n- Test before release.",
    }
    assert cache_attempts == [(binding, response["manual"])]
    assert calls[0][0:3] == (
        "https://203.0.113.10/api",
        "PATCH",
        "/projects/p1/team/manual",
    )
    assert calls[0][3]["expected_version"] == 2


def test_remote_mcp_caches_live_manual_and_uses_it_only_for_outages(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="e" * 64,
        bearer_token="project-token",
    )
    stored = []
    monkeypatch.setattr(
        mcp_server,
        "store_verified_manual",
        lambda selected, manual: stored.append((selected, manual)),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: {"manual": {"version": 2, "content": "live"}},
    )
    live = mcp_server.call(
        "get_project_manual", {}, "p1", "https://api", binding=binding
    )
    assert live["manual"]["content"] == "live"
    assert stored == [(binding, {"version": 2, "content": "live"})]

    request = httpx.Request("GET", "https://api/projects/p1/team/manual")
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.ConnectError("offline", request=request)
        ),
    )
    monkeypatch.setattr(
        mcp_server,
        "load_verified_manual",
        lambda selected: {"version": 2, "content": "cached"},
    )
    offline = mcp_server.call(
        "get_project_manual", {}, "p1", "https://api", binding=binding
    )
    assert offline["manual"]["content"] == "cached"
    assert offline["source"] == "last_verified_cache"
    assert offline["offline"] is True


def test_remote_mcp_does_not_hide_authorization_or_upgrade_failures(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="f" * 64,
        bearer_token="revoked-token",
    )
    request = httpx.Request("GET", "https://api/projects/p1/team/manual")
    response = httpx.Response(401, request=request)
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        mcp_server,
        "load_verified_manual",
        lambda selected: {"version": 2, "content": "must not bypass auth"},
    )
    with pytest.raises(httpx.HTTPStatusError):
        mcp_server.call("get_project_manual", {}, "p1", "https://api", binding=binding)


def test_remote_mcp_uses_verified_manual_for_a_server_outage(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="f" * 64,
        bearer_token="project-token",
    )
    request = httpx.Request("GET", "https://api/projects/p1/team/manual")
    error = httpx.HTTPStatusError(
        "unavailable",
        request=request,
        response=httpx.Response(503, request=request),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        mcp_server,
        "load_verified_manual",
        lambda selected: {"version": 3, "content": "last verified"},
    )

    cached = mcp_server.call(
        "get_project_manual", {}, "p1", "https://api", binding=binding
    )
    assert cached == {
        "manual": {"version": 3, "content": "last verified"},
        "offline": True,
        "source": "last_verified_cache",
        "response_instruction": cached["response_instruction"],
    }
    assert "do not start local dDuo Docker" in cached["response_instruction"]


def test_remote_manual_cache_failures_are_non_fatal_but_never_invent_a_fallback(
    monkeypatch, tmp_path
):
    binding = mcp_server.ProjectBinding(
        project_id="p1",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="e" * 64,
        bearer_token="project-token",
    )
    monkeypatch.setattr(
        mcp_server,
        "store_verified_manual",
        lambda *_args: (_ for _ in ()).throw(OSError("cache read-only")),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: {
            "operational_manual": {"version": 1, "content": "briefing manual"}
        },
    )
    briefing = mcp_server.call(
        "get_project_briefing", {}, "p1", "https://api", binding=binding
    )
    assert briefing["operational_manual"]["content"] == "briefing manual"

    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: {"manual": {"version": 2, "content": "live manual"}},
    )
    live = mcp_server.call("get_project_manual", {}, "p1", "https://api", binding=binding)
    assert live["manual"]["content"] == "live manual"

    request = httpx.Request("GET", "https://api/projects/p1/team/manual")
    server_error = httpx.HTTPStatusError(
        "unavailable",
        request=request,
        response=httpx.Response(503, request=request),
    )
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(server_error),
    )
    monkeypatch.setattr(mcp_server, "load_verified_manual", lambda _binding: None)
    with pytest.raises(httpx.HTTPStatusError):
        mcp_server.call("get_project_manual", {}, "p1", "https://api", binding=binding)

    network_error = httpx.ConnectError("offline", request=request)
    monkeypatch.setattr(
        mcp_server,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(network_error),
    )
    with pytest.raises(httpx.ConnectError):
        mcp_server.call("get_project_manual", {}, "p1", "https://api", binding=binding)


def test_mcp_remote_binding_ignores_arbitrary_api_environment_override(monkeypatch, tmp_path):
    binding = mcp_server.ProjectBinding(
        project_id="remote",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://198.51.100.8/api",
        dashboard_url=None,
        binding_id="b" * 64,
        bearer_token="secret",
    )
    monkeypatch.setattr(
        mcp_server,
        "load_project",
        lambda _: {"id": "remote", "binding": "remote"},
    )
    monkeypatch.setattr(
        mcp_server, "binding_from_project", lambda *args, **_kwargs: binding
    )
    monkeypatch.setenv("DDUO_SOLO_FOUNDER_API_URL", "http://attacker.invalid")
    arguments = {mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
    runtime = mcp_server._resolve_tool_runtime("codex", arguments)
    assert str(runtime.api) == "https://198.51.100.8/api"
    assert runtime.api.client.binding.bearer_token == "secret"
    assert arguments == {}


def test_mcp_broken_binding_stays_unconfigured_instead_of_using_local_project_memory(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda _path: tmp_path)
    monkeypatch.setattr(mcp_server, "load_project", lambda _root: {"id": "remote"})
    monkeypatch.setattr(
        mcp_server,
        "binding_from_project",
        lambda *args: (_ for _ in ()).throw(ValueError("credential unavailable")),
    )
    monkeypatch.setattr(mcp_server, "validate_project_registration", lambda *_args: {})
    arguments = {mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
    runtime = mcp_server._resolve_tool_runtime("codex", arguments)
    assert runtime.project_id is None
    assert str(runtime.api) == ""
    assert runtime.api.client is None
    assert runtime.binding is None


def test_safe_mcp_errors_never_expose_unexpected_exception_details():
    request = httpx.Request("GET", "https://memory.example.test/health")
    status_error = httpx.HTTPStatusError(
        "private provider body",
        request=request,
        response=httpx.Response(502, request=request),
    )
    assert mcp_server._safe_tool_error(mcp_server.MCPContextError("bad root")) == "bad root"
    assert mcp_server._safe_tool_error(ValueError("bad input")) == "bad input"
    assert mcp_server._safe_tool_error(status_error) == "dDuo memory returned HTTP 502"
    assert "unavailable" in mcp_server._safe_tool_error(
        httpx.ConnectError("private endpoint", request=request)
    )
    assert "private" not in mcp_server._safe_tool_error(RuntimeError("private failure"))


@pytest.mark.asyncio
async def test_mcp_call_handler_fails_closed_for_unknown_invalid_and_runtime_errors(
    monkeypatch, tmp_path
):
    context = SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(
                client_info=mcp_types.Implementation(name="codex", version="test")
            )
        )
    )

    unknown = await mcp_server._call_mcp_tool(
        context,
        mcp_types.CallToolRequestParams(name="not-a-tool", arguments={}),
    )
    assert unknown.is_error is True
    assert "unknown tool" in unknown.content[0].text

    invalid = await mcp_server._call_mcp_tool(
        context,
        mcp_types.CallToolRequestParams(name="health", arguments={}),
    )
    assert invalid.is_error is True
    assert "workspace_root is required" in invalid.content[0].text

    runtime = mcp_server.MCPToolRuntime(
        client="codex",
        project_root=tmp_path,
        project_id=None,
        api=mcp_server.ApiEndpoint(""),
        dashboard_url=None,
        plans_dashboard_url=None,
        binding=None,
    )
    monkeypatch.setattr(mcp_server, "_resolve_tool_runtime", lambda *_args: runtime)
    request = httpx.Request("GET", "https://memory.example.test")
    monkeypatch.setattr(
        mcp_server,
        "_invoke_mcp_tool",
        lambda *_args: (_ for _ in ()).throw(httpx.ConnectError("private", request=request)),
    )
    failed = await mcp_server._call_mcp_tool(
        context,
        mcp_types.CallToolRequestParams(
            name="health", arguments={mcp_server.MCP_WORKSPACE_ARGUMENT: str(tmp_path)}
        ),
    )
    assert failed.is_error is True
    assert "currently unavailable" in failed.content[0].text
    assert "private" not in failed.content[0].text


@pytest.mark.asyncio
async def test_stdio_runner_uses_the_sdk_transport(monkeypatch):
    events = []

    class FakeServer:
        def create_initialization_options(self, notification_options):
            events.append(("options", notification_options.tools_changed))
            return "initialization"

        async def run(self, read_stream, write_stream, initialization):
            events.append(("run", read_stream, write_stream, initialization))

    @asynccontextmanager
    async def fake_stdio_server():
        yield "read", "write"

    monkeypatch.setattr(mcp_server, "stdio_server", fake_stdio_server)
    await mcp_server._serve_stdio(FakeServer())
    assert events == [
        ("options", False),
        ("run", "read", "write", "initialization"),
    ]


def test_mcp_main_delegates_to_anyio(monkeypatch):
    invoked = []
    monkeypatch.setattr(mcp_server.anyio, "run", lambda function: invoked.append(function))
    mcp_server.main()
    assert invoked == [mcp_server._serve_stdio]
