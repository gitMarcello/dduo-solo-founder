from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.config import get_settings
from dduo_solo_founder.embeddings import (
    EmbeddingCall,
    EmbeddingService,
    VectorOperationError,
    provider_request_count,
)
from dduo_solo_founder.models import Memory, RetrievalRun, Turn
from dduo_solo_founder.memory_engine import ACTIONABLE_TYPES, serialize_memory
from dduo_solo_founder.observability import Observation, embedding_price, record_many
from dduo_solo_founder.team import lock_writable_project


VECTOR_CANDIDATE_LIMIT = 48
SEMANTIC_QUERY_CHAR_LIMIT = 12_000
ELLIPTICAL_PHRASES = frozenset(
    {
        "procedi",
        "continua",
        "prosegui",
        "vai avanti",
        "fallo",
        "fai pure",
        "come prima",
        "proceed",
        "continue",
        "go ahead",
        "do it",
        "keep going",
        "carry on",
        "same as before",
    }
)
ELLIPTICAL_FILLERS = frozenset(
    {
        "adesso",
        "allora",
        "bene",
        "grazie",
        "now",
        "ok",
        "okay",
        "per",
        "please",
        "pure",
        "si",
        "sì",
        "yes",
    }
)
ELLIPTICAL_ACTIONS = frozenset(
    {
        "continua",
        "continue",
        "fallo",
        "procedi",
        "proceed",
        "prosegui",
    }
)


@dataclass(slots=True)
class SearchOutcome:
    items: list[dict]
    embedding: EmbeddingCall | None
    vector_store_duration_ms: int | None
    duration_ms: int
    error: Exception | None = None


