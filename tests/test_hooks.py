from __future__ import annotations

import io
import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from conftest import assert_private_file
from dduo_solo_founder import hooks, manual_cache, project_config
from dduo_solo_founder.client_http import ClientUpgradeRequired, CompatibilityDirective
from dduo_solo_founder.context_budget import context_units, utf16_units


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_client_upgrade_notice_is_explicit_and_never_uses_offline_memory():
    request = httpx.Request("GET", "https://memory.example/api/health")
    response = httpx.Response(426, request=request)
    error = ClientUpgradeRequired(
        response,
        CompatibilityDirective("blocked", "0.1.0-alpha.48"),
    )

    notice = hooks.client_upgrade_notice(error)

    assert notice is not None
    assert "0.1.0-alpha.48" in notice.content
    assert "official repository" in notice.content
    assert "open a new chat" in notice.content
    assert hooks.remote_manual_cache_allowed(error) is False


def hook_input(monkeypatch, payload):
    root_value = payload.get("cwd")
    if isinstance(root_value, str) and root_value:
        root = Path(root_value)
        try:
            configured = hooks.load_project(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            configured = None
        if (
            configured
            and str(configured.get("binding") or "local") == "local"
            and configured.get("id")
            and configured.get("api_port") is not None
            and configured.get("web_port") is not None
        ):
            project_config.register_project_config(root, configured)
    monkeypatch.setattr(hooks.sys, "stdin", io.StringIO(json.dumps(payload)))
    output = io.StringIO()
    monkeypatch.setattr(hooks.sys, "stdout", output)
    return output


def project():
    return {"id": "p1", "name": "ExampleApp", "api_port": 8765, "web_port": 4173}


def test_hook_binding_enforces_only_local_project_claims(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        hooks,
        "binding_from_project",
        lambda *args, **kwargs: calls.append(kwargs) or object(),
    )

    hooks._binding_for_project(tmp_path, project())
    hooks._binding_for_project(
        tmp_path,
        {"id": "remote", "binding": "remote", "api_url": "https://memory.example/api"},
    )

    assert [call["enforce_project_claim"] for call in calls] == [True, False]


def founder_lines(context: str) -> list[dict]:
    return [json.loads(line) for line in context.splitlines()]


def founder_items(context: str, component: str | None = None) -> list[dict]:
    return [
        item
        for item in founder_lines(context)
        if "component" in item and (component is None or item["component"] == component)
    ]


def test_hook_envelopes_and_private_state(monkeypatch, tmp_path):
    output = hook_input(monkeypatch, {"client": "codex"})
    hooks.emit("context", "UserPromptSubmit")
    envelope = json.loads(output.getvalue())
    assert envelope == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "context",
        }
    }
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    output = hook_input(monkeypatch, {})
    hooks.emit("context", "SessionStart")
    assert json.loads(output.getvalue()) == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "context",
        }
    }
    path = tmp_path / "state.json"
    hooks.write_state(path, {"turn_id": "private"})
    assert hooks.read_state(path)["turn_id"] == "private"
    assert_private_file(path)


def test_emit_flushes_and_wraps_stdout_failures(monkeypatch):
    class TrackingOutput(io.StringIO):
        flushed = False

        def flush(self):
            self.flushed = True
            super().flush()

    output = TrackingOutput()
    monkeypatch.setattr(hooks.sys, "stdout", output)
    hooks.emit("context", "SessionStart")
    assert output.flushed is True

    class BrokenOutput:
        def write(self, _value):
            raise BrokenPipeError("closed")

        def flush(self):
            raise BrokenPipeError("closed")

    monkeypatch.setattr(hooks.sys, "stdout", BrokenOutput())
    with pytest.raises(hooks.HookEmissionError):
        hooks.emit("context", "SessionStart")


def test_task_contract_reuses_automatic_and_requested_context():
    assert hooks.HOOK_CONTEXT_RENDER_VERSION == "hook-context-v6"
    assert "standing Founder Brief already present" in hooks.TURN_CONTRACT
    assert "Founder Brief is current" in hooks.TASK_CONTRACT
    assert "search ambiguity once" in hooks.TASK_CONTRACT
    assert "reuse its ID and snapshot hash" in hooks.TASK_CONTRACT
    assert "human title" in hooks.TASK_CONTRACT
    assert "internal ID or UUID" in hooks.TASK_CONTRACT
    assert "explicit execution request authorizes" in hooks.TASK_CONTRACT
    assert "solo-or-team operating manual" in hooks.TASK_CONTRACT
    assert "Do not nag if it is empty" in hooks.TASK_CONTRACT
    assert "Product principles and invariants belong in the profile" in hooks.TASK_CONTRACT
    assert "Never duplicate a rule" in hooks.TASK_CONTRACT
    assert "Prefer best practices intelligently" in hooks.COFOUNDER_CONTRACT
    assert "Do not be a yes-man" in hooks.COFOUNDER_CONTRACT
    assert "adversarial review" in hooks.COFOUNDER_CONTRACT


def test_stable_delivery_measurement_matches_partial_unicode_jsonl_exactly():
    briefing = {
        "project": {
            "id": "p1",
            "name": "Memoria 🧠",
            "context": "Contesto verificato 🚀 " * 90,
        },
        "operational_manual": {
            "version": 8,
            "content": "Regola operativa già verificata. " * 500,
        },
    }
    composed = hooks.compose_founder_context(
        briefing,
        contracts="",
        standing_contracts=hooks.COFOUNDER_CONTRACT + hooks.TASK_CONTRACT,
    )
    stable_lines = [
        line
        for line in composed.content.splitlines()
        if (
            (item := json.loads(line)).get("reference") == "standing-operating-contract"
            or item.get("component") in {"manual", "profile"}
        )
    ]
    rendered = "\n".join(stable_lines)
    measurement = hooks._delivered_stable_measurements(composed)["foundation"]

    assert founder_items(composed.content, "manual")[0]["delivery"] == "partial"
    assert measurement["items"] == len(stable_lines)
    assert measurement["characters"] == len(rendered)
    assert measurement["utf16_units"] == utf16_units(rendered)
    assert measurement["utf8_bytes"] == len(rendered.encode("utf-8"))
    assert measurement["budget_units"] == context_units(rendered)


def test_distinct_external_session_ids_never_share_a_spool_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    first = hooks.state_path("p1", "codex", "topic/a")
    second = hooks.state_path("p1", "codex", "topic?a")
    assert first != second


def test_unconfigured_session_asks_once_for_activation(monkeypatch, tmp_path):
    output = hook_input(monkeypatch, {"cwd": str(tmp_path)})
    hooks.session_start()
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert context_units(context) <= 9_000
    assert "activate dDuo Solo Founder locally" in context
    assert "remote project invitation" in context
    assert "open_setup" in context
    assert "check_setup" in context
    assert "reply 'fatto'" in context
    assert "dduo-solo-founder setup" not in context
    assert "OpenAI API key" not in context


def test_declined_session_and_unconfigured_prompts_are_silent(monkeypatch, tmp_path):
    from dduo_solo_founder.project_activation import decline_setup

    decline_setup(tmp_path)
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "prompt": "Work without memory"})
    hooks.session_start()
    hooks.user_prompt_submit()
    assert all(json.loads(line) == {"continue": True} for line in output.getvalue().splitlines())
    assert not (tmp_path / project_config.CONFIG_PATH).exists()


def test_unconfigured_prompt_is_not_claimed_as_queued(monkeypatch, tmp_path):
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "prompt": "Hello"})
    hooks.user_prompt_submit()
    assert json.loads(output.getvalue()) == {"continue": True}


def test_failed_private_spool_never_promises_recovery(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(hooks, "merge_state_update", lambda *args: (_ for _ in ()).throw(OSError("full")))
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "prompt": "Work", "session_id": "s"})
    hooks.user_prompt_submit()
    rendered = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "may not be saved" in rendered
    assert "queued locally" not in rendered


def test_session_start_surfaces_expired_auth_before_opening_setup(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    telemetry_flushes = []
    monkeypatch.setattr(
        hooks,
        "flush_client_telemetry",
        lambda transport, project_id: telemetry_flushes.append(project_id),
    )
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: calls.append(args[0]) or None)
    responses = [
        Response({"id": "session"}),
        Response(
            {
                "project": {"name": "ExampleApp"},
                "tasks": [],
                "onboarding_required": True,
                "memory_status": {
                    "state": "connection_required",
                    "provider": "claude",
                    "summary": "Connect Claude",
                },
            }
        ),
    ]
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(hooks.httpx, "get", lambda *args, **kwargs: responses.pop(0))
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "client": "claude", "session_id": "s1"})
    hooks.session_start()
    assert telemetry_flushes == ["p1"]
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "Before project work ask whether to fix it now" in context
    assert "After the user chooses to fix it" in context
    assert "what this project is for" in context
    assert "smallest useful Plan, Epic, or Task structure" in context
    assert ["dduo-solo-founder", "setup", "--project-root", str(tmp_path)] not in calls
    assert ["dduo-solo-founder", "start", "--project-root", str(tmp_path)] in calls
    state = hooks.read_state(hooks.state_path("p1", "claude", "s1"))
    observation = state["pending_context_observations"][0]
    assert observation["operation"] == "context.session_start"
    assert observation["turn_id"] is None
    assert observation["content"] == context
    assert observation["content_sha256"] == hashlib.sha256(context.encode("utf-8")).hexdigest()
    assert datetime.fromisoformat(observation["occurred_at"]).utcoffset() is not None
    assert sum(observation["component_bytes"].values()) == observation["utf8_bytes"]
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "client": "claude", "session_id": "s1"})
    responses.extend(
        [
            Response({"id": "session"}),
            Response(
                {
                    "project": {},
                    "memory_status": {"state": "connection_required", "provider": "claude"},
                }
            ),
        ]
    )
    hooks.session_start()
    assert telemetry_flushes == ["p1", "p1"]
    assert calls.count(["dduo-solo-founder", "setup", "--project-root", str(tmp_path)]) == 0


def test_session_start_other_client_uses_the_global_briefing(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"id": "session"}))
    get_calls = []

    def get_briefing(*args, **kwargs):
        get_calls.append((args, kwargs))
        return Response({"project": {"id": "p1", "name": "ExampleApp"}, "memory_status": {}})

    monkeypatch.setattr(hooks.httpx, "get", get_briefing)
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "client": "other", "session_id": "other-session"},
    )

    hooks.session_start()

    assert "params" not in get_calls[0][1]
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert founder_items(context, "profile")[0]["payload"]["name"] == "ExampleApp"


def test_user_prompt_opens_turn_retrieves_compact_context_and_never_auth_gates(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    responses = iter(
        [
            Response({"id": "session"}),
            Response(
                {
                    "turn": {"id": "turn-1"},
                    "project": {"principles": ["Protect production"]},
                    "tasks": [{"id": "task-release", "title": "Release"}],
                    "plans": [
                        {"id": "plan-android", "title": "Android launch", "status": "decided"}
                    ],
                    "memories": [{"id": str(index)} for index in range(8)],
                    "memory_status": {"state": "updated", "summary": "Memory is up to date."},
                }
            ),
        ]
    )
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: next(responses))
    output = hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "client": "codex",
            "session_id": "s1",
            "message_id": "m1",
            "prompt": "Ship Android",
        },
    )
    hooks.user_prompt_submit()
    state = hooks.read_state(hooks.state_path("p1", "codex", "s1"))
    assert state["turn_id"] == "turn-1"
    assert state["external_turn_id"].startswith("prompt-") is False
    assert state["external_turn_id"] != "m1"
    assert_private_file(hooks.state_path("p1", "codex", "s1"))
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    memories = founder_items(context, "memories")
    assert [item["reference"] for item in memories] == [str(index) for index in range(8)]
    assert next(item for item in founder_items(context, "plans"))["payload"]["title"] == (
        "Android launch"
    )
    assert "Authentication preflight" not in context
    assert "smallest useful Plan, Epic, or Task structure" in context
    session_item = next(
        item
        for item in founder_items(context, "instructions")
        if item["reference"] == "current-dduo-session"
    )
    assert session_item["payload"] == {
        "dduo_session_id": "session",
        "off_record": False,
    }
    observation = state["pending_context_observations"][0]
    assert observation["operation"] == "context.turn_injection"
    assert observation["turn_id"] == "turn-1"
    assert observation["utf8_bytes"] == len(context.encode("utf-8"))
    assert observation["budget"]["client_character_units"] <= 9_000
    assert observation["budget"]["included_items"] >= 8
    assert sum(observation["component_bytes"].values()) == observation["utf8_bytes"]
    assert observation["content"] == context
    assert observation["content_sha256"] == hashlib.sha256(context.encode("utf-8")).hexdigest()
    assert observation["event_id"] == hooks.automatic_context_event_id(
        "context.turn_injection",
        session_id="session",
        turn_id="turn-1",
        content=context,
        occurrence_id="m1",
    )


def test_steering_prompts_keep_one_turn_and_distinct_delivery_observations(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        if url.endswith("/sessions"):
            return Response({"id": "session"})
        if url.endswith("/turns/begin"):
            return Response(
                {
                    "turn": {"id": "internal-turn", "retrieval_run_id": "latest-run"},
                    "project": {},
                    "tasks": [],
                    "plans": [],
                    "memories": [],
                    "memory_status": {},
                    "retrieval": {"retrieval_run_id": f"run-{kwargs['json']['prompt_event_id']}"},
                }
            )
        if url.endswith("/stop-check"):
            return Response({"allow": True})
        raise AssertionError(url)

    monkeypatch.setattr(hooks.httpx, "post", post)
    for message_id, prompt in (("message-one", "Start"), ("message-two", "Also verify QA")):
        output = hook_input(
            monkeypatch,
            {
                "cwd": str(tmp_path),
                "session_id": "thread",
                "turn_id": "codex-turn",
                "message_id": message_id,
                "prompt": prompt,
            },
        )
        hooks.user_prompt_submit()
        assert json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]

    begins = [payload for url, payload in calls if url.endswith("/turns/begin")]
    assert [payload["external_id"] for payload in begins] == ["codex-turn", "codex-turn"]
    assert [payload["prompt_event_id"] for payload in begins] == [
        "message-one",
        "message-two",
    ]
    assert [payload["off_record"] for payload in begins] == [False, False]
    state = hooks.read_state(hooks.state_path("p1", "codex", "thread"))
    observations = state["pending_context_observations"]
    assert len(observations) == 2
    assert observations[0]["event_id"] != observations[1]["event_id"]
    assert [item["retrieval_run_id"] for item in observations] == [
        "run-message-one",
        "run-message-two",
    ]

    hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "thread", "assistant_response": "Done"},
    )
    hooks.stop()
    stop_payload = next(payload for url, payload in calls if url.endswith("/stop-check"))
    assert stop_payload["context_observations"] == observations


def test_claude_prompts_without_native_turn_ids_share_only_the_active_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(hooks, "flush_client_telemetry", lambda *args: None)
    begins = []

    def post(url, **kwargs):
        if url.endswith("/sessions"):
            return Response({"id": "session", "off_record": False})
        if url.endswith("/turns/begin"):
            begins.append(kwargs["json"])
            return Response(
                {
                    "turn": {"id": "internal-turn"},
                    "project": {},
                    "tasks": [],
                    "plans": [],
                    "memories": [],
                    "memory_status": {},
                    "retrieval": {"retrieval_run_id": f"run-{len(begins)}"},
                }
            )
        if url.endswith("/stop-check"):
            return Response({"allow": True})
        raise AssertionError(url)

    monkeypatch.setattr(hooks.httpx, "post", post)
    for message_id, prompt in (
        ("message-one", "Prima richiesta"),
        ("message-two", "Aggiungo un vincolo"),
    ):
        hook_input(
            monkeypatch,
            {
                "client": "claude",
                "cwd": str(tmp_path),
                "session_id": "s",
                "message_id": message_id,
                "prompt": prompt,
            },
        )
        hooks.user_prompt_submit()
    assert begins[0]["external_id"] == begins[1]["external_id"]
    active_external = begins[0]["external_id"]

    hook_input(
        monkeypatch,
        {"client": "claude", "cwd": str(tmp_path), "session_id": "s", "assistant_response": "Ok"},
    )
    hooks.stop()
    hook_input(
        monkeypatch,
        {"client": "claude", "cwd": str(tmp_path), "session_id": "s", "prompt": "Nuovo turno"},
    )
    hooks.user_prompt_submit()
    assert begins[2]["external_id"] != active_external


def test_rejected_late_prompt_does_not_leave_context_for_the_next_turn(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(hooks, "flush_client_telemetry", lambda *args: None)

    def post(url, **_kwargs):
        if url.endswith("/sessions"):
            return Response({"id": "session", "off_record": False})
        if url.endswith("/turns/begin"):
            request = httpx.Request("POST", url)
            return httpx.Response(409, request=request)
        raise AssertionError(url)

    monkeypatch.setattr(hooks.httpx, "post", post)
    hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "session_id": "session",
            "turn_id": "closed-turn",
            "message_id": "late-message",
            "prompt": "Too late",
        },
    )
    hooks.user_prompt_submit()
    state = hooks.read_state(hooks.state_path("p1", "codex", "session"))
    assert "pending_context_observations" not in state
    assert "spooled_active" not in state


def test_budgeted_founder_brief_is_the_exact_complete_jsonl_emitted_by_the_hook(
    monkeypatch,
):
    memories = [
        {
            "id": f"memory-{index}",
            "node_type": "decision",
            "node_key": f"decision-{index}",
            "revision": 1,
            "text": f"Decision {index}. " + ("valuable context " * 300),
        }
        for index in range(12)
    ]
    briefing = {
        "project": {
            "id": "p1",
            "name": "Example Workspace",
            "cause": "Build a reliable example workflow.",
            "principles": ["Protect production data", "Prefer correct dense context"],
        },
        "memories": memories,
        "retrieval": {
            "status": "context_ready",
            "metadata": {
                "selection_mode": "direct",
                "selected_reasons": {item["id"]: "direct" for item in memories},
            },
        },
    }
    composed = hooks.compose_founder_context(
        briefing,
        contracts=hooks.TURN_CONTRACT,
        prompt="Continue the collection architecture",
    )
    observation = hooks.measured_context(
        composed.content,
        event_id="budgeted-founder-brief",
        operation="context.turn_injection",
        scope="automatic",
        client="codex",
        composed=composed,
    )
    output = hook_input(monkeypatch, {"client": "codex"})
    hooks.emit(composed.content, "UserPromptSubmit")
    emitted = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]

    assert emitted == observation["content"] == composed.content
    assert context_units(emitted) <= 9_000
    assert all(isinstance(json.loads(line), dict) for line in emitted.splitlines())
    assert observation["budget"]["outcome"] == "budgeted"
    assert observation["budget"]["candidate_characters"] > observation["characters"]
    assert observation["budget"]["avoided_characters"] == (
        observation["budget"]["candidate_characters"] - observation["characters"]
    )
    memory_component = next(
        item for item in observation["components"] if item["name"] == "memories"
    )
    assert memory_component["partial_item_count"] >= 1
    assert memory_component["candidate_item_count"] == len(memories)
    assert memory_component["item_count"] < len(memories)
    partial = next(
        item for item in founder_items(emitted, "memories") if item["delivery"] == "partial"
    )
    assert partial["payload"]["excerpted"] is True
    assert partial["payload"]["full_memory"] == {
        "tool": "explain_memory",
        "memory_id": partial["reference"],
    }


def test_malformed_upstream_context_becomes_a_bounded_fallback() -> None:
    composed = hooks.compose_founder_context(
        {"project": {"id": "p1", "name": "Bad", "context": "\ud800"}},
        contracts=hooks.TURN_CONTRACT,
    )

    assert composed.fallback_used
    assert composed.manifest.status == "fallback"
    assert composed.manifest.fallback_reason == "composition_failed"
    assert context_units(composed.content) <= 9_000
    assert "could not safely compose" in composed.content


