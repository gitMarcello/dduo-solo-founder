from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from dduo_solo_founder.models import (
    Activity,
    Artifact,
    Memory,
    OutboxEvent,
    RawEvent,
    SleepJob,
    Turn,
)
from dduo_solo_founder.schemas import MemoryActionPayload, MemoryLanguage

_KEY_PARTS = re.compile(r"[^a-z0-9:_./-]+")
_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]+`")
_URL = re.compile(r"https?://\S+")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_ENGLISH_IMPERATIVES = frozenset(
    {
        "add",
        "build",
        "change",
        "check",
        "create",
        "explain",
        "fix",
        "implement",
        "make",
        "remove",
        "show",
        "update",
    }
)
_ITALIAN_IMPERATIVES = frozenset(
    {
        "aggiungi",
        "cambia",
        "controlla",
        "correggi",
        "crea",
        "costruisci",
        "fammi",
        "implementa",
        "mostra",
        "rimuovi",
        "sistema",
        "spiega",
    }
)
_ENGLISH_MARKERS = frozenset(
    {
        "after",
        "always",
        "and",
        "answer",
        "are",
        "as",
        "before",
        "cannot",
        "existing",
        "been",
        "be",
        "for",
        "from",
        "keep",
        "is",
        "it",
        "must",
        "need",
        "never",
        "not",
        "only",
        "please",
        "prepare",
        "preserve",
        "remember",
        "reply",
        "respond",
        "safely",
        "save",
        "should",
        "store",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "use",
        "want",
        "was",
        "we",
        "when",
        "were",
        "will",
        "with",
        "would",
        "write",
        "you",
        "your",
    }
)
_ITALIAN_MARKERS = frozenset(
    {
        "aggiungi",
        "alla",
        "anche",
        "con",
        "dalla",
        "dalle",
        "deve",
        "devono",
        "devo",
        "dopo",
        "gli",
        "il",
        "italiano",
        "la",
        "le",
        "mai",
        "mantieni",
        "memorizza",
        "nella",
        "nelle",
        "non",
        "perché",
        "posso",
        "prima",
        "può",
        "questa",
        "queste",
        "questi",
        "questo",
        "ricorda",
        "rispondi",
        "salva",
        "sempre",
        "sicurezza",
        "sono",
        "scrivi",
        "un",
        "una",
        "usa",
        "utente",
        "vero",
        "voglio",
        "vorrei",
    }
)
MEMORY_LANGUAGE_POLICY_VERSION = "topic-user-language-v2"
ACTIONABLE_TYPES = {"episode", "reusable_fact", "heuristic"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_node_key(value: str) -> str:
    """Return a stable, model-friendly key without introducing a taxonomy."""
    normalized = _KEY_PARTS.sub("-", value.strip().lower()).strip("-:./")
    if not normalized:
        raise ValueError("memory node_key is empty after normalization")
    return normalized[:200]


def infer_text_language(value: str) -> MemoryLanguage | None:
    """Return a language only when dependency-free lexical evidence is unambiguous."""
    cleaned = _URL.sub(" ", _INLINE_CODE.sub(" ", _CODE_BLOCK.sub(" ", value))).casefold()
    tokens = _WORD.findall(cleaned.replace("'", " ").replace("’", " "))
    words = set(tokens)
    if not words:
        return None
    english_markers = words & _ENGLISH_MARKERS
    italian_markers = words & _ITALIAN_MARKERS
    english = len(english_markers) + sum(
        word.endswith(("ing", "ed", "ly")) and len(word) > 5
        for word in words - english_markers
    )
    italian = len(italian_markers) + sum(
        word.endswith(("zione", "zioni", "mente")) and len(word) > 6
        for word in words - italian_markers
    )
    if re.search(r"[àèéìòù]", cleaned):
        italian += 1
    if tokens and tokens[0] in _ENGLISH_IMPERATIVES and italian == 0:
        return "en"
    if tokens and tokens[0] in _ITALIAN_IMPERATIVES and english == 0:
        return "it"
    # Real bilingual prose is deliberately inconclusive. Technical English nouns in
    # Italian prose normally carry no marker and therefore do not trigger this branch.
    if english >= 2 and italian >= 2:
        return None
    if english >= 2 and (italian == 0 or english >= italian + 3):
        return "en"
    if italian >= 2 and (english == 0 or italian >= english + 2):
        return "it"
    return None


def existing_memory_language(memory: Memory) -> MemoryLanguage:
    """Read future language metadata, conservatively inferring legacy text without backfill."""
    stored_language = (memory.metadata_json or {}).get("language")
    if stored_language in {"it", "en"}:
        return stored_language
    return infer_text_language(memory.text) or "it"


def enqueue_memory_index(
    db: AsyncSession,
    memory: Memory,
    *,
    actor_member_id: str | None = None,
) -> None:
    if actor_member_id is None:
        from dduo_solo_founder.team import request_principal

        principal = request_principal()
        actor_member_id = principal.member_id if principal and not principal.anonymous else None
    payload = {
        "text": memory.text,
        "node_type": memory.node_type,
        "node_key": memory.node_key,
        "status": memory.status,
        "revision": memory.revision,
        "memory_group_id": memory.memory_group_id,
    }
    if actor_member_id:
        payload["actor_member_id"] = actor_member_id
    db.add(
        OutboxEvent(
            project_id=memory.project_id,
            event_type="memory.upsert",
            aggregate_id=memory.id,
            payload=payload,
        )
    )


async def _current_by_key(
    db: AsyncSession, project_id: str, node_type: str, node_key: str
) -> Memory | None:
    return await db.scalar(
        select(Memory)
        .where(
            Memory.project_id == project_id,
            Memory.node_type == node_type,
            Memory.node_key == node_key,
            Memory.status == "active",
        )
        .order_by(desc(Memory.revision))
        .with_for_update()
        .limit(1)
    )


async def _target_for_action(
    db: AsyncSession, project_id: str, action: MemoryActionPayload
) -> Memory | None:
    if action.action in {"replace_current", "set_status"}:
        for source_node_id in action.source_node_ids:
            source = await db.scalar(
                select(Memory)
                .where(Memory.id == source_node_id, Memory.project_id == project_id)
                .with_for_update()
                .limit(1)
            )
            if not source:
                continue
            if source.status == "active":
                return source
            current = await db.scalar(
                select(Memory)
                .where(
                    Memory.project_id == project_id,
                    Memory.memory_group_id == source.memory_group_id,
                    Memory.status == "active",
                )
                .order_by(desc(Memory.revision))
                .with_for_update()
                .limit(1)
            )
            if current:
                return current
    return await _current_by_key(
        db,
        project_id,
        action.target_node_type,
        normalize_node_key(action.target_node_key),
    )


async def apply_sleep_action(
    db: AsyncSession,
    *,
    job: SleepJob,
    action: MemoryActionPayload,
    source_turn_ids: list[str],
    source_artifact_ids: list[str],
    memory_language: MemoryLanguage,
) -> tuple[Memory | None, str]:
    """Apply one validated model decision while preserving a complete revision chain."""
    target = await _target_for_action(db, job.project_id, action)
    node_key = target.node_key if target else normalize_node_key(action.target_node_key)
    now = utcnow()

    if action.action == "set_status":
        if not target:
            return None, "skipped"
        target.status = action.status
        target.valid_to = now
        target.updated_at = now
        target.metadata_json = {
            **(target.metadata_json or {}),
            "last_status_reason": action.metadata,
            "last_sleep_job_id": job.id,
        }
        enqueue_memory_index(db, target, actor_member_id=job.actor_member_id)
        db.add(
            Activity(
                project_id=job.project_id,
                kind="memory.status",
                summary=f"Memory set to {action.status}: {target.node_key}",
                detail={"memory_id": target.id, "job_id": job.id, "action": "set_status"},
                actor=f"sleep:{job.provider}",
                actor_member_id=job.actor_member_id,
            )
        )
        return target, "status_updated"

    effective_action = action.action
    if action.action == "create" and target:
        # A model can miss a related node. The persistence invariant is stronger:
        # one current revision per semantic key.
        effective_action = "replace_current"
    if action.action == "replace_current" and not target:
        effective_action = "create_missing_target"

    if target:
        target.status = "superseded"
        target.valid_to = now
        target.updated_at = now

    memory = Memory(
        project_id=job.project_id,
        node_type=target.node_type if target else action.target_node_type,
        node_key=node_key,
        text=action.text.strip(),
        status="active" if action.status == "superseded" else action.status,
        memory_group_id=target.memory_group_id if target else None,
        revision=(target.revision + 1 if target else 1),
        supersedes_id=target.id if target else None,
        source_turn_ids=list(dict.fromkeys(source_turn_ids)),
        source_message_ids=list(dict.fromkeys(action.source_message_ids)),
        source_node_ids=list(dict.fromkeys(action.source_node_ids)),
        source_artifact_ids=list(
            dict.fromkeys(
                [
                    *(target.source_artifact_ids if target else []),
                    *source_artifact_ids,
                    *action.artifact_ids,
                ]
            )
        ),
        sleep_job_id=job.id,
        valid_from=now,
        metadata_json={
            **action.metadata,
            "model_action": action.action,
            "effective_action": effective_action,
            "provider": job.provider,
            "trigger": job.trigger,
            "language": memory_language,
            "language_policy_version": MEMORY_LANGUAGE_POLICY_VERSION,
        },
    )
    # SQLAlchemy defaults are applied at flush time; explicitly preserve a new
    # group id when this is the first revision.
    if target is None:
        from dduo_solo_founder.models import uid

        memory.memory_group_id = uid()
    db.add(memory)
    await db.flush()
    if target:
        target.superseded_by_id = memory.id
        enqueue_memory_index(db, target, actor_member_id=job.actor_member_id)
    enqueue_memory_index(db, memory, actor_member_id=job.actor_member_id)
    db.add(
        Activity(
            project_id=job.project_id,
            kind="memory.replaced" if target else "memory.created",
            summary=f"Memory {'revised' if target else 'created'}: {memory.node_key}",
            detail={
                "memory_id": memory.id,
                "memory_group_id": memory.memory_group_id,
                "revision": memory.revision,
                "job_id": job.id,
                "model_action": action.action,
                "effective_action": effective_action,
            },
            actor=f"sleep:{job.provider}",
            actor_member_id=job.actor_member_id,
        )
    )
    return memory, "replaced" if target else "created"


async def explain_memory(db: AsyncSession, project_id: str, memory_id: str) -> dict:
    memory = await db.get(Memory, memory_id)
    if not memory or memory.project_id != project_id:
        raise LookupError("memory not found")
    revisions = list(
        (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.project_id == project_id,
                    Memory.memory_group_id == memory.memory_group_id,
                )
                .order_by(Memory.revision)
            )
        ).all()
    )
    turn_ids = list(
        dict.fromkeys(item for revision in revisions for item in revision.source_turn_ids)
    )
    turns = (
        list((await db.scalars(select(Turn).where(Turn.id.in_(turn_ids)))).all())
        if turn_ids
        else []
    )
    message_ids = list(
        dict.fromkeys(item for revision in revisions for item in revision.source_message_ids)
    )
    messages = (
        list((await db.scalars(select(RawEvent).where(RawEvent.id.in_(message_ids)))).all())
        if message_ids
        else []
    )
    artifact_ids = list(
        dict.fromkeys(item for revision in revisions for item in revision.source_artifact_ids)
    )
    artifacts = (
        list((await db.scalars(select(Artifact).where(Artifact.id.in_(artifact_ids)))).all())
        if artifact_ids
        else []
    )
    return {
        "memory": serialize_memory(memory),
        "revisions": [serialize_memory(item) for item in revisions],
        "sources": [
            {
                "turn_id": turn.id,
                "session_id": turn.session_id,
                "user_prompt": turn.user_prompt,
                "assistant_response": turn.assistant_response,
                "created_at": turn.created_at,
                "off_record": turn.off_record,
            }
            for turn in turns
        ],
        "source_messages": [
            {
                "message_id": message.id,
                "turn_id": message.turn_id,
                "event_type": message.event_type,
                "payload": message.payload,
                "created_at": message.created_at,
            }
            for message in messages
        ],
        "source_artifacts": [
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind,
                "filename": artifact.filename,
                "mime_type": artifact.mime_type,
                "source_uri": artifact.source_uri,
                "summary": artifact.summary,
                "content_hash": artifact.content_hash,
                "created_at": artifact.created_at,
            }
            for artifact in artifacts
        ],
    }


async def forget_memory(
    db: AsyncSession, project_id: str, memory_id: str, rationale: str
) -> list[Memory]:
    memory = await db.get(Memory, memory_id)
    if not memory or memory.project_id != project_id:
        raise LookupError("memory not found")
    rows = list(
        (
            await db.scalars(
                select(Memory).where(
                    Memory.project_id == project_id,
                    Memory.memory_group_id == memory.memory_group_id,
                )
            )
        ).all()
    )
    now = utcnow()
    for row in rows:
        row.status = "inactive"
        row.valid_to = row.valid_to or now
        row.updated_at = now
        row.metadata_json = {**(row.metadata_json or {}), "forgotten_reason": rationale}
        enqueue_memory_index(db, row)
    return rows


def serialize_memory(memory: Memory) -> dict:
    return {
        "id": memory.id,
        "project_id": memory.project_id,
        "node_type": memory.node_type,
        "node_key": memory.node_key,
        "text": memory.text,
        "status": memory.status,
        "memory_group_id": memory.memory_group_id,
        "revision": memory.revision,
        "supersedes_id": memory.supersedes_id,
        "superseded_by_id": memory.superseded_by_id,
        "source_turn_ids": memory.source_turn_ids,
        "source_message_ids": memory.source_message_ids,
        "source_node_ids": memory.source_node_ids,
        "source_artifact_ids": memory.source_artifact_ids,
        "sleep_job_id": memory.sleep_job_id,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "metadata": memory.metadata_json,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }
