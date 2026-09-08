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


def _state_path(client: str, external_session_id: str) -> Path:
    """The runtime fixture deliberately uses one explicit binding."""
    return hooks.state_path("p1", client, external_session_id, "b" * 64)


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

        state = hooks.read_state(_state_path(client, "same-external-session"))
        observation = [
            item
            for item in state["pending_context_observations"]
            if item["operation"] == "context.session_start"
        ][-1]
        assert observation["delivery"]["kind"] == kind
        assert observation["delivery"]["reason"] == reason

    state = hooks.read_state(_state_path(client, "same-external-session"))
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
    state = hooks.read_state(_state_path(client, "unseen-session"))
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

    state = hooks.read_state(_state_path("codex", "session"))
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

    path = _state_path("codex", "session")
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

    path = _state_path("codex", "session")
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
        assert "cannot identify this chat session" in _emitted_context(output, "codex")

    assert _emitted_context(output, "codex") == (
        "dDuo cannot identify this chat session, so this turn cannot be recorded safely. "
        "Offer to check the plugin connection and start a new chat after repair."
    )
    assert not list((tmp_path / "hook-state").glob("p1-codex-*.json"))


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

    path = _state_path("codex", "session")
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

    state = hooks.read_state(_state_path("codex", "broken-session"))
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

    path = _state_path("codex", "session")
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


def test_successful_hooks_store_content_free_lifecycle_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    common = {"cwd": str(tmp_path), "client": "codex", "session_id": "evidence-session"}

    startup = _hook_input(monkeypatch, {**common, "source": "startup"})
    hooks.session_start()
    assert _emitted_context(startup, "codex")

    prompt = _hook_input(
        monkeypatch,
        {**common, "turn_id": "evidence-turn", "prompt": "private prompt must not be diagnostic"},
    )
    hooks.user_prompt_submit()
    assert _emitted_context(prompt, "codex")

    state = hooks.read_state(_state_path("codex", "evidence-session"))
    evidence = state["lifecycle_evidence"]
    assert evidence["session_start"]["context_emitted_at"]
    assert evidence["prompt"] == {
        "observed_at": evidence["prompt"]["observed_at"],
        "external_turn_id": "evidence-turn",
        "turn_id": "internal-evidence-turn",
        "context_emitted_at": evidence["prompt"]["context_emitted_at"],
    }
    assert "private prompt must not be diagnostic" not in json.dumps(evidence)


def _capture_state(**values) -> dict:
    return {
        "project_id": "p1", "client": "codex", "binding_id": "b" * 64,
        "session_external_id": "capture-session", "session_id": "internal-session",
        "external_turn_id": "native-turn", "turn_id": "internal-turn",
        "turn_off_record": True, **values,
    }


def _stage_capture(path: Path, response: object = "synthetic answer", **payload) -> dict | None:
    return hooks._stage_stop(
        path, {"turn_id": "native-turn", "assistant_response": response, **payload},
        project_id="p1", client="codex", external_session="capture-session", binding_id="b" * 64,
    )


def test_legacy_import_materializes_all_queues_and_invalidates_real_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    legacy = _capture_state(
        pending_commits=[{"turn_id": "older", "payload": {"assistant_response": "synthetic"}}],
        context_delivery_baseline={"version": 2}, last_briefing_context_hash="old",
        lifecycle_evidence={"version": 1},
    )
    legacy.pop("client")  # Older clients omitted this; the exact filename proves the family.
    old_path = hooks.state_path("p1", "codex", "capture-session")
    hooks.write_state(old_path, legacy)
    path, imported = hooks.load_bound_state("p1", "codex", "capture-session", "b" * 64)
    assert hooks.read_state(path) == imported
    assert imported["client"] == "codex"
    expected_queue = [{**legacy["pending_commits"][0], "binding_id": "b" * 64}]
    assert imported["pending_commits"] == expected_queue
    assert not {"context_delivery_baseline", "last_briefing_context_hash", "lifecycle_evidence"} & imported.keys()
    before = deepcopy(imported)
    imported["session_id"] = "new-internal-session"
    hooks.merge_state_update(path, before, imported)
    assert hooks.read_state(path)["pending_commits"] == expected_queue
    hooks.write_state(path, {})
    assert hooks.load_bound_state("p1", "codex", "capture-session", "b" * 64)[1] == {}
    assert old_path.exists()


