from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anyio
import mcp.types as mcp_types
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from dduo_solo_founder import mcp_server, memory_connection
from dduo_solo_founder.client_binding import ProjectBinding
from dduo_solo_founder.client_installation import ClientCommand, ClientInstallation, ClientInstallationError
from dduo_solo_founder.client_readiness import CodexHookStatus


def _status(reason="authorization_required", *, ready=False, trust_statuses=None):
    return CodexHookStatus(
        ready=ready,
        reason=reason,
        hook_count=3,
        trust_statuses=trust_statuses or (["trusted"] if ready else ["untrusted"]),
        detail="private native diagnostic that must not enter an MCP notice",
        events=["sessionStart", "stop", "userPromptSubmit"],
    )


@pytest.fixture(autouse=True)
def isolate_native_checks(monkeypatch, tmp_path):
    def unexpected_probe(*_args, **_kwargs):
        pytest.fail("test attempted an unmocked native hook check")

    monkeypatch.setattr(memory_connection, "codex_hook_status", unexpected_probe)
    monkeypatch.setattr(
        memory_connection, "resolve_client_installation",
        lambda _client: ClientInstallation(
            "codex", "unknown", tmp_path / "config", ClientCommand(tmp_path / "codex"),
        ),
    )
    monkeypatch.setattr(
        mcp_server, "_MEMORY_CONNECTION_CHECKS", memory_connection.MemoryConnectionChecks()
    )
    monkeypatch.delenv(mcp_server.MCP_CLIENT_ENV, raising=False)
    monkeypatch.delenv(mcp_server.MCP_PROJECT_ROOT_ENV, raising=False)
    monkeypatch.setattr(mcp_server, "_read_connection_health", lambda _runtime: {"state": "updated"})


async def _sdk_session(client_name, operation):
    server = mcp_server.create_mcp_server()
    initialization = server.create_initialization_options()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(
                server.run, server_streams[0], server_streams[1], initialization
            )
            async with ClientSession(
                client_streams[0],
                client_streams[1],
                client_info=mcp_types.Implementation(name=client_name, version="test"),
            ) as session:
                await session.initialize()
                value = await operation(session)
            task_group.cancel_scope.cancel()
    return value


@pytest.fixture
def sdk_runtime(monkeypatch, tmp_path):
    state = SimpleNamespace(project_id="project-one", binding_id="binding-one", remote=False)
    calls = []
    monkeypatch.setattr(mcp_server, "find_workspace_root", lambda root: Path(root))

    def resolve(client, arguments):
        root = mcp_server._request_project_root(arguments)
        binding = (
            ProjectBinding(
                project_id=state.project_id,
                name="Test project",
                root_path=root,
                kind="remote" if state.remote else "local",
                api_url="https://memory.example.test/api",
                dashboard_url=None,
                binding_id=state.binding_id,
            )
            if state.project_id
            else None
        )
        return mcp_server.MCPToolRuntime(
            client=client,
            project_root=root,
            project_id=state.project_id,
            api=mcp_server.ApiEndpoint("https://memory.example.test/api"),
            dashboard_url=None,
            plans_dashboard_url=None,
            binding=binding,
        )

    def invoke(name, arguments, runtime):
        calls.append((name, dict(arguments), runtime))
        value = {"executed": name, "arguments": arguments}
        return value, {"content": [{"type": "text", "text": json.dumps(value)}]}

    monkeypatch.setattr(mcp_server, "_resolve_tool_runtime", resolve)
    monkeypatch.setattr(mcp_server, "_invoke_mcp_tool", invoke)
    return SimpleNamespace(state=state, calls=calls, root=tmp_path)


def _payload(response):
    assert response.is_error is False
    assert json.loads(response.content[0].text) == response.structured_content
    return response.structured_content


