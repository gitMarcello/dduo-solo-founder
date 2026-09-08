from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from time import perf_counter

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.config import get_settings
from dduo_solo_founder.embeddings import EmbeddingCall, EmbeddingService, VectorOperationError
from dduo_solo_founder.memory_engine import (
    MEMORY_LANGUAGE_POLICY_VERSION,
    apply_sleep_action,
    existing_memory_language,
    infer_text_language,
    serialize_memory,
)
from dduo_solo_founder.models import (
    Activity,
    Artifact,
    Memory,
    Project,
    RawEvent,
    Segment,
    Session,
    SleepJob,
    SleepTopic,
    Turn,
    TurnArtifact,
)
from dduo_solo_founder.schemas import (
    ConsolidatedTopicPayload,
    MemoryActionPayload,
    MemoryConsolidationPayload,
    MemoryLanguage,
)
from dduo_solo_founder.observability import (
    Observation,
    embedding_price,
    estimated_tokens_for_text,
    record,
)
from dduo_solo_founder.pricing import api_equivalent_cost
from dduo_solo_founder.team import project_authority_predicate, project_authority_writable

MEMORY_CONSOLIDATION_INSTRUCTIONS = f"""
Read the supplied project conversation and return only JSON with a `topics` array. Each topic has
topic_id, label, query_text, language (`it` or `en`), source_turn_ids, source_message_ids, and
memory_actions. Use only IDs present in the input. Group coherent, reusable facts together; an
empty memory_actions array is valid when nothing durable should be stored. User statements and
attached artifacts are sources; assistant proposals are not facts unless the user confirms them.
Keep successive refinements of the same referent in one evolving topic and do not introduce a
fixed source taxonomy.

Write memory text, labels and queries in Italian by default: la lingua predefinita è l'italiano.
Use English only for a clearly English user request or an explicit request to use English.
Apply language policy `{MEMORY_LANGUAGE_POLICY_VERSION}` independently to each topic. An explicit
user request to write or remember in Italian or English has highest priority. Otherwise use the
language of the last substantive user message belonging to that topic. Choose `en` only when that
message is clearly English. If it is ambiguous, code-dominated, or genuinely mixed, keep the
existing memory language for a revision and use `it` for a new memory. The exact revision target is
the only related memory that may supply that fallback language. Do not infer the user's language
from assistant text, system instructions, profile text, unrelated memories, artifacts, pasted
quotations, code, logs, paths, or identifiers. Write label, query_text, and memory action text in
the selected language, while preserving exact proper nouns, technical terms, quotations, paths,
and code. Never translate topic_id, any supplied ID, target_node_key, or another identifier.

For each memory action, action is create, replace_current, or set_status; node type is episode,
reusable_fact, or heuristic. Make one global decision across the entire batch and represent each
durable referent once. Store only a new confirmed event, decision, correction, artifact insight,
or reusable principle beyond the supplied project profile. Never store task CRUD echoes or dDuo,
Docker, CLI, port, authentication, dashboard, backup, or plugin telemetry as project memory.
Use replace_current when the same referent evolves and preserve still-valid earlier context.
For replace_current and set_status, put the exact target memory first in source_node_ids and retain
its target_node_type and target_node_key unchanged. Use only supplied message, memory, and artifact
IDs. Do not create tasks, profile updates, raw events, or artifacts. Return topics and memory
actions only.
""".strip()

MAX_MESSAGE_CHARS = 30_000
MAX_BATCH_CHARS = 40_000
MAX_ARTIFACT_TEXT_CHARS = 12_000
MAX_ARTIFACT_SUMMARY_CHARS = 3_000
MAX_FULL_ARTIFACTS_PER_TURN = 4
MAX_RELATED_MEMORY_CHARS = 20_000

