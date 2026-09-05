from __future__ import annotations

import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from dduo_solo_founder import hooks


class Response:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def _founder_items(context: str, component: str) -> list[dict]:
    return [
        item
        for item in (json.loads(line) for line in context.splitlines())
        if item.get("component") == component
    ]


def _hook_input(monkeypatch: pytest.MonkeyPatch, payload: dict) -> io.StringIO:
    monkeypatch.setattr(hooks.sys, "stdin", io.StringIO(json.dumps(payload)))
    output = io.StringIO()
    monkeypatch.setattr(hooks.sys, "stdout", output)
    return output


def _emitted_context(output: io.StringIO, client: str) -> str:
    del client
    envelope = json.loads(output.getvalue())
    return envelope["hookSpecificOutput"]["additionalContext"]


def _configure_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    client: str,
) -> tuple[dict, dict[str, bool]]:
    config = tmp_path / ".dduo-solo-founder/project.toml"
    config.parent.mkdir()
    config.write_text('id = "p1"\nname = "Project"\napi_port = 8765\nweb_port = 4173\n')
    project = {
        "id": "p1",
        "name": "Project",
        "api_port": 8765,
        "web_port": 4173,
    }
    binding = hooks.ProjectBinding(
        project_id="p1",
        name="Project",
        root_path=tmp_path,
        kind="local",
        api_url="http://127.0.0.1:8765",
        dashboard_url="http://127.0.0.1:4173",
        binding_id="b" * 64,
        api_port=8765,
        web_port=4173,
    )
    briefing = {
        "project": {
            "id": "p1",
            "name": "Project",
            "cause": "Ship the right product",
            "principles": ["Protect production"],
        },
        "operational_manual": {
            "version": 4,
            "content": "Deploy only after the verified release gate.",
        },
        "plans": [{"id": "plan-1", "title": "Release safely", "version": 2}],
        "tasks": [
            {
                "id": "task-1",
                "title": "Run the release gate",
                "status": "in_progress",
                "version": 3,
            }
        ],
        "task_counts": {"total": 1, "nonterminal": 1, "selected": 1, "omitted": 0},
        "task_indexing_pending": False,
        "task_index_status": "ready",
        "memory_status": {"state": "updated", "summary": "Memory is current."},
    }
    availability = {"briefing": True}

    def post(url: str, **kwargs) -> Response:
        if url.endswith("/sessions"):
            return Response({"id": "internal-session", "off_record": False})
        if url.endswith("/turns/begin"):
            external_id = kwargs["json"]["external_id"]
            return Response({"turn": {"id": f"internal-{external_id}"}, **briefing})
        raise AssertionError(f"unexpected POST {url}")

    def get(url: str, **_kwargs) -> Response:
        if not availability["briefing"]:
            raise RuntimeError("briefing unavailable")
        if url.endswith("/briefing"):
            return Response(briefing)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path / "hook-state")
    monkeypatch.setattr(hooks, "load_project", lambda _root: project)
    monkeypatch.setattr(hooks, "_binding_for_project", lambda *_args, **_kwargs: binding)
    monkeypatch.setattr(hooks, "flush_client_telemetry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks, "flush_spooled_turns", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(hooks.httpx, "post", post)
    monkeypatch.setattr(hooks.httpx, "get", get)
    if client == "claude":
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/plugin")
        monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    else:
        monkeypatch.setenv("PLUGIN_ROOT", "/plugin")
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return briefing, availability


def _assert_full_snapshot(context: str) -> None:
    assert _founder_items(context, "manual")
    assert _founder_items(context, "profile")
    task_references = {item["reference"] for item in _founder_items(context, "tasks")}
    assert {"task-1", "task-counts"} <= task_references


def _assert_delta(context: str) -> None:
    assert _founder_items(context, "manual") == []
    assert _founder_items(context, "profile") == []
    assert _founder_items(context, "tasks") == []


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_session_start_rehydrates_boundaries_but_resumes_with_a_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client=client)

    expected = (
        ("startup", _assert_full_snapshot, "snapshot", "startup"),
        ("resume", _assert_delta, "delta", "unchanged"),
        ("clear", _assert_full_snapshot, "snapshot", "clear"),
        ("resume", _assert_delta, "delta", "unchanged"),
        ("compact", _assert_full_snapshot, "snapshot", "compact"),
    )
    for source, assertion, kind, reason in expected:
        output = _hook_input(
            monkeypatch,
            {
                "cwd": str(tmp_path),
                "client": client,
                "session_id": "same-external-session",
                "source": source,
            },
        )
        hooks.session_start()
        assertion(_emitted_context(output, client))

        state = hooks.read_state(hooks.state_path("p1", client, "same-external-session"))
        observation = [
            item
            for item in state["pending_context_observations"]
            if item["operation"] == "context.session_start"
        ][-1]
        assert observation["delivery"]["kind"] == kind
        assert observation["delivery"]["reason"] == reason

    state = hooks.read_state(hooks.state_path("p1", client, "same-external-session"))
    observations = [
        item
        for item in state["pending_context_observations"]
        if item["operation"] == "context.session_start"
    ]
    assert len(observations) == len(expected)
    assert len({item["event_id"] for item in observations}) == len(expected)


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_resume_without_a_trusted_baseline_fails_safe_to_a_full_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client=client)

    output = _hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "client": client,
            "session_id": "unseen-session",
            "source": "resume",
        },
    )
    hooks.session_start()

    _assert_full_snapshot(_emitted_context(output, client))
    state = hooks.read_state(hooks.state_path("p1", client, "unseen-session"))
    delivery = state["pending_context_observations"][-1]["delivery"]
    assert delivery["kind"] == "snapshot"
    assert delivery["reason"] == "state_missing"


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_compact_session_start_resets_the_delta_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client=client)
    common = {"cwd": str(tmp_path), "client": client, "session_id": "session"}

    initial = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(initial, client))

    first_delta = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-1", "message_id": "message-1", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_delta(_emitted_context(first_delta, client))

    compact = _hook_input(monkeypatch, {**common, "source": "compact"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(compact, client))

    second_delta = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-2", "message_id": "message-2", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_delta(_emitted_context(second_delta, client))


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_failed_compact_rehydration_forces_the_next_prompt_to_be_full(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: str,
) -> None:
    _briefing, availability = _configure_runtime(monkeypatch, tmp_path, client=client)
    common = {"cwd": str(tmp_path), "client": client, "session_id": "session"}

    initial = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(initial, client))

    delta = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-1", "message_id": "message-1", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_delta(_emitted_context(delta, client))

    availability["briefing"] = False
    failed_compact = _hook_input(monkeypatch, {**common, "source": "compact"})
    hooks.session_start()
    assert "temporarily unavailable" in _emitted_context(failed_compact, client)

    availability["briefing"] = True
    recovered = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-2", "message_id": "message-2", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_full_snapshot(_emitted_context(recovered, client))