@pytest.mark.parametrize(
    "reason, ready, consent",
    [
        ("authorization_required", False, True),
        ("reauthorization_required", False, True),
        ("hooks_missing", False, False),
        ("hooks_incomplete", False, False),
        ("hooks_disabled", False, False),
        ("check_failed", False, False),
        ("authorized", True, False),
    ],
)
def test_native_status_notices_distinguish_consent_from_unverified_capture(
    monkeypatch, tmp_path, reason, ready, consent
):
    monkeypatch.setattr(
        memory_connection, "codex_hook_status", lambda _root, **_kwargs: _status(reason, ready=ready)
    )
    checked = memory_connection.MemoryConnectionChecks().check("codex", tmp_path, "binding")

    assert checked["ready"] is ready
    assert checked["requires_choice"] is (not ready)
    assert checked["reason"] == reason
    assert checked["scope"] == "local_hook_authorization"
    assert checked["registered"] is (None if reason == "check_failed" else True)
    assert checked["hooks"] == {
        "ready": ready, "reason": reason, "hook_count": 3,
        "events": ["sessionStart", "stop", "userPromptSubmit"],
    }
    assert "private native diagnostic" not in json.dumps(checked)
    if ready:
        assert "warning_id" not in checked
        assert "not proof of turn capture" in checked["response_instruction"]
    else:
        assert len(checked["warning_id"]) == 32
        assert "Codex client" in checked["next_action"]
        assert ("needs your authorization" in checked["message"]) is consent
        assert "THIS chat" in checked["response_instruction"]
        assert "explicit consent" in checked["response_instruction"]


def test_native_cache_changes_with_managed_config_and_keeps_vscode_guidance_neutral(monkeypatch, tmp_path):
    selected = [ClientInstallation(
        "codex", "vscode", tmp_path / "a", ClientCommand(tmp_path / "codex"),
    )]
    probes = []
    monkeypatch.setattr(memory_connection, "resolve_client_installation", lambda _client: selected[0])
    monkeypatch.setattr(
        memory_connection, "codex_hook_status",
        lambda _root, *, installation: probes.append(installation) or _status(),
    )
    checks = memory_connection.MemoryConnectionChecks()
    first = checks.check("codex", tmp_path, "binding")
    assert "Reload the VS Code window" in first["next_action"]
    assert "Settings > Hooks" not in first["next_action"]
    assert checks.check("codex", tmp_path, "binding") == first
    selected[0] = replace(selected[0], config_dir=tmp_path / "b")
    second = checks.check("codex", tmp_path, "binding")
    assert second["warning_id"] != first["warning_id"]
    assert len(probes) == 2


def test_native_resolution_error_keeps_registration_unknown_without_legacy_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(
        memory_connection, "resolve_client_installation",
        lambda _client: (_ for _ in ()).throw(ClientInstallationError("private invalid record path")),
    )
    value = memory_connection.MemoryConnectionChecks().check("codex", tmp_path, "binding")
    assert value["registered"] is None
    assert value["hooks"]["reason"] == "check_failed"
    assert "private invalid" not in json.dumps(value)


def test_native_confirmed_missing_hooks_are_reported_separately_from_probe_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        memory_connection, "codex_hook_status",
        lambda _root, **_kwargs: CodexHookStatus(False, "hooks_missing", 0, []),
    )
    value = memory_connection.MemoryConnectionChecks().check("codex", tmp_path, "binding")
    assert value["registered"] is False
    assert value["hooks"]["ready"] is False


def test_ttl_and_explicit_refresh_probe_only_when_needed(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=10.0)
    probes = []
    monkeypatch.setattr(
        memory_connection, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )

    def probe(root, **_kwargs):
        probes.append(root)
        return _status()

    monkeypatch.setattr(memory_connection, "codex_hook_status", probe)
    checks = memory_connection.MemoryConnectionChecks(ttl_seconds=30)
    initial = checks.check("codex", tmp_path, "binding")
    clock.now = 39.9
    assert checks.check("codex", tmp_path, "binding") == initial
    assert probes == [tmp_path]
    clock.now = 40.0
    assert checks.check("codex", tmp_path, "binding") == initial
    assert len(probes) == 2
    assert checks.check("codex", tmp_path, "binding", refresh=True) == initial
    assert len(probes) == 3
    initial["warning_id"] = "caller modification"
    initial["hooks"]["events"].clear()
    assert checks.check("codex", tmp_path, "binding")["warning_id"] != "caller modification"
    assert checks.check("codex", tmp_path, "binding")["hooks"]["events"]