def test_static_briefing_is_delivered_once_per_session_and_turns_keep_dynamic_context(
    monkeypatch, tmp_path
):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    briefing = {
        "project": {"name": "ExampleApp", "principles": ["Protect production"]},
        "plans": [],
        "tasks": [
            {
                "id": "task-1",
                "title": "Indicizzare semanticamente i task",
                "status": "in_progress",
                "priority": "high",
                "version": 3,
            }
        ],
        "task_counts": {
            "total": 27,
            "nonterminal": 9,
            "selected": 1,
            "omitted": 8,
            "by_status": {"todo": 8, "in_progress": 1, "done": 18},
        },
        "memory_status": {"state": "updated", "summary": "Memory is up to date."},
    }

    def post(url, **kwargs):
        if url.endswith("/sessions"):
            external_id = kwargs["json"]["external_id"]
            return Response({"id": f"internal-{external_id}"})
        if url.endswith("/turns/begin"):
            external_id = kwargs["json"]["external_id"]
            return Response({"turn": {"id": f"turn-{external_id}"}, **briefing})
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(hooks.httpx, "post", post)
    monkeypatch.setattr(hooks.httpx, "get", lambda *args, **kwargs: Response(briefing))

    first_session_output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "client": "codex", "session_id": "session-one"},
    )
    hooks.session_start()
    first_session_context = json.loads(first_session_output.getvalue())["hookSpecificOutput"]["additionalContext"]
    first_task_items = founder_items(first_session_context, "tasks")
    assert (
        next(item for item in first_task_items if item["reference"] == "task-counts")["payload"][
            "total"
        ]
        == 27
    )
    assert (
        next(item for item in first_task_items if item["reference"] == "task-1")["payload"]["title"]
        == "Indicizzare semanticamente i task"
    )

    for message_id in ("message-one", "message-two"):
        turn_output = hook_input(
            monkeypatch,
            {
                "cwd": str(tmp_path),
                "client": "codex",
                "session_id": "session-one",
                "message_id": message_id,
                "prompt": "Continua",
            },
        )
        hooks.user_prompt_submit()
        turn_context = json.loads(turn_output.getvalue())["hookSpecificOutput"]["additionalContext"]
        references = {item["reference"] for item in founder_items(turn_context)}
        assert "task-1" not in references
        assert "task-counts" not in references
        assert "project-profile" not in references
        assert "first reuse or create" not in turn_context

    second_session_output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "client": "codex", "session_id": "session-two"},
    )
    hooks.session_start()
    second_session_context = json.loads(second_session_output.getvalue())["hookSpecificOutput"]["additionalContext"]
    second_task_items = founder_items(second_session_context, "tasks")
    assert {item["reference"] for item in second_task_items} >= {"task-1", "task-counts"}


def test_briefing_hash_ignores_backup_bookkeeping_but_tracks_profile_changes():
    base = {
        "project": {
            "id": "p1",
            "name": "ExampleApp",
            "cause": "Ship safely",
            "principles": ["Protect production"],
            "objectives": ["Release"],
            "context": "Current product context",
            "profile_version": 3,
            "backup_dirty": False,
            "backup_generation": 8,
            "updated_at": "2026-08-25T10:00:00Z",
        },
        "plans": [],
        "tasks": [],
        "task_counts": {"total": 0},
    }
    bookkeeping_changed = json.loads(json.dumps(base))
    bookkeeping_changed["project"].update(
        {
            "backup_dirty": True,
            "backup_generation": 9,
            "updated_at": "2026-08-25T10:01:00Z",
        }
    )
    assert hooks.briefing_context_hash(base) == hooks.briefing_context_hash(bookkeeping_changed)

    profile_changed = json.loads(json.dumps(bookkeeping_changed))
    profile_changed["project"]["cause"] = "Ship an excellent product"
    profile_changed["project"]["profile_version"] = 4
    assert hooks.briefing_context_hash(base) != hooks.briefing_context_hash(profile_changed)


def test_prompt_outage_spools_full_turn_for_later_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(
        hooks.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    )
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "message_id": "m1", "prompt": "Keep this"},
    )
    hooks.user_prompt_submit()
    state = hooks.read_state(hooks.state_path("p1", "codex", "s1"))
    assert state["spooled_active"]["user_prompt"] == "Keep this"
    assert state["spooled_active"]["off_record"] is True
    fallback = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "queued locally" in fallback
    observation = state["pending_context_observations"][0]
    assert observation["content"] == fallback
    assert observation["render_version"] == hooks.HOOK_FALLBACK_RENDER_VERSION
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Saved answer"},
    )
    hooks.stop()
    state = hooks.read_state(hooks.state_path("p1", "codex", "s1"))
    assert state["spooled_turns"][0]["assistant_response"] == "Saved answer"
    assert state["spooled_turns"][0]["context_observations"] == [observation]


def test_failed_fallback_stdout_spools_the_turn_without_a_false_delivery(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )
    hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "message_id": "m1", "prompt": "Keep this"},
    )
    monkeypatch.setattr(
        hooks,
        "emit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hooks.HookEmissionError("broken stdout")
        ),
    )

    with pytest.raises(hooks.HookEmissionError):
        hooks.user_prompt_submit()

    state = hooks.read_state(hooks.state_path("p1", "codex", "s1"))
    assert state["spooled_active"]["user_prompt"] == "Keep this"
    assert len(state["spooled_active"]["prompt_fragments"]) == 1
    assert state.get("pending_context_observations", []) == []


def test_prompt_outage_preserves_and_replays_all_steering_fragments(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(
        hooks.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    )
    for message_id, prompt in (("m1", "First"), ("m2", "Second")):
        hook_input(
            monkeypatch,
            {
                "cwd": str(tmp_path),
                "session_id": "s1",
                "turn_id": "shared-turn",
                "message_id": message_id,
                "prompt": prompt,
            },
        )
        hooks.user_prompt_submit()

    path = hooks.state_path("p1", "codex", "s1")
    active = hooks.read_state(path)["spooled_active"]
    assert active["user_prompt"] == "First\n\nSecond"
    assert active["prompt_fragments"] == [
        {"prompt_event_id": "m1", "user_prompt": "First"},
        {"prompt_event_id": "m2", "user_prompt": "Second"},
    ]

    hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Answer"},
    )
    hooks.stop()

    calls = []
    responses = iter(
        [
            Response({"id": "session"}),
            Response({"turn": {"id": "turn"}}),
            Response({"turn": {"id": "turn"}}),
            Response({"ok": True}),
        ]
    )

    def post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        return next(responses)

    monkeypatch.setattr(hooks.httpx, "post", post)
    assert hooks.flush_spooled_turns(project(), "http://api", "codex") == 1
    begins = [payload for url, payload in calls if url.endswith("/turns/begin")]
    assert [item["prompt_event_id"] for item in begins] == ["m1", "m2"]
    assert [item["user_prompt"] for item in begins] == ["First", "Second"]
    assert all(item["external_id"] == "shared-turn" for item in begins)


def test_context_breakdown_accounts_for_unicode_and_overhead_exactly():
    payload = {
        "project": {"name": "Caffè"},
        "tasks": [{"title": "Più qualità"}],
        "memories": ["già verificato"],
        "memory_status": {"state": "ok"},
        "unknown": "remains overhead",
    }
    instruction = "Istruzioni naïve. "
    context = instruction + json.dumps(payload, ensure_ascii=False)
    values = hooks.merge_component_values(
        hooks.briefing_component_values(payload), {"instructions": instruction}
    )
    observation = hooks.measured_context(
        context,
        event_id="unicode-context",
        operation="context.turn_injection",
        scope="automatic",
        client="codex",
        component_values=values,
    )
    assert observation["characters"] == len(context)
    assert observation["utf8_bytes"] == len(context.encode("utf-8"))
    assert sum(observation["component_bytes"].values()) == observation["utf8_bytes"]
    assert observation["component_bytes"]["overhead"] > 0


def test_context_breakdown_degrades_safely_when_components_overcount():
    observation = hooks.measured_context(
        "short",
        event_id="overcount-context",
        operation="context.turn_injection",
        scope="automatic",
        client="codex",
        component_values={"instructions": "longer than the complete context"},
    )
    assert observation["component_bytes"] == {"overhead": observation["utf8_bytes"]}


def test_context_event_ids_and_local_staging_are_stable_across_replay():
    first = hooks.automatic_context_event_id("context.session_start", session_id="external-1")
    second = hooks.automatic_context_event_id("context.session_start", session_id="external-1")
    fallback = hooks.automatic_context_event_id(
        "context.session_start",
        session_id="external-1",
        render_version=hooks.HOOK_FALLBACK_RENDER_VERSION,
    )
    assert first == second
    assert fallback != first
    assert hooks.automatic_context_event_id(
        "context.session_start", session_id="external-1", content="first briefing"
    ) != hooks.automatic_context_event_id(
        "context.session_start", session_id="external-1", content="changed briefing"
    )
    assert hooks.automatic_context_event_id(
        "context.session_start",
        session_id="external-1",
        content="same briefing",
        occurrence_id="delivery-1",
    ) != hooks.automatic_context_event_id(
        "context.session_start",
        session_id="external-1",
        content="same briefing",
        occurrence_id="delivery-2",
    )

    observation = hooks.measured_context(
        "same exact render",
        event_id=first,
        operation="context.session_start",
        scope="automatic",
        client="codex",
        component_values={"overhead": "same exact render"},
    )
    state = {}
    hooks.stage_context_observation(state, observation)
    hooks.stage_context_observation(state, dict(observation))
    assert state["pending_context_observations"] == [observation]


def test_repeated_identical_session_start_contexts_are_distinct_deliveries(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"id": "session"}))
    briefing = {"project": {"name": "ExampleApp"}, "tasks": [], "memory_status": {}}
    monkeypatch.setattr(hooks.httpx, "get", lambda *args, **kwargs: Response(briefing))

    first_output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "client": "codex", "session_id": "same"}
    )
    hooks.session_start()
    second_output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "client": "codex", "session_id": "same"}
    )
    hooks.session_start()

    assert (
        json.loads(first_output.getvalue())["hookSpecificOutput"]["additionalContext"]
        == json.loads(second_output.getvalue())["hookSpecificOutput"]["additionalContext"]
    )
    state = hooks.read_state(hooks.state_path("p1", "codex", "same"))
    observations = state["pending_context_observations"]
    assert len(observations) == 2
    assert observations[0]["content_sha256"] == observations[1]["content_sha256"]
    assert observations[0]["event_id"] != observations[1]["event_id"]


