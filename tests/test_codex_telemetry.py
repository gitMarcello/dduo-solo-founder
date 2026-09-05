from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dduo_solo_founder import codex_telemetry
from dduo_solo_founder.codex_telemetry import (
    CodexRequestUsage,
    CodexTurnUsage,
    capture_codex_transcript_cursor,
    parse_codex_transcript_usage,
    read_codex_transcript_usage,
)
from dduo_solo_founder.schemas import AgentUsageObservation


FIXTURE = Path(__file__).parent / "fixtures/codex/usage-transcript.jsonl"


def fixture_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def test_realistic_transcript_keeps_request_boundaries_and_cache_classes():
    turns = parse_codex_transcript_usage(fixture_lines(), session_id="session-alpha")

    assert [item.turn_id for item in turns] == ["turn-standard", "turn-long"]
    standard, long_context = turns
    assert len(standard.requests) == 2
    assert standard.input_tokens == 500_000
    assert standard.cached_input_tokens == 420_000
    assert standard.cache_write_input_tokens == 10_000
    assert standard.uncached_input_tokens == 70_000
    assert standard.output_tokens == 20_000
    assert standard.reasoning_tokens == 8_000
    assert standard.reported_total_tokens == 520_000
    assert standard.cache_hit_percent == Decimal("84")
    assert standard.requests[0].cache_hit_percent == Decimal("80")

    # The turn aggregate crosses 272k, but neither request does. Pricing each
    # request separately avoids a false long-context multiplier.
    assert standard.api_equivalent_cost_usd == Decimal("0.898")
    assert [item.request_ordinal for item in standard.requests] == [0, 1]
    assert [item.uncached_input_tokens for item in standard.requests] == [50_000, 20_000]

    # The repeated snapshot at the start of the second turn is not another
    # request. Only its real 300k request receives the long-context rates.
    assert len(long_context.requests) == 1
    assert long_context.requests[0].request_ordinal == 0
    assert long_context.api_equivalent_cost_usd == Decimal("1.110")


def test_target_turn_still_scans_prior_cumulative_baseline():
    turns = parse_codex_transcript_usage(
        fixture_lines(), session_id="session-alpha", turn_id="turn-long"
    )
    assert len(turns) == 1
    assert turns[0].turn_id == "turn-long"
    assert len(turns[0].requests) == 1
    assert turns[0].input_tokens == 300_000


def test_events_are_stable_content_free_and_match_server_contract():
    first = parse_codex_transcript_usage(
        fixture_lines(), session_id="session-alpha", turn_id="turn-standard"
    )[0]
    replay = parse_codex_transcript_usage(
        fixture_lines(), session_id="session-alpha", turn_id="turn-standard"
    )[0]

    events = [item.client_event("session-alpha") for item in first.requests]
    replay_events = [item.client_event("session-alpha") for item in replay.requests]
    assert events == replay_events
    assert len({item["event_id"] for item in events}) == 2
    assert all(AgentUsageObservation.model_validate(item) for item in events)
    serialized = json.dumps(events)
    assert "private" not in serialized
    assert "content" not in serialized
    assert all(
        set(item)
        <= {
            "kind",
            "event_id",
            "provider",
            "model",
            "measurement_source",
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "reported_total_tokens",
            "occurred_at",
        }
        for item in events
    )


def test_session_mismatch_invalid_identifiers_and_unknown_turn_fail_open():
    lines = fixture_lines()
    assert parse_codex_transcript_usage(lines, session_id="another-session") == ()
    assert parse_codex_transcript_usage(lines, session_id="") == ()
    assert parse_codex_transcript_usage(lines, session_id="session-alpha", turn_id="") == ()
    assert (
        parse_codex_transcript_usage(lines, session_id="session-alpha", turn_id="not-in-transcript")
        == ()
    )


def test_missing_cache_write_is_reported_unknown_and_never_priced_as_zero():
    lines = [
        json.dumps(
            {
                "timestamp": "2026-08-30T20:00:00Z",
                "type": "session_meta",
                "payload": {"id": "s"},
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-30T20:00:01Z",
                "type": "turn_context",
                "payload": {"turn_id": "t", "model": "gpt-5.6-terra"},
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-30T20:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 50,
                            "output_tokens": 10,
                            "total_tokens": 110,
                        }
                    },
                },
            }
        ),
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert turn.cached_input_tokens == 50
    assert turn.cache_write_input_tokens is None
    assert turn.uncached_input_tokens is None
    assert turn.api_equivalent_cost_usd is None


