"""Private, content-free client telemetry waiting for project delivery."""

from __future__ import annotations

import json
import math
import os
import stat
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from dduo_solo_founder.project_config import portable_file_lock
CLIENT_TELEMETRY_DIR = Path.home() / ".config" / "dduo-solo-founder" / "client-telemetry"
CLIENT_TELEMETRY_PATH = CLIENT_TELEMETRY_DIR / "events.json"
MAX_PENDING_PER_PROJECT = 1_000
CLIENT_COST_DECIMAL_PLACES = 12
CLIENT_COST_WHOLE_DIGITS = 8
COMMON_EVENT_FIELDS = frozenset(
    {"kind", "event_id", "provider", "measurement_source", "occurred_at"}
)
AGENT_USAGE_FIELDS = COMMON_EVENT_FIELDS | {
    "session_id",
    "turn_id",
    "model",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "reported_total_tokens",
    "client_cost_usd",
    "cost_source",
}


class ClientTelemetrySpool:
    """A tiny atomic outbox shared by hooks and the Claude status-line adapter."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CLIENT_TELEMETRY_PATH

    def enqueue(self, project_id: str, event: Mapping[str, Any]) -> bool:
        return bool(self.enqueue_many(project_id, (event,)))

    def enqueue_many(self, project_id: str, events: Iterable[Mapping[str, Any]]) -> int:
        """Atomically stage many measurements with one durable write.

        Validation and capacity checks complete before the outbox changes. A
        repeated event in either the batch or durable queue is idempotent.
        """
        project = _project_id(project_id)
        normalized: list[dict[str, Any]] = []
        batch_ids: set[str] = set()
        for event in events:
            item = _event(event)
            identity = str(item["event_id"])
            if identity not in batch_ids:
                batch_ids.add(identity)
                normalized.append(item)
        if not normalized:
            return 0
        with self._lock():
            document = self._read()
            items = document["projects"].setdefault(project, [])
            existing_ids = {str(item.get("event_id") or "") for item in items}
            additions = [item for item in normalized if item["event_id"] not in existing_ids]
            if not additions:
                return 0
            if len(items) + len(additions) > MAX_PENDING_PER_PROJECT:
                # Never trade an already-measured sample for a newer one. The
                # caller must leave its source ledger unchanged and retry the
                # same deterministic event after delivery frees capacity.
                raise RuntimeError("client telemetry outbox is full")
            items.extend(additions)
            self._write(document)
        return len(additions)

    def peek(self, project_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        project = _project_id(project_id)
        bounded = min(max(int(limit), 1), MAX_PENDING_PER_PROJECT)
        with self._lock():
            document = self._read()
            return [dict(item) for item in document["projects"].get(project, [])[:bounded]]

    def acknowledge(self, project_id: str, event_ids: set[str]) -> int:
        project = _project_id(project_id)
        if not event_ids:
            return 0
        with self._lock():
            document = self._read()
            before = document["projects"].get(project, [])
            after = [item for item in before if item.get("event_id") not in event_ids]
            removed = len(before) - len(after)
            if after:
                document["projects"][project] = after
            else:
                document["projects"].pop(project, None)
            if removed:
                self._write(document)
            return removed

    def _lock(self):
        self._ensure_directory()
        return portable_file_lock(self.path)

    def _ensure_directory(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.is_symlink() or not self.path.parent.is_dir():
            raise RuntimeError("client telemetry directory is unsafe")
        if os.name != "nt":
            self.path.parent.chmod(0o700)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "projects": {}}
        if self.path.is_symlink() or not self.path.is_file():
            raise RuntimeError("client telemetry outbox is unsafe")
        if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise RuntimeError("client telemetry permissions are unsafe")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("client telemetry outbox is unreadable") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise RuntimeError("client telemetry outbox has an unsupported format")
        projects = value.get("projects")
        if not isinstance(projects, dict):
            raise RuntimeError("client telemetry outbox has an unsupported format")
        normalized: dict[str, list[dict[str, Any]]] = {}
        removed_legacy = False
        for project_id, items in projects.items():
            project = _project_id(project_id)
            if not isinstance(items, list):
                raise RuntimeError("client telemetry project queue is invalid")
            if len(items) > MAX_PENDING_PER_PROJECT:
                raise RuntimeError("client telemetry project queue exceeds capacity")
            # Beta.5 could leave policy-transition records in this durable
            # outbox. The reserve no longer exists, so discard those legacy
            # records without allowing them to block newer usage telemetry.
            current = [
                _event(item)
                for item in items
                if not (isinstance(item, Mapping) and item.get("kind") == "usage_guard")
            ]
            removed_legacy = removed_legacy or len(current) != len(items)
            if current:
                normalized[project] = current
        document = {"version": 1, "projects": normalized}
        if removed_legacy:
            self._write(document)
        return document

    def _write(self, value: Mapping[str, Any]) -> None:
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)


def _project_id(value: Any) -> str:
    candidate = str(value).strip()
    if (
        not candidate
        or len(candidate) > 128
        or any(not (character.isalnum() or character in "-_") for character in candidate)
    ):
        raise ValueError("invalid project id for client telemetry")
    return candidate


def _event(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("client telemetry event must be an object")
    event = dict(value)
    event_id = str(event.get("event_id") or "")
    kind = event.get("kind")
    if (
        not event_id
        or len(event_id) > 192
        or any(not (character.isalnum() or character in "._~-") for character in event_id)
        or kind != "agent_usage"
    ):
        raise ValueError("client telemetry event is invalid")
    if set(event) - AGENT_USAGE_FIELDS:
        raise ValueError("client telemetry events must remain content-free")
    # The server owns strict semantic validation. The local spool rejects nested,
    # non-finite, and unknown values so it can never become a transcript.
    for item in event.values():
        if not isinstance(item, (str, int, float, bool, type(None))):
            raise ValueError("client telemetry events must remain scalar")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("client telemetry event numbers must be finite")
    if kind == "agent_usage" and event.get("client_cost_usd") is not None:
        _validate_client_cost(event["client_cost_usd"])
    return event


def _validate_client_cost(value: Any) -> None:
    """Reject values that cannot fit the server's non-negative NUMERIC(20, 12)."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("client telemetry cost must be a decimal number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("client telemetry cost must be a decimal number") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("client telemetry cost must be finite and non-negative")

    _, decimal_digits, decimal_exponent = parsed.as_tuple()
    digits = list(decimal_digits)
    exponent = decimal_exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        digits = [0]
        exponent = 0
    decimal_places = max(-exponent, 0)
    whole_digits = max(len(digits) + exponent, 0)
    if decimal_places > CLIENT_COST_DECIMAL_PLACES or whole_digits > CLIENT_COST_WHOLE_DIGITS:
        raise ValueError("client telemetry cost exceeds NUMERIC(20, 12) precision")