def test_warning_identity_survives_same_issue_but_changes_after_recovery(monkeypatch, tmp_path):
    native = SimpleNamespace(status=_status())
    monkeypatch.setattr(memory_connection, "codex_hook_status", lambda _root, **_kwargs: native.status)
    checks = memory_connection.MemoryConnectionChecks()
    first = checks.check("codex", tmp_path, "binding")
    same = checks.check("codex", tmp_path, "binding", refresh=True)
    assert same["warning_id"] == first["warning_id"]

    native.status = _status("reauthorization_required", trust_statuses=["modified"])
    modified = checks.check("codex", tmp_path, "binding", refresh=True)
    assert modified["warning_id"] != first["warning_id"]
    native.status = _status("authorized", ready=True)
    assert checks.check("codex", tmp_path, "binding", refresh=True)["ready"] is True
    native.status = _status("reauthorization_required", trust_statuses=["modified"])
    regression = checks.check("codex", tmp_path, "binding", refresh=True)
    assert regression["warning_id"] not in {first["warning_id"], modified["warning_id"]}


def test_changed_hook_coverage_invalidates_ack_but_ordering_does_not(monkeypatch, tmp_path):
    native = SimpleNamespace(
        status=replace(
            _status("hooks_incomplete", trust_statuses=["trusted", "untrusted"]),
            hook_count=2,
            events=["sessionStart", "stop"],
        )
    )
    monkeypatch.setattr(memory_connection, "codex_hook_status", lambda _root, **_kwargs: native.status)
    checks = memory_connection.MemoryConnectionChecks()
    first = checks.check("codex", tmp_path, "binding")
    native.status = replace(
        native.status,
        trust_statuses=list(reversed(native.status.trust_statuses)),
        events=list(reversed(native.status.events)),
    )
    same = checks.check("codex", tmp_path, "binding", refresh=True)
    assert same["warning_id"] == first["warning_id"]
    native.status = replace(native.status, events=["sessionStart", "userPromptSubmit"])
    different_missing_hook = checks.check("codex", tmp_path, "binding", refresh=True)
    assert different_missing_hook["warning_id"] != first["warning_id"]
    native.status = replace(native.status, hook_count=3)
    duplicate_hook = checks.check("codex", tmp_path, "binding", refresh=True)
    assert duplicate_hook["warning_id"] != different_missing_hook["warning_id"]


def test_warning_scope_separates_roots_and_bindings_and_cache_is_bounded(monkeypatch, tmp_path):
    probes = []
    monkeypatch.setattr(
        memory_connection, "codex_hook_status", lambda root, **_kwargs: probes.append(root) or _status()
    )
    checks = memory_connection.MemoryConnectionChecks(max_entries=2)
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first = checks.check("codex", first_root, "binding")
    second = checks.check("codex", second_root, "binding")
    assert first["warning_id"] != second["warning_id"]
    assert checks.check("codex", first_root / ".", "binding") == first
    replacement = checks.check("codex", first_root, "replacement-binding")
    assert replacement["warning_id"] != first["warning_id"]
    assert checks.check("codex", first_root, "binding") == first
    revisited = checks.check("codex", second_root, "binding")
    assert revisited["warning_id"] != second["warning_id"]
    assert len(probes) == 4
    assert len(checks._cache) == 2


def test_concurrent_checks_share_one_native_probe(monkeypatch, tmp_path):
    barrier = threading.Barrier(7)
    entered = threading.Event()
    release = threading.Event()
    probes = []

    def probe(root, **_kwargs):
        probes.append(root)
        entered.set()
        assert release.wait(timeout=5)
        return _status()

    monkeypatch.setattr(memory_connection, "codex_hook_status", probe)
    checks = memory_connection.MemoryConnectionChecks()

    def check():
        barrier.wait(timeout=5)
        return checks.check("codex", tmp_path, "binding")

    with ThreadPoolExecutor(max_workers=6) as executor:
        pending = [executor.submit(check) for _ in range(6)]
        barrier.wait(timeout=5)
        try:
            assert entered.wait(timeout=5)
        finally:
            release.set()
        responses = [future.result(timeout=5) for future in pending]
    assert probes == [tmp_path]
    assert len({response["warning_id"] for response in responses}) == 1