def test_ambiguous_cumulative_delta_and_bad_measurements_are_not_invented():
    lines = [
        '{"timestamp":"2026-08-30T20:00:00Z","type":"session_meta","payload":{"id":"s"}}',
        '{"timestamp":"2026-08-30T20:00:01Z","type":"turn_context","payload":{"turn_id":"t","model":"bad model name"}}',
        # Valid first request, but an unknown model keeps cost unavailable.
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"cached_input_tokens":50,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":3,"total_tokens":110},"last_token_usage":{"input_tokens":100,"cached_input_tokens":50,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":3,"total_tokens":110}}}}',
        # Cumulative delta says 100 input, while last says 99: ambiguous, skip.
        '{"timestamp":"2026-08-30T20:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":200,"cached_input_tokens":100,"cache_write_input_tokens":0,"output_tokens":20,"reasoning_output_tokens":6,"total_tokens":220},"last_token_usage":{"input_tokens":99,"cached_input_tokens":50,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":3,"total_tokens":109}}}}',
        # Cache classes exceeding input make the provider record invalid.
        '{"timestamp":"2026-08-30T20:00:04Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"cached_input_tokens":11,"cache_write_input_tokens":0,"output_tokens":1,"total_tokens":11}}}}',
        # Missing timezone and malformed JSON also fail open.
        '{"timestamp":"2026-08-30T20:00:05","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"total_tokens":2}}}}',
        "not json at all",
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert len(turn.requests) == 1
    assert turn.requests[0].model is None
    assert turn.api_equivalent_cost_usd is None


def test_app_server_notification_aliases_share_the_same_numeric_core():
    lines = [
        json.dumps(
            {
                "timestamp": "2026-08-30T20:00:00Z",
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "s",
                    "turnId": "t",
                    "tokenUsage": {
                        "last": {
                            "inputTokens": 100,
                            "cachedInputTokens": 80,
                            "cacheWriteInputTokens": 0,
                            "outputTokens": 10,
                            "reasoningOutputTokens": 4,
                            "totalTokens": 110,
                        },
                        "total": {
                            "inputTokens": 100,
                            "cachedInputTokens": 80,
                            "cacheWriteInputTokens": 0,
                            "outputTokens": 10,
                            "reasoningOutputTokens": 4,
                            "totalTokens": 110,
                        },
                    },
                },
            }
        )
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert turn.turn_id == "t"
    assert turn.uncached_input_tokens == 20
    assert turn.requests[0].reasoning_tokens == 4


def test_model_reroute_prices_only_against_the_active_target_model():
    lines = [
        '{"timestamp":"2026-08-30T20:00:00Z","type":"session_meta","payload":{"id":"s"}}',
        '{"timestamp":"2026-08-30T20:00:01Z","type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}',
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"model_reroute","to_model":"gpt-5.6-luna"}}',
        '{"timestamp":"2026-08-30T20:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":10,"total_tokens":110},"total_token_usage":{"input_tokens":100,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":10,"total_tokens":110}}}}',
    ]
    request = parse_codex_transcript_usage(lines, session_id="s")[0].requests[0]
    assert request.model == "gpt-5.6-luna"
    assert request.api_equivalent_cost() is not None

    unknown = [
        lines[0],
        lines[1],
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"model_reroute","target":{"unexpected":true}}}',
        lines[3],
    ]
    request = parse_codex_transcript_usage(unknown, session_id="s")[0].requests[0]
    assert request.model is None
    assert request.api_equivalent_cost() is None


def test_counter_reset_uses_provider_last_and_sanitizes_optional_totals():
    lines = [
        '{"timestamp":"2026-08-30T20:00:00Z","type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-luna"}}',
        '{"timestamp":"2026-08-30T20:00:01Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":3,"total_tokens":110},"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":3,"total_tokens":110}}}}',
        # A provider epoch reset is unambiguous because last is still present.
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":20,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":1,"total_tokens":999},"last_token_usage":{"input_tokens":20,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":3,"total_tokens":999}}}}',
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert len(turn.requests) == 2
    assert turn.requests[1].reasoning_tokens is None
    assert turn.requests[1].reported_total_tokens is None
    assert turn.api_equivalent_cost_usd == Decimal("0.0000384")


def test_file_reader_is_fail_open_and_does_not_follow_symlinks(monkeypatch, tmp_path: Path):
    assert (
        read_codex_transcript_usage(FIXTURE, session_id="session-alpha", turn_id="turn-long")[
            0
        ].input_tokens
        == 300_000
    )
    assert read_codex_transcript_usage(tmp_path / "missing.jsonl", session_id="session-alpha") == ()
    symlink = tmp_path / "transcript.jsonl"
    symlink.symlink_to(FIXTURE)
    assert read_codex_transcript_usage(symlink, session_id="session-alpha") == ()
    monkeypatch.setattr(codex_telemetry, "_open_regular_transcript", lambda _path: None)
    assert read_codex_transcript_usage(FIXTURE, session_id="session-alpha") == ()


def test_cursor_skips_a_large_prefix_and_streams_an_oversized_tool_line(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(codex_telemetry, "CURSOR_TAIL_SCAN_BYTES", 1024 * 1024)
    transcript = tmp_path / "rollout.jsonl"
    session = '{"timestamp":"2026-08-30T20:00:00Z","type":"session_meta","payload":{"id":"s"}}\n'
    previous_context = (
        '{"timestamp":"2026-08-30T20:00:01Z","type":"turn_context",'
        '"payload":{"turn_id":"previous","model":"gpt-5.6-sol"}}\n'
    )
    previous_usage = (
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"last_token_usage":'
        '{"input_tokens":100,"cached_input_tokens":80,"cache_write_input_tokens":0,'
        '"output_tokens":10,"total_tokens":110},"total_token_usage":'
        '{"input_tokens":100,"cached_input_tokens":80,"cache_write_input_tokens":0,'
        '"output_tokens":10,"total_tokens":110}}}}\n'
    )
    large_prefix = "p" * (3 * 1024 * 1024)
    transcript.write_text(
        session + large_prefix + "\n" + previous_context + previous_usage,
        encoding="utf-8",
    )
    cursor = capture_codex_transcript_cursor(transcript, session_id="s", turn_id="current")
    assert cursor is not None
    start = transcript.stat().st_size
    assert cursor["offset"] == start
    assert cursor["baseline_total"]["input_tokens"] == 100

    current_context = (
        '{"timestamp":"2026-08-30T20:00:03Z","type":"turn_context",'
        '"payload":{"turn_id":"current","model":"gpt-5.6-sol"}}\n'
    )
    huge_tool_line = (
        '{"type":"response_item","payload":{"tool":"'
        + "x" * (2 * 1024 * 1024 + 1)
        + '"}}\n'
    )
    current_usage = (
        '{"timestamp":"2026-08-30T20:00:04Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"last_token_usage":'
        '{"input_tokens":50,"cached_input_tokens":40,"cache_write_input_tokens":0,'
        '"output_tokens":5,"total_tokens":55},"total_token_usage":'
        '{"input_tokens":150,"cached_input_tokens":120,"cache_write_input_tokens":0,'
        '"output_tokens":15,"total_tokens":165}}}}\n'
    )
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(current_context + huge_tool_line + current_usage)

    read_sizes = []
    real_read = codex_telemetry.os.read

    def tracked_read(descriptor, size):
        read_sizes.append(size)
        return real_read(descriptor, size)

    monkeypatch.setattr(codex_telemetry.os, "read", tracked_read)
    measured = read_codex_transcript_usage(
        transcript,
        session_id="s",
        turn_id="current",
        cursor=cursor,
    )

    assert len(measured) == 1
    assert measured[0].input_tokens == 50
    assert measured[0].cached_input_tokens == 40
    assert measured[0].requests[0].model == "gpt-5.6-sol"
    assert max(read_sizes) <= codex_telemetry.TRANSCRIPT_READ_CHUNK_BYTES
    assert sum(read_sizes) < start


def test_cursor_requires_a_verified_session_and_unambiguous_baseline(monkeypatch, tmp_path: Path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"type":"turn_context","payload":{"turn_id":"t"}}\n')
    assert capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t") is None

    transcript.write_text(
        '{"type":"session_meta","payload":{"id":"s"}}\n'
        '{"timestamp":"2026-08-30T20:00:00Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"total_token_usage":'
        '{"input_tokens":10,"output_tokens":1,"total_tokens":11},"last_token_usage":'
        '{"input_tokens":10,"output_tokens":1,"total_tokens":11}}}}\n'
        + ("x" * 1024)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_telemetry, "CURSOR_TAIL_SCAN_BYTES", 128)
    assert capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t") is None


def test_unverified_cursor_is_never_treated_as_prevalidated(tmp_path: Path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"type":"session_meta","payload":{"id":"s"}}\n')
    cursor = capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t")
    assert cursor is not None
    cursor["session_verified"] = False
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}\n'
            '{"timestamp":"2026-08-30T20:00:01Z","type":"event_msg",'
            '"payload":{"type":"token_count","info":{"total_token_usage":'
            '{"input_tokens":10,"output_tokens":1,"total_tokens":11},"last_token_usage":'
            '{"input_tokens":10,"output_tokens":1,"total_tokens":11}}}}\n'
        )
    assert read_codex_transcript_usage(
        transcript, session_id="s", turn_id="t", cursor=cursor
    ) == ()