@dataclass(slots=True)
class CandidateSelection:
    memories: list[Memory]
    scores: dict[str, float]
    reasons: dict[str, str]


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def bounded_semantic_query(value: str, limit: int = SEMANTIC_QUERY_CHAR_LIMIT) -> str:
    """Bound provider input while preserving both the original ask and latest steering."""
    if len(value) <= limit:
        return value
    marker = "\n\n[… semantic query compacted …]\n\n"
    available = max(limit - len(marker), 2)
    head = min(available // 4, 3_000)
    tail = available - head
    return f"{value[:head]}{marker}{value[-tail:]}"


def is_elliptical_prompt(prompt: str) -> bool:
    """Recognize short Italian/English continuations without classifying all short prompts."""
    normalized = _normalized_words(prompt)
    if not normalized:
        return True
    if normalized in ELLIPTICAL_PHRASES:
        return True
    meaningful = [token for token in normalized.split() if token not in ELLIPTICAL_FILLERS]
    reduced = " ".join(meaningful)
    return reduced in ELLIPTICAL_PHRASES or (
        len(meaningful) <= 2 and bool(meaningful) and meaningful[0] in ELLIPTICAL_ACTIONS
    )


def _has_exact_reference(prompt: str, memory: Memory) -> bool:
    """Match stable memory identifiers, never fuzzy natural-language fragments."""
    normalized_prompt = f" {_normalized_words(prompt)} "
    for value in (memory.id, memory.node_key):
        normalized_value = _normalized_words(str(value or ""))
        if normalized_value and f" {normalized_value} " in normalized_prompt:
            return True
    return False


def _score_map(items: list[dict]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in items:
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        score = float(item.get("score") or 0)
        scores[memory_id] = max(scores.get(memory_id, float("-inf")), score)
    return scores


async def _select_candidates(
    db: AsyncSession,
    *,
    project_id: str,
    prompt: str,
    items: list[dict],
    threshold: float,
    default_reason: str,
) -> CandidateSelection:
    scores = _score_map(items)
    candidate_ids = list(scores)
    rows = (
        list(
            (
                await db.scalars(
                    select(Memory).where(
                        Memory.project_id == project_id,
                        Memory.id.in_(candidate_ids),
                        Memory.status == "active",
                        Memory.node_type.in_(ACTIONABLE_TYPES),
                    )
                )
            ).all()
        )
        if candidate_ids
        else []
    )

    # An active duplicate should not normally exist, but PostgreSQL remains
    # authoritative: retain its latest revision instead of whichever Qdrant hit
    # happens to rank first.
    latest_by_key: dict[tuple[str, str], Memory] = {}
    for memory in rows:
        identity = (memory.node_type, memory.node_key)
        previous = latest_by_key.get(identity)
        if previous is None or (memory.revision, memory.id) > (
            previous.revision,
            previous.id,
        ):
            latest_by_key[identity] = memory

    qualified: list[Memory] = []
    reasons: dict[str, str] = {}
    for memory in latest_by_key.values():
        exact_reference = _has_exact_reference(prompt, memory)
        if scores[memory.id] < threshold and not exact_reference:
            continue
        qualified.append(memory)
        reasons[memory.id] = "exact_reference" if exact_reference else default_reason

    qualified.sort(
        key=lambda memory: (
            reasons[memory.id] != "exact_reference",
            -scores[memory.id],
            -memory.revision,
            memory.node_type,
            memory.node_key,
            memory.id,
        )
    )
    return CandidateSelection(
        memories=qualified,
        scores={memory.id: scores[memory.id] for memory in qualified},
        reasons={memory.id: reasons[memory.id] for memory in qualified},
    )


def _selection_metadata(
    *,
    selected: list[Memory],
    scores: dict[str, float],
    reasons: dict[str, str],
    run: RetrievalRun,
    selection_mode: str,
) -> dict:
    bucket_counts: dict[str, int] = {}
    for memory in selected:
        bucket_counts[memory.node_type] = bucket_counts.get(memory.node_type, 0) + 1
    return {
        "selected_memory_ids": [memory.id for memory in selected],
        "selected_scores": scores,
        "selected_reasons": reasons,
        "selection_mode": selection_mode,
        "dropped_memory_ids": run.dropped_memory_ids,
        "degraded_reasons": run.degraded_reasons,
        "threshold": run.threshold,
        "bucket_counts": bucket_counts,
    }


def _observed_search(
    embeddings: EmbeddingService, project_id: str, query: str, limit: int
) -> SearchOutcome:
    started = perf_counter()
    try:
        observed = getattr(embeddings, "search_observed", None)
        if callable(observed):
            result = observed(project_id, query, limit)
            return SearchOutcome(
                items=result.items,
                embedding=result.embedding,
                vector_store_duration_ms=result.vector_store_duration_ms,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        return SearchOutcome(
            items=embeddings.search(project_id, query, limit),
            embedding=None,
            vector_store_duration_ms=None,
            duration_ms=round((perf_counter() - started) * 1000),
        )
    except VectorOperationError as exc:
        return SearchOutcome(
            items=[],
            embedding=exc.embedding,
            vector_store_duration_ms=exc.vector_store_duration_ms,
            duration_ms=round((perf_counter() - started) * 1000),
            error=exc,
        )
    except Exception as exc:
        return SearchOutcome(
            items=[],
            embedding=None,
            vector_store_duration_ms=None,
            duration_ms=round((perf_counter() - started) * 1000),
            error=exc,
        )


def _embedding_observation(
    *,
    project_id: str,
    run_id: str,
    operation: str,
    outcome: SearchOutcome,
    session_id: str,
    turn_id: str | None,
    limit: int,
) -> Observation:
    embedding = outcome.embedding
    usage = embedding.usage if embedding else None
    cost, unit_price, pricing_version = embedding_price(
        usage.model if usage else None, usage.input_tokens if usage else None
    )
    return Observation(
        project_id=project_id,
        idempotency_key=f"retrieval:{run_id}:{operation}",
        category="embedding",
        operation=operation,
        status=(
            "success"
            if outcome.error is None
            else "partial_failure"
            if embedding is not None
            else "failed"
        ),
        provider=usage.provider if usage else getattr(outcome.error, "provider", None),
        model=usage.model if usage else getattr(outcome.error, "model", None),
        measurement_source=usage.measurement_source if usage else "unavailable",
        session_id=session_id,
        turn_id=turn_id,
        retrieval_run_id=run_id,
        input_tokens=usage.input_tokens if usage else None,
        reported_total_tokens=usage.reported_total_tokens if usage else None,
        duration_ms=outcome.duration_ms,
        provider_duration_ms=(
            embedding.provider_duration_ms
            if embedding
            else getattr(outcome.error, "provider_duration_ms", None)
        ),
        vector_store_duration_ms=outcome.vector_store_duration_ms,
        request_count=provider_request_count(embedding, outcome.error),
        item_count=len(outcome.items),
        candidate_count=len(outcome.items),
        cost_usd=cost,
        unit_price_usd_per_million=unit_price,
        pricing_version=pricing_version,
        details={"limit": limit, "error_code": "vector_operation_failed" if outcome.error else ""},
    )


async def build_retrieval_query(db: AsyncSession, *, session_id: str, current_prompt: str) -> str:
    recent = list(
        (
            await db.scalars(
                select(Turn)
                .where(
                    Turn.session_id == session_id,
                    Turn.committed.is_(True),
                    Turn.off_record.is_(False),
                )
                .order_by(desc(Turn.committed_at), desc(Turn.created_at))
                .limit(3)
            )
        ).all()
    )
    lines = [turn.user_prompt.strip() for turn in reversed(recent) if turn.user_prompt.strip()]
    lines.append(current_prompt.strip())
    return bounded_semantic_query("\n".join(lines))


async def retrieve_context(
    db: AsyncSession,
    embeddings: EmbeddingService,
    *,
    project_id: str,
    session_id: str,
    turn_id: str | None,
    current_prompt: str,
    requested_limit: int = 11,
    release_before_provider: bool = True,
) -> dict:
    """Retrieve every relevant memory, bounded later by render size rather than K."""
    started = perf_counter()
    settings = get_settings()
    # Keep the public argument compatible with older hooks and clients. It is no
    # longer a semantic selector: Qdrant's top-48 is only a technical candidate
    # window and the model-facing renderer owns the byte budget.
    legacy_requested_limit = requested_limit
    if release_before_provider:
        await db.commit()
    degraded: list[str] = []
    direct_outcome: SearchOutcome | None = None
    history_outcome: SearchOutcome | None = None
    preflight_failed = False
    ensure_collection = getattr(embeddings, "ensure_collection", None)
    try:
        if callable(ensure_collection):
            await asyncio.to_thread(ensure_collection, project_id)
    except Exception as exc:
        preflight_failed = True
        degraded.append(f"vector_search_unavailable:{exc}")
    else:
        direct_outcome = await asyncio.to_thread(
            _observed_search,
            embeddings,
            project_id,
            current_prompt,
            VECTOR_CANDIDATE_LIMIT,
        )
        if direct_outcome.error:
            degraded.append(f"vector_search_unavailable:{direct_outcome.error}")

    direct_selection = await _select_candidates(
        db,
        project_id=project_id,
        prompt=current_prompt,
        items=direct_outcome.items if direct_outcome else [],
        threshold=settings.retrieval_similarity_threshold,
        default_reason="direct",
    )
    selected_result = direct_selection
    selection_mode = "direct"
    selected_query = current_prompt

    direct_has_exact_reference = "exact_reference" in direct_selection.reasons.values()
    direct_search_succeeded = direct_outcome is not None and direct_outcome.error is None
    needs_history = direct_search_succeeded and (
        (is_elliptical_prompt(current_prompt) and not direct_has_exact_reference)
        or not direct_selection.memories
    )
    if needs_history and not preflight_failed:
        history_query = await build_retrieval_query(
            db, session_id=session_id, current_prompt=current_prompt
        )
        # A first-turn fallback would repeat the exact paid query and cannot add
        # evidence, so keep the direct result and its single observation.
        if history_query.strip() != current_prompt.strip():
            if release_before_provider:
                await db.commit()
            history_outcome = await asyncio.to_thread(
                _observed_search,
                embeddings,
                project_id,
                history_query,
                VECTOR_CANDIDATE_LIMIT,
            )
            if history_outcome.error:
                reason = f"vector_search_unavailable:{history_outcome.error}"
                if reason not in degraded:
                    degraded.append(reason)
            history_selection = await _select_candidates(
                db,
                project_id=project_id,
                prompt=current_prompt,
                items=history_outcome.items,
                threshold=settings.retrieval_similarity_threshold,
                default_reason="history_fallback",
            )
            if history_selection.memories:
                selected_result = history_selection
                selection_mode = "history_fallback"
                selected_query = history_query
            elif not direct_selection.memories:
                # Persist that the contextual fallback was the decisive empty
                # result, so replay can expose the same selection metadata.
                selected_result = history_selection
                selection_mode = "history_fallback"
                selected_query = history_query

    selected = selected_result.memories
    selected_scores = selected_result.scores
    selected_reasons = selected_result.reasons
    all_candidate_ids: list[str] = []
    for outcome in (direct_outcome, history_outcome):
        if outcome is None:
            continue
        for memory_id in _score_map(outcome.items):
            if memory_id not in all_candidate_ids:
                all_candidate_ids.append(memory_id)
    selected_ids = {memory.id for memory in selected}
    dropped = [memory_id for memory_id in all_candidate_ids if memory_id not in selected_ids]
    status = "degraded" if degraded else ("context_ready" if selected else "insufficient_context")
    # ``retrieve_context`` releases the request transaction before provider
    # latency.  A transfer may therefore freeze this project while the search
    # is running.  Reacquire the project row before persisting the retrieval so
    # the old node never commits a late result after the freeze boundary.
    await lock_writable_project(db, project_id)
    run = RetrievalRun(
        project_id=project_id,
        session_id=session_id,
        turn_id=turn_id,
        query_text=selected_query,
        latest_query_text=current_prompt,
        status=status,
        selected_memory_ids=[item.id for item in selected],
        selected_scores=selected_scores,
        dropped_memory_ids=list(dict.fromkeys(dropped)),
        degraded_reasons=degraded,
        threshold=settings.retrieval_similarity_threshold,
    )
    db.add(run)
    await db.flush()
    observations: list[Observation] = []
    if direct_outcome is not None:
        observations.append(
            _embedding_observation(
                project_id=project_id,
                run_id=run.id,
                operation="embedding.retrieval_latest",
                outcome=direct_outcome,
                session_id=session_id,
                turn_id=turn_id,
                limit=VECTOR_CANDIDATE_LIMIT,
            )
        )
    if history_outcome is not None:
        observations.append(
            _embedding_observation(
                project_id=project_id,
                run_id=run.id,
                operation="embedding.retrieval_history",
                outcome=history_outcome,
                session_id=session_id,
                turn_id=turn_id,
                limit=VECTOR_CANDIDATE_LIMIT,
            )
        )
    observations.append(
        Observation(
            project_id=project_id,
            idempotency_key=f"retrieval:{run.id}:pipeline",
            category="retrieval",
            operation="retrieval.pipeline",
            status="degraded" if degraded else "success",
            session_id=session_id,
            turn_id=turn_id,
            retrieval_run_id=run.id,
            measurement_source="unavailable",
            duration_ms=round((perf_counter() - started) * 1000),
            request_count=int(direct_outcome is not None) + int(history_outcome is not None),
            candidate_count=len(all_candidate_ids),
            selected_count=len(selected),
            dropped_count=len(run.dropped_memory_ids),
            details={
                "candidate_limit": VECTOR_CANDIDATE_LIMIT,
                "degraded_count": len(degraded),
                "history_fallback": selection_mode == "history_fallback",
                "preflight_failed": preflight_failed,
                "requested_limit_ignored": legacy_requested_limit,
            },
        )
    )
    await record_many(
        db,
        observations,
    )
    metadata = _selection_metadata(
        selected=selected,
        scores=selected_scores,
        reasons=selected_reasons,
        run=run,
        selection_mode=selection_mode,
    )
    return {
        "retrieval_run_id": run.id,
        "status": status,
        "query": selected_query,
        "items": [
            {**serialize_memory(item), "score": selected_scores[item.id]} for item in selected
        ],
        "metadata": metadata,
    }


async def replay_retrieval(db: AsyncSession, run_id: str) -> dict | None:
    """Replay the exact context selected for an idempotently repeated turn."""
    run = await db.get(RetrievalRun, run_id)
    if not run:
        return None
    rows = (
        list((await db.scalars(select(Memory).where(Memory.id.in_(run.selected_memory_ids)))).all())
        if run.selected_memory_ids
        else []
    )
    by_id = {item.id: item for item in rows}
    selected = [by_id[item] for item in run.selected_memory_ids if item in by_id]
    selection_mode = (
        "history_fallback" if run.query_text.strip() != run.latest_query_text.strip() else "direct"
    )
    reasons = {
        memory.id: (
            "exact_reference"
            if _has_exact_reference(run.latest_query_text, memory)
            else selection_mode
        )
        for memory in selected
    }
    scores = {memory.id: run.selected_scores.get(memory.id, 0) for memory in selected}
    return {
        "retrieval_run_id": run.id,
        "status": run.status,
        "query": run.query_text,
        "items": [
            {**serialize_memory(item), "score": run.selected_scores.get(item.id, 0)}
            for item in selected
        ],
        "metadata": {
            **_selection_metadata(
                selected=selected,
                scores=scores,
                reasons=reasons,
                run=run,
                selection_mode=selection_mode,
            ),
            "replayed": True,
        },
    }
