"""Content-free Codex turn usage extracted from the supported JSONL transcript.

Codex writes cumulative and last-request token counters to ``token_count``
events.  A turn can contain many provider requests, so this module deliberately
keeps one sample per request.  That preserves cache classes and lets pricing
apply long-context multipliers to the request that actually crossed a provider
threshold instead of to an artificial turn aggregate.

The parser is fail-open: malformed or unknown records are ignored.  Its public
outputs contain identifiers, timestamps, model names, and numeric counters only;
prompt, response, tool, and error text is never returned or persisted here.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from dduo_solo_founder.pricing import EquivalentApiCost, api_equivalent_cost


TRANSCRIPT_CURSOR_VERSION = "codex-jsonl-byte-offset-v1"
# A lifecycle hook must never walk an unbounded multi-gigabyte rollout. The
# cursor makes normal work incremental; this ceiling makes an exceptional
# single turn fail open instead of delaying or exhausting the client.
MAX_INCREMENTAL_SCAN_BYTES = 512 * 1024 * 1024
MAX_JSONL_RECORD_BYTES = 256 * 1024
MAX_REQUESTS_PER_TURN = 1_000
TRANSCRIPT_READ_CHUNK_BYTES = 64 * 1024
CURSOR_HEADER_SCAN_BYTES = 256 * 1024
# Cursor capture is on the prompt path, so it remains bounded. 512 MiB is large
# enough to step over unusually large tool-result records without ever falling
# back to an O(session) scan. If no trustworthy cumulative counter is found in
# this window, capture fails open instead of inventing an ambiguous baseline.
CURSOR_TAIL_SCAN_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CodexRequestUsage:
    """One provider request measured inside one Codex turn."""

    turn_id: str
    request_ordinal: int
    model: str | None
    occurred_at: datetime
    input_tokens: int
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int
    reasoning_tokens: int | None
    reported_total_tokens: int | None

    @property
    def uncached_input_tokens(self) -> int | None:
        """Return provider input not served from, or written to, a cache."""
        if self.cached_input_tokens is None or self.cache_write_input_tokens is None:
            return None
        value = self.input_tokens - self.cached_input_tokens - self.cache_write_input_tokens
        return value if value >= 0 else None

    @property
    def cache_hit_percent(self) -> Decimal | None:
        if self.cached_input_tokens is None or self.input_tokens <= 0:
            return None
        return Decimal(self.cached_input_tokens) * Decimal(100) / Decimal(self.input_tokens)

    def api_equivalent_cost(self) -> EquivalentApiCost | None:
        """Price this request, never an aggregate spanning multiple requests."""
        return api_equivalent_cost(
            provider="codex",
            model=self.model,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            occurred_at=self.occurred_at,
        )

    def client_event(self, session_id: str) -> dict[str, Any]:
        """Build the existing content-free, idempotent client telemetry shape."""
        identity = _identity(session_id, self.turn_id, str(self.request_ordinal))
        return {
            "kind": "agent_usage",
            "event_id": f"agent.codex.{identity}",
            "provider": "codex",
            "model": self.model,
            "measurement_source": "provider_reported",
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "reported_total_tokens": self.reported_total_tokens,
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CodexTurnUsage:
    """A content-free view of all measured requests in one turn."""

    turn_id: str
    requests: tuple[CodexRequestUsage, ...]

    @property
    def input_tokens(self) -> int:
        return sum(item.input_tokens for item in self.requests)

    @property
    def cached_input_tokens(self) -> int | None:
        return _optional_sum(item.cached_input_tokens for item in self.requests)

    @property
    def cache_write_input_tokens(self) -> int | None:
        return _optional_sum(item.cache_write_input_tokens for item in self.requests)

    @property
    def uncached_input_tokens(self) -> int | None:
        return _optional_sum(item.uncached_input_tokens for item in self.requests)

    @property
    def output_tokens(self) -> int:
        return sum(item.output_tokens for item in self.requests)

    @property
    def reasoning_tokens(self) -> int | None:
        return _optional_sum(item.reasoning_tokens for item in self.requests)

    @property
    def reported_total_tokens(self) -> int | None:
        return _optional_sum(item.reported_total_tokens for item in self.requests)

    @property
    def cache_hit_percent(self) -> Decimal | None:
        cached = self.cached_input_tokens
        if cached is None or self.input_tokens <= 0:
            return None
        return Decimal(cached) * Decimal(100) / Decimal(self.input_tokens)

    @property
    def api_equivalent_cost_usd(self) -> Decimal | None:
        """Return a total only when every individual request is priceable."""
        if not self.requests:
            return None
        prices = [item.api_equivalent_cost() for item in self.requests]
        if any(item is None for item in prices):
            return None
        return sum((item.cost_usd for item in prices if item is not None), Decimal(0))


@dataclass(frozen=True, slots=True)
class CodexTranscriptReadResult:
    """Tri-state read result used by the durable hook retry source."""

    usage: tuple[CodexTurnUsage, ...]
    status: Literal["complete", "retryable_unavailable", "terminal_unavailable"]


class _UsageLimitExceeded(ValueError):
    pass


def parse_codex_transcript_usage(
    lines: Iterable[str],
    *,
    session_id: str,
    turn_id: str | None = None,
) -> tuple[CodexTurnUsage, ...]:
    """Extract measured request usage from a Codex transcript JSONL stream.

    ``session_id`` is required both to reject an accidentally selected
    transcript from another session and to create stable event identifiers.
    Passing ``turn_id`` limits the result while still scanning earlier token
    snapshots, which is necessary to deduplicate cumulative counters correctly.
    """
    try:
        return _parse_codex_transcript_usage(
            lines,
            session_id=session_id,
            turn_id=turn_id,
            initial_total=None,
            initial_model=None,
            session_prevalidated=False,
        )
    except _UsageLimitExceeded:
        return ()


def _parse_codex_transcript_usage(
    lines: Iterable[str],
    *,
    session_id: str,
    turn_id: str | None,
    initial_total: _UsageCounters | None,
    initial_model: str | None,
    session_prevalidated: bool,
) -> tuple[CodexTurnUsage, ...]:
    expected_session = _identifier(session_id)
    expected_turn = _identifier(turn_id) if turn_id is not None else None
    if expected_session is None or (turn_id is not None and expected_turn is None):
        return ()

    transcript_session: str | None = expected_session if session_prevalidated else None
    active_turn: str | None = expected_turn if session_prevalidated else None
    models: dict[str, str | None] = (
        {expected_turn: initial_model} if expected_turn is not None else {}
    )
    ordinals: dict[str, int] = {}
    samples: dict[str, list[CodexRequestUsage]] = {}
    previous_total = initial_total

    for line in lines:
        event = _json_object(line)
        if event is None:
            continue

        record_type = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if record_type == "session_meta":
            candidate = _identifier(payload.get("session_id") or payload.get("id"))
            if candidate is not None:
                transcript_session = candidate
            continue
        if record_type == "turn_context":
            candidate = _identifier(payload.get("turn_id"))
            if candidate is not None:
                active_turn = candidate
                models[candidate] = _model(payload.get("model"))
            continue
        payload_type = payload.get("type")
        if record_type == "model_reroute" or payload_type == "model_reroute":
            rerouted_turn = _identifier(payload.get("turn_id")) or active_turn
            if rerouted_turn is not None:
                target = (
                    payload.get("to_model")
                    or payload.get("target_model")
                    or payload.get("target")
                    or event.get("to_model")
                    or event.get("target_model")
                )
                if isinstance(target, Mapping):
                    target = target.get("id") or target.get("model") or target.get("name")
                # A recognized reroute with an unfamiliar target is not priced
                # as the old model. Unknown is safer than a false estimate.
                models[rerouted_turn] = _model(target)
            continue
        if _is_task_complete(payload):
            completed = _identifier(payload.get("turn_id"))
            if completed is None or completed == active_turn:
                active_turn = None
            continue

        token_event = _token_event(event, payload)
        if token_event is None:
            continue
        explicit_turn, last, total, occurred_at = token_event
        measured_turn = explicit_turn or active_turn

        # Always advance the cumulative baseline, even when a caller requested
        # another turn. Otherwise the first selected event would include usage
        # from all preceding turns.
        request = _request_delta(last=last, total=total, previous_total=previous_total)
        if total is not None:
            previous_total = total
        if measured_turn is None or request is None or occurred_at is None:
            continue

        raw_ordinal = ordinals.get(measured_turn, 0)
        ordinals[measured_turn] = raw_ordinal + 1
        if expected_turn is not None and measured_turn != expected_turn:
            continue
        samples.setdefault(measured_turn, []).append(
            CodexRequestUsage(
                turn_id=measured_turn,
                request_ordinal=raw_ordinal,
                model=models.get(measured_turn),
                occurred_at=occurred_at,
                input_tokens=request.input_tokens,
                cached_input_tokens=request.cached_input_tokens,
                cache_write_input_tokens=request.cache_write_input_tokens,
                output_tokens=request.output_tokens,
                reasoning_tokens=request.reasoning_tokens,
                reported_total_tokens=request.total_tokens,
            )
        )
        if len(samples[measured_turn]) > MAX_REQUESTS_PER_TURN:
            # The local spool and server batch share this exact ceiling. Never
            # expose or persist a partial turn as if it were complete.
            raise _UsageLimitExceeded("Codex turn exceeds telemetry request ceiling")

    if transcript_session is not None and transcript_session != expected_session:
        return ()
    return tuple(
        CodexTurnUsage(turn_id=key, requests=tuple(items))
        for key, items in samples.items()
        if items
    )


def read_codex_transcript_usage(
    path: Path,
    *,
    session_id: str,
    turn_id: str | None = None,
    cursor: Mapping[str, Any] | None = None,
) -> tuple[CodexTurnUsage, ...]:
    """Read a bounded JSONL range without following a final symlink.

    Hook callers pass the cursor captured at ``UserPromptSubmit``. Reading an
    uncursored small file remains supported for fixtures and diagnostics, but
    is subject to the same hard byte ceiling.
    """
    return read_codex_transcript_usage_result(
        path,
        session_id=session_id,
        turn_id=turn_id,
        cursor=cursor,
    ).usage


def read_codex_transcript_usage_result(
    path: Path,
    *,
    session_id: str,
    turn_id: str | None = None,
    cursor: Mapping[str, Any] | None = None,
) -> CodexTranscriptReadResult:
    """Read usage while preserving retryable I/O versus terminal absence."""
    descriptor: int | None = None
    try:
        opened = _open_regular_transcript(path)
        if opened is None:
            return CodexTranscriptReadResult((), "retryable_unavailable")
        descriptor, status = opened
        start = 0
        initial_total = None
        initial_model = None
        prevalidated = False
        if cursor is not None:
            normalized = _validated_cursor(
                cursor,
                session_id=session_id,
                turn_id=turn_id,
                status=status,
            )
            if normalized is None:
                return CodexTranscriptReadResult((), "terminal_unavailable")
            start, initial_total, initial_model = normalized
            prevalidated = True
        span = status.st_size - start
        if span < 0 or span > MAX_INCREMENTAL_SCAN_BYTES:
            return CodexTranscriptReadResult((), "terminal_unavailable")
        lines = _bounded_jsonl_lines(descriptor, start=start, end=status.st_size)
        try:
            usage = _parse_codex_transcript_usage(
                lines,
                session_id=session_id,
                turn_id=turn_id,
                initial_total=initial_total,
                initial_model=initial_model,
                session_prevalidated=prevalidated,
            )
        except _UsageLimitExceeded:
            return CodexTranscriptReadResult((), "terminal_unavailable")
        return CodexTranscriptReadResult(usage, "complete")
    except OSError:
        return CodexTranscriptReadResult((), "retryable_unavailable")
    except (UnicodeError, ValueError):
        return CodexTranscriptReadResult((), "terminal_unavailable")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def capture_codex_transcript_cursor(
    path: Path,
    *,
    session_id: str,
    turn_id: str,
) -> dict[str, Any] | None:
    """Capture a content-free cursor at prompt delivery.

    Only file identity, byte offset, the current model, and cumulative numeric
    counters are retained. A bounded head verifies the session when metadata
    is available; a bounded tail supplies the cumulative baseline without ever
    rescanning the rollout.
    """
    expected_session = _identifier(session_id)
    expected_turn = _identifier(turn_id)
    if expected_session is None or expected_turn is None:
        return None
    descriptor: int | None = None
    try:
        opened = _open_regular_transcript(path)
        if opened is None:
            return None
        descriptor, status = opened
        offset = status.st_size
        header_end = min(offset, CURSOR_HEADER_SCAN_BYTES)
        header_session = _transcript_session(
            _bounded_jsonl_lines(descriptor, start=0, end=header_end)
        )
        # A cursor is later allowed to skip the transcript header. That is safe
        # only when capture itself positively matched the native session. An
        # absent header is not equivalent to a verified header.
        if header_session != expected_session:
            return None

        tail_start = max(0, offset - CURSOR_TAIL_SCAN_BYTES)
        scan_state = _JsonlScanState()
        baseline, model, saw_token_event, baseline_trusted = _cursor_tail_state(
            _bounded_jsonl_lines(
                descriptor,
                start=tail_start,
                end=offset,
                scan_state=scan_state,
            ),
            turn_id=expected_turn,
            scan_state=scan_state,
        )
        baseline_empty = (
            baseline is None
            and tail_start == 0
            and not saw_token_event
            and scan_state.ambiguity_epoch == 0
        )
        if baseline is not None and not baseline_trusted:
            return None
        if baseline is None and not baseline_empty:
            # Without the preceding cumulative snapshot, a repeated snapshot
            # after the cursor is indistinguishable from new provider usage.
            return None
        result: dict[str, Any] = {
            "version": TRANSCRIPT_CURSOR_VERSION,
            "session_id": expected_session,
            "turn_id": expected_turn,
            "device": int(status.st_dev),
            "inode": int(status.st_ino),
            "offset": offset,
            "session_verified": True,
            "baseline_empty": baseline_empty,
        }
        if baseline is not None:
            result["baseline_total"] = _counters_payload(baseline)
        if model is not None:
            result["model"] = model
        return result
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


@dataclass(frozen=True, slots=True)
class _UsageCounters:
    input_tokens: int
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int
    reasoning_tokens: int | None
    total_tokens: int | None


@dataclass(slots=True)
class _JsonlScanState:
    """Track skipped records that could hide a newer cumulative snapshot."""

    ambiguity_epoch: int = 0


def _open_regular_transcript(path: Path) -> tuple[int, os.stat_result] | None:
    """Open one stable regular file and reject every final symlink."""
    descriptor: int | None = None
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        after = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(after.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
        ):
            os.close(descriptor)
            return None
        return descriptor, opened
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None


def _bounded_jsonl_lines(
    descriptor: int,
    *,
    start: int,
    end: int,
    scan_state: _JsonlScanState | None = None,
) -> Iterable[str]:
    """Yield complete, reasonably-sized records with constant memory.

    Transcript lines commonly contain arbitrary tool output. Once a record
    exceeds the numeric-envelope ceiling, bytes are discarded chunk by chunk
    until its newline; the record is never materialized.
    """
    if start < 0 or end < start:
        raise ValueError("invalid transcript range")
    os.lseek(descriptor, start, os.SEEK_SET)
    discarding = False
    if start:
        os.lseek(descriptor, start - 1, os.SEEK_SET)
        discarding = os.read(descriptor, 1) != b"\n"
        if discarding and scan_state is not None:
            scan_state.ambiguity_epoch += 1
        os.lseek(descriptor, start, os.SEEK_SET)

    remaining = end - start
    pending = bytearray()
    while remaining:
        chunk = os.read(descriptor, min(TRANSCRIPT_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        position = 0
        while position < len(chunk):
            newline = chunk.find(b"\n", position)
            boundary = len(chunk) if newline < 0 else newline
            segment = chunk[position:boundary]
            if discarding:
                if newline < 0:
                    break
                discarding = False
            elif len(pending) + len(segment) > MAX_JSONL_RECORD_BYTES:
                pending.clear()
                if scan_state is not None:
                    scan_state.ambiguity_epoch += 1
                if newline < 0:
                    discarding = True
                    break
            else:
                pending.extend(segment)
                if newline >= 0:
                    try:
                        yield pending.decode("utf-8")
                    except UnicodeError:
                        if scan_state is not None:
                            scan_state.ambiguity_epoch += 1
                    pending.clear()
            if newline < 0:
                break
            position = newline + 1

    if pending and not discarding:
        try:
            yield pending.decode("utf-8")
        except UnicodeError:
            if scan_state is not None:
                scan_state.ambiguity_epoch += 1


def _validated_cursor(
    cursor: Mapping[str, Any],
    *,
    session_id: str,
    turn_id: str | None,
    status: os.stat_result,
) -> tuple[int, _UsageCounters | None, str | None] | None:
    expected_session = _identifier(session_id)
    expected_turn = _identifier(turn_id)
    if (
        cursor.get("version") != TRANSCRIPT_CURSOR_VERSION
        or cursor.get("session_verified") is not True
        or expected_session is None
        or expected_turn is None
        or _identifier(cursor.get("session_id")) != expected_session
        or _identifier(cursor.get("turn_id")) != expected_turn
    ):
        return None
    device = _number(cursor.get("device"))
    inode = _number(cursor.get("inode"))
    offset = _number(cursor.get("offset"))
    if (
        device is None
        or inode is None
        or offset is None
        or device != status.st_dev
        or inode != status.st_ino
        or offset > status.st_size
    ):
        return None
    baseline = _counters(cursor.get("baseline_total"))
    baseline_empty = cursor.get("baseline_empty") is True
    if (baseline is None) == (not baseline_empty):
        # Exactly one trustworthy baseline representation is required: either
        # a provider cumulative snapshot or a fully scanned empty prefix.
        return None
    model = _model(cursor.get("model"))
    return offset, baseline, model


def _transcript_session(lines: Iterable[str]) -> str | None:
    for line in lines:
        event = _json_object(line)
        if event is None or event.get("type") != "session_meta":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        candidate = _identifier(payload.get("session_id") or payload.get("id"))
        if candidate is not None:
            return candidate
    return None


def _cursor_tail_state(
    lines: Iterable[str],
    *,
    turn_id: str,
    scan_state: _JsonlScanState,
) -> tuple[_UsageCounters | None, str | None, bool, bool]:
    active_turn: str | None = None
    models: dict[str, str | None] = {}
    baseline: _UsageCounters | None = None
    baseline_epoch: int | None = None
    saw_token_event = False
    for line in lines:
        event = _json_object(line)
        if event is None:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        record_type = event.get("type")
        if record_type == "turn_context":
            candidate = _identifier(payload.get("turn_id"))
            if candidate is not None:
                active_turn = candidate
                models[candidate] = _model(payload.get("model"))
            continue
        payload_type = payload.get("type")
        if record_type == "model_reroute" or payload_type == "model_reroute":
            rerouted_turn = _identifier(payload.get("turn_id")) or active_turn
            if rerouted_turn is not None:
                target = (
                    payload.get("to_model")
                    or payload.get("target_model")
                    or payload.get("target")
                    or event.get("to_model")
                    or event.get("target_model")
                )
                if isinstance(target, Mapping):
                    target = target.get("id") or target.get("model") or target.get("name")
                models[rerouted_turn] = _model(target)
            continue
        if _is_task_complete(payload):
            completed = _identifier(payload.get("turn_id"))
            if completed is None or completed == active_turn:
                active_turn = None
            continue
        token_event = _token_event(event, payload)
        if token_event is not None:
            saw_token_event = True
            if token_event[2] is not None:
                baseline = token_event[2]
                baseline_epoch = scan_state.ambiguity_epoch
    return (
        baseline,
        models.get(turn_id),
        saw_token_event,
        baseline is not None and baseline_epoch == scan_state.ambiguity_epoch,
    )


def _counters_payload(value: _UsageCounters) -> dict[str, int | None]:
    return {
        "input_tokens": value.input_tokens,
        "cached_input_tokens": value.cached_input_tokens,
        "cache_write_input_tokens": value.cache_write_input_tokens,
        "output_tokens": value.output_tokens,
        "reasoning_output_tokens": value.reasoning_tokens,
        "total_tokens": value.total_tokens,
    }


def _token_event(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str | None, _UsageCounters | None, _UsageCounters | None, datetime | None] | None:
    # Codex transcript JSONL.
    if payload.get("type") == "token_count":
        info = payload.get("info") if isinstance(payload.get("info"), Mapping) else {}
        return (
            None,
            _counters(info.get("last_token_usage")),
            _counters(info.get("total_token_usage")),
            _timestamp(event.get("timestamp")),
        )

    # The supported App Server notification uses the same normalized counters
    # with camelCase keys. Keeping this small compatibility path costs nothing
    # and lets a future live adapter reuse the tested core.
    if event.get("method") == "thread/tokenUsage/updated":
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), Mapping) else {}
        return (
            _identifier(params.get("turnId")),
            _counters(usage.get("last")),
            _counters(usage.get("total")),
            _timestamp(params.get("occurredAt") or event.get("timestamp")),
        )
    return None


def _request_delta(
    *,
    last: _UsageCounters | None,
    total: _UsageCounters | None,
    previous_total: _UsageCounters | None,
) -> _UsageCounters | None:
    if total is not None and previous_total is not None:
        if (
            total.input_tokens < previous_total.input_tokens
            or total.output_tokens < previous_total.output_tokens
        ):
            # A complete provider counter epoch can reset between requests. In
            # that case ``last`` remains the only request-local source.
            return last if last is not None and not _is_zero(last) else None
        delta = _subtract(total, previous_total)
        if delta is None or _is_zero(delta):
            return None
        # ``last`` is the provider's single-request measurement. The
        # cumulative delta is only a guard against duplicate snapshots or an
        # ambiguous association; it must never replace that request.
        if last is None or not _compatible_request(last, delta):
            return None
        return last
    return last if last is not None and not _is_zero(last) else None


def _compatible_request(last: _UsageCounters, delta: _UsageCounters) -> bool:
    if last.input_tokens != delta.input_tokens or last.output_tokens != delta.output_tokens:
        return False
    for last_value, delta_value in (
        (last.cached_input_tokens, delta.cached_input_tokens),
        (last.cache_write_input_tokens, delta.cache_write_input_tokens),
        (last.reasoning_tokens, delta.reasoning_tokens),
        (last.total_tokens, delta.total_tokens),
    ):
        if last_value is not None and delta_value is not None and last_value != delta_value:
            return False
    return True


def _subtract(current: _UsageCounters, previous: _UsageCounters) -> _UsageCounters | None:
    required = (
        current.input_tokens - previous.input_tokens,
        current.output_tokens - previous.output_tokens,
    )

    def optional_delta(current_value: int | None, previous_value: int | None) -> int | None:
        if current_value is None or previous_value is None:
            return None
        value = current_value - previous_value
        if value < 0:
            raise ValueError
        return value

    try:
        reported_total = optional_delta(current.total_tokens, previous.total_tokens)
        return _validated_counters(
            input_tokens=required[0],
            cached_input_tokens=optional_delta(
                current.cached_input_tokens, previous.cached_input_tokens
            ),
            cache_write_input_tokens=optional_delta(
                current.cache_write_input_tokens, previous.cache_write_input_tokens
            ),
            output_tokens=required[1],
            reasoning_tokens=optional_delta(current.reasoning_tokens, previous.reasoning_tokens),
            total_tokens=reported_total,
        )
    except ValueError:
        return None


def _counters(value: Any) -> _UsageCounters | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = _number(value.get("input_tokens", value.get("inputTokens")))
    output_tokens = _number(value.get("output_tokens", value.get("outputTokens")))
    if input_tokens is None or output_tokens is None:
        return None
    return _validated_counters(
        input_tokens=input_tokens,
        cached_input_tokens=_number(
            value.get("cached_input_tokens", value.get("cachedInputTokens"))
        ),
        cache_write_input_tokens=_number(
            value.get("cache_write_input_tokens", value.get("cacheWriteInputTokens"))
        ),
        output_tokens=output_tokens,
        reasoning_tokens=_number(
            value.get("reasoning_output_tokens", value.get("reasoningOutputTokens"))
        ),
        total_tokens=_number(value.get("total_tokens", value.get("totalTokens"))),
    )


def _validated_counters(
    *,
    input_tokens: int,
    cached_input_tokens: int | None,
    cache_write_input_tokens: int | None,
    output_tokens: int,
    reasoning_tokens: int | None,
    total_tokens: int | None,
) -> _UsageCounters | None:
    cached = cached_input_tokens or 0
    cache_write = cache_write_input_tokens or 0
    if cached + cache_write > input_tokens:
        return None
    if reasoning_tokens is not None and reasoning_tokens > output_tokens:
        reasoning_tokens = None
    if total_tokens is not None and total_tokens != input_tokens + output_tokens:
        total_tokens = None
    return _UsageCounters(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
    )


def _json_object(line: str) -> dict[str, Any] | None:
    # Most transcript records contain user/model text. Do not deserialize them
    # unless their envelope can affect usage state.
    if not any(
        marker in line
        for marker in (
            '"session_meta"',
            '"turn_context"',
            '"model_reroute"',
            '"token_count"',
            '"task_complete"',
            '"thread/tokenUsage/updated"',
        )
    ):
        return None
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _is_task_complete(payload: Mapping[str, Any]) -> bool:
    return payload.get("type") == "task_complete"


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 192:
        return None
    return candidate


def _model(value: Any) -> str | None:
    candidate = _identifier(value)
    if candidate is None or any(
        not (character.isalnum() or character in "._:/+-") for character in candidate
    ):
        return None
    return candidate


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_zero(value: _UsageCounters) -> bool:
    return value.input_tokens == 0 and value.output_tokens == 0


def _identity(*values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
    return digest[:40]


def _optional_sum(values: Iterable[int | None]) -> int | None:
    items = tuple(values)
    if any(item is None for item in items):
        return None
    return sum(item for item in items if item is not None)