def test_cursor_baseline_prevents_repeated_cumulative_snapshot_overcount(tmp_path: Path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        '{"type":"session_meta","payload":{"id":"s"}}\n'
        '{"timestamp":"2026-08-30T20:00:00Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"total_token_usage":'
        '{"input_tokens":100,"output_tokens":10,"total_tokens":110},"last_token_usage":'
        '{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}\n',
        encoding="utf-8",
    )
    cursor = capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t")
    assert cursor is not None and cursor["baseline_total"]["input_tokens"] == 100
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}\n'
            # Codex may repeat the latest cumulative snapshot at a boundary.
            '{"timestamp":"2026-08-30T20:00:01Z","type":"event_msg",'
            '"payload":{"type":"token_count","info":{"total_token_usage":'
            '{"input_tokens":100,"output_tokens":10,"total_tokens":110},"last_token_usage":'
            '{"input_tokens":100,"output_tokens":10,"total_tokens":110}}}}\n'
            '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg",'
            '"payload":{"type":"token_count","info":{"total_token_usage":'
            '{"input_tokens":150,"output_tokens":15,"total_tokens":165},"last_token_usage":'
            '{"input_tokens":50,"output_tokens":5,"total_tokens":55}}}}\n'
        )
    measured = read_codex_transcript_usage(
        transcript, session_id="s", turn_id="t", cursor=cursor
    )
    assert len(measured) == 1
    assert [request.input_tokens for request in measured[0].requests] == [50]


def test_request_ceiling_accepts_1000_and_rejects_1001_without_partial_usage(tmp_path: Path):
    prefix = [
        '{"type":"session_meta","payload":{"id":"s"}}',
        '{"type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}',
    ]

    def token_line(index: int) -> str:
        return json.dumps(
            {
                "timestamp": "2026-08-30T20:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                        "total_token_usage": {
                            "input_tokens": index,
                            "output_tokens": index,
                            "total_tokens": index * 2,
                        },
                    },
                },
            },
            separators=(",", ":"),
        )

    thousand = [*prefix, *(token_line(index) for index in range(1, 1001))]
    measured = parse_codex_transcript_usage(thousand, session_id="s", turn_id="t")
    assert len(measured) == 1 and len(measured[0].requests) == 1000
    assert parse_codex_transcript_usage(
        [*thousand, token_line(1001)], session_id="s", turn_id="t"
    ) == ()

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(prefix[0] + "\n", encoding="utf-8")
    cursor = capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t")
    assert cursor is not None
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write("\n".join([prefix[1], *(token_line(index) for index in range(1, 1002))]))
        handle.write("\n")
    result = codex_telemetry.read_codex_transcript_usage_result(
        transcript, session_id="s", turn_id="t", cursor=cursor
    )
    assert result.status == "terminal_unavailable"
    assert result.usage == ()


