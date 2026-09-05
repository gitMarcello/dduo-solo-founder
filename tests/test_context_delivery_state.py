from __future__ import annotations

from copy import deepcopy

import pytest

from dduo_solo_founder import hooks


def _measurement(*, items: int = 1) -> dict[str, int]:
    value = 0 if items == 0 else 1
    return {
        "items": items,
        "characters": value,
        "utf16_units": value,
        "utf8_bytes": value,
        "budget_units": value,
    }


def _baseline() -> dict:
    return {
        "version": hooks.CONTEXT_DELIVERY_STATE_VERSION,
        "render_version": hooks.HOOK_CONTEXT_RENDER_VERSION,
        "binding_id": "binding-id",
        "foundation_hash": "a" * 64,
        "work_hash": "b" * 64,
        "delivered_work_references": {"plans": ["plan-1"], "tasks": ["task-1"]},
        "stable_measurements": {
            "foundation": _measurement(),
            "work": _measurement(),
        },
    }


def test_delivery_baseline_accepts_only_the_current_trusted_shape() -> None:
    baseline = _baseline()

    assert (
        hooks._delivery_baseline(
            {"context_delivery_baseline": baseline},
            binding_id="binding-id",
            external_session_id="session-1",
        )
        is baseline
    )


@pytest.mark.parametrize(
    ("path", "invalid"),
    [
        (("version",), 0),
        (("render_version",), "old-render"),
        (("binding_id",), "another-binding"),
        (("foundation_hash",), "not-a-sha256"),
        (("delivered_work_references",), []),
        (("delivered_work_references", "plans"), "plan-1"),
        (("delivered_work_references", "tasks"), [""]),
        (("stable_measurements",), []),
        (("stable_measurements", "foundation"), []),
    ],
)
def test_delivery_baseline_rejects_stale_or_malformed_state(path, invalid) -> None:
    baseline = _baseline()
    target = baseline
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid

    assert (
        hooks._delivery_baseline(
            {"context_delivery_baseline": baseline},
            binding_id="binding-id",
            external_session_id="session-1",
        )
        is None
    )


def test_delivery_measurement_validation_rejects_booleans_and_skips_unknown_records() -> None:
    invalid = _measurement()
    invalid["characters"] = True
    partially_invalid = _measurement()
    partially_invalid["utf8_bytes"] = True

    assert hooks._valid_measurement_record(invalid) is False
    assert hooks._add_measurement_records("invalid", _measurement()) == _measurement()
    assert hooks._add_measurement_records(partially_invalid) == {
        "items": 1,
        "characters": 1,
        "utf16_units": 1,
        "utf8_bytes": 0,
        "budget_units": 1,
    }


@pytest.mark.parametrize(
    ("foundation_changed", "work_changed", "expected"),
    [
        (True, True, "foundation_and_work_changed"),
        (True, False, "foundation_changed"),
        (False, True, "work_changed"),
        (False, False, "unchanged"),
    ],
)
def test_context_delivery_reason_distinguishes_each_delta(
    foundation_changed: bool,
    work_changed: bool,
    expected: str,
) -> None:
    assert (
        hooks._context_delivery_reason(
            foundation_changed=foundation_changed,
            work_changed=work_changed,
        )
        == expected
    )


def test_context_reuse_measurement_ignores_untrusted_state_and_selects_stable_groups() -> None:
    empty = _measurement(items=0)
    assert (
        hooks._context_reuse_measurement(
            baseline={"stable_measurements": []},
            foundation_changed=False,
            work_changed=False,
        )
        == empty
    )

    baseline = _baseline()
    assert (
        hooks._context_reuse_measurement(
            baseline=baseline,
            foundation_changed=True,
            work_changed=False,
        )
        == _measurement()
    )


def test_fallback_composition_never_advances_the_delivery_baseline() -> None:
    state = {"context_delivery_baseline": _baseline()}
    original = deepcopy(state)

    hooks._store_delivery_baseline(
        state,
        context={},
        composed=hooks.compose_unavailable_context("Memory is temporarily unavailable."),
        binding_id="binding-id",
    )

    assert state == original


def test_remote_offline_context_requires_a_remote_binding_and_cached_manual() -> None:
    assert (
        hooks.compose_remote_offline_context(
            None,
            contracts="",
        )
        is None
    )
    local = hooks.ProjectBinding(
        project_id="project",
        name="Project",
        root_path=hooks.Path("/project"),
        kind="local",
        api_url="http://127.0.0.1:8765",
        dashboard_url="http://127.0.0.1:4173",
        binding_id="binding-id",
    )
    assert hooks.compose_remote_offline_context(local, contracts="") is None

    remote = hooks.ProjectBinding(
        project_id="project",
        name="Project",
        root_path=hooks.Path("/project"),
        kind="remote",
        api_url="https://memory.example/api",
        dashboard_url=None,
        binding_id="binding-id",
        bearer_token="token",
    )
    assert hooks.compose_remote_offline_context(remote, contracts="") is None


def test_hook_runtime_detection_and_event_validation_are_explicit(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/claude")
    monkeypatch.delenv("PLUGIN_ROOT", raising=False)
    assert hooks.is_claude_runtime() is True

    monkeypatch.setenv("PLUGIN_ROOT", "/codex")
    assert hooks.is_claude_runtime() is False

    with pytest.raises(ValueError, match="unsupported context hook event"):
        hooks.emit("context", "Unsupported")