def test_session_start_emits_briefing_when_observability_staging_fails(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"id": "session"}))
    monkeypatch.setattr(
        hooks.httpx,
        "get",
        lambda *args, **kwargs: Response({"project": {"name": "ExampleApp"}, "tasks": []}),
    )
    monkeypatch.setattr(
        hooks,
        "write_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("unwritable")),
    )
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "session_id": "s1"})
    hooks.session_start()
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "project's operating cofounder" in context
    assert any(item["component"] == "profile" for item in founder_items(context))


def test_stop_uses_private_turn_id_and_queues_failed_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(
        path,
        {
            "api": "http://api",
            "turn_id": "private-turn",
            "pending_context_observations": [{"event_id": "observation-1"}],
        },
    )
    calls = []
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs["json"])) or Response({"ok": True}),
    )
    output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Answer"}
    )
    hooks.stop()
    assert calls == [
        (
            "http://api/turns/private-turn/stop-check",
            {
                "assistant_response": "Answer",
                "context_observations": [{"event_id": "observation-1"}],
            },
        )
    ]
    persisted = hooks.read_state(path)
    assert "turn_id" not in persisted
    assert "pending_context_observations" not in persisted
    assert json.loads(output.getvalue()) == {"continue": True}
    hooks.write_state(path, {"api": "http://api", "turn_id": "retry-turn"})
    monkeypatch.setattr(
        hooks.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Retry"}
    )
    hooks.stop()
    assert hooks.read_state(path)["pending_commits"][0]["turn_id"] == "retry-turn"


@pytest.mark.parametrize("offline", [False, True])
def test_late_stop_never_closes_a_newer_active_turn(monkeypatch, tmp_path, offline):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    path = hooks.state_path("p1", "codex", "session")
    active = {
        "session_external_id": "session",
        "external_turn_id": "turn-new",
        "prompt_event_id": "prompt-new",
        "user_prompt": "New work",
    }
    state = {
        "api": "http://api",
        "external_turn_id": "turn-new",
        "pending_context_observations": [{"event_id": "new-context"}],
        "codex_transcript_cursor": {"turn_id": "turn-new"},
    }
    if offline:
        state["spooled_active"] = active
        state.pop("external_turn_id")
    else:
        state["turn_id"] = "internal-new"
    hooks.write_state(path, state)
    monkeypatch.setattr(
        hooks,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a stale Stop must not call the project API")
        ),
    )
    output = hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "client": "codex",
            "session_id": "session",
            "turn_id": "turn-old",
            "assistant_response": "Old answer",
        },
    )

    hooks.stop()

    assert hooks.read_state(path) == state
    assert json.loads(output.getvalue()) == {"continue": True}


def test_flush_spooled_turns_replays_idempotently(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(
        path,
        {
            "spooled_turns": [
                {
                    "session_external_id": "s1",
                    "external_turn_id": "m1",
                    "user_prompt": "Question",
                    "assistant_response": "Answer",
                }
            ]
        },
    )
    calls = []
    responses = iter(
        [Response({"id": "session"}), Response({"turn": {"id": "turn"}}), Response({"ok": True})]
    )

    def post(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        return next(responses)

    monkeypatch.setattr(hooks.httpx, "post", post)
    assert hooks.flush_spooled_turns(project(), "http://api", "codex") == 1
    begin_payload = next(payload for url, payload in calls if url.endswith("/turns/begin"))
    assert begin_payload["off_record"] is True
    assert "spooled_turns" not in hooks.read_state(path)


def test_off_record_capture_survives_offline_spool_and_later_session_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())

    def offline_after_session(url, **kwargs):
        if url.endswith("/sessions"):
            return Response({"id": "session", "off_record": True})
        if url.endswith("/turns/begin"):
            raise RuntimeError("offline")
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(hooks.httpx, "post", offline_after_session)
    hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "session_id": "s1",
            "message_id": "m1",
            "prompt": "VPS_PASSWORD=must-never-reappear",
        },
    )
    hooks.user_prompt_submit()
    path = hooks.state_path("p1", "codex", "s1")
    assert hooks.read_state(path)["spooled_active"]["off_record"] is True

    hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Configured"},
    )
    hooks.stop()

    calls = []

    def online_after_privacy_reset(url, **kwargs):
        calls.append((url, kwargs.get("json")))
        if url.endswith("/sessions"):
            return Response({"id": "session", "off_record": False})
        if url.endswith("/turns/begin"):
            return Response({"turn": {"id": "turn"}})
        if url.endswith("/stop-check"):
            return Response({"ok": True})
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(hooks.httpx, "post", online_after_privacy_reset)
    assert hooks.flush_spooled_turns(project(), "http://api", "codex") == 1
    begin_payload = next(payload for url, payload in calls if url.endswith("/turns/begin"))
    assert begin_payload["off_record"] is True
    assert "spooled_turns" not in hooks.read_state(path)


def test_artifact_registration_excludes_secrets_and_retired_hooks_are_absent(
    monkeypatch, tmp_path
):
    note = tmp_path / "note.txt"
    secret = tmp_path / ".env"
    note.write_text("release evidence")
    secret.write_text("TOKEN=secret")
    calls = []
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda *args, **kwargs: calls.append(kwargs["json"]) or Response({"id": "a"}),
    )
    hooks.register_prompt_artifacts(
        "http://api", "p1", "t1", {"attachments": [str(note), str(secret)]}
    )
    assert calls[0]["filename"] == "note.txt"
    assert len(calls) == 1
    assert not any(
        hasattr(hooks, name)
        for name in ("pre_tool_use", "pre_compact", "post_compact", "post_tool_use")
    )


def test_hook_helpers_handle_invalid_input_artifacts_and_retry_queues(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks.sys, "stdin", io.StringIO("not-json"))
    assert hooks.read_input() == {}
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
    assert hooks.client_name({}) == "claude"
    monkeypatch.setenv("PLUGIN_ROOT", "/plugin")
    assert hooks.client_name({}) == "codex"

    assert hooks._safe_attachment_body({}, "turn") is None
    binary = tmp_path / "image.bin"
    binary.write_bytes(b"data")
    body = hooks._safe_attachment_body({"path": str(binary), "kind": "image"}, "turn")
    assert body["kind"] == "image" and body["extracted_text"] == ""

    calls = []
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda *args, **kwargs: (
            calls.append((args, kwargs)) or (_ for _ in ()).throw(hooks.httpx.HTTPError("offline"))
        ),
    )
    hooks.register_prompt_artifacts("http://api", "p1", "t1", {"attachments": [str(binary)]})
    hooks.register_prompt_artifacts("http://api", "p1", "t1", {"attachments": "not-a-list"})
    assert len(calls) == 1

    state = {"pending_commits": [{"turn_id": "a", "payload": {}}, "invalid"]}
    assert hooks.flush_pending_commits("http://api", state) == 0
    assert state["pending_commits"][0]["turn_id"] == "a"
    hooks.queue_pending_commit(state, "a", {"assistant_response": "new"})
    assert state["pending_commits"] == [{"turn_id": "a", "payload": {"assistant_response": "new"}}]


def test_prompt_fragment_identifiers_and_active_spool_are_idempotent(monkeypatch):
    assert (
        hooks.prompt_event_id(
            {"message_id": "message-1"}, session_id="s", turn_id="t", prompt="same"
        )
        == "message-1"
    )
    fallback = hooks.prompt_event_id({}, session_id="s", turn_id="t", prompt="same")
    repeated = hooks.prompt_event_id({}, session_id="s", turn_id="t", prompt="same")
    assert fallback != repeated
    assert fallback.startswith("prompt-") and repeated.startswith("prompt-")
    assert (
        len(
            hooks.prompt_event_id(
                {"message_id": "x" * 600}, session_id="s", turn_id="t", prompt="same"
            )
        )
        < 500
    )

    state = {}
    kwargs = {
        "session_external_id": "s",
        "external_turn_id": "t",
        "prompt_event_id": "m1",
        "user_prompt": "First",
        "binding_id": "binding",
        "off_record": False,
    }
    hooks.stage_spooled_prompt(state, **kwargs)
    hooks.stage_spooled_prompt(state, **kwargs)
    assert len(state["spooled_active"]["prompt_fragments"]) == 1
    hooks.stage_spooled_prompt(
        state,
        **{
            **kwargs,
            "prompt_event_id": "m2",
            "user_prompt": "Private correction",
            "off_record": True,
        },
    )
    assert state["spooled_active"]["off_record"] is True
    with pytest.raises(ValueError, match="conflicts"):
        hooks.stage_spooled_prompt(state, **{**kwargs, "user_prompt": "Different"})

    calls = []
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda url, **request: (
            calls.append((url, request["json"])) or Response({"turn": {"id": "turn"}})
        ),
    )
    assert (
        hooks.flush_spooled_active_prompts(
            project(),
            "http://api",
            state,
            session_id="session",
            session_external_id="other",
            external_turn_id="t",
        )
        is False
    )
    assert hooks.flush_spooled_active_prompts(
        project(),
        "http://api",
        state,
        session_id="session",
        session_external_id="s",
        external_turn_id="t",
    )
    assert calls[0][1]["prompt_event_id"] == "m1"
    assert "spooled_active" not in state