def test_explicit_recheck_cannot_be_overwritten_by_an_older_probe(monkeypatch, tmp_path):
    entered = threading.Event()
    waiting = threading.Event()
    release = threading.Event()
    probes = []

    def probe(root, **_kwargs):
        probes.append(root)
        if len(probes) == 1:
            entered.set()
            assert release.wait(timeout=5)
            return _status()
        return _status("authorized", ready=True)

    monkeypatch.setattr(memory_connection, "codex_hook_status", probe)
    checks = memory_connection.MemoryConnectionChecks()
    original_wait = checks._condition.wait

    def wait_for_same_binding(*args, **kwargs):
        waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(checks._condition, "wait", wait_for_same_binding)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(checks.check, "codex", tmp_path, "binding")
        try:
            assert entered.wait(timeout=5)
            refresh = executor.submit(checks.check, "codex", tmp_path, "binding", refresh=True)
            assert waiting.wait(timeout=5)
        finally:
            release.set()
        assert first.result(timeout=5)["ready"] is False
        assert refresh.result(timeout=5)["ready"] is True
    assert checks.check("codex", tmp_path, "binding")["ready"] is True
    assert len(probes) == 2


@pytest.mark.parametrize("cached", [True, False])
def test_stalled_root_does_not_block_another_roots_cache_or_probe(monkeypatch, tmp_path, cached):
    stalled_root = tmp_path / "stalled"
    healthy_root = tmp_path / "healthy"
    entered = threading.Event()
    release = threading.Event()
    probes = []

    def probe(root, **_kwargs):
        probes.append(root)
        if root == stalled_root:
            entered.set()
            assert release.wait(timeout=5)
            return _status()
        return _status("authorized", ready=True)

    monkeypatch.setattr(memory_connection, "codex_hook_status", probe)
    checks = memory_connection.MemoryConnectionChecks()
    if cached:
        assert checks.check("codex", healthy_root, "healthy-binding")["ready"] is True
    with ThreadPoolExecutor(max_workers=2) as executor:
        stalled = executor.submit(checks.check, "codex", stalled_root, "stalled-binding")
        try:
            assert entered.wait(timeout=5)
            healthy = executor.submit(checks.check, "codex", healthy_root, "healthy-binding")
            assert healthy.result(timeout=2)["ready"] is True
            assert not stalled.done()
        finally:
            release.set()
        assert stalled.result(timeout=5)["ready"] is False
    assert probes.count(healthy_root) == 1
    assert not checks._inflight


def test_failed_probe_releases_its_singleflight_key(monkeypatch, tmp_path):
    native = SimpleNamespace(fail=True)

    def probe(_root, **_kwargs):
        if native.fail:
            raise RuntimeError("simulated native failure")
        return _status("authorized", ready=True)

    monkeypatch.setattr(memory_connection, "codex_hook_status", probe)
    checks = memory_connection.MemoryConnectionChecks()
    with pytest.raises(RuntimeError, match="simulated native failure"):
        checks.check("codex", tmp_path, "binding")
    assert not checks._inflight
    native.fail = False
    assert checks.check("codex", tmp_path, "binding")["ready"] is True
    assert not checks._inflight


async def test_sdk_pending_notice_prevents_mutation_and_acknowledgement_is_per_call(
    monkeypatch, sdk_runtime
):
    monkeypatch.setattr(memory_connection, "codex_hook_status", lambda _root, **_kwargs: _status())
    arguments = {"workspace_root": str(sdk_runtime.root), "title": "Ship release"}

    async def sequence(session):
        pending = _payload(await session.call_tool("create_task", arguments))
        assert pending["tool_executed"] is False
        assert sdk_runtime.calls == []
        warning_id = pending["warning_id"]
        accepted = _payload(
            await session.call_tool("create_task", {**arguments, "memory_warning_ack": warning_id})
        )
        assert accepted["executed"] == "create_task"
        assert accepted["arguments"] == {"title": "Ship release"}
        blocked_again = _payload(await session.call_tool("create_task", arguments))
        assert blocked_again["tool_executed"] is False
        assert blocked_again["warning_id"] == warning_id
        assert len(sdk_runtime.calls) == 1
        return warning_id

    warning_id = await _sdk_session("Codex Desktop", sequence)

    async def another_chat(session):
        pending = _payload(await session.call_tool("create_task", arguments))
        assert pending["warning_id"] == warning_id
        assert pending["tool_executed"] is False

    await _sdk_session("Codex Desktop", another_chat)
    assert len(sdk_runtime.calls) == 1
    assert sdk_runtime.calls[0][1] == {"title": "Ship release"}


