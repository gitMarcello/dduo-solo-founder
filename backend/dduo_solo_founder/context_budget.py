"""Deterministic hard-budget composition for automatic model context.

The composer deliberately does not truncate strings.  A caller may provide an
explicit alternate payload for a fragment (``excerpt``); otherwise the whole
fragment is either emitted or omitted.  The rendered format is JSON Lines so
every emitted fragment and the final delivery manifest remain independently
valid JSON values.

The delivery budget is checked against both Python code points and UTF-16 code
units.  This matters for clients implemented in JavaScript, where an astral
Unicode character such as an emoji occupies two string units.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


DEFAULT_CONTEXT_BUDGET = 9_000
CONTEXT_FORMAT_VERSION = 1
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

DeliveryStatus = Literal["within_budget", "budgeted", "fallback"]
FragmentOutcome = Literal["full", "partial", "omitted"]


def utf16_units(value: str) -> int:
    """Return the number of UTF-16 code units used by a valid Unicode string."""
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ValueError("context contains an unpaired Unicode surrogate") from exc


def context_units(value: str) -> int:
    """Return the stricter of Python and JavaScript-style string lengths."""
    return max(len(value), utf16_units(value))


@dataclass(frozen=True, slots=True)
class ContextMeasurement:
    """Exact local measurements for one deterministic string."""

    characters: int
    utf16_units: int
    utf8_bytes: int
    budget_units: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ContextFragment:
    """One ordered, independently deliverable context unit.

    Lower ``priority`` values are more important.  Input order is the stable
    tie-breaker.  ``excerpt`` is an explicit caller-authored representation;
    the composer never manufactures an excerpt by slicing ``payload``.

    A required fragment that cannot be represented in full or by its explicit
    excerpt forces the small, valid fallback response.
    """

    component: str
    priority: int
    reference: str
    payload: Mapping[str, Any]
    excerpt: Mapping[str, Any] | None = None
    required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.component, str) or not _COMPONENT_RE.fullmatch(
            self.component
        ):
            raise ValueError(
                "component must be 1-64 characters using letters, digits, dot, underscore, or dash"
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if not isinstance(self.required, bool):
            raise TypeError("required must be a boolean")
        if not isinstance(self.reference, str) or not self.reference:
            raise ValueError("reference must be a non-empty string")
        if len(self.reference) > 256:
            raise ValueError("reference must be at most 256 characters")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if self.excerpt is not None and not isinstance(self.excerpt, Mapping):
            raise TypeError("excerpt must be a mapping when provided")


@dataclass(frozen=True, slots=True)
class ComponentDelivery:
    """Per-component delivery counts, references, and exact fragment measures.

    ``emitted`` includes both full and partial fragments. The
    ``non_full_source_measurement`` describes the complete source representation
    behind partial and omitted fragments; it is intentionally not presented as
    the amount avoided by the final render.
    """

    component: str
    produced: int
    emitted: int
    partial: int
    omitted: int
    produced_references: tuple[str, ...]
    emitted_references: tuple[str, ...]
    partial_references: tuple[str, ...]
    omitted_references: tuple[str, ...]
    produced_measurement: ContextMeasurement
    emitted_measurement: ContextMeasurement
    non_full_source_measurement: ContextMeasurement


@dataclass(frozen=True, slots=True)
class DeliveryManifest:
    """Structured result used by observability without reparsing rendered text."""

    format_version: int
    budget: int
    status: DeliveryStatus
    components: tuple[ComponentDelivery, ...]
    fallback_reason: str | None = None

    @property
    def produced(self) -> int:
        return sum(item.produced for item in self.components)

    @property
    def emitted(self) -> int:
        return sum(item.emitted for item in self.components)

    @property
    def partial(self) -> int:
        return sum(item.partial for item in self.components)

    @property
    def omitted(self) -> int:
        return sum(item.omitted for item in self.components)


@dataclass(frozen=True, slots=True)
class ComposedContext:
    """Final automatic context plus candidate and deferred-source measures."""

    content: str
    measurement: ContextMeasurement
    produced_measurement: ContextMeasurement
    deferred_source_measurement: ContextMeasurement
    manifest: DeliveryManifest
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class _Candidate:
    index: int
    fragment: ContextFragment

    @property
    def full_line(self) -> str:
        return _fragment_line(self.fragment, self.fragment.payload, "full")

    @property
    def excerpt_line(self) -> str | None:
        if self.fragment.excerpt is None:
            return None
        return _fragment_line(self.fragment, self.fragment.excerpt, "partial")


def _canonical_json(value: object) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # json.dumps can retain an unpaired surrogate when ensure_ascii=False;
        # reject it now so every emitted line is valid UTF-8 as well as JSON.
        rendered.encode("utf-8")
        return rendered
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("context fragment must contain deterministic JSON and valid Unicode") from exc


def _measure(value: str) -> ContextMeasurement:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("context contains an unpaired Unicode surrogate") from exc
    utf16 = utf16_units(value)
    return ContextMeasurement(
        characters=len(value),
        utf16_units=utf16,
        utf8_bytes=len(encoded),
        budget_units=max(len(value), utf16),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _nonexpanding_excerpt(candidate: _Candidate) -> str | None:
    """Return an excerpt only when it cannot inflate any observed measure."""
    excerpt_line = candidate.excerpt_line
    if excerpt_line is None:
        return None
    full_measurement = _measure(candidate.full_line)
    excerpt_measurement = _measure(excerpt_line)
    if (
        excerpt_measurement.characters > full_measurement.characters
        or excerpt_measurement.utf16_units > full_measurement.utf16_units
        or excerpt_measurement.utf8_bytes > full_measurement.utf8_bytes
    ):
        return None
    return excerpt_line


def _join_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines)


def _fragment_line(
    fragment: ContextFragment,
    payload: Mapping[str, Any],
    delivery: Literal["full", "partial"],
) -> str:
    return _canonical_json(
        {
            "component": fragment.component,
            "delivery": delivery,
            "payload": payload,
            "reference": fragment.reference,
        }
    )


def _component_counts(
    candidates: Sequence[_Candidate], outcomes: Sequence[FragmentOutcome]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        counts = result.setdefault(
            candidate.fragment.component,
            {"produced": 0, "emitted": 0, "partial": 0, "omitted": 0},
        )
        counts["produced"] += 1
        if outcome == "omitted":
            counts["omitted"] += 1
        else:
            counts["emitted"] += 1
            if outcome == "partial":
                counts["partial"] += 1
    return result


def _manifest_line(
    candidates: Sequence[_Candidate],
    outcomes: Sequence[FragmentOutcome],
    *,
    budget: int,
    status: DeliveryStatus,
    fallback_reason: str | None = None,
) -> str:
    body: dict[str, object] = {
        "budget": budget,
        "components": _component_counts(candidates, outcomes),
        "status": status,
        "version": CONTEXT_FORMAT_VERSION,
    }
    if fallback_reason is not None:
        body["reason"] = fallback_reason
    return _canonical_json({"_dduo_context": body})


def _emitted_lines(
    candidates: Sequence[_Candidate], outcomes: Sequence[FragmentOutcome]
) -> list[str]:
    lines: list[str] = []
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        if outcome == "full":
            lines.append(candidate.full_line)
        elif outcome == "partial":
            # Construction guarantees this cannot be None for a partial item.
            assert candidate.excerpt_line is not None
            lines.append(candidate.excerpt_line)
    return lines


def _iter_emitted_lines(
    candidates: Sequence[_Candidate], outcomes: Sequence[FragmentOutcome]
) -> Iterable[str]:
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        if outcome == "full":
            yield candidate.full_line
        elif outcome == "partial":
            excerpt_line = candidate.excerpt_line
            assert excerpt_line is not None
            yield excerpt_line


def _render(
    candidates: Sequence[_Candidate],
    outcomes: Sequence[FragmentOutcome],
    *,
    budget: int,
    status: DeliveryStatus,
    fallback_reason: str | None = None,
) -> str:
    lines = _emitted_lines(candidates, outcomes)
    lines.append(
        _manifest_line(
            candidates,
            outcomes,
            budget=budget,
            status=status,
            fallback_reason=fallback_reason,
        )
    )
    return _join_lines(lines)


def _status(outcomes: Sequence[FragmentOutcome]) -> DeliveryStatus:
    return "within_budget" if all(value == "full" for value in outcomes) else "budgeted"


def _measure_lines(lines: Iterable[str]) -> ContextMeasurement:
    """Measure newline-joined values without materializing their aggregate string."""
    characters = 0
    utf16 = 0
    utf8_bytes = 0
    digest = hashlib.sha256()
    first = True
    for line in lines:
        if not first:
            characters += 1
            utf16 += 1
            utf8_bytes += 1
            digest.update(b"\n")
        encoded = line.encode("utf-8")
        characters += len(line)
        utf16 += utf16_units(line)
        utf8_bytes += len(encoded)
        digest.update(encoded)
        first = False
    return ContextMeasurement(
        characters=characters,
        utf16_units=utf16,
        utf8_bytes=utf8_bytes,
        budget_units=max(characters, utf16),
        sha256=digest.hexdigest(),
    )


def measure_fragment_sources(fragments: Sequence[ContextFragment]) -> ContextMeasurement:
    """Measure complete fragment source records without adding a delivery manifest.

    This is intentionally separate from :func:`compose_context`: callers use it
    to quantify stable context that is already present in a live session and is
    therefore not emitted again.  The measurement still uses the exact JSONL
    representation that the composer would use for each full fragment.
    """
    if isinstance(fragments, (str, bytes)) or not isinstance(fragments, Sequence):
        raise TypeError("fragments must be a sequence of ContextFragment values")
    candidates: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()
    for index, fragment in enumerate(fragments):
        if not isinstance(fragment, ContextFragment):
            raise TypeError("fragments must contain only ContextFragment values")
        identity = (fragment.component, fragment.reference)
        if identity in seen:
            raise ValueError("component/reference pairs must be unique")
        seen.add(identity)
        candidates.append(_Candidate(index=index, fragment=fragment))
    return _measure_lines(candidate.full_line for candidate in candidates)


def _measure_render(
    candidates: Sequence[_Candidate],
    outcomes: Sequence[FragmentOutcome],
    *,
    budget: int,
    status: DeliveryStatus,
    fallback_reason: str | None = None,
) -> ContextMeasurement:
    def lines() -> Iterable[str]:
        yield from _iter_emitted_lines(candidates, outcomes)
        yield _manifest_line(
            candidates,
            outcomes,
            budget=budget,
            status=status,
            fallback_reason=fallback_reason,
        )

    return _measure_lines(lines())


def _delivery_manifest(
    candidates: Sequence[_Candidate],
    outcomes: Sequence[FragmentOutcome],
    *,
    budget: int,
    status: DeliveryStatus,
    fallback_reason: str | None,
) -> DeliveryManifest:
    grouped_candidates: dict[str, list[tuple[_Candidate, FragmentOutcome]]] = defaultdict(list)
    for candidate, outcome in zip(candidates, outcomes, strict=True):
        grouped_candidates[candidate.fragment.component].append((candidate, outcome))

    components: list[ComponentDelivery] = []
    for component in sorted(grouped_candidates):
        rows = grouped_candidates[component]
        produced_references = tuple(candidate.fragment.reference for candidate, _ in rows)
        emitted_references = tuple(
            candidate.fragment.reference for candidate, outcome in rows if outcome != "omitted"
        )
        partial_references = tuple(
            candidate.fragment.reference for candidate, outcome in rows if outcome == "partial"
        )
        omitted_references = tuple(
            candidate.fragment.reference for candidate, outcome in rows if outcome == "omitted"
        )
        components.append(
            ComponentDelivery(
                component=component,
                produced=len(rows),
                emitted=len(emitted_references),
                partial=len(partial_references),
                omitted=len(omitted_references),
                produced_references=produced_references,
                emitted_references=emitted_references,
                partial_references=partial_references,
                omitted_references=omitted_references,
                produced_measurement=_measure_lines(
                    candidate.full_line for candidate, _ in rows
                ),
                emitted_measurement=_measure_lines(
                    line
                    for candidate, outcome in rows
                    if outcome != "omitted"
                    for line in [
                        candidate.full_line
                        if outcome == "full"
                        else candidate.excerpt_line
                    ]
                    if line is not None
                ),
                non_full_source_measurement=_measure_lines(
                    candidate.full_line for candidate, outcome in rows if outcome != "full"
                ),
            )
        )
    return DeliveryManifest(
        format_version=CONTEXT_FORMAT_VERSION,
        budget=budget,
        status=status,
        components=tuple(components),
        fallback_reason=fallback_reason,
    )


def _fallback(
    candidates: Sequence[_Candidate],
    *,
    budget: int,
    produced_measurement: ContextMeasurement,
    reason: str,
) -> ComposedContext:
    outcomes: list[FragmentOutcome] = ["omitted"] * len(candidates)
    manifest = _delivery_manifest(
        candidates,
        outcomes,
        budget=budget,
        status="fallback",
        fallback_reason=reason,
    )
    content = _render(
        candidates,
        outcomes,
        budget=budget,
        status="fallback",
        fallback_reason=reason,
    )
    if context_units(content) > budget:
        # Component names are caller-controlled and could make the normal
        # manifest itself too large.  This fixed JSON object is the final guard.
        fallback_reason = "manifest_exceeds_budget"
        content = _canonical_json(
            {
                "_dduo_context": {
                    "budget": budget,
                    "reason": fallback_reason,
                    "status": "fallback",
                    "version": CONTEXT_FORMAT_VERSION,
                }
            }
        )
        # The compact wire manifest intentionally contains no component
        # breakdown; keep the structured manifest exactly aligned with it.
        manifest = DeliveryManifest(
            format_version=CONTEXT_FORMAT_VERSION,
            budget=budget,
            status="fallback",
            components=(),
            fallback_reason=fallback_reason,
        )
    if context_units(content) > budget:
        raise ValueError("budget is too small for the valid fallback context")
    return ComposedContext(
        content=content,
        measurement=_measure(content),
        produced_measurement=produced_measurement,
        deferred_source_measurement=_measure_lines(
            candidate.full_line for candidate in candidates
        ),
        manifest=manifest,
        fallback_used=True,
    )


def compose_fallback_context(
    message: str,
    *,
    reason: str = "composition_failed",
    budget: int = DEFAULT_CONTEXT_BUDGET,
) -> ComposedContext:
    """Return a small model-readable fallback with the same measured contract."""
    if not isinstance(message, str):
        raise TypeError("fallback message must be a string")
    if not isinstance(reason, str) or not _COMPONENT_RE.fullmatch(reason):
        raise ValueError("fallback reason must be a bounded identifier")
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError("budget must be an integer")
    if budget <= 0:
        raise ValueError("budget must be positive")
    fragment = ContextFragment(
        component="health",
        priority=0,
        reference="context-fallback",
        payload={"text": message},
        required=True,
    )
    candidates = [_Candidate(index=0, fragment=fragment)]
    outcomes: list[FragmentOutcome] = ["full"]
    produced_measurement = _measure_render(
        candidates,
        outcomes,
        budget=budget,
        status="fallback",
        fallback_reason=reason,
    )
    content = _render(
        candidates,
        outcomes,
        budget=budget,
        status="fallback",
        fallback_reason=reason,
    )
    if context_units(content) > budget:
        return _fallback(
            candidates,
            budget=budget,
            produced_measurement=produced_measurement,
            reason=reason,
        )
    return ComposedContext(
        content=content,
        measurement=_measure(content),
        produced_measurement=produced_measurement,
        deferred_source_measurement=_measure_lines(()),
        manifest=_delivery_manifest(
            candidates,
            outcomes,
            budget=budget,
            status="fallback",
            fallback_reason=reason,
        ),
        fallback_used=True,
    )


def compose_context(
    fragments: Sequence[ContextFragment],
    *,
    budget: int = DEFAULT_CONTEXT_BUDGET,
) -> ComposedContext:
    """Compose automatic context without ever exceeding ``budget`` string units.

    Selection is a stable priority prefix, not a size-optimising knapsack.  If
    a high-priority fragment cannot fit in full or through its explicit
    excerpt, lower-priority fragments are not opportunistically substituted.
    """
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError("budget must be an integer")
    if budget <= 0:
        raise ValueError("budget must be positive")
    if isinstance(fragments, (str, bytes)) or not isinstance(fragments, Sequence):
        raise TypeError("fragments must be a sequence of ContextFragment values")

    indexed: list[tuple[int, ContextFragment]] = []
    seen: set[tuple[str, str]] = set()
    for index, fragment in enumerate(fragments):
        if not isinstance(fragment, ContextFragment):
            raise TypeError("fragments must contain only ContextFragment values")
        identity = (fragment.component, fragment.reference)
        if identity in seen:
            raise ValueError("component/reference pairs must be unique")
        seen.add(identity)
        indexed.append((index, fragment))

    # Required context is an absolute invariant, so it must never sit behind
    # an optional size barrier even if a caller assigns inconsistent priorities.
    indexed.sort(key=lambda value: (not value[1].required, value[1].priority, value[0]))
    candidates = [
        _Candidate(
            index=index,
            fragment=fragment,
        )
        for index, fragment in indexed
    ]

    produced_outcomes: list[FragmentOutcome] = ["full"] * len(candidates)
    produced_measurement = _measure_render(
        candidates,
        produced_outcomes,
        budget=budget,
        status="within_budget",
    )

    outcomes: list[FragmentOutcome] = ["omitted"] * len(candidates)

    def reserve_later_required(
        candidate_outcomes: list[FragmentOutcome], current_index: int
    ) -> list[FragmentOutcome]:
        """Reserve the smallest authored representation of later required context."""
        reserved = list(candidate_outcomes)
        for future_index in range(current_index + 1, len(candidates)):
            future = candidates[future_index]
            if not future.fragment.required:
                continue
            reserved[future_index] = (
                "partial" if _nonexpanding_excerpt(future) is not None else "full"
            )
        return reserved

    barrier_index: int | None = None
    for index, candidate in enumerate(candidates):
        full_outcomes = list(outcomes)
        full_outcomes[index] = "full"
        reserved_full_outcomes = reserve_later_required(full_outcomes, index)
        full_content = _render(
            candidates,
            reserved_full_outcomes,
            budget=budget,
            # ``within_budget`` is the longest status spelling.  Reserving it
            # during selection guarantees that a final all-full manifest can
            # never cross the limit merely because its status grew by five
            # characters on the last accepted fragment.
            status="within_budget",
        )
        if context_units(full_content) <= budget:
            outcomes = full_outcomes
            continue

        if _nonexpanding_excerpt(candidate) is not None:
            partial_outcomes = list(outcomes)
            partial_outcomes[index] = "partial"
            reserved_partial_outcomes = reserve_later_required(partial_outcomes, index)
            partial_content = _render(
                candidates,
                reserved_partial_outcomes,
                budget=budget,
                status="within_budget",
            )
            if context_units(partial_content) <= budget:
                outcomes = partial_outcomes
                continue

        barrier_index = index
        break

    if barrier_index is not None:
        blocked = candidates[barrier_index:]
        if any(candidate.fragment.required for candidate in blocked):
            return _fallback(
                candidates,
                budget=budget,
                produced_measurement=produced_measurement,
                reason="required_fragment_exceeds_budget",
            )

    status = _status(outcomes)
    content = _render(candidates, outcomes, budget=budget, status=status)
    if context_units(content) > budget:
        return _fallback(
            candidates,
            budget=budget,
            produced_measurement=produced_measurement,
            reason="final_budget_guard",
        )

    manifest = _delivery_manifest(
        candidates,
        outcomes,
        budget=budget,
        status=status,
        fallback_reason=None,
    )
    deferred_source_lines = (
        candidate.full_line
        for candidate, outcome in zip(candidates, outcomes, strict=True)
        if outcome != "full"
    )
    return ComposedContext(
        content=content,
        measurement=_measure(content),
        produced_measurement=produced_measurement,
        deferred_source_measurement=_measure_lines(deferred_source_lines),
        manifest=manifest,
        fallback_used=False,
    )


__all__ = [
    "CONTEXT_FORMAT_VERSION",
    "DEFAULT_CONTEXT_BUDGET",
    "ComponentDelivery",
    "ComposedContext",
    "ContextFragment",
    "ContextMeasurement",
    "DeliveryManifest",
    "compose_context",
    "compose_fallback_context",
    "context_units",
    "measure_fragment_sources",
    "utf16_units",
]