def test_codex_cursor_is_captured_once_for_all_steering_in_the_turn(monkeypatch, tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    calls = []

    def capture(path, *, session_id, turn_id):
        calls.append((path, session_id, turn_id))
        return {
            "version": "test",
            "session_id": session_id,
            "turn_id": turn_id,
            "offset": len(calls),
        }

    monkeypatch.setattr(hooks, "capture_codex_transcript_cursor", capture)
    payload = {"client": "codex", "transcript_path": str(transcript)}
    state = {}

    assert hooks.stage_codex_transcript_cursor(
        payload, state, external_session="session", external_turn="turn-1"
    )
    first = dict(state["codex_transcript_cursor"])
    assert hooks.stage_codex_transcript_cursor(
        payload, state, external_session="session", external_turn="turn-1"
    )
    assert state["codex_transcript_cursor"] == first
    assert hooks.stage_codex_transcript_cursor(
        payload, state, external_session="session", external_turn="turn-2"
    )
    assert state["codex_transcript_cursor"]["turn_id"] == "turn-2"
    assert calls == [
        (transcript, "session", "turn-1"),
        (transcript, "session", "turn-2"),
    ]


def test_spool_recovery_keeps_incomplete_turns_and_setup_notice_recovers(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(
        path,
        {
            "spooled_turns": [
                {"session_external_id": "s1", "external_turn_id": "m1", "user_prompt": "pending"},
                {
                    "session_external_id": "s1",
                    "external_turn_id": "m2",
                    "user_prompt": "fails",
                    "assistant_response": "answer",
                },
            ]
        },
    )
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert hooks.flush_spooled_turns(project(), "http://api", "codex") == 0
    assert len(hooks.read_state(path)["spooled_turns"]) == 2

    opened = []
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: opened.append(command) or None)
    assert hooks.setup_notice_path("p1", "codex") != hooks.setup_notice_path("p1", "claude")
    assert hooks.request_setup_once("p1", "claude", tmp_path) is True
    assert hooks.request_setup_once("p1", "claude", tmp_path) is False
    assert hooks.setup_is_pending("p1", "claude") is True
    assert "still waiting" in hooks.setup_handoff_notice("claude", opened=False)
    assert "page opened" in hooks.setup_handoff_notice("claude", opened=True)
    hooks.clear_setup_notice("p1", "claude")
    assert hooks.setup_is_pending("p1", "claude") is False
    assert hooks.request_setup_once("p1", "claude", tmp_path) is True
    assert len(opened) == 2


def test_hook_memory_notices_and_degraded_paths_never_block_work(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    assert hooks._memory_notice({"memory_status": {"available": True}}) == ""
    assert "retry" in hooks._memory_notice(
        {"memory_status": {"available": False, "summary": "Retry later"}}
    )
    context = hooks._context_for_model({"project": {"name": "P"}, "tasks": ["t"]})
    assert context["project"]["name"] == "P" and context["memory_status"] == {}
    stale = {"memory_status": {"state": "connection_required", "provider": "claude"}}
    assert hooks._scope_memory_status(stale, "codex") == stale["memory_status"]

    monkeypatch.setattr(hooks, "load_project", lambda _: (_ for _ in ()).throw(FileNotFoundError()))
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "prompt": "Continue"})
    hooks.user_prompt_submit()
    assert json.loads(output.getvalue()) == {"continue": True}  # No memory outage or spool.

    output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "client": "claude", "assistant_response": "Answer"}
    )
    hooks.stop()
    assert json.loads(output.getvalue()) == {"suppressOutput": True}


def test_hook_recovery_paths_cover_tty_setup_failures_and_provider_notice_cleanup(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")

    class TtyInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(hooks.sys, "stdin", TtyInput())
    assert hooks.read_input() == {}

    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable"))
    )
    assert hooks.request_setup_once("p1", "claude", tmp_path) is False
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: type("Result", (), {"returncode": 1})()
    )
    assert hooks.request_setup_once("p1", "claude", tmp_path) is False

    large = tmp_path / "too-large.txt"
    large.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    assert hooks._safe_attachment_body({"path": str(large)}, "turn") is None

    state = {"pending_commits": [{"turn_id": "turn-1", "payload": {"assistant_response": "saved"}}]}
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"ok": True}))
    assert hooks.flush_pending_commits("http://api", state) == 1
    assert "pending_commits" not in state

    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    hooks.write_state(hooks.setup_notice_path("p1", "codex"), {"pending": True})
    hooks.write_state(hooks.setup_notice_path("p1", "claude"), {"pending": True})
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"id": "session"}))
    monkeypatch.setattr(
        hooks.httpx,
        "get",
        lambda *args, **kwargs: Response({"project": {}, "memory_status": {"state": "updated"}}),
    )
    output = hook_input(
        monkeypatch, {"cwd": str(tmp_path), "client": "codex", "session_id": "clean"}
    )
    hooks.session_start()
    assert (
        "Memory consolidation is asynchronous"
        not in json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    )
    assert hooks.setup_is_pending("p1", "codex") is False
    assert hooks.setup_is_pending("p1", "claude") is True

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("start failed")),
    )
    output = hook_input(monkeypatch, {"cwd": str(tmp_path)})
    hooks.session_start()
    assert "temporarily unavailable" in json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]

    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    responses = iter(
        [
            Response({"id": "session"}),
            Response(
                {
                    "turn": {"id": "turn-connection"},
                    "project": {},
                    "tasks": [],
                    "memories": [],
                    "memory_status": {"state": "connection_required", "provider": "claude"},
                }
            ),
        ]
    )
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(hooks, "request_setup_once", lambda *args, **kwargs: True)
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "client": "claude", "prompt": "Resume"})
    hooks.user_prompt_submit()
    rendered = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "Before project work ask whether to fix it now" in rendered
    assert "A local Setup page opened" not in rendered


def test_founder_projection_ignores_dirty_optional_rows_and_keeps_actionable_health() -> None:
    assert hooks._project_context_for_model("not-a-project") == {}
    assert hooks._prompt_mentions({}, "   ") is False
    assert hooks._memory_fragment({}, reason="direct") is None
    assert hooks._context_excerpt("x" * 1_000, 100) == ("x" * 99) + "…"

    briefing = {
        "project": {
            "id": "project-1",
            "name": "Example Workspace",
            "ignored_backup_field": "must-not-leak",
        },
        "plans": [
            "invalid-plan",
            {},
            {
                "id": "plan-executing",
                "title": "Release architecture",
                "status": "executing",
                "content_excerpt": "Complete the rollout safely.",
            },
        ],
        "tasks": [
            None,
            {},
            {
                "id": "task-blocked",
                "title": "Repair task index",
                "status": "blocked",
                "truncated_fields": ["description"],
            },
        ],
        "retrieval": {
            "status": "degraded",
            "metadata": {
                "selection_mode": "history_fallback",
                "selected_reasons": {"memory-direct": "exact_reference"},
            },
        },
        "memories": [
            "invalid-memory",
            {},
            {
                "id": "memory-direct",
                "node_type": "decision",
                "node_key": "release",
                "text": "Protect production.",
            },
            {
                "id": "memory-direct",
                "node_type": "decision",
                "text": "Duplicate must not be emitted.",
            },
        ],
        "recent_handoffs": [
            "invalid-handoff",
            {},
            {
                "id": "handoff-1",
                "summary": "Continue from the verified release gate.",
            },
        ],
        "task_index_status": "degraded",
        "task_indexing_pending": True,
        "dashboard_url": "http://127.0.0.1:20003/?tab=tasks",
    }

    fragments = hooks.founder_context_fragments(
        briefing,
        contracts=hooks.TURN_CONTRACT,
        prompt="continue",
    )
    by_reference = {item.reference: item for item in fragments}

    assert "ignored_backup_field" not in by_reference["project-1"].payload
    assert by_reference["plan-executing"].priority == 20
    assert by_reference["plan-executing"].payload["url"].endswith(
        "?tab=tasks&view=plans&plan=plan-executing"
    )
    assert by_reference["task-blocked"].priority == 22
    assert by_reference["task-blocked"].payload["url"].endswith(
        "?tab=tasks&work=task-blocked"
    )
    assert by_reference["task-blocked"].payload["full_task"] == {
        "tool": "get_task",
        "task_id": "task-blocked",
    }
    assert by_reference["memory-direct"].priority == 28
    assert by_reference["memory-direct"].payload["text"] == "Protect production."
    assert by_reference["memory-retrieval"].component == "health"
    assert by_reference["task-index"].payload == {
        "task_index_status": "degraded",
        "indexing_pending": True,
    }
    assert by_reference["handoff-1"].component == "handoffs"
    assert by_reference["work-dashboard"].component == "tasks"
    assert sum(item.reference == "memory-direct" for item in fragments) == 1


def test_founder_relevance_uses_explicit_id_or_complete_title_mentions() -> None:
    plans = hooks.founder_context_fragments(
        {
            "plans": [
                {"id": "plan-a", "title": "Unrelated release", "status": "decided"},
                {"id": "plan-b", "title": "Android launch", "status": "decided"},
            ],
            "tasks": [
                {"id": "task-a", "title": "Tiny", "status": "todo"},
                {"id": "task-b", "title": "Database migration", "status": "todo"},
            ],
        },
        contracts=hooks.TURN_CONTRACT,
        prompt="Open plan-a and review the Database migration",
    )
    by_reference = {item.reference: item for item in plans}

    assert by_reference["plan-a"].priority == 20
    assert by_reference["plan-b"].priority == 55
    assert by_reference["task-a"].priority == 60
    assert by_reference["task-b"].priority == 22


def test_legacy_component_metadata_is_bounded_deduplicated_and_exact() -> None:
    briefing = {
        "project": {"id": "project-1"},
        "tasks": [{"id": "task-1"}, {"id": "task-1"}, "legacy-row"],
        "memories": [],
        "memory_status": {},
        "backup": None,
    }
    metadata = hooks.briefing_component_metadata(briefing)

    assert metadata["profile"] == {"item_count": 1, "references": ["project-1"]}
    assert metadata["tasks"] == {
        "item_count": 3,
        "references": ["task-1", "task-1"],
    }
    assert metadata["memories"] == {"item_count": 0, "references": []}
    assert metadata["health"]["item_count"] == 0

    context = "legacy context"
    observation = hooks.measured_context(
        context,
        event_id="legacy-metadata",
        operation="context.turn_injection",
        scope="automatic",
        client="codex",
        component_values={"tasks": "task", "unknown": "ignored"},
        component_metadata={
            "tasks": {
                "item_count": -2,
                "candidate_item_count": -4,
                "partial_item_count": 0,
                "omitted_item_count": -2,
                "references": ["task-1", "task-1", ""],
                "omitted_references": ["task-2", "task-2", ""],
            }
        },
    )
    task_manifest = next(item for item in observation["components"] if item["name"] == "tasks")
    assert task_manifest["item_count"] == 0
    assert task_manifest["candidate_item_count"] == 0
    assert task_manifest["omitted_item_count"] == 0
    assert task_manifest["references"] == ["task-1"]
    assert task_manifest["omitted_references"] == ["task-2"]
    assert "unknown" not in observation["component_bytes"]
    assert sum(observation["component_bytes"].values()) == observation["utf8_bytes"]


