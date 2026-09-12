import hashlib

import pytest
from pydantic import ValidationError

from dduo_solo_founder.schemas import (
    AgentUsageObservation,
    ClientTelemetryBatch,
    CompactionRecord,
    ConsolidatedTopicPayload,
    ContextBudgetObservation,
    ContextComponent,
    ContextDeliveryObservation,
    ContextObservation,
    MemoryActionPayload,
    PlanCreate,
    PlanUpdate,
    SleepRequest,
    StopCheck,
    TaskAction,
    TaskCreate,
    TaskSearch,
    TaskUpdate,
    TurnBegin,
    TurnCommit,
)


def test_sleep_request_describes_scope_without_changing_defaults_or_fields():
    assert SleepRequest().model_dump() == {
        "session_id": None, "provider": None, "resume_auth": False, "trigger": "manual",
    }
    schema = SleepRequest.model_json_schema()
    assert set(schema["properties"]) == {"session_id", "provider", "resume_auth", "trigger"}
    assert not schema.get("required")
    descriptions = {name: field["description"] for name, field in schema["properties"].items()}
    assert "all project sessions" in descriptions["session_id"]
    assert "Other members must" in descriptions["session_id"]
    assert "non-off-record" in descriptions["session_id"]
    assert "Ignored for other triggers" in descriptions["provider"]
    assert "Ignored for other triggers" in descriptions["resume_auth"]
    assert "override automatic sleep-provider selection" in descriptions["provider"]
    assert "does not mean consolidation has completed" in descriptions["trigger"]


def exact_context_payload(content: str = "contesto già pronto") -> dict:
    encoded = content.encode("utf-8")
    estimated_tokens = (len(encoded) + 3) // 4
    return {
        "event_id": "stable-context-event",
        "operation": "context.turn_injection",
        "scope": "automatic",
        "client": "codex",
        "characters": len(content),
        "utf8_bytes": len(encoded),
        "estimated_tokens": estimated_tokens,
        "estimator_version": "utf8_bytes_div_4_v1",
        "component_bytes": {"overhead": len(encoded)},
        "components": [
            {
                "name": "overhead",
                "utf8_bytes": len(encoded),
                "estimated_tokens": estimated_tokens,
            }
        ],
        "content": content,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "producer_version": "test",
        "render_version": "test-v1",
        "occurred_at": "2026-08-25T12:00:00+00:00",
    }


def context_component_payload() -> dict:
    content = "caffè 🚀"
    utf8_bytes = len(content.encode("utf-8"))
    return {
        "name": "memories",
        "utf8_bytes": utf8_bytes,
        "estimated_tokens": (utf8_bytes + 3) // 4,
        "item_count": 1,
        "candidate_item_count": 2,
        "partial_item_count": 1,
        "omitted_item_count": 1,
        "references": ["mémoire-α"],
        "omitted_references": ["記憶-二"],
    }