def test_foundation_and_work_changes_are_delivered_as_independent_deltas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    briefing, _availability = _configure_runtime(monkeypatch, tmp_path, client="codex")
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "session"}

    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(startup, "codex"))

    briefing["operational_manual"] = {
        "version": 5,
        "content": "Deploy only after both release gates pass.",
    }
    foundation = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-foundation", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    foundation_context = _emitted_context(foundation, "codex")
    assert _founder_items(foundation_context, "manual")
    assert _founder_items(foundation_context, "profile")
    assert _founder_items(foundation_context, "tasks") == []

    briefing["tasks"][0]["version"] = 4
    briefing["tasks"][0]["title"] = "Run both release gates"
    work = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-work", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    work_context = _emitted_context(work, "codex")
    assert _founder_items(work_context, "manual") == []
    assert _founder_items(work_context, "profile") == []
    assert {item["reference"] for item in _founder_items(work_context, "tasks")} >= {
        "task-1"
    }

    state = hooks.read_state(hooks.state_path("p1", "codex", "session"))
    observations = [
        item
        for item in state["pending_context_observations"]
        if item["operation"] == "context.turn_injection"
    ]
    assert observations[-2]["delivery"] == {
        "kind": "delta",
        "reason": "foundation_changed",
        "foundation_changed": True,
        "work_changed": False,
        "reused_characters": observations[-2]["delivery"]["reused_characters"],
        "reused_utf8_bytes": observations[-2]["delivery"]["reused_utf8_bytes"],
        "reused_estimated_tokens": observations[-2]["delivery"]["reused_estimated_tokens"],
    }
    assert observations[-2]["delivery"]["reused_utf8_bytes"] > 0
    assert observations[-1]["delivery"]["reason"] == "work_changed"
    assert observations[-1]["delivery"]["foundation_changed"] is False
    assert observations[-1]["delivery"]["work_changed"] is True
    assert observations[-1]["delivery"]["reused_utf8_bytes"] > 0