def test_measurement_rejects_a_different_render_and_accounts_unknown_composed_components() -> None:
    composed = hooks.compose_context(
        [
            hooks.ContextFragment(
                component="custom",
                priority=1,
                reference="custom-1",
                payload={"text": "model-visible"},
            )
        ]
    )
    with pytest.raises(ValueError, match="must equal the composed founder brief"):
        hooks.measured_context(
            "different",
            event_id="wrong-render",
            operation="context.turn_injection",
            scope="automatic",
            client="codex",
            composed=composed,
        )

    observation = hooks.measured_context(
        composed.content,
        event_id="custom-component",
        operation="context.turn_injection",
        scope="automatic",
        client="codex",
        composed=composed,
    )
    assert observation["component_bytes"] == {"overhead": observation["utf8_bytes"]}
    assert observation["components"][0]["name"] == "overhead"
    assert observation["budget"]["included_items"] == 1


def test_sentence_boundary_excerpt_and_nested_session_recovery_remain_safe(
    monkeypatch, tmp_path
) -> None:
    text = ("a" * 60) + ". " + ("b" * 60)
    assert hooks._context_excerpt(text, 100) == ("a" * 60) + "…"

    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("launcher unavailable")),
    )
    monkeypatch.setattr(
        hooks,
        "load_project",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("state unavailable")),
    )
    output = hook_input(monkeypatch, {"cwd": str(tmp_path), "session_id": "recovery"})

    hooks.session_start()

    fallback = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "temporarily unavailable" in fallback
    assert context_units(fallback) <= 9_000


def test_stop_spools_an_offline_turn_without_inventing_context_observations(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(
        path,
        {
            "api": "http://api",
            "spooled_active": {
                "session_external_id": "s1",
                "external_turn_id": "m1",
                "user_prompt": "Question",
            },
        },
    )
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Answer"},
    )

    hooks.stop()

    state = hooks.read_state(path)
    assert state["spooled_turns"] == [
        {
            "session_external_id": "s1",
            "external_turn_id": "m1",
            "user_prompt": "Question",
            "assistant_response": "Answer",
        }
    ]
    assert "context_observations" not in state["spooled_turns"][0]
    assert json.loads(output.getvalue()) == {"continue": True}


def test_connection_required_without_an_open_or_pending_setup_adds_no_false_notice(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", lambda *args, **kwargs: Response({"id": "session"}))
    monkeypatch.setattr(
        hooks.httpx,
        "get",
        lambda *args, **kwargs: Response(
            {
                "project": {"id": "p1", "name": "ExampleApp"},
                "memory_status": {"state": "connection_required", "provider": "codex"},
            }
        ),
    )
    monkeypatch.setattr(hooks, "request_setup_once", lambda *args, **kwargs: False)
    monkeypatch.setattr(hooks, "setup_is_pending", lambda *args, **kwargs: False)
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "client": "codex", "session_id": "no-setup"},
    )

    hooks.session_start()

    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "A local Setup page opened" not in context
    assert "Setup is still waiting" not in context
    assert founder_items(context, "profile")[0]["payload"]["name"] == "ExampleApp"


def test_stop_with_no_active_or_spooled_turn_is_a_clean_noop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(path, {"api": "http://api", "session_id": "session"})
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "assistant_response": "Nothing active"},
    )

    hooks.stop()

    assert hooks.read_state(path) == {"api": "http://api", "session_id": "session"}
    assert json.loads(output.getvalue()) == {"continue": True}


def test_large_operational_manual_is_required_head_tail_context_with_pointer():
    manual = {
        "version": 7,
        "content": "HEAD-RULE\n" + ("operational constraint " * 900) + "\nTAIL-RULE",
    }
    briefing = {
        "operational_manual": manual,
        "project": {"id": "p1", "name": "Project", "context": "profile" * 500},
        "tasks": [
            {"id": f"task-{index}", "title": "task " + ("detail " * 300), "version": 1}
            for index in range(20)
        ],
        "memories": [{"id": f"memory-{index}", "text": "memory " * 500} for index in range(10)],
    }
    composed = hooks.compose_founder_context(briefing, contracts="contract")
    items = founder_items(composed.content, "manual")
    assert len(items) == 1
    assert items[0]["delivery"] == "partial"
    payload = items[0]["payload"]
    assert payload["content_head"].startswith("HEAD-RULE")
    assert payload["content_tail"].endswith("TAIL-RULE")
    assert payload["full_manual"] == {"tool": "get_project_manual"}
    lines = [json.loads(line) for line in composed.content.splitlines()[:-1]]
    assert next(i for i, item in enumerate(lines) if item["component"] == "manual") < next(
        i for i, item in enumerate(lines) if item["component"] == "profile"
    )


def test_compact_operational_manual_fits_alongside_a_dense_founder_brief():
    content = "M" * 4_000
    briefing = {
        "project": {
            "id": "p1",
            "name": "Project",
            "cause": "c" * 500,
            "context": "p" * 800,
            "objectives": ["o" * 200] * 3,
            "principles": ["r" * 200] * 3,
        },
        "operational_manual": {"version": 3, "content": content},
        "tasks": [
            {"id": f"task-{index}", "title": "task " + ("x" * 200), "version": 1}
            for index in range(4)
        ],
        "memories": [
            {"id": f"memory-{index}", "text": "memory " + ("y" * 300)} for index in range(4)
        ],
    }

    composed = hooks.compose_founder_context(
        briefing,
        contracts=hooks.COFOUNDER_CONTRACT + hooks.TASK_CONTRACT,
    )
    manual = founder_items(composed.content, "manual")[0]

    assert len(composed.content.encode("utf-8")) <= hooks.DEFAULT_CONTEXT_BUDGET
    assert not composed.fallback_used
    assert manual["delivery"] in {"full", "partial"}
    assert founder_items(composed.content, "profile")
    assert founder_items(composed.content, "tasks") or founder_items(
        composed.content, "memories"
    )


def test_unchanged_briefing_reuses_manual_without_reinjecting_it(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text('id = "p1"\nname = "ExampleApp"\napi_port = 8765\nweb_port = 4173\n')
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    manual = {"version": 3, "content": "Never deploy without the release gate."}
    briefing = {
        "project": {"id": "p1", "name": "ExampleApp"},
        "operational_manual": manual,
        "plans": [],
        "tasks": [],
        "task_counts": {},
        "task_indexing_pending": False,
        "task_index_status": "ready",
        "memory_status": {},
    }
    binding = hooks.ProjectBinding(
        project_id="p1",
        name="ExampleApp",
        root_path=tmp_path,
        kind="local",
        api_url="http://127.0.0.1:8765",
        dashboard_url="http://127.0.0.1:4173",
        binding_id="b" * 64,
        api_port=8765,
        web_port=4173,
    )
    monkeypatch.setattr(hooks, "_binding_for_project", lambda *_args, **_kwargs: binding)
    full_context = {**briefing, "dashboard_url": binding.dashboard_link("tasks")}
    full_composed = hooks.compose_founder_context(
        full_context,
        contracts=hooks.COFOUNDER_CONTRACT + hooks.TASK_CONTRACT,
    )
    state = {}
    hooks._store_delivery_baseline(
        state,
        context=full_context,
        composed=full_composed,
        binding_id=binding.binding_id,
    )
    path = hooks.state_path("p1", "codex", "s1")
    hooks.write_state(path, state)

    def post(url, **kwargs):
        if url.endswith("/sessions"):
            return Response({"id": "session"})
        if url.endswith("/turns/begin"):
            return Response({"turn": {"id": "turn"}, **briefing})
        raise AssertionError(url)

    monkeypatch.setattr(hooks.httpx, "post", post)
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "session_id": "s1", "message_id": "m1", "prompt": "Ship"},
    )
    hooks.user_prompt_submit()
    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert founder_items(context, "manual") == []
    assert founder_items(context, "profile") == []
    state = hooks.read_state(path)
    observation = state["pending_context_observations"][-1]
    names = {item["name"] for item in observation["components"]}
    assert "manual" not in names
    assert observation["delivery"]["kind"] == "delta"
    assert observation["delivery"]["reason"] == "unchanged"
    assert observation["delivery"]["reused_utf8_bytes"] > 0


