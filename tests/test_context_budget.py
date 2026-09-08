from __future__ import annotations

import hashlib
import json

import pytest

from dduo_solo_founder import context_budget as budget_module
from dduo_solo_founder.context_budget import (
    DEFAULT_CONTEXT_BUDGET,
    ContextFragment,
    compose_context,
    compose_fallback_context,
    context_units,
    measure_fragment_sources,
    utf16_units,
)


def fragment(
    reference: str,
    text: str,
    *,
    component: str = "memories",
    priority: int = 10,
    excerpt: str | None = None,
    required: bool = False,
) -> ContextFragment:
    return ContextFragment(
        component=component,
        priority=priority,
        reference=reference,
        payload={"text": text},
        excerpt={"text": excerpt, "truncated": True} if excerpt is not None else None,
        required=required,
    )


def decoded_lines(content: str) -> list[dict]:
    return [json.loads(line) for line in content.splitlines()]


def fragment_lines(content: str) -> list[dict]:
    return [line for line in decoded_lines(content) if "component" in line]


def component(result, name: str):
    return next(item for item in result.manifest.components if item.component == name)


def test_units_use_the_stricter_of_python_and_utf16_lengths() -> None:
    value = "a😀b"

    assert len(value) == 3
    assert utf16_units(value) == 4
    assert context_units(value) == 4


@pytest.mark.parametrize("target", [8_999, 9_000])
def test_ascii_context_can_reach_the_boundary_exactly(target: int) -> None:
    empty = compose_context([fragment("boundary", "")])
    text_size = target - empty.measurement.budget_units
    assert text_size >= 0

    result = compose_context([fragment("boundary", "x" * text_size)])

    assert result.measurement.characters == target
    assert result.measurement.utf16_units == target
    assert result.measurement.budget_units == target
    assert result.manifest.status == "within_budget"
    assert result.manifest.emitted == 1
    assert not result.fallback_used


def test_candidate_at_9001_units_is_not_emitted() -> None:
    empty = compose_context([fragment("boundary", "")])
    text_size = 9_001 - empty.measurement.budget_units

    result = compose_context([fragment("boundary", "x" * text_size)])

    assert result.produced_measurement.budget_units == 9_001
    assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET
    assert result.manifest.status == "budgeted"
    assert result.manifest.emitted == 0
    assert result.manifest.omitted == 1
    assert fragment_lines(result.content) == []


def test_utf16_budget_rejects_full_emoji_payload_and_uses_explicit_excerpt() -> None:
    full = "😀" * 4_500
    explicit_excerpt = "😀" * 100

    result = compose_context([fragment("emoji-memory", full, excerpt=explicit_excerpt)])

    assert result.produced_measurement.characters < result.produced_measurement.utf16_units
    assert result.produced_measurement.utf16_units > DEFAULT_CONTEXT_BUDGET
    assert result.measurement.characters < result.measurement.utf16_units
    assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET
    assert result.manifest.partial == 1
    assert result.manifest.emitted == 1
    assert result.manifest.omitted == 0
    emitted = fragment_lines(result.content)[0]
    assert emitted["delivery"] == "partial"
    assert emitted["payload"] == {"text": explicit_excerpt, "truncated": True}
    assert "�" not in result.content


def test_excerpt_that_reduces_client_units_but_inflates_utf8_is_not_emitted() -> None:
    result = compose_context(
        [fragment("byte-inflating-excerpt", "x" * 8_800, excerpt="😀" * 2_250)]
    )

    assert result.produced_measurement.budget_units > DEFAULT_CONTEXT_BUDGET
    assert result.manifest.partial == 0
    assert result.manifest.omitted == 1
    assert fragment_lines(result.content) == []
    assert result.measurement.utf8_bytes <= result.produced_measurement.utf8_bytes


def test_composer_never_manufactures_or_blindly_slices_an_excerpt() -> None:
    full = "start-" + ("x" * 20_000) + "-end"

    result = compose_context([fragment("too-large", full)])

    assert fragment_lines(result.content) == []
    assert "start-" not in result.content
    assert "-end" not in result.content
    assert "…" not in result.content
    assert result.manifest.omitted == 1


def test_explicit_excerpt_is_emitted_verbatim_and_allows_later_work() -> None:
    explicit_excerpt = "caller selected this complete sentence."
    result = compose_context(
        [
            fragment("important", "x" * 20_000, priority=1, excerpt=explicit_excerpt),
            fragment("later", "small", component="tasks", priority=2),
        ]
    )

    lines = fragment_lines(result.content)
    assert [line["reference"] for line in lines] == ["important", "later"]
    assert lines[0]["delivery"] == "partial"
    assert lines[0]["payload"]["text"] == explicit_excerpt
    assert lines[1]["delivery"] == "full"
    assert result.manifest.partial == 1
    assert result.manifest.emitted == 2
    assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET


def test_large_high_priority_item_is_not_replaced_by_small_lower_priority_item() -> None:
    result = compose_context(
        [
            fragment("high", "x" * 20_000, priority=1),
            fragment("small-low", "fits", component="tasks", priority=100),
        ]
    )

    assert fragment_lines(result.content) == []
    assert result.manifest.emitted == 0
    assert result.manifest.omitted == 2
    assert component(result, "memories").omitted_references == ("high",)
    assert component(result, "tasks").omitted_references == ("small-low",)


def test_priority_then_input_order_is_deterministic() -> None:
    result = compose_context(
        [
            fragment("last", "3", priority=20),
            fragment("first", "1", priority=10),
            fragment("second", "2", priority=10),
        ]
    )

    assert [line["reference"] for line in fragment_lines(result.content)] == [
        "first",
        "second",
        "last",
    ]


def test_mapping_key_order_does_not_change_output_or_hash() -> None:
    first = ContextFragment(
        component="profile",
        priority=1,
        reference="project",
        payload={"z": 1, "a": 2},
    )
    second = ContextFragment(
        component="profile",
        priority=1,
        reference="project",
        payload={"a": 2, "z": 1},
    )

    first_result = compose_context([first])
    second_result = compose_context([second])

    assert first_result.content == second_result.content
    assert first_result.measurement.sha256 == second_result.measurement.sha256
    assert first_result.produced_measurement.sha256 == second_result.produced_measurement.sha256


def test_manifest_tracks_full_partial_and_omitted_items_per_component() -> None:
    result = compose_context(
        [
            fragment("full", "short", priority=1),
            fragment("partial", "x" * 20_000, priority=2, excerpt="summary"),
            fragment("omitted", "y" * 20_000, priority=3),
        ]
    )

    memories = component(result, "memories")
    assert memories.produced == 3
    assert memories.emitted == 2
    assert memories.partial == 1
    assert memories.omitted == 1
    assert memories.produced_references == ("full", "partial", "omitted")
    assert memories.emitted_references == ("full", "partial")
    assert memories.partial_references == ("partial",)
    assert memories.omitted_references == ("omitted",)
    assert memories.produced_measurement.budget_units > memories.emitted_measurement.budget_units
    # The complete source for both the partial and omitted items remains
    # excluded from the inline payload and is measured directly.
    assert memories.non_full_source_measurement.budget_units > DEFAULT_CONTEXT_BUDGET
    manifest_line = decoded_lines(result.content)[-1]["_dduo_context"]
    assert manifest_line["components"]["memories"] == {
        "produced": 3,
        "emitted": 2,
        "partial": 1,
        "omitted": 1,
    }


def test_required_fragment_without_fitting_representation_uses_valid_fallback() -> None:
    result = compose_context(
        [
            fragment("contract", "x" * 20_000, component="instructions", priority=0, required=True),
            fragment("memory", "small", priority=10),
        ]
    )

    assert result.fallback_used
    assert result.manifest.status == "fallback"
    assert result.manifest.fallback_reason == "required_fragment_exceeds_budget"
    assert result.manifest.emitted == 0
    assert result.manifest.omitted == 2
    assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET
    parsed = decoded_lines(result.content)
    assert len(parsed) == 1
    assert parsed[0]["_dduo_context"]["status"] == "fallback"


def test_required_fragment_may_use_its_explicit_excerpt_without_fallback() -> None:
    result = compose_context(
        [
            fragment(
                "contract",
                "x" * 20_000,
                component="instructions",
                priority=0,
                excerpt="minimal safe contract",
                required=True,
            )
        ]
    )

    assert not result.fallback_used
    assert result.manifest.status == "budgeted"
    assert result.manifest.partial == 1
    assert fragment_lines(result.content)[0]["payload"]["text"] == "minimal safe contract"


def test_required_fragment_reserves_space_for_later_required_representation() -> None:
    result = compose_context(
        [
            ContextFragment(
                component="manual",
                priority=0,
                reference="manual",
                payload={"text": "m" * 7_000},
                excerpt={"text": "manual excerpt"},
                required=True,
            ),
            ContextFragment(
                component="profile",
                priority=1,
                reference="profile",
                payload={"text": "p" * 4_000},
                excerpt={"text": "profile excerpt"},
                required=True,
            ),
        ]
    )

    assert not result.fallback_used
    assert result.manifest.status == "budgeted"
    assert component(result, "manual").emitted == 1
    assert component(result, "profile").emitted == 1
    assert result.manifest.partial == 1