def test_cursor_rejects_rotation_mismatch_and_an_unbounded_single_turn(monkeypatch, tmp_path: Path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text('{"type":"session_meta","payload":{"id":"s"}}\n', encoding="utf-8")
    cursor = capture_codex_transcript_cursor(transcript, session_id="s", turn_id="t")
    assert cursor is not None

    rotated = tmp_path / "rotated.jsonl"
    rotated.write_bytes(transcript.read_bytes())
    assert read_codex_transcript_usage(rotated, session_id="s", turn_id="t", cursor=cursor) == ()

    with transcript.open("r+b") as handle:
        handle.seek(cursor["offset"] + codex_telemetry.MAX_INCREMENTAL_SCAN_BYTES + 1)
        handle.write(b"\n")
    monkeypatch.setattr(
        codex_telemetry,
        "_bounded_jsonl_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    assert read_codex_transcript_usage(transcript, session_id="s", turn_id="t", cursor=cursor) == ()


def test_empty_and_partially_unknown_turn_metrics_remain_unavailable():
    empty = CodexTurnUsage(turn_id="empty", requests=())
    assert empty.input_tokens == 0
    assert empty.output_tokens == 0
    assert empty.cache_hit_percent is None
    assert empty.api_equivalent_cost_usd is None

    sample = CodexRequestUsage(
        turn_id="t",
        request_ordinal=0,
        model="gpt-5.6-sol",
        occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        input_tokens=0,
        cached_input_tokens=None,
        cache_write_input_tokens=None,
        output_tokens=0,
        reasoning_tokens=None,
        reported_total_tokens=None,
    )
    unknown = CodexTurnUsage(turn_id="t", requests=(sample,))
    assert sample.uncached_input_tokens is None
    assert sample.cache_hit_percent is None
    assert unknown.cached_input_tokens is None
    assert unknown.cache_write_input_tokens is None
    assert unknown.uncached_input_tokens is None
    assert unknown.reasoning_tokens is None
    assert unknown.reported_total_tokens is None
    assert unknown.cache_hit_percent is None
    assert unknown.api_equivalent_cost_usd is None


def test_bad_envelopes_timestamps_and_partial_counter_resets_fail_open():
    lines = [
        # Invalid session metadata must not override the caller session.
        '{"type":"session_meta","payload":{"id":42}}',
        # Missing turn id, then a token event cannot be correlated.
        '{"type":"turn_context","payload":{"turn_id":null}}',
        '{"timestamp":"2026-08-30T20:00:00Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"total_tokens":2}}}}',
        # Set a usable turn and baseline.
        '{"timestamp":"2026-08-30T20:00:01Z","type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}',
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"cached_input_tokens":8,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11},"last_token_usage":{"input_tokens":10,"cached_input_tokens":8,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11}}}}',
        # Input/output grow but cached cumulative regresses: partial reset is
        # ambiguous, so the apparently valid last counter is still rejected.
        '{"timestamp":"2026-08-30T20:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":20,"cached_input_tokens":7,"cache_write_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":0,"total_tokens":22},"last_token_usage":{"input_tokens":10,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11}}}}',
        # Unknown event and malformed relevant JSON exercise fail-open parsing.
        '{"type":"response_item","payload":{"note":"token_count"}}',
        '{"timestamp":null,"type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":1,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":1,"total_tokens":2}}}}',
        '{"timestamp":"2026-08-30T20:00:04Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":null,"output_tokens":1}}}}',
        '{"timestamp":"bad","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":1,"output_tokens":1}}}}',
        '{"type":"event_msg","payload":{"type":"task_complete"}}',
        '{"type":"event_msg","payload":{"type":"token_count"',
        '[] "token_count"',
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert len(turn.requests) == 1


def test_known_optional_delta_disagreement_is_rejected():
    lines = [
        '{"timestamp":"2026-08-30T20:00:00Z","type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-sol"}}',
        '{"timestamp":"2026-08-30T20:00:01Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"cached_input_tokens":2,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11},"last_token_usage":{"input_tokens":10,"cached_input_tokens":2,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11}}}}',
        # The required totals match a 10/1 request, but cached delta is 5 and
        # last reports 4. Neither source wins: the sample is unavailable.
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":20,"cached_input_tokens":7,"cache_write_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":0,"total_tokens":22},"last_token_usage":{"input_tokens":10,"cached_input_tokens":4,"cache_write_input_tokens":0,"output_tokens":1,"reasoning_output_tokens":0,"total_tokens":11}}}}',
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert len(turn.requests) == 1


def test_optional_counter_difference_and_task_completion_for_another_turn():
    lines = [
        '{"timestamp":"2026-08-30T20:00:00Z","type":"turn_context","payload":{"turn_id":"t","model":"gpt-5.6-luna"}}',
        # A completion for another turn must not clear the active one.
        '{"timestamp":"2026-08-30T20:00:01Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"other"}}',
        '{"timestamp":"2026-08-30T20:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"output_tokens":1,"total_tokens":11},"last_token_usage":{"input_tokens":10,"output_tokens":1,"total_tokens":11}}}}',
        # Missing optional cumulative classes remain unknown but do not make an
        # otherwise matching request ambiguous.
        '{"timestamp":"2026-08-30T20:00:03Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":20,"output_tokens":2,"total_tokens":22},"last_token_usage":{"input_tokens":10,"output_tokens":1,"total_tokens":11}}}}',
    ]
    turn = parse_codex_transcript_usage(lines, session_id="s")[0]
    assert len(turn.requests) == 2
    assert turn.cached_input_tokens is None
    assert turn.api_equivalent_cost_usd is None