_LANGUAGE_REQUEST_VERBS = (
    r"answer|keep|memorize|phrase|record|remember|reply|respond|save|store|translate|use|write|"
    r"formula\w*|mantien\w*|memorizz\w*|redig\w*|ricord\w*|rispond\w*|salv\w*|scriv\w*|"
    r"traduc\w*|usa\w*"
)
_EXPLICIT_LANGUAGE_REQUESTS: tuple[tuple[MemoryLanguage, re.Pattern[str]], ...] = (
    (
        "en",
        re.compile(
            rf"\b(?:{_LANGUAGE_REQUEST_VERBS})\b[^.!?\n]{{0,100}}"
            r"(?:\b(?:in(?:to)?|using)\s+(?:the\s+)?english\b|"
            r"\b(?:in|all|l)['’]?\s*(?:lingua\s+)?inglese\b|\benglish\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "it",
        re.compile(
            rf"\b(?:{_LANGUAGE_REQUEST_VERBS})\b[^.!?\n]{{0,100}}"
            r"(?:\b(?:in(?:to)?|using)\s+(?:the\s+)?italian\b|"
            r"\b(?:in|all|l)['’]?\s*(?:lingua\s+)?italiano\b|\bitalian\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "en",
        re.compile(r"\b(?:language|lingua)\s*[:=]\s*(?:english|inglese)\b", re.IGNORECASE),
    ),
    (
        "it",
        re.compile(r"\b(?:language|lingua)\s*[:=]\s*(?:italian|italiano)\b", re.IGNORECASE),
    ),
    (
        "en",
        re.compile(r"^\s*(?:english|inglese)\s*(?:,?\s*(?:please|per favore))?[.!]?\s*$", re.I),
    ),
    (
        "it",
        re.compile(r"^\s*(?:italian|italiano)\s*(?:,?\s*(?:please|per favore))?[.!]?\s*$", re.I),
    ),
)
_FENCED_USER_CODE = re.compile(r"```.*?```", re.DOTALL)
_ATTACHMENT_PREAMBLE = re.compile(r"\A\s*# Files (?:mentioned|pasted) by (?:the )?user:\s*\n")
_ATTACHMENT_REQUEST = re.compile(r"^## My request:\s*$", re.MULTILINE)
_CODE_LOG_OR_QUOTE_LINE = re.compile(
    r"^\s*(?:>|[/{}\[]|\$\s|>>>|traceback\b|file\s+\"|at\s+\S+|"
    r"(?:error|exception|fatal|warning)\s*:|(?:class|const|def|function|import|let|var)\s+|"
    r"(?:docker|git|npm|pnpm|pytest|python|ruff|yarn)\s+|"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|[\w.-]+\s+\||"
    r"(?:&#x20;\s*)+[\"']?[\w.-]+[\"']?\s*:|[\"'][\w.-]+[\"']\s*:)",
    re.IGNORECASE,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _explicit_language_request(value: str) -> MemoryLanguage | None:
    matches = [
        (match.end(), language)
        for language, pattern in _EXPLICIT_LANGUAGE_REQUESTS
        for match in pattern.finditer(value)
    ]
    return max(matches, default=(-1, None), key=lambda item: item[0])[1]


def _is_substantive_user_message(value: str) -> bool:
    words = re.findall(r"[^\W\d_]+", value, flags=re.UNICODE)
    letters = sum(len(word) for word in words)
    if len(words) >= 3 and letters >= 10:
        return True
    # Code and logs are substantive input even though they intentionally provide no
    # language evidence. Tiny acknowledgements are skipped in favor of the prior turn.
    return len(value.strip()) >= 40


def _user_language_sample(value: str) -> str:
    # Attachment headings are client UI text, not the user's language. This
    # filtering affects only preference detection; the full source is preserved.
    if _ATTACHMENT_PREAMBLE.match(value):
        parts = _ATTACHMENT_REQUEST.split(value, maxsplit=1)
        value = parts[1] if len(parts) == 2 else ""
    without_fenced_code = _FENCED_USER_CODE.sub(" ", value)
    return "\n".join(
        line
        for line in without_fenced_code.splitlines()
        if not _CODE_LOG_OR_QUOTE_LINE.match(line)
        and not line.startswith("[user_prompt_fragment ")
        and line not in {"[... earlier middle content omitted ...]"}
    )


def _topic_memory_language(
    topic: ConsolidatedTopicPayload,
    turn_by_id: dict[str, dict],
    prompt_fragments_by_turn: dict[str, list[dict[str, str]]] | None = None,
) -> MemoryLanguage | None:
    source_ids = set(topic.source_turn_ids)
    selected_message_ids = set(getattr(topic, "source_message_ids", []) or [])
    messages: list[str] = []
    for turn_id, turn in turn_by_id.items():
        if turn_id not in source_ids:
            continue
        fragments = (prompt_fragments_by_turn or {}).get(turn_id, [])
        selected_fragments = [
            fragment
            for fragment in fragments
            if not selected_message_ids or fragment["message_id"] in selected_message_ids
        ]
        if not selected_fragments:
            selected_fragments = fragments
        # Only rendered fragments are supplied here. Language and provenance
        # must never be influenced by a middle fragment omitted from the
        # bounded model payload.
        if selected_fragments:
            messages.extend(fragment["content"] for fragment in selected_fragments)
        else:
            messages.append(str(turn["user_message"].get("content") or ""))
    for message in reversed(messages):
        if explicit := _explicit_language_request(_user_language_sample(message)):
            return explicit
    for message in reversed(messages):
        if _is_substantive_user_message(message):
            return infer_text_language(_user_language_sample(message))
    return None


def _resolved_action_language(
    hint: MemoryLanguage | None,
    action: MemoryActionPayload,
    related_by_id: dict[str, Memory],
) -> MemoryLanguage:
    if hint is not None:
        return hint
    if target := _source_action_target(action, related_by_id):
        return existing_memory_language(target)
    return "it"


def _source_action_target(
    action: MemoryActionPayload, related_by_id: dict[str, Memory]
) -> Memory | None:
    if action.action not in {"replace_current", "set_status"}:
        return None
    return next(
        (
            related_by_id[source_node_id]
            for source_node_id in action.source_node_ids
            if source_node_id in related_by_id
        ),
        None,
    )


def _resolved_topic_language(
    hint: MemoryLanguage | None,
    topic: ConsolidatedTopicPayload,
    related_by_id: dict[str, Memory],
) -> MemoryLanguage:
    if hint is not None:
        return hint
    for action in topic.memory_actions:
        if _source_action_target(action, related_by_id):
            return _resolved_action_language(hint, action, related_by_id)
    return "it"


def _generated_language_differs(value: str, expected: MemoryLanguage) -> bool:
    detected = infer_text_language(value) if _is_substantive_user_message(value) else None
    return detected is not None and detected != expected


def _stored_action_language(
    action: MemoryActionPayload,
    declared: MemoryLanguage,
    related_by_id: dict[str, Memory],
) -> MemoryLanguage:
    """Describe the saved text, not the preferred language; never translate it."""
    if action.action == "set_status":
        if target := _source_action_target(action, related_by_id):
            return existing_memory_language(target)
    return infer_text_language(action.text) or declared


class CliSleepProvider:
    def __init__(self, url: str | None = None, token: str | None = None):
        settings = get_settings()
        self.url = (url or settings.cli_bridge_url).rstrip("/")
        self.token = token if token is not None else settings.cli_bridge_token
        self.timeout = settings.sleep_cli_timeout_seconds

    async def generate_observed(
        self,
        *,
        provider: str,
        task: str,
        instructions: str,
        payload: dict,
        schema: dict,
        project_id: str | None = None,
    ) -> "SleepGeneration":
        if not self.token:
            raise SleepProviderError(
                "dependency_unavailable", "The local memory service needs to be restarted."
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout + 30) as client:
                response = await client.post(
                    f"{self.url}/v1/generate",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json=jsonable_encoder(
                        {
                            "provider": provider,
                            "task": task,
                            "instructions": instructions,
                            "input": payload,
                            "schema": schema,
                            "timeout_seconds": self.timeout,
                            **({"project_id": project_id} if project_id else {}),
                        }
                    ),
                )
        except httpx.HTTPError as exc:
            generation = SleepGeneration.from_bridge(
                output=None,
                provider=provider,
                model=None,
                duration_ms=None,
                usage={},
                instructions=instructions,
                payload=payload,
                estimate_when_missing=False,
            )
            raise SleepGenerationError(
                "The local memory service is temporarily unavailable.",
                generation,
                kind="bridge_unavailable",
            ) from exc
        try:
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ValueError("bridge response must be an object")
            body = decoded
        except ValueError as exc:
            generation = SleepGeneration.from_bridge(
                output=None,
                provider=provider,
                model=None,
                duration_ms=None,
                usage={},
                instructions=instructions,
                payload=payload,
                estimate_when_missing=False,
            )
            raise SleepGenerationError(
                "The local memory service returned an invalid response.",
                generation,
                kind="bridge_unavailable",
            ) from exc
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        output = body.get("output") if isinstance(body.get("output"), dict) else None
        reported_provider = body.get("provider")
        normalized_provider = (
            reported_provider if reported_provider in {"codex", "claude"} else provider
        )
        raw_kind = str(body.get("error") or "bridge_unavailable")
        kind = {
            "invalid_structured_output": "invalid_model_output",
            "cli_timeout": "bridge_unavailable",
            "cli_unavailable": "bridge_unavailable",
        }.get(raw_kind, raw_kind)
        if kind not in {
            "auth_required",
            "rate_limited",
            "bridge_unavailable",
            "dependency_unavailable",
            "invalid_model_output",
        }:
            kind = "bridge_unavailable"
        generation = SleepGeneration.from_bridge(
            output=output,
            provider=normalized_provider,
            model=_model_identifier(body.get("model")),
            duration_ms=_duration_ms(body.get("duration_seconds")),
            usage=usage,
            instructions=instructions,
            payload=payload,
            estimate_when_missing=(response.status_code == 200 and body.get("ok") is True)
            or raw_kind == "invalid_structured_output",
            client_cost_usd=body.get("client_cost_usd"),
            cost_source=body.get("cost_source"),
        )
        if response.status_code != 200:
            messages = {
                "auth_required": f"{provider.title()} needs to be connected again.",
                "rate_limited": f"{provider.title()} reached a temporary usage limit.",
                "dependency_unavailable": "The local memory service needs to be restarted.",
                "invalid_model_output": f"{provider.title()} returned invalid structured memory output.",
                "bridge_unavailable": "The local memory service is temporarily unavailable.",
            }
            retry_after = body.get("retry_after_seconds")
            diagnostic = body.get("diagnostic")
            raise SleepGenerationError(
                messages[kind],
                generation,
                kind=kind,
                retry_after_seconds=retry_after if isinstance(retry_after, int) else None,
                diagnostic=(
                    diagnostic
                    if isinstance(diagnostic, str)
                    and diagnostic.strip()
                    and kind != "bridge_unavailable"
                    else None
                ),
            )
        if not body.get("ok") or generation.output is None:
            raise SleepGenerationError(
                f"{provider.title()} returned invalid structured memory output.",
                generation,
                kind="invalid_model_output",
            )
        return generation

    async def generate(
        self,
        *,
        provider: str,
        task: str,
        instructions: str,
        payload: dict,
        schema: dict,
        project_id: str | None = None,
    ) -> dict:
        """Compatibility entry point for callers that only need structured output."""
        generation = await self.generate_observed(
            provider=provider,
            task=task,
            instructions=instructions,
            payload=payload,
            schema=schema,
            project_id=project_id,
        )
        if generation.output is None:
            raise SleepProviderError(
                "invalid_model_output",
                f"{provider.title()} returned invalid structured memory output.",
            )
        return generation.output


class SleepProviderError(RuntimeError):
    """A typed provider failure with a safe retry policy input."""

    def __init__(
        self,
        kind: str,
        detail: str,
        *,
        retry_after_seconds: int | None = None,
        diagnostic: str | None = None,
    ):
        super().__init__(detail)
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds
        self.diagnostic = diagnostic


def _usage_number(usage: dict, key: str) -> int | None:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def _duration_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return max(0, round(parsed * 1000))


def _model_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if re.fullmatch(r"[A-Za-z0-9._:/+-]{1,160}", candidate) else None


def _client_cost_usd(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    normalized = format(parsed, "f")
    integer, _, fraction = normalized.partition(".")
    if len(integer.lstrip("-")) > 8 or len(fraction) > 12:
        return None
    return parsed


@dataclass(slots=True)
class SleepGeneration:
    output: dict | None
    provider: str
    model: str | None
    duration_ms: int | None
    measurement_source: str
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    reported_total_tokens: int | None
    client_cost_usd: Decimal | None = None
    cost_source: str | None = None

    @classmethod
    def from_bridge(
        cls,
        *,
        output: dict | None,
        provider: str,
        model: str | None,
        duration_ms: int | None,
        usage: dict,
        instructions: str,
        payload: dict,
        estimate_when_missing: bool = True,
        client_cost_usd: object = None,
        cost_source: object = None,
    ) -> "SleepGeneration":
        normalized_cost = _client_cost_usd(client_cost_usd)
        normalized_cost_source = (
            "claude_code_client_estimate"
            if provider == "claude"
            and normalized_cost is not None
            and cost_source == "claude_code_client_estimate"
            else None
        )
        if normalized_cost_source is None:
            normalized_cost = None
        reported = {
            "input_tokens": _usage_number(usage, "input_tokens"),
            "cached_input_tokens": _usage_number(usage, "cached_input_tokens"),
            "cache_write_input_tokens": _usage_number(usage, "cache_write_input_tokens"),
            "output_tokens": _usage_number(usage, "output_tokens"),
            "reasoning_tokens": _usage_number(usage, "reasoning_tokens"),
            "reported_total_tokens": _usage_number(usage, "total_tokens"),
        }
        if any(value is not None for value in reported.values()):
            return cls(
                output=output,
                provider=provider,
                model=_model_identifier(model),
                duration_ms=duration_ms,
                measurement_source="provider_reported",
                client_cost_usd=normalized_cost,
                cost_source=normalized_cost_source,
                **reported,
            )
        if not estimate_when_missing:
            return cls(
                output=output,
                provider=provider,
                model=_model_identifier(model),
                duration_ms=duration_ms,
                measurement_source="unavailable",
                input_tokens=None,
                cached_input_tokens=None,
                cache_write_input_tokens=None,
                output_tokens=None,
                reasoning_tokens=None,
                reported_total_tokens=None,
                client_cost_usd=normalized_cost,
                cost_source=normalized_cost_source,
            )
        input_estimate, _, _ = estimated_tokens_for_text(
            instructions + "\n" + json.dumps(payload, ensure_ascii=False, default=str)
        )
        output_estimate = None
        if output is not None:
            output_estimate, _, _ = estimated_tokens_for_text(
                json.dumps(output, ensure_ascii=False)
            )
        return cls(
            output=output,
            provider=provider,
            model=_model_identifier(model),
            duration_ms=duration_ms,
            measurement_source="local_estimate",
            input_tokens=input_estimate,
            cached_input_tokens=None,
            cache_write_input_tokens=None,
            output_tokens=output_estimate,
            reasoning_tokens=None,
            reported_total_tokens=None,
            client_cost_usd=normalized_cost,
            cost_source=normalized_cost_source,
        )


class SleepGenerationError(SleepProviderError):
    """A failed model call that still carries privacy-safe provider telemetry."""

    def __init__(
        self,
        message: str,
        generation: SleepGeneration,
        *,
        kind: str = "invalid_model_output",
        retry_after_seconds: int | None = None,
        diagnostic: str | None = None,
    ):
        super().__init__(
            kind,
            message,
            retry_after_seconds=retry_after_seconds,
            diagnostic=diagnostic,
        )
        self.generation = generation


SLEEP_EXECUTOR_PROVIDERS = frozenset({"codex", "claude"})


def sleep_executor_provider(project: Project | None = None, *, source_client: str | None = None) -> str:
    """Return one project's stable preferred sleep executor.

    The bridge resolves this preference against the subscriptions available on
    the machine that owns the memory immediately before each model call.  This
    function intentionally has no environment-variable override: a global
    executor made a Claude project silently depend on a Codex subscription.
    """
    configured = getattr(project, "sleep_executor_preference", None)
    if configured in SLEEP_EXECUTOR_PROVIDERS:
        return configured
    if source_client in SLEEP_EXECUTOR_PROVIDERS:
        return str(source_client)
    # Existing pre-migration data and direct test construction retain the
    # historic default until a supported session establishes a preference.
    return "codex"


async def _generate_sleep(
    provider: object,
    *,
    provider_name: str,
    task: str,
    instructions: str,
    payload: dict,
    schema: dict,
    project_id: str | None = None,
) -> SleepGeneration:
    started = perf_counter()
    observed = getattr(provider, "generate_observed", None)
    if callable(observed):
        return await observed(
            provider=provider_name,
            task=task,
            instructions=instructions,
            payload=payload,
            schema=schema,
            project_id=project_id,
        )
    output = await provider.generate(  # type: ignore[attr-defined]
        provider=provider_name,
        task=task,
        instructions=instructions,
        payload=payload,
        schema=schema,
        project_id=project_id,
    )
    generation = SleepGeneration.from_bridge(
        output=output if isinstance(output, dict) else None,
        provider=provider_name,
        model=None,
        duration_ms=round((perf_counter() - started) * 1000),
        usage={},
        instructions=instructions,
        payload=payload,
    )
    if generation.output is None:
        raise SleepGenerationError("sleep provider returned invalid structured output", generation)
    return generation


async def _record_sleep_generation(
    db: AsyncSession,
    *,
    job: SleepJob,
    operation: str,
    generation: SleepGeneration | None,
    status: str,
    duration_ms: int,
) -> None:
    client_cost = (
        generation.client_cost_usd
        if generation is not None
        and generation.provider == "claude"
        and generation.cost_source == "claude_code_client_estimate"
        else None
    )
    equivalent_cost = (
        api_equivalent_cost(
            provider=generation.provider,
            model=generation.model,
            input_tokens=generation.input_tokens,
            cached_input_tokens=generation.cached_input_tokens,
            cache_write_input_tokens=generation.cache_write_input_tokens,
            output_tokens=generation.output_tokens,
            reasoning_tokens=generation.reasoning_tokens,
        )
        if generation and client_cost is None
        else None
    )
    await record(
        db,
        Observation(
            project_id=job.project_id,
            idempotency_key=f"sleep:{job.id}:attempt:{job.attempts}:{operation}",
            category="sleep_model",
            operation=operation,
            status=status,
            provider=generation.provider if generation else job.provider,
            model=generation.model if generation else None,
            attempt=job.attempts,
            measurement_source=generation.measurement_source if generation else "unavailable",
            session_id=job.session_id,
            sleep_job_id=job.id,
            actor_member_id=job.actor_member_id,
            input_tokens=generation.input_tokens if generation else None,
            cached_input_tokens=generation.cached_input_tokens if generation else None,
            cache_write_input_tokens=generation.cache_write_input_tokens if generation else None,
            output_tokens=generation.output_tokens if generation else None,
            reasoning_tokens=generation.reasoning_tokens if generation else None,
            reported_total_tokens=generation.reported_total_tokens if generation else None,
            duration_ms=duration_ms,
            provider_duration_ms=generation.duration_ms if generation else None,
            request_count=1,
            cost_usd=(client_cost if client_cost is not None else equivalent_cost.cost_usd)
            if equivalent_cost is not None or client_cost is not None
            else None,
            unit_price_usd_per_million=(
                equivalent_cost.unit_price_usd_per_million if equivalent_cost else None
            ),
            pricing_version=(
                "claude-code-client-estimate-v1"
                if client_cost is not None
                else equivalent_cost.pricing_version
                if equivalent_cost
                else None
            ),
            details={
                "cost_kind": "api_equivalent",
                **(equivalent_cost.numeric_details() if equivalent_cost is not None else {}),
            },
        ),
    )


def _dedupe_key(project_id: str, session_id: str, turn_ids: list[str]) -> str:
    material = "\0".join([project_id, session_id, *sorted(turn_ids)])
    return hashlib.sha256(material.encode()).hexdigest()


async def schedule_sleep(
    db: AsyncSession,
    *,
    project_id: str,
    session_id: str,
    trigger: str,
    segment_id: str | None = None,
    turn_ids: list[str] | None = None,
) -> SleepJob | None:
    session = await db.scalar(select(Session).where(Session.id == session_id).with_for_update())
    if not session or session.project_id != project_id:
        raise LookupError("session not found")
    if session.client not in {"codex", "claude"}:
        raise ValueError("sleep requires a Codex or Claude session")
    project = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise LookupError("project not found")
    preference = sleep_executor_provider(project, source_client=session.client)
    if turn_ids is None:
        statement = select(Turn.id).where(
            Turn.project_id == project_id,
            Turn.session_id == session_id,
            Turn.sleep_status == "pending",
            Turn.off_record.is_(False),
        )
        if segment_id:
            statement = statement.where(Turn.segment_id == segment_id)
        turn_ids = list((await db.scalars(statement.order_by(Turn.created_at))).all())
    turn_ids = list(dict.fromkeys(turn_ids))
    if not turn_ids:
        return None

    active_jobs = list(
        (
            await db.scalars(
                select(SleepJob)
                .where(
                    SleepJob.project_id == project_id,
                    SleepJob.session_id == session_id,
                    SleepJob.status.in_(["pending", "running", "waiting"]),
                )
                .order_by(SleepJob.created_at)
            )
        ).all()
    )
    if trigger == "manual":
        waiting = next((item for item in active_jobs if item.status == "waiting"), None)
        if waiting:
            waiting.status = "pending"
            waiting.not_before = utcnow()
            waiting.last_error = None
            waiting.error_kind = None
            waiting.retry_at = None
            return waiting

    assigned_turn_ids = {
        turn_id for active_job in active_jobs for turn_id in active_job.input_turn_ids
    }
    turn_ids = [turn_id for turn_id in turn_ids if turn_id not in assigned_turn_ids]
    batch_size = max(1, min(get_settings().sleep_batch_size, 100))
    turn_ids = turn_ids[:batch_size]
    if not turn_ids:
        return active_jobs[0] if trigger == "manual" and active_jobs else None
    key = _dedupe_key(project_id, session_id, turn_ids)
    existing = await db.scalar(select(SleepJob).where(SleepJob.dedupe_key == key))
    if existing:
        if trigger == "manual" and existing.status == "waiting":
            existing.status = "pending"
            existing.not_before = utcnow()
            existing.last_error = None
            existing.error_kind = None
            existing.retry_at = None
        return existing
    job = SleepJob(
        project_id=project_id,
        session_id=session_id,
        segment_id=segment_id,
        executor_preference=preference,
        provider=preference,
        source_client=session.client,
        actor_member_id=session.member_id,
        trigger=trigger,
        dedupe_key=key,
        input_turn_ids=turn_ids,
    )
    db.add(job)
    await db.flush()
    db.add(
        Activity(
            project_id=project_id,
            kind="sleep.scheduled",
            summary=f"Memory sleep scheduled via {job.provider}",
            detail={
                "job_id": job.id,
                "trigger": trigger,
                "turn_count": len(turn_ids),
                "source_client": session.client,
                "executor_preference": preference,
            },
            actor="system",
            actor_member_id=session.member_id,
        )
    )
    return job


async def schedule_project_sleep(
    db: AsyncSession, project_id: str, *, trigger: str, session_id: str | None = None
) -> list[SleepJob]:
    sessions = (
        [await db.get(Session, session_id)]
        if session_id
        else list(
            (
                await db.scalars(
                    select(Session).where(
                        Session.project_id == project_id,
                        Session.client.in_(["codex", "claude"]),
                    )
                )
            ).all()
        )
    )
    jobs: list[SleepJob] = []
    for session in sessions:
        if not session or session.project_id != project_id:
            raise LookupError("session not found")
        job = await schedule_sleep(
            db,
            project_id=project_id,
            session_id=session.id,
            trigger=trigger,
        )
        if job:
            jobs.append(job)
    return jobs


async def resume_project_sleep(
    db: AsyncSession,
    project_id: str,
    *,
    session_id: str | None = None,
    provider: str | None = None,
    resume_auth: bool = False,
) -> list[SleepJob]:
    """Resume paused work after a verified host subscription reconnects.

    A reconnect of either supported provider can unblock a job: the bridge
    resolves the preferred provider and any verified fallback at execution.
    """
    active_jobs = list(
        (
            await db.scalars(
                select(SleepJob)
                .where(
                    SleepJob.project_id == project_id,
                    SleepJob.status.in_(["pending", "running", "waiting"]),
                )
                .with_for_update()
            )
        ).all()
    )
    for active_job in active_jobs:
        active_job.source_client = active_job.source_client or active_job.provider
        active_job.executor_preference = active_job.executor_preference or active_job.provider
    waiting_statement = select(SleepJob).where(
        SleepJob.project_id == project_id,
        SleepJob.status == "waiting",
    )
    if session_id:
        waiting_statement = waiting_statement.where(SleepJob.session_id == session_id)
    waiting = list(
        (await db.scalars(waiting_statement.order_by(SleepJob.created_at).with_for_update())).all()
    )
    now = utcnow()
    automatic_kinds = {
        None,
        "bridge_unavailable",
        "dependency_unavailable",
        "invalid_model_output",
        "rate_limited",
    }
    recoverable = [
        job
        for job in waiting
        if (job.error_kind in automatic_kinds and (job.retry_at is None or job.retry_at <= now))
        or (
            resume_auth
            and job.error_kind == "auth_required"
            and provider in SLEEP_EXECUTOR_PROVIDERS
        )
    ]
    for job in recoverable:
        job.status = "pending"
        job.not_before = now
        job.last_error = None
        job.error_kind = None
        job.retry_at = None

    session_statement = select(Session).where(
        Session.project_id == project_id,
        Session.client.in_(["codex", "claude"]),
    )
    if session_id:
        session_statement = session_statement.where(Session.id == session_id)
    sessions = list((await db.scalars(session_statement)).all())
    jobs = list(recoverable)
    known_ids = {item.id for item in jobs}
    for session in sessions:
        job = await schedule_sleep(
            db,
            project_id=project_id,
            session_id=session.id,
            trigger="session_start",
        )
        if job and job.id not in known_ids:
            jobs.append(job)
            known_ids.add(job.id)

    if recoverable:
        db.add(
            Activity(
                project_id=project_id,
                kind="sleep.resumed",
                summary="Pending memory sleep resumed at session start",
                detail={"job_ids": [item.id for item in recoverable]},
                actor="system",
            )
        )
    return jobs


async def schedule_due_sleep(db: AsyncSession) -> int:
    settings = get_settings()
    now = utcnow()
    active_jobs = list(
        (
            await db.scalars(
                select(SleepJob)
                .join(Project, Project.id == SleepJob.project_id)
                .where(
                    SleepJob.status.in_(["pending", "running", "waiting"]),
                    project_authority_predicate(),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for active_job in active_jobs:
        active_job.source_client = active_job.source_client or active_job.provider
        active_job.executor_preference = active_job.executor_preference or active_job.provider
    retryable = list(
        (
            await db.scalars(
                select(SleepJob)
                .join(Project, Project.id == SleepJob.project_id)
                .where(
                    SleepJob.status == "waiting",
                    SleepJob.error_kind.in_(
                        [
                            "rate_limited",
                            "bridge_unavailable",
                            "dependency_unavailable",
                            "invalid_model_output",
                        ]
                    ),
                    SleepJob.retry_at.is_not(None),
                    SleepJob.retry_at <= now,
                    project_authority_predicate(),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for job in retryable:
        job.status = "pending"
        job.not_before = now
        job.last_error = None
        job.error_kind = None
        job.retry_at = None
    sessions = list(
        (
            await db.scalars(
                select(Session)
                .join(Project, Project.id == Session.project_id)
                .where(
                    Session.client.in_(["codex", "claude"]),
                    project_authority_predicate(),
                )
            )
        ).all()
    )
    scheduled = len(retryable)
    cutoff = now - timedelta(seconds=settings.sleep_idle_seconds)
    for session in sessions:
        turns = list(
            (
                await db.scalars(
                    select(Turn)
                    .where(
                        Turn.session_id == session.id,
                        Turn.sleep_status == "pending",
                        Turn.off_record.is_(False),
                    )
                    .order_by(Turn.created_at)
                )
            ).all()
        )
        if not turns:
            continue
        trigger = None
        if len(turns) >= settings.sleep_turn_threshold:
            trigger = "threshold"
        elif (turns[-1].committed_at or turns[-1].created_at) <= cutoff:
            trigger = "idle"
        if trigger and await schedule_sleep(
            db,
            project_id=session.project_id,
            session_id=session.id,
            trigger=trigger,
            turn_ids=[item.id for item in turns],
        ):
            scheduled += 1
    if scheduled:
        await db.commit()
    return scheduled


def _head_tail(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n\n[... earlier middle content omitted ...]\n\n"
    available = max(limit - len(marker), 0)
    head = available // 2
    return f"{value[:head]}{marker}{value[-(available - head):]}"[:limit]


def _normalized_prompt_fragments(
    turn: Turn,
    message_ids: list[str],
    fragments: list[dict[str, str]] | None,
) -> list[dict[str, object]]:
    normalized = fragments or []
    fragment_aggregate = "\n\n".join(item["content"] for item in normalized)
    if not normalized or (len(normalized) == 1 and fragment_aggregate != turn.user_prompt):
        # Turn is the authoritative aggregate. This also keeps pre-fragment
        # history and manually repaired/imported turns semantically intact.
        normalized = [{"message_id": message_ids[0], "content": turn.user_prompt}]
    return [
        {
            "message_id": fragment["message_id"],
            "ordinal": index + 1,
            "content": fragment["content"],
        }
        for index, fragment in enumerate(normalized)
    ]


def _fragment_header(fragment: dict[str, object]) -> str:
    return (
        f"[user_prompt_fragment {fragment['ordinal']}; "
        f"message_id={fragment['message_id']}]\n"
    )


def _bounded_fragment_content(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    marker = "\n\n[... earlier middle content omitted ...]\n\n"
    # Tiny allocations keep the latest correction rather than returning only
    # a truncated omission marker.
    return value[-limit:] if limit <= len(marker) else _head_tail(value, limit)


def _presented_prompt_fragments(
    turn: Turn,
    message_ids: list[str],
    fragments: list[dict[str, str]] | None,
    *,
    limit: int = MAX_MESSAGE_CHARS,
) -> list[dict[str, object]]:
    """Give every declared source a real bounded sample, or omit its ID too."""
    candidates = _normalized_prompt_fragments(turn, message_ids, fragments)
    minimum_sample = 128

    def required(selected: list[dict[str, object]]) -> int:
        headers = sum(len(_fragment_header(item)) for item in selected)
        separators = max(len(selected) - 1, 0) * 2
        samples = sum(min(len(str(item["content"])), minimum_sample) for item in selected)
        return headers + separators + samples

    selected = list(candidates)
    if required(selected) > limit:
        # Latest steering is authoritative; retain the opening request next,
        # then add as many recent middle fragments as fit. The manifest below
        # exposes only this actually presented subset.
        selected = []
        priority = [candidates[-1], *candidates[:1], *reversed(candidates[1:-1])]
        seen: set[str] = set()
        for candidate in priority:
            identity = str(candidate["message_id"])
            if identity in seen:
                continue
            proposed = sorted([*selected, candidate], key=lambda item: int(item["ordinal"]))
            if required(proposed) <= limit or not selected:
                selected = proposed
                seen.add(identity)

    overhead = sum(len(_fragment_header(item)) for item in selected) + max(
        len(selected) - 1, 0
    ) * 2
    remaining = max(limit - overhead, 0)
    allocations = [0] * len(selected)
    active = {index for index, item in enumerate(selected) if str(item["content"])}
    while remaining > 0 and active:
        share = max(remaining // len(active), 1)
        progressed = False
        for index in sorted(active):
            content = str(selected[index]["content"])
            grant = min(len(content) - allocations[index], share, remaining)
            if grant > 0:
                allocations[index] += grant
                remaining -= grant
                progressed = True
            if allocations[index] >= len(content):
                active.discard(index)
            if remaining <= 0:
                break
        if not progressed:
            break

    return [
        {
            "message_id": item["message_id"],
            "ordinal": item["ordinal"],
            "content": _bounded_fragment_content(str(item["content"]), allocations[index]),
        }
        for index, item in enumerate(selected)
    ]


def _render_prompt_fragments(
    turn: Turn,
    message_ids: list[str],
    fragments: list[dict[str, str]] | None,
    *,
    limit: int = MAX_MESSAGE_CHARS,
) -> tuple[str, list[dict[str, object]]]:
    presented = _presented_prompt_fragments(
        turn, message_ids, fragments, limit=limit
    )
    rendered = "\n\n".join(
        f"[user_prompt_fragment {fragment['ordinal']}; message_id={fragment['message_id']}]\n"
        f"{fragment['content']}"
        for fragment in presented
    )
    manifest = [
        {"message_id": fragment["message_id"], "ordinal": fragment["ordinal"]}
        for fragment in presented
    ]
    return rendered[:limit], manifest


def _serialize_turn(
    turn: Turn,
    artifacts: list[Artifact],
    message_ids: dict[str, list[str]] | None = None,
    prompt_fragments: list[dict[str, str]] | None = None,
    *,
    message_char_limit: int = MAX_MESSAGE_CHARS,
) -> dict:
    message_ids = message_ids or {}
    user_message_ids = message_ids.get("user_prompt") or [f"user:{turn.id}"]
    assistant_message_ids = message_ids.get("assistant_response") or [f"assistant:{turn.id}"]
    user_content, fragment_manifest = _render_prompt_fragments(
        turn, user_message_ids, prompt_fragments, limit=message_char_limit
    )
    presented_user_ids = [str(item["message_id"]) for item in fragment_manifest]
    return {
        "turn_id": turn.id,
        "timestamp": turn.created_at.isoformat(),
        "user_message": {
            "message_id": presented_user_ids[0],
            "source_message_ids": presented_user_ids,
            "content": user_content,
            "fragments": fragment_manifest,
            "occurred_at": turn.created_at.isoformat(),
            "artifacts": [
                _serialize_artifact_for_sleep(item, full=index < MAX_FULL_ARTIFACTS_PER_TURN)
                for index, item in enumerate(artifacts)
            ],
        },
        "assistant_message": {
            "message_id": assistant_message_ids[0],
            "source_message_ids": assistant_message_ids,
            "content": _head_tail(turn.assistant_response, message_char_limit),
            "occurred_at": (turn.committed_at or turn.created_at).isoformat(),
        },
    }


def _serialize_artifact_for_sleep(artifact: Artifact, *, full: bool = True) -> dict:
    return {
        "artifact_id": artifact.id,
        "kind": artifact.kind,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "source_uri": artifact.source_uri,
        "summary": artifact.summary[: MAX_ARTIFACT_SUMMARY_CHARS if full else 500],
        "extracted_text": artifact.extracted_text[:MAX_ARTIFACT_TEXT_CHARS] if full else "",
        "metadata": artifact.metadata_json,
    }


async def _artifacts_by_turn(db: AsyncSession, turn_ids: list[str]) -> dict[str, list[Artifact]]:
    if not turn_ids:
        return {}
    rows = (
        await db.execute(
            select(TurnArtifact.turn_id, Artifact)
            .join(Artifact, Artifact.id == TurnArtifact.artifact_id)
            .where(TurnArtifact.turn_id.in_(turn_ids), Artifact.status == "active")
        )
    ).all()
    result: dict[str, list[Artifact]] = {}
    for turn_id, artifact in rows:
        result.setdefault(turn_id, []).append(artifact)
    return result


def _serialize_related_memory(memory: Memory) -> dict:
    payload = serialize_memory(memory)
    payload["text"] = memory.text[:MAX_RELATED_MEMORY_CHARS]
    return payload


async def _message_ids_by_turn(
    db: AsyncSession, turn_ids: list[str]
) -> dict[str, dict[str, list[str]]]:
    if not turn_ids:
        return {}
    rows = (
        await db.execute(
            select(RawEvent.turn_id, RawEvent.event_type, RawEvent.id)
            .where(
                RawEvent.turn_id.in_(turn_ids),
                RawEvent.event_type.in_(["user_prompt", "assistant_response"]),
            )
            .order_by(RawEvent.created_at, RawEvent.id)
        )
    ).all()
    result: dict[str, dict[str, list[str]]] = {}
    for turn_id, event_type, event_id in rows:
        if turn_id:
            result.setdefault(turn_id, {}).setdefault(event_type, []).append(event_id)
    return result


async def _prompt_fragments_by_turn(
    db: AsyncSession, turn_ids: list[str]
) -> dict[str, list[dict[str, str]]]:
    if not turn_ids:
        return {}
    rows = (
        await db.execute(
            select(RawEvent.turn_id, RawEvent.id, RawEvent.payload)
            .where(
                RawEvent.turn_id.in_(turn_ids),
                RawEvent.event_type == "user_prompt",
            )
            .order_by(RawEvent.created_at, RawEvent.id)
        )
    ).all()
    result: dict[str, list[dict[str, str]]] = {}
    for turn_id, event_id, payload in rows:
        if not turn_id:
            continue
        content = str(payload.get("text") or "") if isinstance(payload, dict) else ""
        result.setdefault(turn_id, []).append(
            {"message_id": str(event_id), "content": content}
        )
    return result


def _serialized_message_ids(message: dict) -> list[str]:
    values = message.get("source_message_ids")
    ids = [str(value) for value in values if str(value)] if isinstance(values, list) else []
    primary = str(message.get("message_id") or "")
    return list(dict.fromkeys([*ids, *([primary] if primary else [])]))


async def _related_memories(
    db: AsyncSession,
    embeddings: EmbeddingService,
    project_id: str,
    query: str,
    job: SleepJob,
) -> list[Memory]:
    settings = get_settings()
    started = perf_counter()
    embedding: EmbeddingCall | None = None
    vector_store_duration_ms: int | None = None
    error: Exception | None = None
    hits: list[dict] = []
    try:
        observed = getattr(embeddings, "search_observed", None)
        if callable(observed):
            result = await asyncio.to_thread(observed, project_id, query, 24)
            hits = result.items
            embedding = result.embedding
            vector_store_duration_ms = result.vector_store_duration_ms
        else:
            hits = await asyncio.to_thread(embeddings.search, project_id, query, 24)
    except VectorOperationError as exc:
        error = exc
        embedding = exc.embedding
        vector_store_duration_ms = exc.vector_store_duration_ms
    except Exception as exc:
        error = exc
    usage = embedding.usage if embedding else None
    cost, unit_price, pricing_version = embedding_price(
        usage.model if usage else None, usage.input_tokens if usage else None
    )
    await record(
        db,
        Observation(
            project_id=project_id,
            idempotency_key=(
                f"sleep-related:{job.id}:attempt:{job.attempts}:"
                f"{hashlib.sha256(query.encode()).hexdigest()[:16]}"
            ),
            category="embedding",
            operation="embedding.sleep_related",
            status=(
                "success"
                if error is None
                else "partial_failure"
                if embedding is not None
                else "failed"
            ),
            provider=usage.provider if usage else None,
            model=usage.model if usage else None,
            attempt=job.attempts,
            measurement_source=usage.measurement_source if usage else "unavailable",
            session_id=job.session_id,
            sleep_job_id=job.id,
            actor_member_id=job.actor_member_id,
            input_tokens=usage.input_tokens if usage else None,
            reported_total_tokens=usage.reported_total_tokens if usage else None,
            duration_ms=round((perf_counter() - started) * 1000),
            provider_duration_ms=embedding.provider_duration_ms if embedding else None,
            vector_store_duration_ms=vector_store_duration_ms,
            request_count=1,
            item_count=len(hits),
            candidate_count=len(hits),
            cost_usd=cost,
            unit_price_usd_per_million=unit_price,
            pricing_version=pricing_version,
            details={"limit": 24, "error_code": "vector_operation_failed" if error else ""},
        ),
    )
    await db.commit()
    if error:
        raise error
    ids = [
        str(item["id"])
        for item in hits
        if float(item.get("score") or 0) >= settings.retrieval_similarity_threshold
    ]
    if not ids:
        return []
    memories = list(
        (
            await db.scalars(
                select(Memory).where(
                    Memory.project_id == project_id,
                    Memory.id.in_(ids),
                    Memory.status == "active",
                )
            )
        ).all()
    )
    scores = {str(item["id"]): float(item.get("score") or 0) for item in hits}
    memories.sort(key=lambda item: scores.get(item.id, 0), reverse=True)
    return memories[:12]


def _bounded_turn_batch(turns: list[dict], limit: int) -> list[dict]:
    """Keep whole turns whenever possible and make one oversized turn safe to send."""
    selected: list[dict] = []
    used = 0
    for turn in turns:
        encoded = json.dumps(turn, ensure_ascii=False, separators=(",", ":"))
        if selected and used + len(encoded) > limit:
            break
        if not selected and len(encoded) > limit:
            turn = json.loads(json.dumps(turn, ensure_ascii=False))
            user = turn["user_message"]
            assistant = turn["assistant_message"]
            user["content"] = _head_tail(user["content"], max(2_000, limit // 3))
            assistant["content"] = _head_tail(
                assistant["content"], max(2_000, limit // 3)
            )
            user["artifacts"] = [
                {**artifact, "extracted_text": artifact.get("extracted_text", "")[:1_000]}
                for artifact in user.get("artifacts", [])[:2]
            ]
        selected.append(turn)
        used += len(json.dumps(turn, ensure_ascii=False, separators=(",", ":")))
    return selected


def _retry_delay(error: SleepProviderError, attempts: int) -> int | None:
    if error.kind == "auth_required":
        return None
    if error.kind == "rate_limited":
        return error.retry_after_seconds or 30 * 60
    if error.kind in {"bridge_unavailable", "dependency_unavailable"}:
        return min(5 * 60 * (2 ** max(0, attempts - 1)), 60 * 60)
    return min(10 * 60 * max(1, attempts), 60 * 60)


def _failure_summary(error: SleepProviderError, provider: str) -> str:
    if error.kind == "auth_required":
        return f"Connect {provider.title()} to resume memory consolidation."
    if error.kind == "rate_limited":
        return f"{provider.title()} has a temporary usage limit."
    if error.kind == "invalid_model_output":
        return "Memory consolidation will retry after an invalid structured result."
    return "Local memory consolidation will retry automatically."


async def _mark_sleep_waiting(db: AsyncSession, job_id: str, error: SleepProviderError) -> None:
    """Persist a typed failure with redacted evidence hidden from the UI."""
    await db.rollback()
    current = await db.get(SleepJob, job_id)
    if not current or current.status == "cancelled":
        return
    delay = _retry_delay(error, current.attempts)
    now = utcnow()
    summary = _failure_summary(error, current.provider)
    current.status = "waiting"
    current.error_kind = error.kind
    current.last_error = (error.diagnostic or str(error)).strip()[-2000:]
    current.retry_at = now + timedelta(seconds=delay) if delay else None
    current.not_before = current.retry_at or now + timedelta(days=365)
    current.result = {
        **(current.result or {}),
        "error_kind": error.kind,
        "retry_at": current.retry_at.isoformat() if current.retry_at else None,
    }
    db.add(
        Activity(
            project_id=current.project_id,
            kind="sleep.waiting",
            summary=summary,
            detail={"job_id": current.id, "error_kind": error.kind},
            actor="system",
            actor_member_id=current.actor_member_id,
        )
    )
    await db.commit()


async def process_one_sleep_job(
    db: AsyncSession, embeddings: EmbeddingService, provider: CliSleepProvider
) -> bool:
    """Consolidate one provider-owned batch with exactly one structured model call."""
    job = await db.scalar(
        select(SleepJob)
        .join(Project, Project.id == SleepJob.project_id)
        .where(
            SleepJob.status == "pending",
            SleepJob.not_before <= utcnow(),
            project_authority_predicate(),
        )
        .order_by(SleepJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return False
    job.source_client = job.source_client or job.provider
    job.executor_preference = getattr(job, "executor_preference", None) or job.provider
    job_id = job.id
    job_project_id = job.project_id
    claimed_project = await db.scalar(
        select(Project).where(Project.id == job_project_id).with_for_update()
    )
    if claimed_project is None or not project_authority_writable(claimed_project):
        await db.rollback()
        return False
    competing = await db.scalar(
        select(SleepJob.id)
        .where(
            SleepJob.project_id == job_project_id,
            SleepJob.status == "running",
            SleepJob.id != job.id,
        )
        .limit(1)
    )
    if competing:
        await db.rollback()
        return False
    job.status = "running"
    job.started_at = utcnow()
    job.attempts += 1
    await db.commit()

    try:
        turns = list(
            (
                await db.scalars(
                    select(Turn)
                    .where(
                        Turn.project_id == job.project_id,
                        Turn.id.in_(job.input_turn_ids),
                        Turn.off_record.is_(False),
                    )
                    .order_by(Turn.created_at)
                )
            ).all()
        )
        if not turns:
            job.status = "completed"
            job.completed_at = utcnow()
            job.result = {"skipped_reason": "no_recordable_turns"}
            await db.commit()
            return True

        artifacts_by_turn = await _artifacts_by_turn(db, [turn.id for turn in turns])
        message_ids_by_turn = await _message_ids_by_turn(db, [turn.id for turn in turns])
        prompt_fragments_by_turn = await _prompt_fragments_by_turn(
            db, [turn.id for turn in turns]
        )
        settings = get_settings()
        batch_char_limit = max(
            1_000, getattr(settings, "sleep_batch_char_limit", MAX_BATCH_CHARS)
        )
        message_char_limit = min(MAX_MESSAGE_CHARS, max(2_000, batch_char_limit // 3))
        serialized = [
            _serialize_turn(
                turn,
                artifacts_by_turn.get(turn.id, []),
                message_ids_by_turn.get(turn.id),
                prompt_fragments_by_turn.get(turn.id),
                message_char_limit=message_char_limit,
            )
            for turn in turns
        ]
        serialized_turns = _bounded_turn_batch(serialized, batch_char_limit)
        selected_turn_ids = [item["turn_id"] for item in serialized_turns]
        if not selected_turn_ids:
            raise SleepProviderError(
                "invalid_model_output", "The memory batch had no usable turns."
            )

        selected_turns = [turn for turn in turns if turn.id in set(selected_turn_ids)]
        presented_prompt_fragments_by_turn = {
            turn.id: [
                {
                    "message_id": str(item["message_id"]),
                    "content": str(item["content"]),
                }
                for item in _presented_prompt_fragments(
                    turn,
                    (message_ids_by_turn.get(turn.id) or {}).get("user_prompt")
                    or [f"user:{turn.id}"],
                    prompt_fragments_by_turn.get(turn.id),
                    limit=message_char_limit,
                )
            ]
            for turn in selected_turns
        }
        source_segment = await db.get(Segment, job.segment_id) if job.segment_id else None
        project = await db.get(Project, job.project_id)
        # Final steering often contains the decisive correction. Preserve both
        # ends instead of dropping the tail when the sleep query is bounded.
        query = _head_tail("\n".join(turn.user_prompt for turn in selected_turns), 8_000)
        related = (
            await _related_memories(db, embeddings, job.project_id, query, job) if query else []
        )
        candidate_artifacts = list(
            {
                artifact.id: artifact
                for turn_id in selected_turn_ids
                for artifact in artifacts_by_turn.get(turn_id, [])
            }.values()
        )
        await db.commit()

        consolidation_payload = {
            "current_time": utcnow().isoformat(),
            "conversation_turns": serialized_turns,
            "segment_handoff": source_segment.summary if source_segment else "",
            "project_profile": {
                "cause": project.cause if project else "",
                "objectives": project.objectives if project else [],
                "principles": project.principles if project else [],
                "context": project.context if project else "",
            },
            "related_nodes": [_serialize_related_memory(memory) for memory in related],
            "candidate_artifacts": [
                _serialize_artifact_for_sleep(artifact, full=index < 8)
                for index, artifact in enumerate(candidate_artifacts)
            ],
        }
        generation_started = perf_counter()
        generation: SleepGeneration | None = None
        try:
            generation = await _generate_sleep(
                provider,
                provider_name=job.executor_preference,
                task="memory_consolidation",
                instructions=MEMORY_CONSOLIDATION_INSTRUCTIONS,
                payload=consolidation_payload,
                schema=MemoryConsolidationPayload.model_json_schema(),
                project_id=job.project_id,
            )
            # The bridge chooses before the call and returns the executor that
            # actually produced this generation.  Do not rewrite the stored
            # preference: future jobs should still try the project's preferred
            # subscription first.
            job.provider = generation.provider
            consolidation = MemoryConsolidationPayload.model_validate(generation.output)
        except Exception as exc:
            if isinstance(exc, SleepGenerationError):
                generation = exc.generation
                # Keep operational health and the failed-attempt event honest
                # about the provider that was actually selected by the bridge.
                job.provider = generation.provider
            await _record_sleep_generation(
                db,
                job=job,
                operation="sleep.memory_consolidation",
                generation=generation,
                status="failed",
                duration_ms=round((perf_counter() - generation_started) * 1000),
            )
            await db.commit()
            raise
        await _record_sleep_generation(
            db,
            job=job,
            operation="sleep.memory_consolidation",
            generation=generation,
            status="success",
            duration_ms=round((perf_counter() - generation_started) * 1000),
        )
        await db.commit()
        allowed_turn_ids = set(selected_turn_ids)
        related_ids = {memory.id for memory in related}
        related_by_id = {memory.id: memory for memory in related}
        allowed_artifact_ids = {artifact.id for artifact in candidate_artifacts} | {
            artifact_id for memory in related for artifact_id in memory.source_artifact_ids
        }
        turn_by_id = {turn["turn_id"]: turn for turn in serialized_turns}
        planned: list[dict] = []
        seen_topics: set[str] = set()
        action_count = 0
        language_warnings = 0
        for topic in consolidation.topics:
            if topic.topic_id in seen_topics:
                raise SleepProviderError(
                    "invalid_model_output", "The memory model returned a duplicate topic."
                )
            seen_topics.add(topic.topic_id)
            source_turn_ids = list(dict.fromkeys(topic.source_turn_ids))
            if not source_turn_ids or not set(source_turn_ids).issubset(allowed_turn_ids):
                raise SleepProviderError(
                    "invalid_model_output", "The memory model referenced a turn outside its batch."
                )
            topic_message_ids = {
                message_id
                for turn_id in source_turn_ids
                for message in (
                    turn_by_id[turn_id]["user_message"],
                    turn_by_id[turn_id]["assistant_message"],
                )
                for message_id in _serialized_message_ids(message)
            }
            source_message_ids = list(
                dict.fromkeys(
                    item for item in topic.source_message_ids if item in topic_message_ids
                )
            ) or sorted(topic_message_ids)
            source_artifact_ids = {
                artifact.id
                for turn_id in source_turn_ids
                for artifact in artifacts_by_turn.get(turn_id, [])
            }
            language_hint = _topic_memory_language(
                topic, turn_by_id, presented_prompt_fragments_by_turn
            )
            topic_language = _resolved_topic_language(language_hint, topic, related_by_id)
            # Language is a preference, not a reason to discard valid knowledge
            # or spend another model call. Keep provenance/structure checks strict.
            language_warning = (
                topic.language != topic_language
                or _generated_language_differs(topic.label, topic_language)
                or _generated_language_differs(topic.query_text, topic_language)
            )
            actions = []
            for action in topic.memory_actions:
                if not set(action.source_node_ids).issubset(related_ids):
                    raise SleepProviderError(
                        "invalid_model_output", "The memory model referenced an unknown memory."
                    )
                if not set(action.artifact_ids).issubset(allowed_artifact_ids):
                    raise SleepProviderError(
                        "invalid_model_output", "The memory model referenced an unknown artifact."
                    )
                action_count += 1
                if action_count > 100:
                    raise SleepProviderError(
                        "invalid_model_output", "The memory model returned too many actions."
                    )
                sanitized_action = action.model_copy(
                    update={
                        "source_message_ids": list(
                            dict.fromkeys(
                                item
                                for item in action.source_message_ids
                                if item in topic_message_ids
                            )
                        )
                        or sorted(topic_message_ids)
                    }
                )
                if target := _source_action_target(sanitized_action, related_by_id):
                    sanitized_action = sanitized_action.model_copy(
                        update={
                            "target_node_type": target.node_type,
                            "target_node_key": target.node_key,
                        }
                    )
                preferred_language = _resolved_action_language(
                    language_hint, sanitized_action, related_by_id
                )
                action_language = _stored_action_language(
                    sanitized_action, topic.language, related_by_id
                )
                if sanitized_action.action in {"create", "replace_current"}:
                    language_warning |= (
                        action_language != preferred_language
                        or _generated_language_differs(sanitized_action.text, topic.language)
                    )
                actions.append((sanitized_action, action_language))
            language_warnings += int(language_warning)
            planned.append(
                {
                    "topic": topic,
                    "source_turn_ids": source_turn_ids,
                    "source_message_ids": source_message_ids,
                    "source_artifact_ids": sorted(source_artifact_ids),
                    "actions": actions,
                }
            )

        db.expire_all()
        writable_project = await db.scalar(
            select(Project).where(Project.id == job_project_id).with_for_update()
        )
        if writable_project is None or not project_authority_writable(writable_project):
            await db.rollback()
            return True
        current = await db.scalar(select(SleepJob).where(SleepJob.id == job_id).with_for_update())
        if not current or current.status != "running":
            raise SleepProviderError(
                "bridge_unavailable", "The memory batch was interrupted before it could finish."
            )
        totals = {
            "topics": 0, "created": 0, "replaced": 0, "status_updated": 0, "skipped": 0,
            "language_warnings": language_warnings,
        }
        for item in planned:
            serialized_actions = []
            for action, memory_language in item["actions"]:
                _, outcome = await apply_sleep_action(
                    db,
                    job=current,
                    action=action,
                    source_turn_ids=item["source_turn_ids"],
                    source_artifact_ids=item["source_artifact_ids"],
                    memory_language=memory_language,
                )
                totals[outcome] += 1
                serialized_actions.append(action.model_dump())
            topic = item["topic"]
            db.add(
                SleepTopic(
                    project_id=current.project_id,
                    job_id=current.id,
                    topic_id=topic.topic_id,
                    label=topic.label,
                    query_text=topic.query_text,
                    source_turn_ids=item["source_turn_ids"],
                    source_message_ids=item["source_message_ids"],
                    related_memory_ids=sorted(related_ids),
                    planned_actions=serialized_actions,
                )
            )
            totals["topics"] += 1
        processed_turns = list(
            (
                await db.scalars(
                    select(Turn).where(Turn.id.in_(selected_turn_ids)).with_for_update()
                )
            ).all()
        )
        for turn in processed_turns:
            turn.sleep_status = "consolidated"
        segment_ids = {turn.segment_id for turn in processed_turns if turn.segment_id}
        if current.segment_id:
            segment_ids.add(current.segment_id)
        for segment_id in segment_ids:
            segment = await db.get(Segment, segment_id)
            if segment:
                remaining = await db.scalar(
                    select(Turn.id)
                    .where(
                        Turn.segment_id == segment_id,
                        Turn.sleep_status.in_(["open", "pending"]),
                    )
                    .limit(1)
                )
                segment.sleep_status = "pending" if remaining else "consolidated"
        current.input_turn_ids = selected_turn_ids
        current.status = "completed"
        current.completed_at = utcnow()
        current.last_error = None
        current.error_kind = None
        current.retry_at = None
        current.result = {
            **totals,
            "source_client": current.source_client,
            "executor_preference": current.executor_preference,
            "executor_provider": current.provider,
        }
        active_project = await db.get(Project, current.project_id)
        if active_project:
            active_project.backup_dirty = True
            active_project.backup_generation += 1
            active_project.updated_at = utcnow()
        db.add(
            Activity(
                project_id=current.project_id,
                kind="sleep.completed",
                summary=f"Memory consolidated with {current.provider}",
                detail={
                    "job_id": current.id,
                    "source_client": current.source_client,
                    "executor_preference": current.executor_preference,
                    "executor_provider": current.provider,
                    **totals,
                },
                actor="system",
                actor_member_id=current.actor_member_id,
            )
        )
        await db.commit()
    except SleepProviderError as exc:
        writable_project = await db.scalar(
            select(Project).where(Project.id == job_project_id).with_for_update()
        )
        if writable_project is None or not project_authority_writable(writable_project):
            await db.rollback()
            return True
        await _mark_sleep_waiting(db, job_id, exc)
    except Exception:
        writable_project = await db.scalar(
            select(Project).where(Project.id == job_project_id).with_for_update()
        )
        if writable_project is None or not project_authority_writable(writable_project):
            await db.rollback()
            return True
        await _mark_sleep_waiting(
            db,
            job_id,
            SleepProviderError(
                "invalid_model_output",
                "Memory consolidation will retry after an internal validation error.",
            ),
        )
    return True


async def recover_interrupted_jobs(db: AsyncSession) -> int:
    """Return interrupted work to the retry queue without losing the original input batch."""
    jobs = list(
        (
            await db.scalars(
                select(SleepJob)
                .join(Project, Project.id == SleepJob.project_id)
                .where(
                    SleepJob.status == "running",
                    project_authority_predicate(),
                )
            )
        ).all()
    )
    now = utcnow()
    for job in jobs:
        job.status = "waiting"
        job.last_error = "Local memory consolidation was interrupted and will retry."
        job.error_kind = "bridge_unavailable"
        job.retry_at = now
        job.not_before = now
    if jobs:
        await db.commit()
    return len(jobs)