def context_budget_payload(content: str = "brief già pronto") -> dict:
    encoded = content.encode("utf-8")
    client_character_units = max(len(content), len(content.encode("utf-16-le")) // 2)
    return {
        "limit_characters": 9_000,
        "client_character_units": client_character_units,
        "candidate_characters": len(content),
        "candidate_utf8_bytes": len(encoded),
        "candidate_estimated_tokens": (len(encoded) + 3) // 4,
        "avoided_characters": 0,
        "avoided_utf8_bytes": 0,
        "avoided_estimated_tokens": 0,
        "included_items": 1,
        "partial_items": 0,
        "omitted_items": 0,
        "outcome": "within_budget",
        "delivery_expectation": "inline_expected",
    }


def measured_context_payload() -> dict:
    return {
        "event_id": "measured-context-event",
        "operation": "context.turn_injection",
        "scope": "automatic",
        "client": "codex",
        "characters": 4,
        "utf8_bytes": 5,
        "estimated_tokens": 2,
        "estimator_version": "utf8_bytes_div_4_v1",
    }


def context_delivery_payload() -> dict:
    reused_content = "manuale stabile 🚀"
    reused_bytes = len(reused_content.encode("utf-8"))
    return {
        "kind": "delta",
        "reason": "unchanged",
        "foundation_changed": False,
        "work_changed": False,
        "reused_characters": len(reused_content),
        "reused_utf8_bytes": reused_bytes,
        "reused_estimated_tokens": (reused_bytes + 3) // 4,
    }


def test_memory_actions_are_semantic_but_structurally_constrained():
    action = MemoryActionPayload(
        action="create",
        target_node_type="heuristic",
        target_node_key="release-gate",
        text="Do not release while a blocking test fails.",
    )
    assert action.target_node_type == "heuristic"
    assert "memory_actions" not in TurnCommit.model_json_schema()["properties"]


def test_consolidated_topic_requires_a_supported_language():
    payload = {
        "topic_id": "release",
        "label": "Release",
        "query_text": "Release safety",
        "source_turn_ids": ["turn"],
    }
    assert ConsolidatedTopicPayload(**payload, language="it").language == "it"
    with pytest.raises(ValidationError):
        ConsolidatedTopicPayload(**payload)
    with pytest.raises(ValidationError):
        ConsolidatedTopicPayload(**payload, language="fr")


def test_invalid_memory_action_is_rejected():
    with pytest.raises(ValidationError):
        MemoryActionPayload(action="guess", target_node_type="secret", target_node_key="x")


def test_retrieval_limit_is_bounded():
    with pytest.raises(ValidationError):
        TurnBegin(session_id="s", external_id="t", user_prompt="hello", limit=100)


def test_compaction_shape_is_bounded_and_typed():
    record = CompactionRecord(session_id="s", phase="post", trigger="auto", summary="handoff")
    assert record.summary == "handoff"
    with pytest.raises(ValidationError):
        CompactionRecord(session_id="s", phase="during")


def test_context_component_accepts_unicode_references_and_exact_counts():
    component = ContextComponent.model_validate(context_component_payload())

    assert component.references == ["mémoire-α"]
    assert component.omitted_references == ["記憶-二"]
    assert component.candidate_item_count == (component.item_count + component.omitted_item_count)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"name": "unknown"}, "name is unsupported", id="unsupported-name"),
        pytest.param(
            {"estimated_tokens": 99},
            "estimated_tokens does not match",
            id="token-estimate",
        ),
        pytest.param({"references": [""]}, "bounded non-empty", id="empty-reference"),
        pytest.param(
            {"references": ["r" * 201]},
            "bounded non-empty",
            id="long-reference",
        ),
        pytest.param(
            {"references": ["same", "same"]},
            "references must be unique",
            id="duplicate-reference",
        ),
        pytest.param(
            {"omitted_references": [""]},
            "bounded non-empty",
            id="empty-omitted-reference",
        ),
        pytest.param(
            {"omitted_references": ["r" * 201]},
            "bounded non-empty",
            id="long-omitted-reference",
        ),
        pytest.param(
            {"omitted_references": ["same", "same"]},
            "omitted_references must be unique",
            id="duplicate-omitted-reference",
        ),
        pytest.param(
            {"references": ["same"], "omitted_references": ["same"]},
            "must be disjoint",
            id="overlapping-references",
        ),
        pytest.param(
            {"partial_item_count": 2},
            "partial_item_count cannot exceed",
            id="partial-exceeds-included",
        ),
        pytest.param(
            {"candidate_item_count": 3},
            "candidate_item_count must equal",
            id="candidate-item-total",
        ),
    ],
)
def test_context_component_rejects_inconsistent_manifest_details(changes, message):
    payload = {**context_component_payload(), **changes}

    with pytest.raises(ValidationError, match=message):
        ContextComponent.model_validate(payload)