def test_corrupt_or_legacy_delivery_state_fails_safe_to_a_full_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "session"}
    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(startup, "codex"))

    path = hooks.state_path("p1", "codex", "session")
    state = hooks.read_state(path)
    state["context_delivery_baseline"]["stable_measurements"]["foundation"]["items"] = 0
    hooks.write_state(path, state)

    output = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-corrupt", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_full_snapshot(_emitted_context(output, "codex"))
    recovered = hooks.read_state(path)["pending_context_observations"][-1]["delivery"]
    assert recovered["kind"] == "snapshot"
    assert recovered["reason"] == "state_missing"


def test_duplicate_delivered_work_references_invalidate_the_delta_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "session"}
    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(startup, "codex"))

    path = hooks.state_path("p1", "codex", "session")
    state = hooks.read_state(path)
    references = state["context_delivery_baseline"]["delivered_work_references"]["tasks"]
    assert references
    references.append(references[0])
    hooks.write_state(path, state)

    output = _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-duplicate", "prompt": "Continue"},
    )
    hooks.user_prompt_submit()
    _assert_full_snapshot(_emitted_context(output, "codex"))


def test_missing_native_session_identifier_never_assumes_live_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client="codex")

    for turn_id in ("turn-1", "turn-2"):
        output = _hook_input(
            monkeypatch,
            {"cwd": str(tmp_path), "client": "codex", "turn_id": turn_id, "prompt": "Continue"},
        )
        hooks.user_prompt_submit()
        _assert_full_snapshot(_emitted_context(output, "codex"))

    state = hooks.read_state(hooks.state_path("p1", "codex", "default"))
    reasons = [
        item["delivery"]["reason"]
        for item in state["pending_context_observations"]
        if item["operation"] == "context.turn_injection"
    ]
    assert reasons == ["session_unknown", "session_unknown"]