def test_remote_session_start_is_authenticated_and_never_starts_local_docker(monkeypatch, tmp_path):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'version = 2\nid = "remote"\nname = "Remote"\nbinding = "remote"\n'
        'api_url = "https://203.0.113.10/api"\n'
    )
    binding = hooks.ProjectBinding(
        project_id="remote",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="a" * 64,
        bearer_token="remote-token",
    )
    monkeypatch.setattr(hooks, "binding_from_project", lambda *args, **kwargs: binding)
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    process_calls = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: process_calls.append(args[0]))
    http_calls = []

    def post(url, **kwargs):
        http_calls.append(("POST", url, kwargs))
        return Response({"id": "session"})

    def get(url, **kwargs):
        http_calls.append(("GET", url, kwargs))
        return Response(
            {
                "project": {"id": "remote", "name": "Remote"},
                "operational_manual": {"version": 1, "content": "Remote invariant"},
                "memory_status": {},
            }
        )

    monkeypatch.setattr(hooks.httpx, "post", post)
    monkeypatch.setattr(hooks.httpx, "get", get)
    output = hook_input(
        monkeypatch,
        {"cwd": str(tmp_path), "client": "codex", "session_id": "remote-session"},
    )
    hooks.session_start()
    assert process_calls == []
    assert http_calls[0][1] == "https://203.0.113.10/api/projects/remote/sessions"
    assert all(call[2]["headers"]["Authorization"] == "Bearer remote-token" for call in http_calls)
    assert all(call[2]["headers"]["X-DDUO-Binding-ID"] == "a" * 64 for call in http_calls)
    assert founder_items(json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"], "manual")


@pytest.mark.parametrize("event", ["session_start", "user_prompt_submit"])
def test_remote_outage_uses_only_the_last_authenticated_manual(monkeypatch, tmp_path, event):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'version = 2\nid = "remote"\nname = "Remote"\nbinding = "remote"\n'
        'api_url = "https://203.0.113.10/api"\n'
    )
    binding = hooks.ProjectBinding(
        project_id="remote",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="d" * 64,
        bearer_token="remote-token",
    )
    monkeypatch.setattr(hooks, "binding_from_project", lambda *args, **kwargs: binding)
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manual_cache, "MANUAL_CACHE_DIR", tmp_path / "manual-cache")
    manual_cache.store_verified_manual(
        binding,
        {"version": 8, "content": "Production deploys require a verified smoke test."},
    )
    process_calls = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: process_calls.append(args[0]))

    def unavailable(*args, **kwargs):
        request = httpx.Request("POST", "https://203.0.113.10/api")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(hooks.httpx, "post", unavailable)
    payload = {"cwd": str(tmp_path), "client": "codex", "session_id": "remote-session"}
    if event == "user_prompt_submit":
        payload.update({"message_id": "turn-1", "prompt": "Continue the release"})
    output = hook_input(monkeypatch, payload)
    getattr(hooks, event)()

    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    manual = founder_items(context, "manual")
    assert manual[0]["payload"]["version"] == 8
    assert "verified smoke test" in context
    assert "last verified" in context
    assert "Do not start a local dDuo stack" in context
    assert process_calls == []
    state = hooks.read_state(hooks.state_path("remote", "codex", "remote-session"))
    observation = state["pending_context_observations"][-1]
    assert {item["name"] for item in observation["components"]} >= {
        "manual",
        "health",
    }


def test_remote_outage_does_not_repeat_manual_already_present_in_live_session(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / ".dduo-solo-founder/project.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'version = 2\nid = "remote"\nname = "Remote"\nbinding = "remote"\n'
        'api_url = "https://203.0.113.10/api"\n'
    )
    binding = hooks.ProjectBinding(
        project_id="remote",
        name="Remote",
        root_path=tmp_path,
        kind="remote",
        api_url="https://203.0.113.10/api",
        dashboard_url=None,
        binding_id="d" * 64,
        bearer_token="remote-token",
    )
    monkeypatch.setattr(hooks, "binding_from_project", lambda *args, **kwargs: binding)
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manual_cache, "MANUAL_CACHE_DIR", tmp_path / "manual-cache")
    manual = {"version": 8, "content": "Production deploys require a verified smoke test."}
    manual_cache.store_verified_manual(binding, manual)

    full_context = {
        "project": {"id": "remote", "name": "Remote"},
        "operational_manual": manual,
        "plans": [],
        "tasks": [],
        "task_counts": {},
        "task_indexing_pending": False,
        "task_index_status": "ready",
        "recent_handoffs": [],
        "memory_status": {},
        "dashboard_url": binding.dashboard_link("tasks"),
    }
    composed = hooks.compose_founder_context(
        full_context,
        contracts="",
        standing_contracts=hooks.COFOUNDER_CONTRACT + hooks.TASK_CONTRACT,
    )
    state = {
        "session_id": "internal-session",
        "session_off_record": False,
        "binding_id": binding.binding_id,
    }
    hooks._store_delivery_baseline(
        state,
        context=full_context,
        composed=composed,
        binding_id=binding.binding_id,
    )
    path = hooks.state_path("remote", "codex", "remote-session")
    hooks.write_state(path, state)

    def unavailable(*args, **kwargs):
        request = httpx.Request("POST", "https://203.0.113.10/api")
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(hooks.httpx, "post", unavailable)
    output = hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "client": "codex",
            "session_id": "remote-session",
            "message_id": "message-1",
            "prompt": "Continue the release",
        },
    )
    hooks.user_prompt_submit()

    context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert founder_items(context, "manual") == []
    assert "already present in this live session" in context
    observation = hooks.read_state(path)["pending_context_observations"][-1]
    assert "manual" not in {item["name"] for item in observation["components"]}


