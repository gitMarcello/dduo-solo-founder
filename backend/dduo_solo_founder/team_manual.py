"""Versioned project operating-manual helpers with a deterministic fallback."""

from __future__ import annotations

import re
from collections.abc import Callable

from dduo_solo_founder.models import OperationalManual, Project
from dduo_solo_founder.sleep_engine import (
    CliSleepProvider,
    SleepGeneration,
    SleepGenerationError,
    SleepProviderError,
    sleep_executor_provider,
)


# The operational manual shares the 9k Founder Brief with the profile, active
# work, memories and delivery metadata.  Keeping both the warning and the
# compaction target at 4k leaves useful room for those other sources while a
# complete manual remains available through ``get_project_manual``.
MANUAL_SOFT_WARNING_CHARACTERS = 4_000
MANUAL_DRAFT_MAX_CHARACTERS = 4_000
MANUAL_HARD_LIMIT_CHARACTERS = 100_000
MANUAL_COMPACTION_INSTRUCTIONS = """
Create a compact operating manual for the project. Preserve every concrete invariant,
constraint, environment rule, safety rule, release gate, ownership boundary and recurring
procedure. Remove repetition and narrative history. Do not invent facts. Return one concise
Markdown document in `content`, using the language of the source manual and project profile.
""".strip()
MANUAL_COMPACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {"type": "string", "maxLength": MANUAL_DRAFT_MAX_CHARACTERS},
    },
    "required": ["content"],
}


def manual_warnings(content: str) -> list[str]:
    warnings: list[str] = []
    if not content.strip():
        warnings.append("manual_empty")
    if len(content) > MANUAL_SOFT_WARNING_CHARACTERS:
        warnings.append("manual_above_soft_limit")
    return warnings


def serialize_manual(manual: OperationalManual | None) -> dict:
    content = manual.content if manual else ""
    return {
        "content": content,
        "version": manual.version if manual else 0,
        "updated_by_member_id": manual.updated_by_member_id if manual else None,
        "updated_at": manual.updated_at if manual else None,
        "characters": len(content),
        "soft_limit_characters": MANUAL_SOFT_WARNING_CHARACTERS,
        "hard_limit_characters": MANUAL_HARD_LIMIT_CHARACTERS,
        "warnings": manual_warnings(content),
    }


def _truncate_at_boundary(value: str, limit: int = MANUAL_DRAFT_MAX_CHARACTERS) -> str:
    normalized = re.sub(r"[ \t]+", " ", value).strip()
    if len(normalized) <= limit:
        return normalized
    window = normalized[: limit - 1]
    boundary = max(window.rfind("\n"), window.rfind(". "), window.rfind(" "))
    if boundary < limit // 2:
        boundary = len(window)
    return f"{window[:boundary].rstrip()}…"


def deterministic_manual_draft(project: Project, content: str) -> str:
    if content.strip():
        return _truncate_at_boundary(content)
    sections = [f"# {project.name}"]
    if (project.cause or "").strip():
        sections.extend(["", "## Scopo", project.cause.strip()])
    if project.objectives:
        sections.extend(
            ["", "## Obiettivi", *[f"- {item}" for item in project.objectives if str(item).strip()]]
        )
    if project.principles:
        sections.extend(
            ["", "## Principi", *[f"- {item}" for item in project.principles if str(item).strip()]]
        )
    if (project.context or "").strip():
        sections.extend(["", "## Contesto operativo", project.context.strip()])
    return _truncate_at_boundary("\n".join(sections))


async def compact_manual_draft(
    project: Project,
    manual: OperationalManual | None,
    *,
    provider_factory: Callable[[], CliSleepProvider] = CliSleepProvider,
) -> dict:
    """Return a provider-authored draft, falling back without persisting either result."""
    content = manual.content if manual else ""
    warnings = manual_warnings(content)
    payload = {
        "project_id": project.id,
        "project_profile": {
            "name": project.name,
            "cause": project.cause or "",
            "objectives": project.objectives or [],
            "principles": project.principles or [],
            "context": project.context or "",
        },
        "current_manual": content,
        "limits": {
            "soft_warning_characters": MANUAL_SOFT_WARNING_CHARACTERS,
            "draft_max_characters": MANUAL_DRAFT_MAX_CHARACTERS,
        },
    }
    source = "provider"
    generation: SleepGeneration | None = None
    telemetry_status = "unavailable"
    try:
        provider = provider_factory()
        arguments = {
            "provider": sleep_executor_provider(),
            "project_id": project.id,
            "task": "operational_manual_compaction",
            "instructions": MANUAL_COMPACTION_INSTRUCTIONS,
            "payload": payload,
            "schema": MANUAL_COMPACTION_SCHEMA,
        }
        observed = getattr(provider, "generate_observed", None)
        if callable(observed):
            generation = await observed(**arguments)
            result = generation.output or {}
            telemetry_status = "success"
        else:
            result = await provider.generate(**arguments)
        draft = str(result.get("content") or "").strip()
        if not draft:
            if generation is not None:
                raise SleepGenerationError(
                    "manual provider returned an empty draft",
                    generation,
                    kind="invalid_model_output",
                )
            raise SleepProviderError(
                "invalid_model_output", "manual provider returned an empty draft"
            )
        if len(draft) > MANUAL_DRAFT_MAX_CHARACTERS:
            draft = _truncate_at_boundary(draft)
            warnings.append("draft_truncated")
    except SleepProviderError as exc:
        if isinstance(exc, SleepGenerationError):
            generation = exc.generation
            telemetry_status = "error"
        if exc.kind not in {"bridge_unavailable", "dependency_unavailable"}:
            raise
        source = "deterministic_fallback"
        warnings.append("provider_unavailable")
        draft = deterministic_manual_draft(project, content)
    result = {
        "draft": draft,
        "based_on_version": manual.version if manual else 0,
        "source": source,
        "characters": len(draft),
        "warnings": list(dict.fromkeys(warnings)),
        "persisted": False,
    }
    if generation is not None:
        # Private handoff consumed by the API before serialization. Keeping the
        # usage next to the provider result prevents this auxiliary model call
        # from becoming invisible to project observability.
        result["_telemetry"] = {
            "status": telemetry_status,
            "provider": generation.provider,
            "model": generation.model,
            "duration_ms": generation.duration_ms,
            "measurement_source": generation.measurement_source,
            "input_tokens": generation.input_tokens,
            "cached_input_tokens": generation.cached_input_tokens,
            "cache_write_input_tokens": generation.cache_write_input_tokens,
            "output_tokens": generation.output_tokens,
            "reasoning_tokens": generation.reasoning_tokens,
            "reported_total_tokens": generation.reported_total_tokens,
            "client_cost_usd": generation.client_cost_usd,
            "cost_source": generation.cost_source,
        }
    return result