def test_context_budget_accepts_utf16_units_for_unicode_content():
    content = "brief café 😀 記憶"
    budget = ContextBudgetObservation.model_validate(context_budget_payload(content))

    assert budget.client_character_units == len(content.encode("utf-16-le")) // 2
    assert budget.candidate_utf8_bytes == len(content.encode("utf-8"))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param(
            {"client_character_units": 9_001},
            "exceeds the configured context budget",
            id="client-units-over-limit",
        ),
        pytest.param(
            {"candidate_estimated_tokens": 99},
            "candidate_estimated_tokens does not match",
            id="candidate-token-estimate",
        ),
        pytest.param(
            {"avoided_utf8_bytes": 4, "avoided_estimated_tokens": 0},
            "avoided_estimated_tokens does not match",
            id="avoided-token-estimate",
        ),
        pytest.param(
            {"included_items": 1, "partial_items": 2},
            "partial_items cannot exceed",
            id="partial-exceeds-included",
        ),
        pytest.param(
            {"omitted_items": 1},
            "within_budget cannot report",
            id="within-budget-omission",
        ),
        pytest.param(
            {"included_items": 1, "partial_items": 1},
            "within_budget cannot report",
            id="within-budget-partial",
        ),
        pytest.param(
            {"outcome": "budgeted"},
            "budgeted requires",
            id="budgeted-without-reduction",
        ),
    ],
)
def test_context_budget_rejects_inconsistent_measurements(changes, message):
    payload = {**context_budget_payload(), **changes}

    with pytest.raises(ValidationError, match=message):
        ContextBudgetObservation.model_validate(payload)


def test_context_observation_accepts_unicode_tool_name_without_snapshot():
    observation = ContextObservation.model_validate(
        {**measured_context_payload(), "tool_name": "mémoire/検索:α"}
    )

    assert observation.tool_name == "mémoire/検索:α"
    assert observation.content is None


def test_context_delivery_accepts_exact_delta_reuse_and_nests_in_observation():
    delivery = ContextDeliveryObservation.model_validate(context_delivery_payload())
    observation = ContextObservation.model_validate(
        {**measured_context_payload(), "delivery": context_delivery_payload()}
    )

    assert delivery.kind == "delta"
    assert delivery.reused_characters < delivery.reused_utf8_bytes
    assert observation.delivery == delivery


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param(
            {"reused_estimated_tokens": 99},
            "reused_estimated_tokens does not match",
            id="token-estimate",
        ),
        pytest.param(
            {"reused_characters": 999},
            "reused_characters cannot exceed",
            id="characters-over-bytes",
        ),
        pytest.param(
            {
                "kind": "snapshot",
                "reason": "startup",
                "foundation_changed": True,
                "work_changed": True,
            },
            "only delta delivery can reuse",
            id="snapshot-cannot-reuse",
        ),
        pytest.param(
            {"kind": "fallback", "reason": "remote_offline"},
            "only delta delivery can reuse",
            id="fallback-cannot-reuse",
        ),
        pytest.param({"reason": "periodic_refresh"}, "Input should be", id="unknown-reason"),
        pytest.param({"provider_cache_hits": 4}, "Extra inputs", id="extra-field"),
    ],
)
def test_context_delivery_rejects_inconsistent_or_unapproved_state(changes, message):
    payload = {**context_delivery_payload(), **changes}

    with pytest.raises(ValidationError, match=message):
        ContextDeliveryObservation.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        pytest.param("snapshot", "startup", id="startup-snapshot"),
        pytest.param("snapshot", "compact", id="compact-snapshot"),
        pytest.param("fallback", "remote_offline", id="remote-fallback"),
        pytest.param("fallback", "composition_failed", id="composition-fallback"),
    ],
)
def test_context_delivery_snapshot_and_fallback_require_zero_reuse(kind, reason):
    delivery = ContextDeliveryObservation.model_validate(
        {
            "kind": kind,
            "reason": reason,
            "foundation_changed": kind == "snapshot",
            "work_changed": kind == "snapshot",
        }
    )

    assert delivery.reused_characters == 0
    assert delivery.reused_utf8_bytes == 0
    assert delivery.reused_estimated_tokens == 0


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"kind": "snapshot", "reason": "unchanged"},
            id="snapshot-delta-reason",
        ),
        pytest.param(
            {"kind": "snapshot", "reason": "startup"},
            id="snapshot-missing-replacement-flags",
        ),
        pytest.param(
            {
                "kind": "delta",
                "reason": "work_changed",
                "foundation_changed": True,
                "work_changed": False,
            },
            id="delta-mismatched-flags",
        ),
        pytest.param(
            {"kind": "fallback", "reason": "remote_offline", "work_changed": True},
            id="fallback-advances-work",
        ),
    ],
)
def test_context_delivery_rejects_impossible_kind_reason_and_change_combinations(payload):
    with pytest.raises(ValidationError):
        ContextDeliveryObservation.model_validate(payload)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param(
            {"component_bytes": {"unknown": 0}},
            "unsupported component",
            id="unsupported-component",
        ),
        pytest.param(
            {"component_bytes": {"overhead": -1}},
            "must be non-negative",
            id="negative-component-bytes",
        ),
        pytest.param(
            {"characters": 6},
            "characters cannot exceed utf8_bytes",
            id="characters-over-bytes",
        ),
        pytest.param(
            {"component_bytes": {"overhead": 6}},
            "component_bytes cannot exceed",
            id="components-over-total-bytes",
        ),
        pytest.param(
            {"tool_name": "memory result"},
            "tool_name contains unsupported",
            id="invalid-tool-name",
        ),
        pytest.param(
            {"producer_version": "test"},
            "snapshot metadata requires content",
            id="metadata-without-content",
        ),
    ],
)
def test_context_observation_rejects_invalid_numeric_or_optional_state(changes, message):
    payload = {**measured_context_payload(), **changes}

    with pytest.raises(ValidationError, match=message):
        ContextObservation.model_validate(payload)