@pytest.mark.parametrize("key,value", [
    ("project_id", "other-project"), ("binding_id", None),
    ("client", "claude"), ("session_external_id", "other-session"),
])
def test_legacy_import_requires_every_scope_identity(monkeypatch, tmp_path, key, value):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    legacy = _capture_state(**{key: value})
    old = hooks.state_path("p1", "codex", "capture-session")
    hooks.write_state(old, legacy)
    new, state = hooks.load_bound_state("p1", "codex", "capture-session", "b" * 64)
    assert state == {} and not new.exists() and old.exists()


@pytest.mark.parametrize("queue", ["pending_commits", "spooled_turns"])
def test_old_ack_keeps_concurrent_snapshot_enrichment(tmp_path, queue):
    path = tmp_path / "state.json"
    item = {
        "revision": "revision", "turn_id": "turn", "binding_id": "binding",
        "external_turn_id": "native", "session_external_id": "session",
        "payload": {"assistant_response": "synthetic"}, "assistant_response": "synthetic",
    }
    before = {queue: [deepcopy(item)]}
    live = deepcopy(before)
    live[queue][0]["context_observations"] = [{"event_id": "new"}]
    hooks.write_state(path, live)
    assert hooks.merge_state_update(path, before, {})[queue] == live[queue]


@pytest.mark.parametrize("queue", ["pending_commits", "spooled_turns"])
def test_concurrent_stop_writers_preserve_first_answer_and_both_observations(tmp_path, queue):
    path = tmp_path / "state.json"
    identity = {"turn_id": "turn", "binding_id": "binding",
                "external_turn_id": "native", "session_external_id": "session"}

    def snapshot(answer, event):
        body = {"assistant_response": answer, "context_observations": [{"event_id": event}]}
        return {**identity, **({"payload": body} if queue == "pending_commits" else body)}

    before = {queue: [dict(identity)]}
    live = {queue: [snapshot("first answer", "first")]}
    hooks.write_state(path, live)
    result = hooks.merge_state_update(path, before, {queue: [snapshot("second answer", "second")]})
    assert len(result[queue]) == 1
    item = result[queue][0]
    body = item["payload"] if queue == "pending_commits" else item
    assert body["assistant_response"] == "first answer"
    assert body["context_observations"] == [{"event_id": "first"}, {"event_id": "second"}]
    assert result["lifecycle_evidence"]["stop"]["conflict"] is True


@pytest.mark.parametrize("damage,expected", [
    ({"binding_id": "other"}, RuntimeError),
    ({"turn_id": " "}, ValueError),
    ({"turn_id": 123}, ValueError),
])
def test_stage_stop_rejects_binding_change_and_invalid_identity_without_mutation(tmp_path, damage, expected):
    path = tmp_path / "state.json"
    state = _capture_state()
    payload = {"turn_id": "native-turn", "assistant_response": "answer"}
    if "binding_id" in damage:
        state.update(damage)
    else:
        payload.update(damage)
    hooks.write_state(path, state)
    with pytest.raises(expected):
        hooks._stage_stop(path, payload, project_id="p1", client="codex",
                          external_session="capture-session", binding_id="b" * 64)
    assert hooks.read_state(path) == state


def test_ack_compares_live_snapshot_under_lock(tmp_path):
    path = tmp_path / "state.json"
    state = _capture_state()
    first = hooks.queue_pending_commit(state, "turn", {"assistant_response": "answer"}, "b" * 64)
    hooks.write_state(path, state)
    changed = hooks.read_state(path)
    changed["pending_commits"][0]["payload"]["context_observations"] = [{"event_id": "later"}]
    hooks.write_state(path, changed)
    assert not hooks._ack_pending_commit(path, first)
    assert hooks.read_state(path)["pending_commits"] == changed["pending_commits"]


def test_same_response_merges_observations_without_conflict(tmp_path):
    path = tmp_path / "state.json"
    state = _capture_state()
    first = deepcopy(hooks.queue_pending_commit(state, "turn", {
        "assistant_response": "same", "context_observations": [{"event_id": "one"}],
    }, "b" * 64))
    enriched = hooks.queue_pending_commit(state, "turn", {
        "assistant_response": "same", "context_observations": [{"event_id": "two"}],
    }, "b" * 64)
    assert len(state["pending_commits"]) == 1
    assert enriched["revision"] != first["revision"]
    assert enriched["payload"]["context_observations"] == [{"event_id": "one"}, {"event_id": "two"}]
    assert not state.get("lifecycle_evidence", {}).get("stop", {}).get("conflict")
    hooks.write_state(path, state)
    assert not hooks._ack_pending_commit(path, first)