def test_required_fragment_is_never_blocked_by_an_optional_priority_mistake() -> None:
    result = compose_context(
        [
            fragment("optional-huge", "x" * 20_000, priority=0),
            fragment(
                "required-contract",
                "must survive",
                component="instructions",
                priority=10,
                required=True,
            ),
        ]
    )

    assert not result.fallback_used
    assert [line["reference"] for line in fragment_lines(result.content)] == ["required-contract"]


@pytest.mark.parametrize("target", [9_000, 9_001])
def test_astral_unicode_uses_the_exact_utf16_boundary(target: int) -> None:
    empty = compose_context([fragment("astral-boundary", "")])
    remaining = target - empty.measurement.utf16_units
    value = "😀" + ("x" * (remaining - 2))

    result = compose_context([fragment("astral-boundary", value)])

    assert result.produced_measurement.utf16_units == target
    if target == DEFAULT_CONTEXT_BUDGET:
        assert result.measurement.utf16_units == target
        assert result.manifest.emitted == 1
    else:
        assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET
        assert result.manifest.omitted == 1


def test_compact_fallback_content_and_structured_manifest_remain_aligned() -> None:
    fragments = [
        fragment(
            "required",
            "x" * 5_000,
            component="instructions",
            priority=0,
            required=True,
        )
    ]
    fragments.extend(
        fragment(
            f"optional-{index}",
            "x",
            component=f"component-{index}",
            priority=index + 1,
        )
        for index in range(80)
    )

    result = compose_context(fragments, budget=180)
    wire = decoded_lines(result.content)[-1]["_dduo_context"]

    assert result.fallback_used
    assert wire["reason"] == "manifest_exceeds_budget"
    assert result.manifest.fallback_reason == wire["reason"]
    assert result.manifest.components == ()


def test_model_readable_fallback_is_measured_and_bounded() -> None:
    result = compose_fallback_context("Use normal project work while context recovers.")

    assert result.fallback_used
    assert result.manifest.status == "fallback"
    assert result.manifest.fallback_reason == "composition_failed"
    assert result.measurement.budget_units <= DEFAULT_CONTEXT_BUDGET
    assert fragment_lines(result.content)[0]["payload"]["text"].startswith("Use normal")


def test_every_rendered_line_is_complete_json_even_when_payload_contains_newlines() -> None:
    result = compose_context([fragment("multiline", "first line\nsecond line\nthird line")])

    lines = result.content.splitlines()
    assert len(lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert json.loads(lines[0])["payload"]["text"] == "first line\nsecond line\nthird line"


def test_invalid_unicode_and_non_json_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="valid Unicode"):
        compose_context([fragment("bad-unicode", "\ud800")])

    with pytest.raises(ValueError, match="deterministic JSON"):
        compose_context(
            [
                ContextFragment(
                    component="profile",
                    priority=1,
                    reference="bad-json",
                    payload={"value": object()},
                )
            ]
        )


def test_duplicate_identity_and_invalid_budget_are_rejected() -> None:
    duplicate = fragment("same", "one")
    with pytest.raises(ValueError, match="must be unique"):
        compose_context([duplicate, fragment("same", "two")])

    with pytest.raises(ValueError, match="positive"):
        compose_context([], budget=0)

    with pytest.raises(TypeError, match="integer"):
        compose_context([], budget=True)

    with pytest.raises(TypeError, match="boolean"):
        ContextFragment(
            component="profile",
            priority=1,
            reference="project",
            payload={},
            required="false",  # type: ignore[arg-type]
        )


def test_small_budget_raises_only_when_even_fixed_valid_fallback_cannot_fit() -> None:
    with pytest.raises(ValueError, match="too small"):
        compose_context(
            [fragment("required", "large", component="instructions", required=True)],
            budget=1,
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("component", "contains spaces", "component must be"),
        ("priority", True, "priority must be an integer"),
        ("reference", "", "reference must be a non-empty string"),
        ("reference", "r" * 257, "reference must be at most 256 characters"),
        ("payload", "not-a-mapping", "payload must be a mapping"),
        ("excerpt", "not-a-mapping", "excerpt must be a mapping"),
    ],
)
def test_fragment_contract_rejects_ambiguous_or_unbounded_values(
    field: str, value: object, error: str
) -> None:
    values = {
        "component": "profile",
        "priority": 1,
        "reference": "project",
        "payload": {},
        "excerpt": None,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=error):
        ContextFragment(**values)  # type: ignore[arg-type]