async def test_sdk_recheck_recovers_then_rejects_old_acknowledgement_after_regression(
    monkeypatch, sdk_runtime
):
    native = SimpleNamespace(status=_status())
    monkeypatch.setattr(memory_connection, "codex_hook_status", lambda _root, **_kwargs: native.status)
    arguments = {"workspace_root": str(sdk_runtime.root)}

    async def sequence(session):
        initial = _payload(await session.call_tool("check_memory_connection", arguments))
        assert sdk_runtime.calls == []
        native.status = _status("authorized", ready=True)
        recovered = _payload(await session.call_tool("check_memory_connection", arguments))
        assert recovered["ready"] is True
        assert "warning_id" not in recovered
        assert sdk_runtime.calls == []
        assert _payload(await session.call_tool("list_tasks", arguments))["executed"] == "list_tasks"
        native.status = _status()
        regressed = _payload(await session.call_tool("check_memory_connection", arguments))
        assert regressed["warning_id"] != initial["warning_id"]
        stale = _payload(
            await session.call_tool(
                "list_tasks", {**arguments, "memory_warning_ack": initial["warning_id"]}
            )
        )
        assert stale["tool_executed"] is False
        assert stale["warning_id"] == regressed["warning_id"]

    await _sdk_session("codex-mcp-client", sequence)
    assert len(sdk_runtime.calls) == 1


async def test_sdk_remote_binding_checks_the_current_local_root_and_rejects_foreign_ack(
    monkeypatch, sdk_runtime
):
    sdk_runtime.state.remote = True
    probes = []
    monkeypatch.setattr(
        memory_connection, "codex_hook_status", lambda root, **_kwargs: probes.append(root) or _status()
    )
    second_root = sdk_runtime.root / "second-project"
    second_root.mkdir()

    async def sequence(session):
        first = _payload(
            await session.call_tool("list_tasks", {"workspace_root": str(sdk_runtime.root)})
        )
        second = _payload(
            await session.call_tool(
                "list_tasks",
                {"workspace_root": str(second_root), "memory_warning_ack": first["warning_id"]},
            )
        )
        assert second["tool_executed"] is False
        assert second["warning_id"] != first["warning_id"]
        sdk_runtime.state.binding_id = "changed-remote-binding"
        changed = _payload(
            await session.call_tool(
                "list_tasks",
                {"workspace_root": str(sdk_runtime.root), "memory_warning_ack": first["warning_id"]},
            )
        )
        assert changed["tool_executed"] is False
        assert changed["warning_id"] != first["warning_id"]

    await _sdk_session("Codex Desktop", sequence)
    assert probes == [sdk_runtime.root, second_root, sdk_runtime.root]
    assert sdk_runtime.calls == []


async def test_sdk_claude_continues_without_claiming_native_verification(sdk_runtime):
    arguments = {"workspace_root": str(sdk_runtime.root)}

    async def sequence(session):
        checked = _payload(await session.call_tool("check_memory_connection", arguments))
        assert checked["ready"] is None
        assert checked["reason"] == "native_verification_unavailable"
        assert checked["requires_choice"] is False
        assert "warning_id" not in checked
        result = _payload(await session.call_tool("list_tasks", arguments))
        assert result["executed"] == "list_tasks"

    await _sdk_session("Claude Code", sequence)
    assert len(sdk_runtime.calls) == 1


async def test_sdk_unconfigured_project_uses_existing_activation_flow(sdk_runtime):
    sdk_runtime.state.project_id = None
    arguments = {"workspace_root": str(sdk_runtime.root)}

    async def sequence(session):
        checked = _payload(await session.call_tool("check_memory_connection", arguments))
        assert checked["ready"] is None
        assert checked["reason"] == "unconfigured"
        assert checked["requires_choice"] is False
        assert "warning_id" not in checked
        assert sdk_runtime.calls == []
        await session.call_tool("list_tasks", arguments)

    await _sdk_session("Codex Desktop", sequence)
    assert len(sdk_runtime.calls) == 1


