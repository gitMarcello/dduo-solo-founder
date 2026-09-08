from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import assert_private_file
from dduo_solo_founder.client_telemetry import ClientTelemetrySpool
from dduo_solo_founder.schemas import AgentUsageObservation
from dduo_solo_founder import client_telemetry


def usage_event(event_id: str = "agent.claude.session.0.1") -> dict:
    return {
        "kind": "agent_usage",
        "event_id": event_id,
        "provider": "claude",
        "measurement_source": "local_estimate",
        "client_cost_usd": "0.001",
        "cost_source": "claude_code_client_estimate",
        "occurred_at": "2026-08-29T12:00:00+00:00",
    }


def test_spool_is_private_project_scoped_idempotent_and_acknowledged(tmp_path: Path):
    path = tmp_path / "private/events.json"
    spool = ClientTelemetrySpool(path)
    first = usage_event()
    second = usage_event("agent.claude.session.0.2")

    assert spool.enqueue("project-1", first) is True
    assert spool.enqueue("project-1", first) is False
    assert spool.enqueue("project-1", second) is True
    assert spool.enqueue("project-2", usage_event()) is True
    assert spool.peek("project-1", limit=1) == [first]
    assert [item["event_id"] for item in spool.peek("project-1")] == [
        first["event_id"],
        second["event_id"],
    ]
    assert spool.peek("project-2") == [first]

    assert spool.acknowledge("project-1", {first["event_id"]}) == 1
    assert spool.acknowledge("project-1", {first["event_id"]}) == 0
    assert spool.peek("project-1") == [second]
    assert spool.acknowledge("project-1", {second["event_id"]}) == 1
    assert spool.peek("project-1") == []
    assert spool.acknowledge("project-1", set()) == 0

    assert_private_file(path)
    if os.name != "nt":
        assert path.parent.stat().st_mode & 0o077 == 0
    persisted = json.loads(path.read_text())
    assert set(persisted["projects"]) == {"project-2"}


def test_spool_capacity_is_atomic_and_never_evicts_measured_events(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(client_telemetry, "MAX_PENDING_PER_PROJECT", 2)
    path = tmp_path / "private/events.json"
    spool = ClientTelemetrySpool(path)
    first = usage_event("agent.claude.session.0.1")
    second = usage_event("agent.claude.session.0.2")
    third = usage_event("agent.claude.session.0.3")

    assert spool.enqueue("p1", first) is True
    assert spool.enqueue("p1", second) is True
    # An idempotent retry remains harmless even while the queue is full.
    assert spool.enqueue("p1", first) is False
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="outbox is full"):
        spool.enqueue("p1", third)

    assert path.read_bytes() == before
    assert spool.peek("p1") == [first, second]


def test_enqueue_many_is_one_atomic_write_and_deduplicates_the_batch(monkeypatch, tmp_path: Path):
    spool = ClientTelemetrySpool(tmp_path / "private/events.json")
    writes = []
    original_write = spool._write

    def tracked_write(document):
        writes.append(document)
        original_write(document)

    monkeypatch.setattr(spool, "_write", tracked_write)
    events = [usage_event(f"agent.claude.session.0.{index}") for index in range(318)]

    assert spool.enqueue_many("p1", [*events, events[0], events[317]]) == 318
    assert len(writes) == 1
    assert len(spool.peek("p1", limit=100)) == 100
    assert spool.enqueue_many("p1", events) == 0
    assert len(writes) == 1


def test_enqueue_many_capacity_failure_does_not_persist_a_partial_batch(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(client_telemetry, "MAX_PENDING_PER_PROJECT", 3)
    spool = ClientTelemetrySpool(tmp_path / "events.json")
    assert spool.enqueue("p1", usage_event("agent.claude.existing")) is True
    before = spool.path.read_bytes()

    with pytest.raises(RuntimeError, match="outbox is full"):
        spool.enqueue_many(
            "p1",
            [
                usage_event("agent.claude.new.1"),
                usage_event("agent.claude.new.2"),
                usage_event("agent.claude.new.3"),
            ],
        )

    assert spool.path.read_bytes() == before
    assert [item["event_id"] for item in spool.peek("p1")] == ["agent.claude.existing"]


def test_spool_rejects_an_oversized_persisted_queue_instead_of_truncating_it(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(client_telemetry, "MAX_PENDING_PER_PROJECT", 2)
    path = tmp_path / "private/events.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": [
                        usage_event("agent.claude.session.0.1"),
                        usage_event("agent.claude.session.0.2"),
                        usage_event("agent.claude.session.0.3"),
                    ]
                },
            }
        )
    )
    path.chmod(0o600)

    with pytest.raises(RuntimeError, match="exceeds capacity"):
        ClientTelemetrySpool(path).peek("p1")