def test_context_observation_rejects_components_without_snapshot_content():
    payload = {
        **measured_context_payload(),
        "component_bytes": {"overhead": 5},
        "components": [{"name": "overhead", "utf8_bytes": 5, "estimated_tokens": 2}],
    }

    with pytest.raises(ValidationError, match="components require content"):
        ContextObservation.model_validate(payload)


def test_exact_context_snapshot_requires_faithful_hash_size_manifest_and_time():
    payload = exact_context_payload("caffè 🚀")
    snapshot = ContextObservation.model_validate(payload)
    assert snapshot.content == "caffè 🚀"

    with pytest.raises(ValidationError, match="content_sha256"):
        ContextObservation.model_validate({**payload, "content_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="every UTF-8 byte"):
        ContextObservation.model_validate(
            {
                **payload,
                "component_bytes": {"overhead": payload["utf8_bytes"] - 1},
                "components": [
                    {
                        "name": "overhead",
                        "utf8_bytes": payload["utf8_bytes"] - 1,
                        "estimated_tokens": (payload["utf8_bytes"] - 1 + 3) // 4,
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="timezone"):
        ContextObservation.model_validate({**payload, "occurred_at": "2026-08-25T12:00:00"})


def test_context_snapshot_rejects_incomplete_or_inconsistent_measurements():
    payload = exact_context_payload("caffè 🚀")
    payload["components"] = [payload["components"][0], {**payload["components"][0]}]
    with pytest.raises(ValidationError, match="unique names"):
        ContextObservation.model_validate(payload)

    payload = exact_context_payload("caffè 🚀")
    payload["component_bytes"] = {"result": payload["utf8_bytes"]}
    with pytest.raises(ValidationError, match="must match component_bytes"):
        ContextObservation.model_validate(payload)

    with pytest.raises(ValidationError, match="content requires content_sha256"):
        ContextObservation.model_validate(
            {**exact_context_payload("caffè 🚀"), "content_sha256": None}
        )
    with pytest.raises(ValidationError, match="characters does not match content"):
        payload = exact_context_payload("caffè 🚀")
        ContextObservation.model_validate({**payload, "characters": payload["characters"] - 1})
    with pytest.raises(ValidationError, match="utf8_bytes does not match content"):
        payload = exact_context_payload("caffè 🚀")
        ContextObservation.model_validate({**payload, "utf8_bytes": payload["utf8_bytes"] + 1})
    with pytest.raises(ValidationError, match="estimated_tokens does not match content"):
        payload = exact_context_payload("caffè 🚀")
        ContextObservation.model_validate(
            {**payload, "estimated_tokens": payload["estimated_tokens"] + 1}
        )

    payload = exact_context_payload("caffè 🚀")
    with pytest.raises(ValidationError, match="require a component manifest"):
        ContextObservation.model_validate({**payload, "component_bytes": {}, "components": []})


def test_exact_context_snapshot_accepts_mixed_unicode_with_utf16_budget_units():
    content = "Piano café 😀 記憶"
    payload = exact_context_payload(content)
    payload["budget"] = context_budget_payload(content)
    payload["tool_name"] = "brief/éxécution:記憶"

    snapshot = ContextObservation.model_validate(payload)

    assert snapshot.budget is not None
    assert snapshot.budget.client_character_units == len(content.encode("utf-16-le")) // 2
    assert snapshot.content_sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_context_snapshot_has_no_post_render_size_limit_and_session_start_has_no_turn():
    content = "è" * 100_001
    snapshot = ContextObservation.model_validate(exact_context_payload(content))
    assert snapshot.content == content
    with pytest.raises(ValidationError, match="session-scoped"):
        ContextObservation.model_validate(
            {
                **exact_context_payload(),
                "operation": "context.session_start",
                "turn_id": "turn-id",
            }
        )


def test_context_budget_requires_exact_candidate_to_emitted_delta() -> None:
    content = "bounded context"
    payload = exact_context_payload(content)
    payload["components"] = [
        {
            **payload["components"][0],
            "item_count": 1,
            "candidate_item_count": 2,
            "partial_item_count": 0,
            "omitted_item_count": 1,
            "references": ["memory-1"],
            "omitted_references": ["memory-2"],
        }
    ]
    candidate_characters = len(content) + 20
    candidate_bytes = len(content.encode("utf-8")) + 20
    payload["budget"] = {
        "limit_characters": 9_000,
        "client_character_units": len(content),
        "candidate_characters": candidate_characters,
        "candidate_utf8_bytes": candidate_bytes,
        "candidate_estimated_tokens": (candidate_bytes + 3) // 4,
        "avoided_characters": 20,
        "avoided_utf8_bytes": 20,
        "avoided_estimated_tokens": 5,
        "included_items": 1,
        "partial_items": 0,
        "omitted_items": 1,
        "outcome": "budgeted",
        "delivery_expectation": "inline_expected",
    }

    observation = ContextObservation.model_validate(payload)
    assert observation.budget is not None
    assert observation.budget.avoided_characters == 20

    invalid = {**payload, "budget": {**payload["budget"], "avoided_characters": 19}}
    with pytest.raises(ValidationError, match="candidate minus emitted"):
        ContextObservation.model_validate(invalid)
    with pytest.raises(ValidationError, match="configured context budget"):
        ContextObservation.model_validate(
            {
                **payload,
                "budget": {
                    **payload["budget"],
                    "limit_characters": len(content) - 1,
                },
            }
        )
    with pytest.raises(ValidationError, match="automatic context"):
        ContextObservation.model_validate({**payload, "scope": "requested"})

    astral_content = "😀" * 5_000
    astral_payload = exact_context_payload(astral_content)
    astral_payload["budget"] = {
        "limit_characters": 9_000,
        "client_character_units": len(astral_content),
        "candidate_characters": len(astral_content),
        "candidate_utf8_bytes": len(astral_content.encode("utf-8")),
        "candidate_estimated_tokens": (len(astral_content.encode("utf-8")) + 3) // 4,
        "avoided_characters": 0,
        "avoided_utf8_bytes": 0,
        "avoided_estimated_tokens": 0,
        "included_items": 1,
        "partial_items": 0,
        "omitted_items": 0,
        "outcome": "within_budget",
        "delivery_expectation": "inline_expected",
    }
    with pytest.raises(ValidationError, match="client_character_units does not match"):
        ContextObservation.model_validate(astral_payload)


@pytest.mark.parametrize(
    ("budget_changes", "message"),
    [
        pytest.param(
            {"client_character_units": 3},
            "characters cannot exceed client_character_units",
            id="emitted-characters-over-client-units",
        ),
        pytest.param(
            {"candidate_characters": 3},
            "emitted characters cannot exceed candidate",
            id="emitted-characters-over-candidate",
        ),
        pytest.param(
            {"candidate_utf8_bytes": 4, "candidate_estimated_tokens": 1},
            "emitted bytes cannot exceed candidate",
            id="emitted-bytes-over-candidate",
        ),
        pytest.param(
            {
                "candidate_characters": 6,
                "candidate_utf8_bytes": 7,
                "candidate_estimated_tokens": 2,
                "avoided_characters": 2,
                "avoided_utf8_bytes": 1,
                "avoided_estimated_tokens": 1,
            },
            "avoided_utf8_bytes must equal candidate minus emitted",
            id="avoided-byte-delta",
        ),
    ],
)
def test_context_observation_budget_rejects_impossible_emitted_measurements(
    budget_changes, message
):
    payload = measured_context_payload()
    budget = {
        "limit_characters": 100,
        "client_character_units": 5,
        "candidate_characters": 4,
        "candidate_utf8_bytes": 5,
        "candidate_estimated_tokens": 2,
        "avoided_characters": 0,
        "avoided_utf8_bytes": 0,
        "avoided_estimated_tokens": 0,
        "included_items": 1,
        "partial_items": 0,
        "omitted_items": 0,
        "outcome": "within_budget",
        "delivery_expectation": "inline_expected",
    }
    payload["budget"] = {**budget, **budget_changes}

    with pytest.raises(ValidationError, match=message):
        ContextObservation.model_validate(payload)


def test_task_hierarchy_is_shallow_and_typed():
    assert TaskCreate(title="Launch", kind="epic").kind == "epic"
    with pytest.raises(ValidationError, match="cannot belong"):
        TaskCreate(title="Nested", kind="epic", epic_id="parent")
    with pytest.raises(ValidationError, match="cannot belong"):
        TaskAction(action="create", title="Nested", kind="epic", epic_id="parent")
    with pytest.raises(ValidationError, match="cannot be blank"):
        TaskCreate(title="   ")
    with pytest.raises(ValidationError, match="cannot be null"):
        TaskUpdate(status=None)
    with pytest.raises(ValidationError, match="cannot be blank"):
        TaskUpdate(title="  ")
    assert TaskUpdate(epic_id=None).epic_id is None


def test_plan_payloads_keep_documents_and_links_well_formed():
    created = PlanCreate(
        title="Release design",
        objective="Choose the path",
        work_item_ids=["epic", "task"],
    )
    assert created.status == "draft"
    assert PlanUpdate(work_item_ids=[]).work_item_ids == []
    with pytest.raises(ValidationError):
        PlanCreate(title="  ")
    with pytest.raises(ValidationError):
        PlanCreate(title="Duplicate", work_item_ids=["same", "same"])
    with pytest.raises(ValidationError, match="cannot be blank"):
        PlanCreate(title="Blank link", work_item_ids=[" "])
    with pytest.raises(ValidationError):
        PlanUpdate(status=None)
    with pytest.raises(ValidationError, match="title cannot be blank"):
        PlanUpdate(title=" ")
    with pytest.raises(ValidationError, match="ids cannot be blank"):
        PlanUpdate(work_item_ids=[" "])
    with pytest.raises(ValidationError, match="ids must be unique"):
        PlanUpdate(work_item_ids=["same", "same"])


def test_turn_and_task_actions_reject_incomplete_semantic_state():
    assert StopCheck(context_observations="not-a-list").context_observations == []
    with pytest.raises(ValidationError, match="segment_summary"):
        StopCheck(topic_changed=True)
    with pytest.raises(ValidationError, match="segment_summary"):
        TurnCommit(assistant_response="x", topic_changed=True)
    with pytest.raises(ValidationError, match="requires task_id"):
        TaskAction(action="complete")
    with pytest.raises(ValidationError, match="cannot be null"):
        TaskAction(action="create", title="Task", labels=None)
    with pytest.raises(ValidationError, match="create requires title"):
        TaskAction(action="create", title="  ")
    with pytest.raises(ValidationError, match="title cannot be blank"):
        TaskAction(action="update", task_id="task", title="  ")
    with pytest.raises(ValidationError, match="cannot belong"):
        TaskUpdate(kind="epic", epic_id="parent")
    with pytest.raises(ValidationError, match="requires non-empty"):
        MemoryActionPayload(action="create", target_node_type="episode", target_node_key="x")
    with pytest.raises(ValidationError, match="must invalidate"):
        MemoryActionPayload(
            action="set_status", target_node_type="episode", target_node_key="x", status="active"
        )


def test_task_search_normalizes_queries_and_rejects_whitespace_only():
    assert TaskSearch(query="  android   parity ").query == "android parity"
    with pytest.raises(ValidationError, match="cannot be blank"):
        TaskSearch(query="   \n\t")


def test_client_telemetry_accepts_usage_and_discards_retired_guard_items():
    usage = AgentUsageObservation.model_validate(
        {
            "event_id": "agent.session-1.1",
            "provider": "claude",
            "model": "claude-sonnet-4-5-20250929",
            "measurement_source": "local_estimate",
            "input_tokens": 100,
            "cached_input_tokens": 90,
            "output_tokens": 10,
            "client_cost_usd": "0.001250000000",
            "cost_source": "claude_code_client_estimate",
            "occurred_at": "2026-08-29T12:00:00+00:00",
        }
    )
    legacy_guard = {
        "kind": "usage_guard",
        "event_id": "guard.codex.blocked-1",
        "provider": "codex",
        "operation": "usage_guard.threshold_reached",
        "measurement_source": "provider_reported",
        "occurred_at": "2026-08-29T12:00:01+00:00",
    }
    batch = ClientTelemetryBatch(items=[usage, legacy_guard])
    assert [item.kind for item in batch.items] == ["agent_usage"]
    assert ClientTelemetryBatch.model_validate({"items": [legacy_guard]}).items == []

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ClientTelemetryBatch.model_validate(
            {"items": [{**legacy_guard, "content": "must never enter telemetry"}]}
        )
    with pytest.raises(ValidationError, match="provider-reported"):
        ClientTelemetryBatch.model_validate(
            {"items": [{**legacy_guard, "measurement_source": "local_estimate"}]}
        )


def test_client_telemetry_rejects_guessed_costs():
    with pytest.raises(ValidationError, match="Claude Code"):
        AgentUsageObservation.model_validate(
            {
                "event_id": "agent.bad",
                "provider": "codex",
                "measurement_source": "local_estimate",
                "client_cost_usd": "1",
                "cost_source": "claude_code_client_estimate",
                "occurred_at": "2026-08-29T12:00:00+00:00",
            }
        )


def test_client_telemetry_is_strict_and_batch_event_ids_are_unique():
    base_usage = {
        "event_id": "agent.strict.1",
        "provider": "codex",
        "model": "gpt-5.6-terra",
        "measurement_source": "provider_reported",
        "input_tokens": 100,
        "occurred_at": "2026-08-29T12:00:00+00:00",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentUsageObservation.model_validate(
            {**base_usage, "content": "must never enter telemetry"}
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ClientTelemetryBatch.model_validate({"items": [base_usage], "prompt": "private"})
    with pytest.raises(ValidationError, match="cannot exceed input tokens"):
        AgentUsageObservation.model_validate(
            {
                **base_usage,
                "cached_input_tokens": 70,
                "cache_write_input_tokens": 31,
            }
        )
    with pytest.raises(ValidationError, match="must be unique"):
        ClientTelemetryBatch.model_validate({"items": [base_usage, base_usage]})

    full_outbox = [{**base_usage, "event_id": f"agent.batch.{index}"} for index in range(1_000)]
    assert len(ClientTelemetryBatch.model_validate({"items": full_outbox}).items) == 1_000
    with pytest.raises(ValidationError, match="at most 1000"):
        ClientTelemetryBatch.model_validate(
            {
                "items": [
                    *full_outbox,
                    {**base_usage, "event_id": "agent.batch.overflow"},
                ]
            }
        )