def test_invalid_utf16_and_fragment_collections_fail_before_delivery() -> None:
    with pytest.raises(ValueError, match="unpaired Unicode surrogate"):
        utf16_units("\udfff")

    with pytest.raises(TypeError, match="must be a sequence"):
        compose_context("not-a-fragment-sequence")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="only ContextFragment"):
        compose_context(["not-a-fragment"])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"message": 42}, "fallback message must be a string"),
        ({"message": "recover", "reason": "contains spaces"}, "fallback reason"),
        ({"message": "recover", "budget": False}, "budget must be an integer"),
        ({"message": "recover", "budget": 0}, "budget must be positive"),
    ],
)
def test_fallback_contract_rejects_values_that_cannot_be_stably_rendered(
    values: dict, error: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        compose_fallback_context(**values)


def test_oversized_fallback_message_degrades_to_bounded_omission() -> None:
    result = compose_fallback_context("recovery detail " * 1_000, budget=180)
    wire = decoded_lines(result.content)[-1]["_dduo_context"]

    assert result.fallback_used
    assert result.manifest.emitted == 0
    assert result.manifest.omitted == 1
    assert result.manifest.components[0].component == "health"
    assert wire["status"] == "fallback"
    assert wire["reason"] == "composition_failed"
    assert result.measurement.budget_units <= 180


def test_empty_and_mixed_delivery_manifests_report_exact_aggregate_totals() -> None:
    empty = compose_context([])
    assert empty.manifest.produced == 0
    assert empty.manifest.emitted == 0
    assert empty.manifest.partial == 0
    assert empty.manifest.omitted == 0
    assert empty.deferred_source_measurement.characters == 0

    mixed = compose_context(
        [
            fragment("full", "short", priority=1),
            fragment("partial", "x" * 20_000, priority=2, excerpt="summary"),
            fragment("blocked", "y" * 20_000, priority=3),
        ]
    )
    assert mixed.manifest.produced == 3
    assert mixed.manifest.emitted == 2
    assert mixed.manifest.partial == 1
    assert mixed.manifest.omitted == 1


def test_streaming_measurement_matches_the_exact_partial_jsonl_render() -> None:
    source = fragment("partial", "x" * 10_000, excerpt="explicit summary")
    candidates = [budget_module._Candidate(index=0, fragment=source)]
    outcomes = ["partial"]
    rendered = budget_module._render(
        candidates,
        outcomes,
        budget=DEFAULT_CONTEXT_BUDGET,
        status="budgeted",
    )

    streamed = budget_module._measure_render(
        candidates,
        outcomes,
        budget=DEFAULT_CONTEXT_BUDGET,
        status="budgeted",
    )

    assert streamed == budget_module._measure(rendered)
    assert streamed.budget_units == context_units(rendered)
    assert fragment_lines(rendered)[0]["delivery"] == "partial"


def test_internal_exact_measurement_rejects_unencodable_unicode() -> None:
    with pytest.raises(ValueError, match="unpaired Unicode surrogate"):
        budget_module._measure("\udfff")


def test_fragment_source_measurement_is_exact_for_unicode_jsonl() -> None:
    fragments = [
        fragment("one", "Memoria già verificata 🧠"),
        fragment("two", "Seconda riga", component="profile"),
    ]

    measurement = measure_fragment_sources(fragments)
    rendered = "\n".join(
        budget_module._Candidate(index=index, fragment=value).full_line
        for index, value in enumerate(fragments)
    )

    assert measurement.characters == len(rendered)
    assert measurement.utf8_bytes == len(rendered.encode("utf-8"))
    assert measurement.sha256 == hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    assert measurement.characters < measurement.utf8_bytes


def test_fragment_source_measurement_rejects_duplicate_identities() -> None:
    with pytest.raises(ValueError, match="pairs must be unique"):
        measure_fragment_sources([fragment("same", "one"), fragment("same", "two")])


@pytest.mark.parametrize("invalid", ["fragment", b"fragment", {"fragment": "value"}])
def test_fragment_source_measurement_requires_a_fragment_sequence(invalid) -> None:
    with pytest.raises(TypeError, match="sequence of ContextFragment"):
        measure_fragment_sources(invalid)


def test_fragment_source_measurement_rejects_non_fragment_members() -> None:
    with pytest.raises(TypeError, match="only ContextFragment"):
        measure_fragment_sources([fragment("valid", "value"), object()])