def test_spool_discards_retired_guard_events_without_blocking_usage(tmp_path: Path):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": [
                        {
                            "kind": "usage_guard",
                            "event_id": "guard.codex.legacy",
                            "provider": "codex",
                            "operation": "usage_guard.threshold_reached",
                            "measurement_source": "provider_reported",
                            "occurred_at": "2026-08-29T12:00:00+00:00",
                        },
                        usage_event(),
                    ]
                },
            }
        )
    )
    path.chmod(0o600)
    spool = ClientTelemetrySpool(path)

    assert spool.peek("p1") == [usage_event()]
    persisted = json.loads(path.read_text())
    assert persisted["projects"]["p1"] == [usage_event()]
    assert spool.enqueue("p1", usage_event("agent.claude.session.0.2")) is True
    assert [item["kind"] for item in spool.peek("p1")] == ["agent_usage", "agent_usage"]


def test_spool_removes_a_guard_only_legacy_queue_on_read(tmp_path: Path):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": {
                    "p1": [
                        {
                            "kind": "usage_guard",
                            "event_id": "guard.codex.legacy",
                            "provider": "codex",
                            "operation": "usage_guard.threshold_reached",
                            "measurement_source": "provider_reported",
                            "occurred_at": "2026-08-29T12:00:00+00:00",
                        }
                    ]
                },
            }
        )
    )
    path.chmod(0o600)

    assert ClientTelemetrySpool(path).peek("p1") == []
    assert json.loads(path.read_text()) == {"version": 1, "projects": {}}


def test_spool_capacity_is_project_scoped_without_touching_other_projects(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(client_telemetry, "MAX_PENDING_PER_PROJECT", 2)
    spool = ClientTelemetrySpool(tmp_path / "events.json")
    for index in range(2):
        spool.enqueue("p1", usage_event(f"agent.claude.session.0.{index}"))
    spool.enqueue("p2", usage_event("agent.claude.other.0.1"))
    assert spool.enqueue("p1", usage_event("agent.claude.session.0.0")) is False
    with pytest.raises(RuntimeError, match="outbox is full"):
        spool.enqueue("p1", usage_event("agent.claude.session.0.2"))
    assert [item["event_id"] for item in spool.peek("p1")] == [
        "agent.claude.session.0.0",
        "agent.claude.session.0.1",
    ]
    assert spool.peek("p2") == [usage_event("agent.claude.other.0.1")]


@pytest.mark.parametrize(
    "event",
    [
        {**usage_event(), "prompt": "never persist content"},
        {**usage_event(), "nested": {"content": "no"}},
        {**usage_event(), "client_cost_usd": float("nan")},
        {**usage_event(), "event_id": "bad id"},
        {**usage_event(), "kind": "other"},
    ],
)
def test_spool_rejects_content_unknown_shapes_and_nonfinite_numbers(tmp_path: Path, event: dict):
    spool = ClientTelemetrySpool(tmp_path / "events.json")
    with pytest.raises(ValueError):
        spool.enqueue("p1", event)
    assert not spool.path.exists()


@pytest.mark.parametrize(
    "cost",
    [
        0,
        "0.000000000001",
        "0.1234567890120",
        "99999999.999999999999",
    ],
)
def test_client_cost_matches_server_decimal_precision_without_rounding(
    tmp_path: Path, cost: str | int
):
    event = {**usage_event(), "client_cost_usd": cost}
    server_value = AgentUsageObservation.model_validate(event).client_cost_usd
    spool = ClientTelemetrySpool(tmp_path / "events.json")

    assert spool.enqueue("p1", event) is True
    assert spool.peek("p1")[0]["client_cost_usd"] == cost
    assert server_value == Decimal(str(cost))


@pytest.mark.parametrize(
    "cost",
    [
        "100000000",
        "0.0000000000001",
        "99999999.9999999999999",
        "-0.000000000001",
        "NaN",
        "Infinity",
        "not-a-number",
        True,
    ],
)
def test_client_cost_rejects_every_value_the_server_decimal_contract_rejects(
    tmp_path: Path, cost: str | bool
):
    event = {**usage_event(), "client_cost_usd": cost}
    with pytest.raises(ValidationError):
        AgentUsageObservation.model_validate(event)

    spool = ClientTelemetrySpool(tmp_path / "events.json")
    with pytest.raises(ValueError, match="client telemetry cost"):
        spool.enqueue("p1", event)
    assert not spool.path.exists()


def test_spool_rejects_invalid_projects_corruption_and_unsafe_permissions(tmp_path: Path):
    path = tmp_path / "events.json"
    spool = ClientTelemetrySpool(path)
    with pytest.raises(ValueError, match="project"):
        spool.enqueue("../other", usage_event())

    path.write_text("not json")
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="unreadable"):
        spool.peek("p1")

    path.write_text(json.dumps({"version": 2, "projects": {}}))
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="unsupported"):
        spool.peek("p1")

    if os.name != "nt":
        path.write_text(json.dumps({"version": 1, "projects": {}}))
        path.chmod(0o644)
        with pytest.raises(RuntimeError, match="permissions"):
            spool.peek("p1")

    for value, message in (
        ({"version": 1, "projects": []}, "unsupported"),
        ({"version": 1, "projects": {"p1": {}}}, "queue"),
        ({"version": 1, "projects": {"../bad": []}}, "project"),
    ):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        with pytest.raises((RuntimeError, ValueError), match=message):
            spool.peek("p1")