def test_client_telemetry_flush_acknowledges_success_and_retries_failures(monkeypatch):
    class Spool:
        def __init__(self):
            self.items = [{"event_id": "one"}, {"event_id": "two"}]
            self.acknowledged = []

        def peek(self, project_id, *, limit=100):
            return list(self.items)

        def acknowledge(self, project_id, event_ids):
            self.acknowledged.append((project_id, event_ids))
            return len(event_ids)

    spool = Spool()
    monkeypatch.setattr(hooks, "ClientTelemetrySpool", lambda: spool)
    calls = []
    monkeypatch.setattr(
        hooks,
        "_request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response({}),
    )
    hooks.flush_client_telemetry("http://api", "p1")
    assert calls[0][0][2].endswith("/observability/client-events/batch")
    assert calls[0][1]["json"] == {"items": spool.items}
    assert spool.acknowledged == [("p1", {"one", "two"})]

    spool.items = []
    hooks.flush_client_telemetry("http://api", "p1")
    assert len(calls) == 1

    spool.items = [{"event_id": "retry"}]
    monkeypatch.setattr(
        hooks,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    hooks.flush_client_telemetry("http://api", "p1")
    assert spool.acknowledged == [("p1", {"one", "two"})]


def test_client_telemetry_flush_drains_the_full_outbox_in_one_request(monkeypatch):
    class Spool:
        def __init__(self):
            self.items = [{"event_id": f"event-{index}"} for index in range(218)]

        def peek(self, _project_id, *, limit=100):
            return [dict(item) for item in self.items[:limit]]

        def acknowledge(self, _project_id, event_ids):
            before = len(self.items)
            self.items = [item for item in self.items if item["event_id"] not in event_ids]
            return before - len(self.items)

    spool = Spool()
    batches = []
    monkeypatch.setattr(hooks, "ClientTelemetrySpool", lambda: spool)
    monkeypatch.setattr(
        hooks,
        "_request",
        lambda *_args, **kwargs: batches.append(kwargs["json"]["items"]) or Response({}),
    )

    hooks.flush_client_telemetry("http://api", "p1")

    assert [len(batch) for batch in batches] == [218]
    assert spool.items == []


def test_codex_stop_usage_is_correlated_and_staged_as_content_free_requests(monkeypatch, tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    calls = []

    class RequestUsage:
        def client_event(self, session_id):
            calls.append(("event", session_id))
            return {
                "kind": "agent_usage",
                "event_id": "agent.codex.request-1",
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "measurement_source": "provider_reported",
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "cache_write_input_tokens": 0,
                "output_tokens": 10,
                "reasoning_tokens": 2,
                "reported_total_tokens": 110,
                "occurred_at": "2026-08-30T20:00:00+00:00",
            }

    class TurnUsage:
        requests = (RequestUsage(),)

    class Spool:
        def enqueue_many(self, project_id, events):
            staged = list(events)
            calls.extend(("enqueue", project_id, event) for event in staged)
            return len(staged)

    parser_calls = []
    monkeypatch.setattr(
        hooks,
        "read_codex_transcript_usage_result",
        lambda path, **kwargs: parser_calls.append((path, kwargs))
        or type("ReadResult", (), {"status": "complete", "usage": (TurnUsage(),)})(),
    )
    monkeypatch.setattr(hooks, "ClientTelemetrySpool", Spool)

    accepted = hooks.enqueue_codex_turn_usage(
        {"client": "codex", "transcript_path": str(transcript), "turn_id": "turn-external"},
        {
            "external_turn_id": "turn-external",
            "session_id": "session-internal",
            "turn_id": "turn-internal",
            "codex_transcript_cursor": {"turn_id": "turn-external"},
        },
        project_id="p1",
        external_session="session-external",
    )

    assert accepted == 1
    assert parser_calls == [
        (
            transcript,
            {
                "session_id": "session-external",
                "turn_id": "turn-external",
                "cursor": {"turn_id": "turn-external"},
            },
        )
    ]
    assert calls[0] == ("event", "session-external")
    staged = calls[1][2]
    assert calls[1][:2] == ("enqueue", "p1")
    assert staged["session_id"] == "session-internal"
    assert staged["turn_id"] == "turn-internal"
    assert "content" not in staged and "prompt" not in staged and "response" not in staged


def test_codex_usage_prefers_stop_turn_and_drops_a_stale_internal_turn_link(monkeypatch, tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    class RequestUsage:
        def client_event(self, _session_id):
            return {
                "kind": "agent_usage",
                "event_id": "agent.codex.current",
                "provider": "codex",
                "measurement_source": "provider_reported",
                "input_tokens": 1,
                "output_tokens": 1,
                "occurred_at": "2026-08-30T20:00:00+00:00",
            }

    captured = []
    monkeypatch.setattr(
        hooks,
        "read_codex_transcript_usage_result",
        lambda _path, **kwargs: type(
            "ReadResult",
            (),
            {
                "status": "complete",
                "usage": captured.append(kwargs)
                or (type("TurnUsage", (), {"requests": (RequestUsage(),)})(),),
            },
        )(),
    )
    monkeypatch.setattr(
        hooks.ClientTelemetrySpool,
        "enqueue_many",
        lambda _self, _project, events: captured.extend(events) or 1,
    )
    hooks.enqueue_codex_turn_usage(
        {"client": "codex", "transcript_path": str(transcript), "turn_id": "current"},
        {
            "external_turn_id": "stale",
            "session_id": "session-internal",
            "turn_id": "stale-internal",
            "codex_transcript_cursor": {"turn_id": "current"},
        },
        project_id="p1",
        external_session="session-external",
    )
    assert captured[0]["turn_id"] == "current"
    assert captured[1]["session_id"] == "session-internal"
    assert "turn_id" not in captured[1]


def test_empty_codex_usage_retries_once_then_consumes_the_source(monkeypatch):
    source = {
        "source_id": "source-1",
        "transcript_path": "/private/transcript.jsonl",
        "external_session": "session",
        "external_turn": "turn",
        "cursor": {"version": "cursor"},
    }
    state = {"pending_codex_usage": [source]}
    monkeypatch.setattr(
        hooks,
        "read_codex_transcript_usage_result",
        lambda *_args, **_kwargs: type(
            "ReadResult", (), {"status": "complete", "usage": ()}
        )(),
    )

    assert hooks.drain_pending_codex_usage(state, project_id="p1") == (0, False)
    assert state["pending_codex_usage"][0]["empty_attempts"] == 1
    assert hooks.drain_pending_codex_usage(state, project_id="p1") == (1, False)
    assert "pending_codex_usage" not in state


def test_retryable_codex_transcript_error_never_consumes_the_source(monkeypatch):
    source = {
        "source_id": "source-1",
        "transcript_path": "/private/transcript.jsonl",
        "external_session": "session",
        "external_turn": "turn",
        "cursor": {"version": "cursor"},
    }
    state = {"pending_codex_usage": [source]}
    monkeypatch.setattr(
        hooks,
        "read_codex_transcript_usage_result",
        lambda *_args, **_kwargs: type(
            "ReadResult", (), {"status": "retryable_unavailable", "usage": ()}
        )(),
    )

    assert hooks.drain_pending_codex_usage(state, project_id="p1") == (0, False)
    assert state == {"pending_codex_usage": [source]}


def test_retryable_codex_source_does_not_block_later_turns(monkeypatch):
    retryable = {"source_id": "retryable"}
    ready = {"source_id": "ready"}
    state = {"pending_codex_usage": [retryable, ready]}
    calls = []

    def enqueue(source, *, project_id):
        calls.append((source["source_id"], project_id))
        if source["source_id"] == "retryable":
            raise RuntimeError("Codex transcript usage is temporarily unavailable")
        return 1

    monkeypatch.setattr(hooks, "enqueue_pending_codex_usage", enqueue)

    assert hooks.drain_pending_codex_usage(state, project_id="p1") == (1, False)
    assert calls == [("retryable", "p1"), ("ready", "p1")]
    assert state == {"pending_codex_usage": [retryable]}


def test_stop_persists_memory_state_before_slow_or_failing_usage_work(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    monkeypatch.setattr(hooks, "flush_client_telemetry", lambda *args: None)
    path = hooks.state_path("p1", "codex", "session")
    hooks.write_state(
        path,
        {
            "turn_id": "turn-internal",
            "session_id": "session-internal",
            "external_turn_id": "turn-external",
            "api": "http://127.0.0.1:8765",
            "codex_transcript_cursor": {
                "session_id": "session",
                "turn_id": "turn-external",
            },
        },
    )
    calls = []
    monkeypatch.setattr(
        hooks.httpx,
        "post",
        lambda url, **kwargs: calls.append((url, kwargs.get("json"))) or Response({"allow": True}),
    )

    def failing_usage(source, **_kwargs):
        persisted = hooks.read_state(path)
        assert "turn_id" not in persisted
        assert persisted["pending_codex_usage"][0]["source_id"] == source["source_id"]
        calls.append(("usage-after-persist", None))
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(hooks, "enqueue_pending_codex_usage", failing_usage)
    output = hook_input(
        monkeypatch,
        {
            "client": "codex",
            "cwd": str(tmp_path),
            "session_id": "session",
            "turn_id": "turn-external",
            "transcript_path": str(tmp_path / "rollout.jsonl"),
            "assistant_response": "Done",
        },
    )
    hooks.stop()
    assert calls[0][0].endswith("/turns/turn-internal/stop-check")
    assert calls[1][0] == "usage-after-persist"
    assert len(hooks.read_state(path)["pending_codex_usage"]) == 1
    assert json.loads(output.getvalue()) == {"continue": True}


def test_stop_retries_a_full_outbox_source_across_process_boundaries(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hooks, "load_project", lambda _: project())
    path = hooks.state_path("p1", "codex", "session")
    hooks.write_state(
        path,
        {
            "turn_id": "turn-internal",
            "session_id": "session-internal",
            "external_turn_id": "turn-external",
            "api": "http://127.0.0.1:8765",
            "codex_transcript_cursor": {
                "session_id": "session",
                "turn_id": "turn-external",
            },
        },
    )
    monkeypatch.setattr(hooks.httpx, "post", lambda *_args, **_kwargs: Response({"allow": True}))
    attempts = []

    def full(source, **_kwargs):
        attempts.append(("enqueue-full", source["source_id"]))
        raise RuntimeError("client telemetry outbox is full")

    def timed_out_upload(*_args, **_kwargs):
        # The production helper swallows a three-second network timeout. The
        # Stop path still retries locally once and leaves the source durable.
        attempts.append(("flush-timeout", None))

    monkeypatch.setattr(hooks, "enqueue_pending_codex_usage", full)
    monkeypatch.setattr(hooks, "flush_client_telemetry", timed_out_upload)
    first_output = hook_input(
        monkeypatch,
        {
            "client": "codex",
            "cwd": str(tmp_path),
            "session_id": "session",
            "turn_id": "turn-external",
            "transcript_path": str(tmp_path / "rollout.jsonl"),
            "assistant_response": "Done",
        },
    )
    hooks.stop()
    after_first = hooks.read_state(path)
    assert json.loads(first_output.getvalue()) == {"continue": True}
    assert "turn_id" not in after_first and "codex_transcript_cursor" not in after_first
    assert len(after_first["pending_codex_usage"]) == 1
    assert [item[0] for item in attempts] == ["enqueue-full", "flush-timeout", "enqueue-full"]

    # A fresh hook process reads only durable files. Idempotent enqueue success
    # is the precise point at which the retry source may be removed.
    attempts.clear()
    monkeypatch.setattr(
        hooks,
        "enqueue_pending_codex_usage",
        lambda source, **_kwargs: attempts.append(("enqueue-ok", source["source_id"])) or 1,
    )
    second_output = hook_input(
        monkeypatch,
        {
            "client": "codex",
            "cwd": str(tmp_path),
            "session_id": "session",
            "turn_id": "turn-external",
            "transcript_path": str(tmp_path / "rollout.jsonl"),
            "assistant_response": "",
        },
    )
    hooks.stop()
    assert json.loads(second_output.getvalue()) == {"continue": True}
    assert "pending_codex_usage" not in hooks.read_state(path)
    assert [item[0] for item in attempts] == ["enqueue-ok", "flush-timeout"]


def test_hook_state_merge_preserves_concurrent_queue_additions(tmp_path):
    path = tmp_path / "state.json"
    old = {
        "session_external_id": "session",
        "external_turn_id": "old",
        "binding_id": "binding",
    }
    concurrent = {
        "session_external_id": "session",
        "external_turn_id": "new",
        "binding_id": "binding",
    }
    before = {
        "spooled_turns": [old],
        "pending_context_observations": [{"event_id": "old-observation"}],
    }
    after = {}
    hooks.write_state(
        path,
        {
            "spooled_turns": [old, concurrent],
            "pending_context_observations": [
                {"event_id": "old-observation"},
                {"event_id": "new-observation"},
            ],
        },
    )
    merged = hooks.merge_state_update(path, before, after)
    assert merged["spooled_turns"] == [concurrent]
    assert merged["pending_context_observations"] == [{"event_id": "new-observation"}]


def test_hook_state_merge_preserves_concurrent_first_queue_additions(tmp_path):
    path = tmp_path / "state.json"
    concurrent = {"event_id": "concurrent-observation"}
    local = {"event_id": "local-observation"}
    hooks.write_state(path, {"pending_context_observations": [concurrent]})

    merged = hooks.merge_state_update(
        path,
        {},
        {"pending_context_observations": [local]},
    )

    assert merged["pending_context_observations"] == [concurrent, local]


def test_prompt_installs_new_turn_after_previous_stop_wins_the_merge_race(tmp_path):
    path = tmp_path / "state.json"
    before = {
        "external_turn_id": "turn-a",
        "turn_id": "internal-a",
        "prompt_event_id": "prompt-a",
        "codex_transcript_cursor": {"turn_id": "turn-a", "offset": 10},
        "pending_context_observations": [{"event_id": "context-a"}],
    }
    after = {
        **before,
        "external_turn_id": "turn-b",
        "turn_id": "internal-b",
        "prompt_event_id": "prompt-b",
        "codex_transcript_cursor": {"turn_id": "turn-b", "offset": 20},
        "pending_context_observations": [
            {"event_id": "context-a"},
            {"event_id": "context-b"},
        ],
    }
    hooks.write_state(
        path,
        {
            "external_turn_id": "turn-a",
            "prompt_event_id": "prompt-a",
            "pending_commits": [{"turn_id": "internal-a", "payload": {}}],
        },
    )

    merged = hooks.merge_state_update(path, before, after)

    assert merged["external_turn_id"] == "turn-b"
    assert merged["turn_id"] == "internal-b"
    assert merged["prompt_event_id"] == "prompt-b"
    assert merged["codex_transcript_cursor"] == {"turn_id": "turn-b", "offset": 20}
    assert merged["pending_context_observations"] == [{"event_id": "context-b"}]
    assert merged["pending_commits"] == [{"turn_id": "internal-a", "payload": {}}]


def test_previous_stop_cannot_remove_a_newer_turn_that_won_the_merge_race(tmp_path):
    path = tmp_path / "state.json"
    before = {
        "external_turn_id": "turn-a",
        "turn_id": "internal-a",
        "codex_transcript_cursor": {"turn_id": "turn-a", "offset": 10},
        "pending_context_observations": [{"event_id": "context-a"}],
    }
    after = {"external_turn_id": "turn-a"}
    hooks.write_state(
        path,
        {
            "external_turn_id": "turn-b",
            "turn_id": "internal-b",
            "codex_transcript_cursor": {"turn_id": "turn-b", "offset": 20},
            "pending_context_observations": [
                {"event_id": "context-a"},
                {"event_id": "context-b"},
            ],
        },
    )

    merged = hooks.merge_state_update(path, before, after)

    assert merged["external_turn_id"] == "turn-b"
    assert merged["turn_id"] == "internal-b"
    assert merged["codex_transcript_cursor"] == {"turn_id": "turn-b", "offset": 20}
    assert merged["pending_context_observations"] == [{"event_id": "context-b"}]