def test_concurrent_stop_stages_one_answer_and_reports_conflict(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "state.json"
    hooks.write_state(path, _capture_state())
    barrier = Barrier(2)

    def capture(answer):
        barrier.wait(timeout=5)
        return _stage_capture(path, answer)

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(capture, ["answer one", "answer two"]))
    state = hooks.read_state(path)
    assert len(state["pending_commits"]) == 1
    first = state["pending_commits"][0]["payload"]["assistant_response"]
    assert first in {"answer one", "answer two"}
    assert all(response["payload"]["assistant_response"] == first for response in responses)
    assert state["lifecycle_evidence"]["stop"]["conflict"] is True
    assert "turn_id" not in state


def test_stop_staging_failure_makes_no_network_request(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    path = _state_path("codex", "capture-session")
    hooks.write_state(path, _capture_state())
    calls = []
    monkeypatch.setattr(hooks, "_request", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(hooks, "write_state", lambda *args: (_ for _ in ()).throw(OSError("disk unavailable")))
    _hook_input(monkeypatch, {
        "cwd": str(tmp_path), "client": "codex", "session_id": "capture-session",
        "turn_id": "native-turn", "assistant_response": "answer",
    })
    hooks.stop()
    assert calls == []
    assert hooks.read_state(path)["turn_id"] == "internal-turn"


@pytest.mark.parametrize("response", [None, 7, {"text": "invalid"}])
def test_incomplete_online_stop_retains_observations_and_can_be_repaired(tmp_path, response):
    path = tmp_path / "state.json"
    hooks.write_state(path, _capture_state(pending_context_observations=[{"event_id": "one"}]))
    assert _stage_capture(path, response) is None
    incomplete = hooks.read_state(path)["pending_commits"][0]
    assert incomplete["payload"] == {"context_observations": [{"event_id": "one"}]}
    assert incomplete["off_record"] is True
    repaired = _stage_capture(path, "")
    assert repaired["payload"]["assistant_response"] == ""
    assert repaired["off_record"] is True
    assert len(hooks.read_state(path)["pending_commits"]) == 1


def test_incomplete_offline_stop_retains_prompt_privacy_and_replays_valid_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    path = _state_path("codex", "capture-session")
    state = _capture_state(spooled_active={
        "session_external_id": "capture-session", "external_turn_id": "native-turn",
        "binding_id": "b" * 64, "off_record": True, "user_prompt": "artificial private prompt",
    })
    state.pop("turn_id")
    hooks.write_state(path, state)
    _stage_capture(path, None)
    queued = hooks.read_state(path)["spooled_turns"][0]
    assert queued["user_prompt"] == "artificial private prompt"
    assert queued["off_record"] is True and "assistant_response" not in queued
    _stage_capture(path, "")
    calls = []

    def request(_transport, _method, url, **kwargs):
        calls.append((url, kwargs["json"]))
        if url.endswith("/sessions"):
            return Response({"id": "s"})
        if url.endswith("/begin"):
            return Response({"turn": {"id": "t"}})
        return Response({"committed": True})

    monkeypatch.setattr(hooks, "_request", request)
    assert hooks.flush_spooled_turns({"id": "p1"}, "http://example", "codex", "b" * 64, allow_legacy=False) == 1
    assert calls[1][1]["off_record"] is True
    assert calls[2][1]["assistant_response"] == ""
    assert not hooks.read_state(path).get("spooled_turns")


def test_late_ack_does_not_replace_newer_stop_evidence(tmp_path):
    path = tmp_path / "state.json"
    state = _capture_state(lifecycle_evidence={"version": 1, "stop": {"turn_id": "newer"}})
    old = hooks.queue_pending_commit(state, "older", {"assistant_response": "answer"}, "b" * 64)
    hooks.write_state(path, state)
    assert hooks._ack_pending_commit(path, old)
    assert hooks.read_state(path)["lifecycle_evidence"]["stop"] == {"turn_id": "newer"}


@pytest.mark.parametrize("hook,event", [("session_start", "SessionStart"), ("user_prompt_submit", "UserPromptSubmit")])
def test_usage_recovery_happens_after_context_and_is_bounded(monkeypatch, tmp_path, hook, event):
    _configure_runtime(monkeypatch, tmp_path, client="codex")
    path = _state_path("codex", "capture-session")
    hooks.write_state(path, _capture_state(pending_codex_usage=[{"source_id": "one"}, {"source_id": "two"}]))
    output = _hook_input(monkeypatch, {
        "cwd": str(tmp_path), "client": "codex", "session_id": "capture-session",
        "turn_id": "new-turn", "prompt": "Continue",
    })
    observed = []

    def enqueue(source, **_kwargs):
        assert json.loads(output.getvalue())["hookSpecificOutput"]["hookEventName"] == event
        observed.append(source["source_id"])

    monkeypatch.setattr(hooks, "enqueue_pending_codex_usage", enqueue)
    getattr(hooks, hook)()
    assert observed == ["one"]
    assert hooks.read_state(path)["pending_codex_usage"] == [{"source_id": "two"}]


def test_expired_recovery_budget_never_sends_a_request(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(hooks, "_request", lambda *args, **kwargs: calls.append(args))
    state = _capture_state()
    hooks.queue_pending_commit(state, "turn", {"assistant_response": "answer"}, "b" * 64)
    before = deepcopy(state)
    assert hooks.flush_pending_commits("http://example", state, "b" * 64, deadline=0, max_items=1) == 0
    assert state == before and calls == []


@pytest.mark.parametrize("invalid", [17, {}, [], "", "x\n", "x\x00"])
def test_invalid_native_session_identity_is_never_coerced(invalid):
    assert hooks.external_session_id({"session_id": invalid}) is None


@pytest.mark.parametrize("archived", [False, True])
def test_bounded_recovery_delivers_closed_session_and_restored_sidecar(monkeypatch, tmp_path, archived):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    binding = "b" * 64
    current = hooks.state_path("p1", "codex", "current-chat", binding)
    hooks.write_state(current, _capture_state(session_external_id="current-chat"))
    path = _state_path("codex", "capture-session")
    if archived:
        hooks.write_state(path, _capture_state())
        path = path.with_name(f"{path.stem}-restored-0123abcd.json")
    state = _capture_state()
    hooks.queue_pending_commit(state, "old-turn", {"assistant_response": "preserved"}, binding)
    hooks.write_state(path, state)
    calls = []
    monkeypatch.setattr(hooks, "_request", lambda *args, **kwargs: calls.append(args[2]) or Response({"committed": True}))
    hooks._recover_one_pending_completion({"id": "p1"}, "http://example", "codex", binding, current, hooks.time.monotonic() + 2)
    assert calls == ["/turns/old-turn/stop-check"]
    assert not hooks.read_state(path).get("pending_commits")
    assert hooks.read_state(current)["session_external_id"] == "current-chat"


def test_recovery_skips_incomplete_and_foreign_ledgers_and_limits_total_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    binding = "b" * 64
    current = hooks.state_path("p1", "codex", "current-chat", binding)
    state = _capture_state(session_external_id="current-chat")
    hooks.queue_pending_commit(state, "incomplete", {}, binding)
    hooks.write_state(current, state)
    for native, actual_binding, client in [("other-binding", "other", "codex"), ("wrong-client", binding, "claude"), ("valid-one", binding, "codex"), ("valid-two", binding, "codex")]:
        state = _capture_state(session_external_id=native, binding_id=actual_binding, client=client)
        hooks.queue_pending_commit(state, native, {"assistant_response": "artificial"}, actual_binding)
        hooks.write_state(hooks.state_path("p1", "codex", native, binding), state)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args[2])
        raise OSError("synthetic outage")

    monkeypatch.setattr(hooks, "_request", unavailable)
    hooks._recover_one_pending_completion({"id": "p1"}, "http://example", "codex", binding, current, hooks.time.monotonic() + 2)
    assert len(calls) == 1 and calls[0] in {"/turns/valid-one/stop-check", "/turns/valid-two/stop-check"}
    assert hooks.read_state(current)["pending_commits"][0]["turn_id"] == "incomplete"


def test_migrated_closed_session_replays_missing_binding_but_never_foreign_binding(monkeypatch, tmp_path):
    monkeypatch.setattr(hooks, "HOOK_STATE_DIR", tmp_path)
    binding = "b" * 64
    legacy = _capture_state(pending_commits=[
        {"turn_id": "foreign", "binding_id": "other", "payload": {"assistant_response": "private"}},
        {"turn_id": "legacy", "payload": {"assistant_response": "synthetic"}, "off_record": True},
    ])
    legacy.pop("client")
    hooks.write_state(hooks.state_path("p1", "codex", "capture-session"), legacy)
    current = hooks.state_path("p1", "codex", "new-chat", binding)
    hooks.write_state(current, _capture_state(session_external_id="new-chat"))
    calls = []
    monkeypatch.setattr(hooks, "_request", lambda *args, **kwargs: calls.append(args[2]) or Response({"committed": True}))
    hooks._recover_one_pending_completion({"id": "p1"}, "http://example", "codex", binding, current, hooks.time.monotonic() + 2)
    assert calls == ["/turns/legacy/stop-check"]
    remaining = hooks.read_state(_state_path("codex", "capture-session"))["pending_commits"]
    assert remaining == [legacy["pending_commits"][0]]