async def test_sdk_unsupported_client_has_no_memory_control_surface(sdk_runtime):
    async def sequence(session):
        assert (await session.list_tools()).tools == []
        response = await session.call_tool(
            "check_memory_connection", {"workspace_root": str(sdk_runtime.root)}
        )
        assert response.is_error is True

    await _sdk_session("cursor", sequence)
    assert sdk_runtime.calls == []


async def test_sdk_privacy_and_setup_remain_available_without_native_probe(sdk_runtime):
    arguments = {"workspace_root": str(sdk_runtime.root)}
    tools = ["health", "open_setup", "check_setup", "decline_setup"]

    async def sequence(session):
        for name in tools:
            assert _payload(await session.call_tool(name, arguments))["executed"] == name
        privacy = _payload(
            await session.call_tool(
                "set_off_record", {**arguments, "session_id": "session-one", "off_record": True}
            )
        )
        assert privacy["arguments"] == {"session_id": "session-one", "off_record": True}

    await _sdk_session("Codex Desktop", sequence)
    assert [call[0] for call in sdk_runtime.calls] == [*tools, "set_off_record"]


@pytest.mark.parametrize("acknowledgement", [True, 123, "", "x" * 32, "0" * 31, "A" * 32, {}, []])
async def test_sdk_invalid_acknowledgement_fails_before_probe_or_operation(
    sdk_runtime, acknowledgement
):
    async def sequence(session):
        response = await session.call_tool(
            "list_tasks",
            {"workspace_root": str(sdk_runtime.root), "memory_warning_ack": acknowledgement},
        )
        assert response.is_error is True
        assert "memory_warning_ack" in response.content[0].text

    await _sdk_session("Codex Desktop", sequence)
    assert sdk_runtime.calls == []


@pytest.mark.parametrize("client_name", ["Codex Desktop", "Claude Code"])
@pytest.mark.parametrize("remote", [False, True])
async def test_sdk_sleep_auth_failure_requires_choice_even_with_approved_hooks(
    monkeypatch, sdk_runtime, client_name, remote
):
    sdk_runtime.state.remote = remote
    health = {"state": "connection_required", "provider": "codex", "issue_id": "first-attempt"}
    monkeypatch.setattr(mcp_server, "_read_connection_health", lambda runtime: health)
    monkeypatch.setattr(memory_connection, "codex_hook_status", lambda root, **_kwargs: _status("authorized", ready=True))
    arguments = {"workspace_root": str(sdk_runtime.root)}

    async def sequence(session):
        first = _payload(await session.call_tool("list_tasks", arguments))
        assert first["tool_executed"] is False
        assert first["reason"] == "sleep_auth_required"
        assert "Saved turns" in first["message"]
        assert "current conversation" in first["response_instruction"]
        assert memory_connection.CONFIGURATION_CHOICE_INSTRUCTION in first["response_instruction"]
        assert ("infrastructure manager" in first["next_action"]) is remote
        assert ("call open_setup" in first["next_action"]) is not remote
        assert sdk_runtime.calls == []
        # Choosing repair can open configuration; it does not acknowledge or
        # silently execute the original Work operation.
        assert _payload(await session.call_tool("open_setup", arguments))["executed"] == "open_setup"
        assert _payload(await session.call_tool("list_tasks", arguments))["tool_executed"] is False
        checked = _payload(await session.call_tool("check_memory_connection", arguments))
        assert checked["warning_id"] == first["warning_id"]
        ack = {**arguments, "memory_warning_ack": first["warning_id"]}
        assert _payload(await session.call_tool("list_tasks", ack))["executed"] == "list_tasks"
        assert _payload(await session.call_tool("list_tasks", arguments))["tool_executed"] is False
        health["issue_id"] = "second-attempt"
        changed = _payload(await session.call_tool("list_tasks", ack))
        assert changed["tool_executed"] is False
        assert changed["warning_id"] != first["warning_id"]
        health.update(state="updating", issue_id=None)
        assert _payload(await session.call_tool("list_tasks", arguments))["executed"] == "list_tasks"

    await _sdk_session(client_name, sequence)
    assert [call[0] for call in sdk_runtime.calls] == ["open_setup", "list_tasks", "list_tasks"]