def test_explicitly_requested_work_omitted_from_snapshot_is_delivered_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    briefing, _availability = _configure_runtime(monkeypatch, tmp_path, client="codex")
    briefing["project"]["context"] = "project " * 150
    briefing["operational_manual"] = {"version": 5, "content": "manual " * 500}
    briefing["tasks"] = [
        {
            "id": f"task-{index}",
            "title": f"Large task {index}",
            "status": "todo",
            "description": "detail " * 220,
        }
        for index in range(8)
    ] + [
        {
            "id": "target-task",
            "title": "Unique target title",
            "status": "todo",
            "description": "Small enough to deliver when requested.",
        }
    ]
    briefing["task_counts"] = {
        "total": len(briefing["tasks"]),
        "nonterminal": len(briefing["tasks"]),
        "selected": len(briefing["tasks"]),
        "omitted": 0,
    }
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "session"}
    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    startup_context = _emitted_context(startup, "codex")
    assert _founder_items(startup_context, "manual")
    assert _founder_items(startup_context, "profile")
    assert "target-task" not in {
        item["reference"] for item in _founder_items(startup_context, "tasks")
    }

    path = hooks.state_path("p1", "codex", "session")
    state = hooks.read_state(path)
    baseline = state["context_delivery_baseline"]
    assert "target-task" not in baseline["delivered_work_references"]["tasks"]
    before_work = dict(baseline["stable_measurements"]["work"])

    requested = _hook_input(
        monkeypatch,
        {
            **common,
            "turn_id": "turn-requested",
            "prompt": "Riprendiamo Unique target title",
        },
    )
    hooks.user_prompt_submit()
    requested_context = _emitted_context(requested, "codex")
    requested_tasks = _founder_items(requested_context, "tasks")
    assert [item["reference"] for item in requested_tasks] == ["target-task"]
    assert requested_tasks[0]["payload"]["title"] == "Unique target title"
    assert "work=target-task" in requested_tasks[0]["payload"]["url"]
    after_work = hooks.read_state(path)["context_delivery_baseline"]["stable_measurements"][
        "work"
    ]
    task_line = next(
        line
        for line in requested_context.splitlines()
        if json.loads(line).get("reference") == "target-task"
    )
    separator = 1 if before_work["items"] else 0
    assert after_work["items"] == before_work["items"] + 1
    assert after_work["characters"] == before_work["characters"] + separator + len(task_line)
    assert after_work["utf8_bytes"] == (
        before_work["utf8_bytes"] + separator + len(task_line.encode("utf-8"))
    )

    repeated = _hook_input(
        monkeypatch,
        {
            **common,
            "turn_id": "turn-repeated",
            "prompt": "Riprendiamo Unique target title",
        },
    )
    hooks.user_prompt_submit()
    assert _founder_items(_emitted_context(repeated, "codex"), "tasks") == []


def test_native_manifests_do_not_filter_session_start_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    codex = json.loads(
        (root / "it.dduo.client-support/codex/hooks/hooks.json").read_text()
    )["hooks"]["SessionStart"]
    claude = json.loads(
        (
            root
            / "it.dduo.client-support/claude-code/.claude-plugin/plugin.json"
        ).read_text()
    )["hooks"]["SessionStart"]

    for registrations in (codex, claude):
        assert len(registrations) == 1
        assert "matcher" not in registrations[0]
        assert len(registrations[0]["hooks"]) == 1


def test_failed_session_start_stdout_never_records_a_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    _hook_input(
        monkeypatch,
        {
            "cwd": str(tmp_path),
            "client": "codex",
            "session_id": "broken-session",
            "source": "startup",
        },
    )
    monkeypatch.setattr(
        hooks,
        "emit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hooks.HookEmissionError("broken stdout")
        ),
    )

    with pytest.raises(hooks.HookEmissionError):
        hooks.session_start()

    state = hooks.read_state(hooks.state_path("p1", "codex", "broken-session"))
    assert "context_delivery_baseline" not in state
    assert state.get("pending_context_observations", []) == []


def test_failed_prompt_stdout_keeps_the_previous_baseline_without_false_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    briefing, _availability = _configure_runtime(monkeypatch, tmp_path, client="codex")
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "session"}
    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    _assert_full_snapshot(_emitted_context(startup, "codex"))

    path = hooks.state_path("p1", "codex", "session")
    before = hooks.read_state(path)
    baseline = deepcopy(before["context_delivery_baseline"])
    observation_ids = {
        item["event_id"] for item in before.get("pending_context_observations", [])
    }
    briefing["operational_manual"] = {
        "version": 9,
        "content": "A changed manual that must not become delivered truth.",
    }
    _hook_input(
        monkeypatch,
        {**common, "turn_id": "turn-broken", "prompt": "Continue"},
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

    after = hooks.read_state(path)
    assert after["context_delivery_baseline"] == baseline
    assert {
        item["event_id"] for item in after.get("pending_context_observations", [])
    } == observation_ids
    assert "spooled_active" not in after