def test_spool_does_not_follow_file_or_directory_symlinks(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text("{}")
    state_link = tmp_path / "events.json"
    state_link.symlink_to(target)
    with pytest.raises(RuntimeError, match="outbox is unsafe"):
        ClientTelemetrySpool(state_link).peek("p1")

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    directory_link = tmp_path / "linked"
    directory_link.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(RuntimeError, match="directory is unsafe"):
        ClientTelemetrySpool(directory_link / "events.json").peek("p1")


def test_spool_rejects_non_object_events_and_nested_values_in_allowed_fields(
    tmp_path: Path,
):
    spool = ClientTelemetrySpool(tmp_path / "events.json")

    with pytest.raises(ValueError, match="must be an object"):
        client_telemetry._event([])
    with pytest.raises(ValueError, match="must remain scalar"):
        spool.enqueue("p1", {**usage_event(), "model": ["claude-sonnet"]})

    assert not spool.path.exists()


@pytest.mark.parametrize(
    "project_id",
    ["", " ", "x" * 129, "project/other", "project.other", "project other"],
)
def test_spool_rejects_every_project_id_outside_the_private_namespace(
    tmp_path: Path, project_id: str
):
    spool = ClientTelemetrySpool(tmp_path / "events.json")

    with pytest.raises(ValueError, match="invalid project id"):
        spool.peek(project_id)

    assert not spool.path.exists()


def test_peek_bounds_batches_without_mutating_the_durable_queue(tmp_path: Path):
    spool = ClientTelemetrySpool(tmp_path / "events.json")
    for index in range(3):
        spool.enqueue("p1", usage_event(f"agent.claude.session.0.{index}"))
    before = spool.path.read_bytes()

    assert len(spool.peek("p1", limit=0)) == 1
    assert len(spool.peek("p1", limit=-100)) == 1
    assert len(spool.peek("p1", limit=1_000)) == 3
    assert spool.path.read_bytes() == before


def test_event_id_contract_accepts_boundary_and_rejects_empty_or_oversized(
    tmp_path: Path,
):
    spool = ClientTelemetrySpool(tmp_path / "events.json")
    boundary = "a" * 192
    assert spool.enqueue("p1", usage_event(boundary)) is True

    for invalid in ("", "a" * 193, "contains:colon"):
        with pytest.raises(ValueError, match="event is invalid"):
            spool.enqueue("p1", usage_event(invalid))

    assert [item["event_id"] for item in spool.peek("p1")] == [boundary]
