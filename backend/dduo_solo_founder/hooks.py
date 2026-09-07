"""Small, failure-tolerant lifecycle hooks for dDuo project memory."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from dduo_solo_founder import __version__
from dduo_solo_founder.client_binding import ProjectBinding, binding_from_project
from dduo_solo_founder.codex_telemetry import (
    capture_codex_transcript_cursor,
    read_codex_transcript_usage_result,
)
from dduo_solo_founder.client_telemetry import (
    MAX_PENDING_PER_PROJECT,
    ClientTelemetrySpool,
)
from dduo_solo_founder.client_http import ClientUpgradeRequired, ProjectHttpClient
from dduo_solo_founder.context_budget import (
    DEFAULT_CONTEXT_BUDGET,
    ComposedContext,
    ContextFragment,
    compose_context,
    compose_fallback_context,
    measure_fragment_sources,
)
from dduo_solo_founder.manual_cache import load_verified_manual, store_verified_manual
from dduo_solo_founder.observability import (
    ESTIMATOR_VERSION,
    estimated_tokens_for_bytes,
    estimated_tokens_for_text,
)
from dduo_solo_founder.operating_contract import COFOUNDER_CONTRACT, TASK_CONTRACT
from dduo_solo_founder.connection_health import sleep_connection_notice
from dduo_solo_founder.project_activation import setup_declined
from dduo_solo_founder.project_config import (
    dashboard_item_url,
    find_workspace_root,
    load_project,
    portable_file_lock,
)
HOOK_STATE_DIR = Path.home() / ".config" / "dduo-solo-founder" / "hook-state"
HOOK_CONTEXT_RENDER_VERSION = "hook-context-v6"
HOOK_FALLBACK_RENDER_VERSION = "hook-fallback-v3"
CONTEXT_DELIVERY_STATE_VERSION = 2

TURN_CONTRACT = (
    "Keep applying the standing Founder Brief already present in this live session. dDuo has recorded "
    "this turn and supplied only changed project state and relevant recall. Memory consolidation is "
    "asynchronous; do not create semantic memories yourself. Ordinary queued sleep does not block work; "
    "if a health notice requires user action, explain it and ask for the user's choice first. "
)
SESSION_REFRESH_CONTRACT = (
    "Keep applying the standing Founder Brief already present in this resumed session. This refresh "
    "contains only changed project state; a missing or untrusted baseline would have produced a full "
    "snapshot instead. Continue normally and do not treat omitted stable context as deleted. "
)


def _binding_for_project(
    root: Path,
    project: dict,
    *,
    require_approval: bool = True,
) -> ProjectBinding:
    """Resolve one hook binding and enforce local checkout ownership."""
    local = str(project.get("binding") or "local").strip().lower() == "local"
    return binding_from_project(
        root,
        project,
        require_approval=require_approval,
        enforce_project_claim=local,
    )


def is_claude_runtime() -> bool:
    return bool(os.getenv("CLAUDE_PLUGIN_ROOT")) and not bool(os.getenv("PLUGIN_ROOT"))


class HookEmissionError(RuntimeError):
    """The client hook envelope could not be written to stdout."""


def emit_no_context() -> None:
    """Explicit success without injection; empty stdout would mean a broken hook."""
    print('{"continue":true}', flush=True)


def read_input() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        value = json.load(sys.stdin)
    except (EOFError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def emit(context: str, event_name: str, *, warn: bool = False, **extra) -> None:
    if event_name not in {"SessionStart", "UserPromptSubmit"}:
        raise ValueError(f"unsupported context hook event: {event_name}")
    output = {
        **extra,
        "hookSpecificOutput": {"hookEventName": event_name, "additionalContext": context},
    }
    if warn:
        output["systemMessage"] = context
    try:
        print(json.dumps(output, ensure_ascii=False), flush=True)
    except Exception as error:
        # A failed stdout write is not a memory outage. In particular, never
        # record a baseline or a fallback delivery the client did not receive.
        raise HookEmissionError("could not write the native hook response") from error


def client_name(payload: dict) -> str:
    declared = payload.get("client")
    if declared in {"codex", "claude", "other"}:
        return declared
    if os.getenv("PLUGIN_ROOT"):
        return "codex"
    return "claude" if os.getenv("CLAUDE_PLUGIN_ROOT") else "codex"


def external_session_id(payload: dict) -> str:
    return str(
        payload.get("session_id")
        or payload.get("conversation_id")
        or payload.get("thread_id")
        or "default"
    )


def prompt_event_id(payload: dict, *, session_id: str, turn_id: str, prompt: str) -> str:
    """Identify one prompt delivery independently from its enclosing turn.

    Codex steering keeps the same turn id. Newer clients also supply a message
    id; otherwise one delivery gets a fresh identifier that its retry spool
    preserves verbatim.
    """
    declared = payload.get("prompt_event_id") or payload.get("message_id")
    if declared:
        value = str(declared)
        return (
            value if len(value) <= 500 else f"message-{hashlib.sha256(value.encode()).hexdigest()}"
        )
    # Without a client event identifier, identical steering text is ambiguous:
    # it may be a retry or a deliberate repeated instruction. Treat each hook
    # delivery as distinct, then persist this generated ID in the local spool so
    # offline replay remains idempotent.
    del session_id, turn_id, prompt
    return f"prompt-{uuid.uuid4()}"


def state_path(project_id: str, client: str, external_id: str) -> Path:
    # A readable prefix helps support, while the digest prevents two client IDs
    # that sanitize to the same filename from ever sharing an outbox.
    safe = "".join(character if character.isalnum() else "_" for character in external_id)[:80]
    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:20]
    return HOOK_STATE_DIR / f"{project_id}-{client}-{safe}-{digest}.json"


def project_root(payload: dict) -> Path:
    return find_workspace_root(
        Path(payload.get("cwd") or os.getenv("DDUO_SOLO_FOUNDER_PROJECT_ROOT") or Path.cwd())
    )


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


_STATE_LIST_IDENTITIES = {
    "pending_context_observations": lambda item: str(item.get("event_id") or ""),
    "pending_commits": lambda item: str(item.get("turn_id") or ""),
    "spooled_turns": lambda item: "|".join(
        str(item.get(key) or "")
        for key in ("session_external_id", "external_turn_id", "binding_id")
    ),
    "pending_codex_usage": lambda item: str(item.get("source_id") or ""),
}


def _state_turn_generation(state: dict) -> str | None:
    """Return the native turn generation represented by one state snapshot."""
    active = state.get("spooled_active")
    candidate = (
        state.get("external_turn_id")
        if state.get("turn_id")
        else active.get("external_turn_id")
        if isinstance(active, dict)
        else state.get("external_turn_id")
    )
    return candidate if isinstance(candidate, str) and candidate else None


def merge_state_update(path: Path, before: dict, after: dict) -> dict:
    """Apply one hook mutation without discarding concurrent queue additions.

    Hook processes are short-lived and may overlap. Atomic replace prevents a
    torn JSON file but not a lost update, so this performs a small optimistic
    merge under the same portable kernel lock used by other private ledgers.
    """
    with portable_file_lock(path, timeout=10):
        current = read_state(path)
        before_generation = _state_turn_generation(before)
        after_generation = _state_turn_generation(after)
        current_generation = _state_turn_generation(current)
        installs_new_generation = bool(
            after_generation
            and after_generation != before_generation
            and current_generation in {None, before_generation}
        )
        keys = set(before) | set(after)
        missing = object()
        for key in keys:
            old = before.get(key, missing)
            new = after.get(key, missing)
            if old == new:
                continue
            identify = _STATE_LIST_IDENTITIES.get(key)
            if (
                identify is not None
                and (old is missing or isinstance(old, list))
                and (new is missing or isinstance(new, list))
            ):
                old_items = [] if old is missing else old
                new_items = [] if new is missing else new
                current_items = current.get(key)
                current_items = current_items if isinstance(current_items, list) else []
                old_by_id = {
                    identify(item): item
                    for item in old_items
                    if isinstance(item, dict) and identify(item)
                }
                new_by_id = {
                    identify(item): item
                    for item in new_items
                    if isinstance(item, dict) and identify(item)
                }
                merged = [
                    item
                    for item in current_items
                    if not (
                        isinstance(item, dict)
                        and identify(item) in old_by_id
                        and identify(item) not in new_by_id
                    )
                ]
                positions = {
                    identify(item): index
                    for index, item in enumerate(merged)
                    if isinstance(item, dict) and identify(item)
                }
                for identity, item in new_by_id.items():
                    if old_by_id.get(identity) == item:
                        continue
                    if identity in positions:
                        merged[positions[identity]] = item
                    else:
                        positions[identity] = len(merged)
                        merged.append(item)
                if merged:
                    current[key] = merged
                else:
                    current.pop(key, None)
                continue
            if key == "spooled_active" and isinstance(new, dict):
                live = current.get(key)
                if isinstance(live, dict) and (
                    live.get("session_external_id") == new.get("session_external_id")
                    and live.get("external_turn_id") == new.get("external_turn_id")
                ):
                    merged_active = {**live, **new}
                    fragments = []
                    seen: set[str] = set()
                    for source in (live, new):
                        for fragment in _spooled_prompt_fragments(source):
                            identity = fragment["prompt_event_id"]
                            if identity not in seen:
                                seen.add(identity)
                                fragments.append(fragment)
                    merged_active["prompt_fragments"] = fragments
                    merged_active["user_prompt"] = "\n\n".join(
                        item["user_prompt"] for item in fragments
                    )
                    merged_active["off_record"] = bool(live.get("off_record")) or bool(
                        new.get("off_record")
                    )
                    current[key] = merged_active
                    continue
            current_value = current.get(key, missing)
            if current_value != old:
                if (
                    installs_new_generation
                    and current_value is missing
                    and key in {"turn_id", "codex_transcript_cursor"}
                    and new is not missing
                ):
                    # Stop A can remove these active-turn fields after Prompt B
                    # took its snapshot but before B installs its state. The
                    # unchanged A generation proves that no newer turn C won
                    # the race, so finish installing B instead of losing it.
                    current[key] = new
                # Another hook changed this scalar/nested value after our
                # snapshot. Preserve the newer value rather than overwriting it.
                continue
            if new is missing:
                current.pop(key, None)
            else:
                current[key] = new
        write_state(path, current)
        return current


def setup_notice_path(project_id: str, provider: str) -> Path:
    digest = hashlib.sha256(f"{project_id}:{provider}".encode("utf-8")).hexdigest()
    return HOOK_STATE_DIR / "setup-notices" / f"{digest}.json"


def request_setup_once(project_id: str, provider: str, root: Path) -> bool:
    """Open protected setup once until this provider's connection recovers."""
    path = setup_notice_path(project_id, provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    try:
        with path.open("x", encoding="utf-8") as claim:
            json.dump({"pending": True}, claim)
        path.chmod(0o600)
    except FileExistsError:
        return False
    import subprocess

    try:
        result = subprocess.run(
            ["dduo-solo-founder", "setup", "--project-root", str(root)],
            timeout=30,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        path.unlink(missing_ok=True)
        return False
    if getattr(result, "returncode", 0):
        path.unlink(missing_ok=True)
        return False
    return True


def clear_setup_notice(project_id: str, provider: str) -> None:
    """Allow one future prompt after a genuine new connection failure."""
    setup_notice_path(project_id, provider).unlink(missing_ok=True)


def setup_is_pending(project_id: str, provider: str) -> bool:
    """Keep the conversational handoff active until the provider really recovers."""
    return setup_notice_path(project_id, provider).exists()


def setup_handoff_notice(provider: str, *, opened: bool) -> str:
    """Give either client the same concise, verified setup handoff."""
    subject = "A local Setup page opened" if opened else "The local Setup page is still waiting"
    return (
        f"{subject} because {provider.title()} needs reconnection. Say in one short sentence that the "
        "founder should complete the required action there and reply 'fatto'; then call check_setup with this "
        "provider before saying memory has resumed. If it is incomplete, state only the next action, reopen "
        "Setup, and wait. "
    )


def _api(project: dict) -> str:
    return f"http://127.0.0.1:{project['api_port']}"


def _project_client(binding: ProjectBinding, component: str, session_id: str) -> ProjectHttpClient:
    return ProjectHttpClient(
        binding,
        component=component,
        session_id=session_id,
        request_function=_httpx_dispatch,
    )


def _httpx_dispatch(method: str, url: str, **kwargs):
    caller = getattr(httpx, method.lower(), httpx.request)
    if caller is httpx.request:
        return caller(method, url, **kwargs)
    return caller(url, **kwargs)


def _request(transport: str | ProjectHttpClient, method: str, path: str, **kwargs):
    if isinstance(transport, ProjectHttpClient):
        return transport.request(method, path, **kwargs)
    caller = getattr(httpx, method.lower(), httpx.request)
    if caller is httpx.request:
        return caller(method, transport.rstrip("/") + "/" + path.lstrip("/"), **kwargs)
    return caller(transport.rstrip("/") + "/" + path.lstrip("/"), **kwargs)


def flush_client_telemetry(
    transport: str | ProjectHttpClient,
    project_id: str,
) -> None:
    """Best-effort delivery; enforcement never depends on observability upload."""
    spool = ClientTelemetrySpool()
    try:
        items = spool.peek(project_id, limit=MAX_PENDING_PER_PROJECT)
        if not items:
            return
        response = _request(
            transport,
            "POST",
            f"/projects/{project_id}/observability/client-events/batch",
            json={"items": items},
            timeout=3,
        )
        response.raise_for_status()
        spool.acknowledge(project_id, {str(item["event_id"]) for item in items})
    except Exception:
        return


def enqueue_codex_turn_usage(
    payload: dict,
    state: dict,
    *,
    project_id: str,
    external_session: str,
) -> int:
    """Stage content-free, per-request Codex usage measured for this turn.

    Codex documents the transcript path but not its internal JSONL schema, so
    the isolated parser is deliberately fail-open. Missing or unfamiliar usage
    never delays Stop and is never replaced with an invented zero.
    """
    source = codex_telemetry_source(
        payload,
        state,
        external_session=external_session,
    )
    if source is None:
        return 0
    return enqueue_pending_codex_usage(source, project_id=project_id)


def codex_telemetry_source(
    payload: dict,
    state: dict,
    *,
    external_session: str,
) -> dict[str, object] | None:
    """Build the small durable source needed to retry one closed turn."""
    if client_name(payload) != "codex":
        return None
    transcript_path = payload.get("transcript_path")
    payload_turn = str(payload.get("turn_id") or "")
    state_turn = str(state.get("external_turn_id") or "")
    external_turn = payload_turn or state_turn
    cursor = state.get("codex_transcript_cursor")
    if (
        not isinstance(transcript_path, str)
        or not transcript_path
        or not external_turn
        or not isinstance(cursor, dict)
    ):
        return None
    source: dict[str, object] = {
        "source_id": hashlib.sha256(
            f"{external_session}\0{external_turn}".encode("utf-8")
        ).hexdigest(),
        "transcript_path": transcript_path,
        "external_session": external_session,
        "external_turn": external_turn,
        "cursor": dict(cursor),
    }
    internal_session = state.get("session_id")
    if isinstance(internal_session, str) and internal_session:
        source["internal_session"] = internal_session
    internal_turn = state.get("turn_id")
    if (
        isinstance(internal_turn, str)
        and internal_turn
        and (not payload_turn or not state_turn or payload_turn == state_turn)
    ):
        source["internal_turn"] = internal_turn
    return source


class _CodexUsageNotYetVisible(RuntimeError):
    """One bounded retry for a valid transcript whose Stop counters lag."""


def enqueue_pending_codex_usage(source: dict, *, project_id: str) -> int:
    """Parse and atomically enqueue one previously persisted source."""
    transcript_path = source.get("transcript_path")
    external_session = source.get("external_session")
    external_turn = source.get("external_turn")
    cursor = source.get("cursor")
    if not all(isinstance(item, str) and item for item in (
        transcript_path,
        external_session,
        external_turn,
    )) or not isinstance(cursor, dict):
        # A malformed private source cannot become valid on a retry.
        return 0
    result = read_codex_transcript_usage_result(
        Path(str(transcript_path)),
        session_id=str(external_session),
        turn_id=str(external_turn),
        cursor=cursor,
    )
    if result.status == "retryable_unavailable":
        raise RuntimeError("Codex transcript usage is temporarily unavailable")
    measured = result.usage
    if result.status == "complete" and not measured and int(source.get("empty_attempts") or 0) < 1:
        raise _CodexUsageNotYetVisible("Codex transcript usage has not been flushed yet")
    spool = ClientTelemetrySpool()
    events: list[dict[str, object]] = []
    for turn in measured:
        for request in turn.requests:
            event = request.client_event(str(external_session))
            internal_session = source.get("internal_session")
            internal_turn = source.get("internal_turn")
            if isinstance(internal_session, str) and internal_session:
                event["session_id"] = internal_session
            if isinstance(internal_turn, str) and internal_turn:
                event["turn_id"] = internal_turn
            events.append(event)
    return spool.enqueue_many(project_id, events)


def drain_pending_codex_usage(state: dict, *, project_id: str) -> tuple[int, bool]:
    """Move durable sources in stable order without head-of-line blocking.

    Returns ``(consumed_sources, capacity_blocked)``. A source is removed only
    after all of its events were durably enqueued (or a valid scan found none).
    """
    raw = state.get("pending_codex_usage")
    pending = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    consumed = 0
    blocked = False
    remaining: list[dict] = []
    for index, source in enumerate(pending):
        try:
            enqueue_pending_codex_usage(source, project_id=project_id)
        except _CodexUsageNotYetVisible:
            source["empty_attempts"] = int(source.get("empty_attempts") or 0) + 1
            remaining.append(source)
        except RuntimeError as exc:
            if "outbox is full" in str(exc):
                blocked = True
                remaining.extend(pending[index:])
                break
            # Private ledger failures may recover on the next lifecycle event.
            remaining.append(source)
        except Exception:
            remaining.append(source)
        else:
            consumed += 1
    if remaining:
        state["pending_codex_usage"] = remaining
    else:
        state.pop("pending_codex_usage", None)
    return consumed, blocked


def stage_codex_transcript_cursor(
    payload: dict,
    state: dict,
    *,
    external_session: str,
    external_turn: str,
) -> bool:
    """Capture one transcript offset for the whole turn, including steering."""
    if client_name(payload) != "codex":
        return False
    current = state.get("codex_transcript_cursor")
    if (
        isinstance(current, dict)
        and current.get("session_id") == external_session
        and current.get("turn_id") == external_turn
    ):
        return True
    # A new native turn must never inherit a cursor from an interrupted turn.
    state.pop("codex_transcript_cursor", None)
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return False
    captured = capture_codex_transcript_cursor(
        Path(transcript_path),
        session_id=external_session,
        turn_id=external_turn,
    )
    if captured is None:
        return False
    state["codex_transcript_cursor"] = captured
    return True


def _session(
    transport: str | ProjectHttpClient, project: dict, client: str, external_id: str
) -> dict:
    response = _request(
        transport,
        "POST",
        f"/projects/{project['id']}/sessions",
        json={"client": client, "external_id": external_id},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _safe_attachment_body(candidate: dict, turn_id: str) -> dict | None:
    path_value = candidate.get("path") or candidate.get("file_path") or candidate.get("local_path")
    if not path_value:
        return None
    path = Path(str(path_value)).expanduser().resolve()
    if path.name.lower().startswith(".env") or path.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }:
        return None
    content = path.read_bytes()
    if len(content) > 10 * 1024 * 1024:
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "turn_id": turn_id,
        "kind": candidate.get("kind") or ("image" if mime_type.startswith("image/") else "file"),
        "filename": candidate.get("filename") or path.name,
        "mime_type": mime_type,
        "source_uri": str(path),
        "content_base64": base64.b64encode(content).decode(),
        "extracted_text": content.decode("utf-8", errors="replace")
        if mime_type.startswith("text/")
        else "",
        "summary": str(candidate.get("summary") or ""),
    }


def register_prompt_artifacts(
    transport: str | ProjectHttpClient, project_id: str, turn_id: str, payload: dict
) -> None:
    candidates = payload.get("attachments") or payload.get("files") or []
    if not isinstance(candidates, list):
        return
    for candidate in candidates:
        body = _safe_attachment_body(
            candidate if isinstance(candidate, dict) else {"path": candidate}, turn_id
        )
        if body is None:
            continue
        try:
            _request(
                transport, "POST", f"/projects/{project_id}/artifacts", json=body, timeout=15
            ).raise_for_status()
        except httpx.HTTPError:
            continue


def _pending_commits(state: dict) -> list[dict]:
    values = state.get("pending_commits")
    return (
        [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []
    )


def queue_pending_commit(
    state: dict, turn_id: str, payload: dict, binding_id: str | None = None
) -> None:
    pending = [item for item in _pending_commits(state) if item.get("turn_id") != turn_id]
    item = {"turn_id": turn_id, "payload": payload}
    if binding_id:
        item["binding_id"] = binding_id
    state["pending_commits"] = [*pending, item]


def flush_pending_commits(
    transport: str | ProjectHttpClient,
    state: dict,
    binding_id: str | None = None,
    *,
    allow_legacy: bool = True,
) -> int:
    remaining = []
    persisted = 0
    for item in _pending_commits(state):
        item_binding = item.get("binding_id")
        if binding_id and item_binding not in (
            {binding_id, None} if allow_legacy else {binding_id}
        ):
            remaining.append(item)
            continue
        try:
            response = _request(
                transport,
                "POST",
                f"/turns/{item['turn_id']}/stop-check",
                json=item.get("payload") or {},
                timeout=10,
            )
            response.raise_for_status()
            persisted += 1
        except Exception:
            remaining.append(item)
    if remaining:
        state["pending_commits"] = remaining
    else:
        state.pop("pending_commits", None)
    return persisted


def _spooled_turns(state: dict) -> list[dict]:
    values = state.get("spooled_turns")
    return (
        [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []
    )


def _spooled_prompt_fragments(item: dict) -> list[dict[str, str]]:
    values = item.get("prompt_fragments")
    fragments = (
        [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []
    )
    normalized = [
        {
            "prompt_event_id": str(
                value.get("prompt_event_id") or item.get("external_turn_id") or "legacy-prompt"
            ),
            "user_prompt": str(value.get("user_prompt") or ""),
        }
        for value in fragments
    ]
    if normalized:
        return normalized
    return [
        {
            "prompt_event_id": str(
                item.get("prompt_event_id") or item.get("external_turn_id") or "legacy-prompt"
            ),
            "user_prompt": str(item.get("user_prompt") or ""),
        }
    ]


def stage_spooled_prompt(
    state: dict,
    *,
    session_external_id: str,
    external_turn_id: str,
    prompt_event_id: str,
    user_prompt: str,
    binding_id: str,
    off_record: bool,
) -> None:
    """Preserve every offline prompt fragment until the turn receives an answer."""
    active = state.get("spooled_active")
    if not isinstance(active, dict) or (
        active.get("session_external_id") != session_external_id
        or active.get("external_turn_id") != external_turn_id
        or active.get("binding_id") not in {None, binding_id}
    ):
        active = {
            "session_external_id": session_external_id,
            "external_turn_id": external_turn_id,
            "binding_id": binding_id,
            "off_record": off_record,
            "prompt_fragments": [],
        }
    fragments = _spooled_prompt_fragments(active) if active.get("prompt_fragments") else []
    existing = next(
        (item for item in fragments if item["prompt_event_id"] == prompt_event_id), None
    )
    if existing is not None and existing["user_prompt"] != user_prompt:
        raise ValueError("prompt event conflicts with the locally spooled fragment")
    if existing is None:
        fragments.append({"prompt_event_id": prompt_event_id, "user_prompt": user_prompt})
    active["prompt_fragments"] = fragments
    # Privacy is fail-closed across steering fragments. Once any fragment was
    # captured off-record, replay must keep the whole turn off-record.
    active["off_record"] = bool(active.get("off_record")) or off_record
    # Keep the legacy aggregate for support tooling and old replay code.
    active["user_prompt"] = "\n\n".join(item["user_prompt"] for item in fragments)
    state["spooled_active"] = active


def flush_spooled_active_prompts(
    project: dict,
    transport: str | ProjectHttpClient,
    state: dict,
    *,
    session_id: str,
    session_external_id: str,
    external_turn_id: str,
) -> bool:
    """Catch up prompt fragments if the API returns before the turn stops."""
    active = state.get("spooled_active")
    if not isinstance(active, dict) or (
        active.get("session_external_id") != session_external_id
        or active.get("external_turn_id") != external_turn_id
    ):
        return False
    for fragment in _spooled_prompt_fragments(active):
        response = _request(
            transport,
            "POST",
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session_id,
                "external_id": external_turn_id,
                "prompt_event_id": fragment["prompt_event_id"],
                "user_prompt": fragment["user_prompt"],
                "off_record": (
                    active["off_record"] if isinstance(active.get("off_record"), bool) else True
                ),
            },
            timeout=30,
        )
        response.raise_for_status()
    state.pop("spooled_active", None)
    return True


def flush_spooled_turns(
    project: dict,
    transport: str | ProjectHttpClient,
    client: str,
    binding_id: str | None = None,
    *,
    allow_legacy: bool = True,
) -> int:
    """Replay fully captured turns that were saved while the project API was offline."""
    persisted = 0
    for path in HOOK_STATE_DIR.glob(f"{project['id']}-{client}-*.json"):
        state = read_state(path)
        before = deepcopy(state)
        remaining = []
        for item in _spooled_turns(state):
            item_binding = item.get("binding_id")
            if binding_id and item_binding not in (
                {binding_id, None} if allow_legacy else {binding_id}
            ):
                remaining.append(item)
                continue
            if not item.get("assistant_response"):
                remaining.append(item)
                continue
            try:
                session = _session(transport, project, client, str(item["session_external_id"]))
                turn = None
                for fragment in _spooled_prompt_fragments(item):
                    turn = _request(
                        transport,
                        "POST",
                        f"/projects/{project['id']}/turns/begin",
                        json={
                            "session_id": session["id"],
                            "external_id": item["external_turn_id"],
                            "prompt_event_id": fragment["prompt_event_id"],
                            "user_prompt": fragment["user_prompt"],
                            # Spools created before the capture-time privacy flag was
                            # introduced cannot prove that the prompt was on record.
                            # Replay those legacy entries fail-closed.
                            "off_record": (
                                item["off_record"]
                                if isinstance(item.get("off_record"), bool)
                                else True
                            ),
                        },
                        timeout=30,
                    )
                    turn.raise_for_status()
                if turn is None:
                    raise RuntimeError("spooled turn has no prompt fragments")
                _request(
                    transport,
                    "POST",
                    f"/turns/{turn.json()['turn']['id']}/stop-check",
                    json={
                        "assistant_response": item["assistant_response"],
                        **(
                            {"context_observations": item["context_observations"]}
                            if isinstance(item.get("context_observations"), list)
                            else {}
                        ),
                    },
                    timeout=10,
                ).raise_for_status()
                persisted += 1
            except Exception:
                remaining.append(item)
        if remaining:
            state["spooled_turns"] = remaining
        else:
            state.pop("spooled_turns", None)
        merge_state_update(path, before, state)
    return persisted


def _memory_notice(briefing: dict, *, remote: bool = False) -> str:
    status = briefing.get("memory_status") or {}
    if not status or (status.get("available", True) and not status.get("state")):
        return ""
    notice = sleep_connection_notice(status, remote=remote)
    if notice is None:
        return ""
    return " ".join((notice["message"], notice["next_action"], notice["response_instruction"]))


def _scope_memory_status(briefing: dict, client: str) -> dict:
    """Return server-owned sleep health independently of the interactive client."""
    status = briefing.get("memory_status") or {}
    briefing["memory_status"] = status
    return status


PROJECT_CONTEXT_FIELDS = (
    "id",
    "name",
    "cause",
    "principles",
    "objectives",
    "context",
    "profile_version",
)


def _project_context_for_model(value: object) -> dict:
    """Exclude backup/activity fields that change without changing model context."""
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in PROJECT_CONTEXT_FIELDS if key in value}


def _context_for_model(briefing: dict) -> dict:
    return {
        "project": _project_context_for_model(briefing.get("project", {})),
        "operational_manual": briefing.get("operational_manual"),
        "plans": briefing.get("plans", []),
        "tasks": briefing.get("tasks", []),
        "task_counts": briefing.get("task_counts", {}),
        "task_indexing_pending": bool(briefing.get("task_indexing_pending")),
        "task_index_status": briefing.get("task_index_status", "ready"),
        "recent_handoffs": briefing.get("recent_handoffs", []),
        "memory_status": briefing.get("memory_status", {}),
    }


def _stable_context_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def foundation_context_hash(context: dict) -> str:
    """Fingerprint project identity, operating rules, and their renderer."""
    return _stable_context_hash(
        {
            "render_version": HOOK_CONTEXT_RENDER_VERSION,
            "cofounder_contract": COFOUNDER_CONTRACT,
            "task_contract": TASK_CONTRACT,
            "project": _project_context_for_model(context.get("project", {})),
            "operational_manual": context.get("operational_manual", {}),
        }
    )


def work_context_hash(context: dict) -> str:
    """Fingerprint the bounded Work snapshot independently from the foundation."""
    return _stable_context_hash(
        {
            "plans": context.get("plans", []),
            "tasks": context.get("tasks", []),
            "task_counts": context.get("task_counts", {}),
            "task_indexing_pending": bool(context.get("task_indexing_pending")),
            "task_index_status": context.get("task_index_status", "ready"),
            "recent_handoffs": context.get("recent_handoffs", []),
            "dashboard_url": context.get("dashboard_url"),
        }
    )


def briefing_context_hash(context: dict) -> str:
    """Backward-compatible aggregate fingerprint for diagnostics and tests."""
    return _stable_context_hash(
        {
            "foundation": foundation_context_hash(context),
            "work": work_context_hash(context),
        }
    )


def _delivery_baseline(state: dict, *, binding_id: str, external_session_id: str) -> dict | None:
    """Return a trusted per-session baseline or fail closed to a full snapshot."""
    if external_session_id == "default":
        return None
    baseline = state.get("context_delivery_baseline")
    if not isinstance(baseline, dict):
        return None
    if baseline.get("version") != CONTEXT_DELIVERY_STATE_VERSION:
        return None
    if baseline.get("render_version") != HOOK_CONTEXT_RENDER_VERSION:
        return None
    if baseline.get("binding_id") != binding_id:
        return None
    for key in ("foundation_hash", "work_hash"):
        value = baseline.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            return None
    references = baseline.get("delivered_work_references")
    if not isinstance(references, dict):
        return None
    for component in ("plans", "tasks"):
        values = references.get(component, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or len(value) > 256 for value in values
        ):
            return None
        if len(set(values)) != len(values):
            return None
    measurements = baseline.get("stable_measurements")
    if not isinstance(measurements, dict):
        return None
    for group in ("foundation", "work"):
        measurement = measurements.get(group)
        if not isinstance(measurement, dict):
            return None
        if not _valid_measurement_record(measurement):
            return None
    return baseline


def _invalidate_delivery_baseline(state: dict) -> None:
    state.pop("context_delivery_baseline", None)
    # The pre-v6 marker fingerprinted candidates before the budget decided
    # what the client actually received, so it must never seed delta delivery.
    state.pop("last_briefing_context_hash", None)


def _invalidate_delivery_baseline_file(path: Path) -> dict:
    """Authoritatively start a new live-context generation without losing outboxes."""
    with portable_file_lock(path, timeout=10):
        state = read_state(path)
        _invalidate_delivery_baseline(state)
        write_state(path, state)
        return state


def _manifest_work_references(composed: ComposedContext) -> dict[str, list[str]]:
    references = {"plans": [], "tasks": []}
    for delivery in composed.manifest.components:
        if delivery.component in references:
            references[delivery.component] = list(delivery.emitted_references)
    return references


def _empty_measurement_record() -> dict[str, int]:
    return {
        "items": 0,
        "characters": 0,
        "utf16_units": 0,
        "utf8_bytes": 0,
        "budget_units": 0,
    }


def _valid_measurement_record(record: dict) -> bool:
    keys = ("items", "characters", "utf16_units", "utf8_bytes", "budget_units")
    if any(
        isinstance(record.get(key), bool)
        or not isinstance(record.get(key), int)
        or record[key] < 0
        for key in keys
    ):
        return False
    items = record["items"]
    characters = record["characters"]
    utf16 = record["utf16_units"]
    utf8_bytes = record["utf8_bytes"]
    budget_units = record["budget_units"]
    if items == 0:
        return characters == utf16 == utf8_bytes == budget_units == 0
    return (
        items <= characters
        and characters <= utf16 <= utf8_bytes
        and budget_units == max(characters, utf16)
    )


def _add_measurement_records(*records: dict) -> dict[str, int]:
    result = _empty_measurement_record()
    for record in records:
        if not isinstance(record, dict):
            continue
        item_count = record.get("items")
        if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count <= 0:
            continue
        if result["items"] > 0:
            for key in ("characters", "utf16_units", "utf8_bytes", "budget_units"):
                result[key] += 1
        result["items"] += item_count
        for key in ("characters", "utf16_units", "utf8_bytes", "budget_units"):
            value = record.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[key] += value
    return result


def _manifest_component_measurement(
    composed: ComposedContext,
    components: set[str],
) -> dict[str, int]:
    result = _empty_measurement_record()
    for delivery in composed.manifest.components:
        if delivery.component not in components:
            continue
        measurement = delivery.emitted_measurement
        result = _add_measurement_records(
            result,
            {
                "items": delivery.emitted,
                "characters": measurement.characters,
                "utf16_units": measurement.utf16_units,
                "utf8_bytes": measurement.utf8_bytes,
                "budget_units": measurement.budget_units,
            },
        )
    return result


def _delivered_stable_measurements(composed: ComposedContext) -> dict[str, dict[str, int]]:
    """Record only stable bytes that actually entered the live client context."""
    standing_delivered = any(
        "standing-operating-contract" in delivery.emitted_references
        for delivery in composed.manifest.components
    )
    contract_record = _empty_measurement_record()
    if standing_delivered:
        contract = measure_fragment_sources(
            [_standing_contract_fragment(COFOUNDER_CONTRACT + TASK_CONTRACT)]
        )
        contract_record = {
            "items": 1,
            "characters": contract.characters,
            "utf16_units": contract.utf16_units,
            "utf8_bytes": contract.utf8_bytes,
            "budget_units": contract.budget_units,
        }
    foundation = _add_measurement_records(
        contract_record,
        _manifest_component_measurement(composed, {"manual", "profile"}),
    )
    return {
        "foundation": foundation,
        "work": _manifest_component_measurement(composed, {"plans", "tasks", "handoffs"}),
    }


def _store_delivery_baseline(
    state: dict,
    *,
    context: dict,
    composed: ComposedContext,
    binding_id: str,
    previous: dict | None = None,
    replace_foundation: bool = True,
    replace_work: bool = True,
) -> None:
    """Advance only a successfully composed delivery; fallback never becomes truth."""
    if composed.fallback_used:
        return
    delivered = _manifest_work_references(composed)
    current_measurements = _delivered_stable_measurements(composed)
    prior_references = (
        previous.get("delivered_work_references")
        if isinstance(previous, dict)
        and isinstance(previous.get("delivered_work_references"), dict)
        else {}
    )
    if not replace_work:
        delivered = {
            component: list(
                dict.fromkeys(
                    [
                        *(
                            prior_references.get(component, [])
                            if isinstance(prior_references.get(component), list)
                            else []
                        ),
                        *delivered.get(component, []),
                    ]
                )
            )
            for component in ("plans", "tasks")
        }
    prior_measurements = (
        previous.get("stable_measurements")
        if isinstance(previous, dict) and isinstance(previous.get("stable_measurements"), dict)
        else {}
    )
    stable_measurements = {
        "foundation": (
            current_measurements["foundation"]
            if replace_foundation
            else dict(prior_measurements.get("foundation") or _empty_measurement_record())
        ),
        "work": (
            current_measurements["work"]
            if replace_work
            else _add_measurement_records(
                prior_measurements.get("work") or {},
                current_measurements["work"],
            )
        ),
    }
    state["context_delivery_baseline"] = {
        "version": CONTEXT_DELIVERY_STATE_VERSION,
        "render_version": HOOK_CONTEXT_RENDER_VERSION,
        "binding_id": binding_id,
        "foundation_hash": (
            foundation_context_hash(context)
            if replace_foundation or not isinstance(previous, dict)
            else previous["foundation_hash"]
        ),
        "work_hash": (
            work_context_hash(context)
            if replace_work or not isinstance(previous, dict)
            else previous["work_hash"]
        ),
        "delivered_work_references": delivered,
        "stable_measurements": stable_measurements,
    }


def _context_delivery_reason(*, foundation_changed: bool, work_changed: bool) -> str:
    if foundation_changed and work_changed:
        return "foundation_and_work_changed"
    if foundation_changed:
        return "foundation_changed"
    if work_changed:
        return "work_changed"
    return "unchanged"


def _dense_payload(value: dict) -> dict:
    """Remove structurally empty top-level fields without rewriting content."""
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != [] and item != {}
    }


def _context_excerpt(value: object, limit: int = 900) -> str:
    """Build an explicit readable excerpt at a sentence or word boundary."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    window = text[: max(1, limit - 1)]
    boundary = max(
        window.rfind(". "),
        window.rfind("! "),
        window.rfind("? "),
        window.rfind("\n"),
    )
    if boundary < limit // 2:
        boundary = window.rfind(" ")
    if boundary < limit // 2:
        boundary = len(window)
    return f"{window[:boundary].rstrip()}…"


def _bounded_values(values: object, *, items: int, characters: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_context_excerpt(value, characters) for value in values[:items] if str(value).strip()]


def _project_excerpt(project: dict) -> dict:
    """Retain the founder-level identity when an unusually large profile cannot fit whole."""
    return _dense_payload(
        {
            "id": project.get("id"),
            "name": project.get("name"),
            "cause": _context_excerpt(project.get("cause"), 600),
            "principles": _bounded_values(project.get("principles"), items=6, characters=240),
            "objectives": _bounded_values(project.get("objectives"), items=6, characters=240),
            "context": _context_excerpt(project.get("context"), 800),
            "profile_version": project.get("profile_version"),
            "excerpted": True,
            "full_profile": "Open the project Overview when complete profile detail is needed.",
        }
    )


def _manual_excerpt(manual: dict, *, head: int = 800, tail: int = 600) -> dict:
    """Keep both operating constraints and late appendices with a full-manual pointer."""
    content = str(manual.get("content") or "")
    if len(content) <= head + tail:
        return dict(manual)
    head_text = content[:head]
    head_boundary = max(head_text.rfind("\n"), head_text.rfind(". "), head_text.rfind(" "))
    if head_boundary >= head // 2:
        head_text = head_text[:head_boundary].rstrip()
    tail_text = content[-tail:]
    tail_boundary = min(
        [value for value in (tail_text.find("\n"), tail_text.find(". ")) if value >= 0] or [0]
    )
    if tail_boundary and tail_boundary <= tail // 2:
        tail_text = tail_text[tail_boundary:].lstrip()
    return {
        "version": manual.get("version", 0),
        "content_head": head_text,
        "content_tail": tail_text,
        "omitted_characters": max(len(content) - len(head_text) - len(tail_text), 0),
        "excerpted": True,
        "full_manual": {"tool": "get_project_manual"},
    }


def _prompt_mentions(item: dict, prompt: str) -> bool:
    normalized = " ".join(prompt.casefold().split())
    if not normalized:
        return False
    identifier = str(item.get("id") or "").casefold()
    title = " ".join(str(item.get("title") or "").casefold().split())
    return bool(
        (identifier and identifier in normalized) or (len(title) >= 4 and title in normalized)
    )


def _filter_undelivered_prompt_work(
    context: dict,
    *,
    prompt: str,
    baseline: dict,
) -> None:
    """Keep only explicitly requested Work omitted from the current live context."""
    delivered = baseline.get("delivered_work_references", {})
    for key, component in (("plans", "plans"), ("tasks", "tasks")):
        known = {
            str(value)
            for value in delivered.get(component, [])
            if isinstance(value, str) and value
        }
        items = context.get(key)
        context[key] = (
            [
                item
                for item in items
                if isinstance(item, dict)
                and item.get("id")
                and str(item["id"]) not in known
                and _prompt_mentions(item, prompt)
            ]
            if isinstance(items, list)
            else []
        )
    for key in (
        "task_counts",
        "task_indexing_pending",
        "task_index_status",
        "recent_handoffs",
    ):
        context.pop(key, None)
    if not context.get("plans") and not context.get("tasks"):
        context.pop("dashboard_url", None)
    else:
        # Item deep links still need the base URL, but the generic Work link is
        # already present in the session snapshot.
        context["include_work_dashboard"] = False


def _memory_fragment(memory: dict, *, reason: str) -> ContextFragment | None:
    memory_id = str(memory.get("id") or "")
    if not memory_id:
        return None
    payload = _dense_payload(
        {
            "id": memory_id,
            "type": memory.get("node_type"),
            "key": memory.get("node_key"),
            "revision": memory.get("revision"),
            "text": str(memory.get("text") or ""),
        }
    )
    excerpt = {
        **payload,
        "text": _context_excerpt(memory.get("text"), 900),
        "excerpted": True,
        "full_memory": {"tool": "explain_memory", "memory_id": memory_id},
    }
    priority = 28 if reason == "exact_reference" else 30 if reason == "direct" else 40
    return ContextFragment(
        component="memories",
        priority=priority,
        reference=memory_id,
        payload=payload,
        excerpt=excerpt,
    )


_CONTEXT_FORMAT_INSTRUCTION = (
    "The remaining private context is JSON Lines. Treat full items as authoritative; "
    "partial items are explicit excerpts and include a pointer when full detail is available."
)


def _standing_contract_fragment(contracts: str) -> ContextFragment:
    return ContextFragment(
        component="instructions",
        priority=0,
        reference="standing-operating-contract",
        payload={"text": contracts, "context_format": _CONTEXT_FORMAT_INSTRUCTION},
        required=True,
    )


def founder_context_fragments(
    briefing: dict,
    *,
    contracts: str,
    standing_contracts: str = "",
    prompt: str = "",
    onboarding: str = "",
    setup_notice: str = "",
    memory_notice: str = "",
    session_id: str = "",
    session_off_record: bool = False,
) -> list[ContextFragment]:
    """Project compact state into independently selectable founder-brief units."""
    dashboard_url = str(briefing.get("dashboard_url") or "").strip() or None
    fragments: list[ContextFragment] = []
    if standing_contracts:
        fragments.append(_standing_contract_fragment(standing_contracts))
    if contracts:
        fragments.append(
            ContextFragment(
                component="instructions",
                priority=0,
                reference="turn-contract",
                payload={
                    "text": contracts,
                    **(
                        {}
                        if standing_contracts
                        else {"context_format": _CONTEXT_FORMAT_INSTRUCTION}
                    ),
                },
                required=True,
            )
        )
    if session_id:
        fragments.append(
            ContextFragment(
                component="instructions",
                priority=1,
                reference="current-dduo-session",
                payload={
                    "dduo_session_id": session_id,
                    "off_record": session_off_record,
                },
                required=True,
            )
        )
    if onboarding:
        fragments.append(
            ContextFragment(
                component="instructions",
                priority=2,
                reference="project-onboarding",
                payload={"text": onboarding},
                required=True,
            )
        )
    manual = briefing.get("operational_manual")
    if isinstance(manual, dict):
        fragments.append(
            ContextFragment(
                component="manual",
                priority=4,
                reference=f"operational-manual@v{manual.get('version', 0)}",
                payload=_dense_payload(dict(manual)),
                excerpt=_manual_excerpt(manual),
                required=True,
            )
        )
    for reference, notice in (
        ("setup", setup_notice),
        ("memory-status", memory_notice),
    ):
        if notice:
            fragments.append(
                ContextFragment(
                    component="health",
                    priority=2,
                    reference=reference,
                    payload={"text": notice},
                    required=True,
                )
            )

    project = _project_context_for_model(briefing.get("project", {}))
    if project:
        fragments.append(
            ContextFragment(
                component="profile",
                priority=10,
                reference=str(project.get("id") or "project-profile"),
                payload=_dense_payload(project),
                excerpt=_project_excerpt(project),
                required=True,
            )
        )

    for plan in briefing.get("plans", []) if isinstance(briefing.get("plans"), list) else []:
        if not isinstance(plan, dict) or not plan.get("id"):
            continue
        relevant = plan.get("status") == "executing" or _prompt_mentions(plan, prompt)
        payload = _dense_payload(dict(plan))
        if dashboard_url:
            try:
                payload["url"] = dashboard_item_url(dashboard_url, plan_id=str(plan["id"]))
            except ValueError:
                pass
        fragments.append(
            ContextFragment(
                component="plans",
                priority=20 if relevant else 55,
                reference=str(plan["id"]),
                payload=payload,
                excerpt={
                    **_dense_payload(
                        {
                            "id": plan.get("id"),
                            "title": plan.get("title"),
                            "status": plan.get("status"),
                            "objective": _context_excerpt(plan.get("objective"), 500),
                            "content_excerpt": _context_excerpt(plan.get("content_excerpt"), 700),
                            "version": plan.get("version"),
                            "url": payload.get("url"),
                        }
                    ),
                    "excerpted": True,
                    "full_plan": {
                        "tool": "activate_plan_context",
                        "plan_id": str(plan["id"]),
                    },
                },
            )
        )

    for task in briefing.get("tasks", []) if isinstance(briefing.get("tasks"), list) else []:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        relevant = task.get("status") in {"in_progress", "blocked"} or _prompt_mentions(
            task, prompt
        )
        payload = _dense_payload(dict(task))
        if dashboard_url:
            try:
                payload["url"] = dashboard_item_url(dashboard_url, work_id=str(task["id"]))
            except ValueError:
                pass
        if task.get("truncated_fields"):
            payload["full_task"] = {"tool": "get_task", "task_id": str(task["id"])}
        fragments.append(
            ContextFragment(
                component="tasks",
                priority=22 if relevant else 60,
                reference=str(task["id"]),
                payload=payload,
            )
        )

    retrieval = briefing.get("retrieval") if isinstance(briefing.get("retrieval"), dict) else {}
    retrieval_metadata = (
        retrieval.get("metadata") if isinstance(retrieval.get("metadata"), dict) else {}
    )
    reasons = (
        retrieval_metadata.get("selected_reasons")
        if isinstance(retrieval_metadata.get("selected_reasons"), dict)
        else {}
    )
    if retrieval.get("status") == "degraded":
        fragments.append(
            ContextFragment(
                component="health",
                priority=3,
                reference="memory-retrieval",
                payload={
                    "text": (
                        "Semantic retrieval is temporarily degraded. Continue normally and do not "
                        "treat an empty memory result as proof that the project has no relevant memory."
                    )
                },
            )
        )
    memories = briefing.get("memories") if isinstance(briefing.get("memories"), list) else []
    seen_memory_ids: set[str] = set()
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        memory_id = str(memory.get("id") or "")
        if not memory_id or memory_id in seen_memory_ids:
            continue
        seen_memory_ids.add(memory_id)
        reason = str(reasons.get(memory_id) or retrieval_metadata.get("selection_mode") or "direct")
        fragment = _memory_fragment(memory, reason=reason)
        if fragment is not None:
            fragments.append(fragment)

    task_counts = briefing.get("task_counts")
    if isinstance(task_counts, dict) and task_counts:
        fragments.append(
            ContextFragment(
                component="tasks",
                priority=70,
                reference="task-counts",
                payload=task_counts,
            )
        )
    for handoff in (
        briefing.get("recent_handoffs", [])
        if isinstance(briefing.get("recent_handoffs"), list)
        else []
    ):
        if not isinstance(handoff, dict) or not handoff.get("id"):
            continue
        fragments.append(
            ContextFragment(
                component="handoffs",
                priority=72,
                reference=str(handoff["id"]),
                payload=_dense_payload(handoff),
                excerpt={
                    **_dense_payload(handoff),
                    "summary": _context_excerpt(handoff.get("summary"), 700),
                    "excerpted": True,
                },
            )
        )

    task_index_status = str(briefing.get("task_index_status") or "ready")
    if briefing.get("task_indexing_pending") or task_index_status != "ready":
        fragments.append(
            ContextFragment(
                component="health",
                priority=75,
                reference="task-index",
                payload={
                    "task_index_status": task_index_status,
                    "indexing_pending": bool(briefing.get("task_indexing_pending")),
                },
            )
        )
    if briefing.get("dashboard_url") and briefing.get("include_work_dashboard", True):
        fragments.append(
            ContextFragment(
                component="tasks",
                priority=80,
                reference="work-dashboard",
                payload={"dashboard_url": briefing["dashboard_url"]},
            )
        )
    return fragments


def compose_founder_context(briefing: dict, **values: str) -> ComposedContext:
    try:
        return compose_context(
            founder_context_fragments(briefing, **values),
            budget=DEFAULT_CONTEXT_BUDGET,
        )
    except (TypeError, ValueError):
        # One malformed upstream value must never suppress the hook response.
        return compose_fallback_context(
            "dDuo could not safely compose project context. Continue normal project work while it recovers.",
            reason="composition_failed",
            budget=DEFAULT_CONTEXT_BUDGET,
        )


def _context_reuse_measurement(
    *,
    baseline: dict | None,
    foundation_changed: bool,
    work_changed: bool,
) -> dict[str, int]:
    """Measure only the exact stable representations already delivered in-session."""
    if baseline is None:
        return _empty_measurement_record()
    measurements = baseline.get("stable_measurements")
    if not isinstance(measurements, dict):
        return _empty_measurement_record()
    return _add_measurement_records(
        {} if foundation_changed else measurements.get("foundation") or {},
        {} if work_changed else measurements.get("work") or {},
    )


def compose_unavailable_context(message: str) -> ComposedContext:
    return compose_fallback_context(
        message,
        reason="memory_unavailable",
        budget=DEFAULT_CONTEXT_BUDGET,
    )


def compose_remote_offline_context(
    binding: ProjectBinding | None,
    *,
    contracts: str,
    standing_contracts: str = "",
    prompt: str = "",
    include_manual: bool = True,
) -> ComposedContext | None:
    """Continue safely with verified stable context while the VPS is offline.

    A live session with a trusted delivery baseline already contains the
    project manual. Repeating its cached copy on every failed turn would waste
    context, so callers can deliver only the outage notice and current turn
    contract. A rehydration boundary still requires the verified cached manual.
    """
    if binding is None or not binding.remote:
        return None
    manual = load_verified_manual(binding)
    if include_manual and manual is None:
        return None
    if include_manual:
        notice = (
            "dDuo remote memory is temporarily unavailable. Continue with this last verified "
            f"project operating manual (version {manual['version']}); current memories and Work "
            "state may be stale or absent. Do not start a local dDuo stack for this remote-bound "
            "project."
        )
        context = {"operational_manual": manual}
    else:
        notice = (
            "dDuo remote memory is temporarily unavailable. Continue with the operating manual "
            "and stable project context already present in this live session; current memories "
            "and Work state may be stale or absent. Do not start a local dDuo stack for this "
            "remote-bound project."
        )
        context = {}
    return compose_founder_context(
        context,
        contracts=contracts,
        standing_contracts=standing_contracts,
        prompt=prompt,
        memory_notice=notice,
    )


def remote_manual_cache_allowed(error: Exception) -> bool:
    """Use cached project instructions only for transport or server outages."""
    if isinstance(error, httpx.RequestError):
        return True
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500


def client_upgrade_notice(error: Exception) -> ComposedContext | None:
    if not isinstance(error, ClientUpgradeRequired):
        return None
    target = error.directive.target_release
    release = f" ({target})" if target else ""
    return compose_unavailable_context(
        "dDuo requires a client update"
        f"{release}. Update dDuo Solo Founder from its official repository, then open a new "
        "chat in this project. Do not start a substitute local memory stack."
    )


def _session_start_reason(payload: dict) -> str:
    source = str(payload.get("source") or payload.get("reason") or "").strip().lower()
    return source if source in {"startup", "resume", "clear", "compact"} else "session_start"


def _delivery_observation(
    *,
    kind: str,
    reason: str,
    foundation_changed: bool = False,
    work_changed: bool = False,
    reused=None,
) -> dict:
    if isinstance(reused, dict):
        reused_characters = int(reused.get("characters", 0) or 0)
        reused_utf8_bytes = int(reused.get("utf8_bytes", 0) or 0)
    else:
        reused_characters = int(getattr(reused, "characters", 0) or 0)
        reused_utf8_bytes = int(getattr(reused, "utf8_bytes", 0) or 0)
    return {
        "kind": kind,
        "reason": reason,
        "foundation_changed": foundation_changed,
        "work_changed": work_changed,
        "reused_characters": reused_characters,
        "reused_utf8_bytes": reused_utf8_bytes,
        "reused_estimated_tokens": estimated_tokens_for_bytes(reused_utf8_bytes),
    }


def measured_context(
    context: str,
    *,
    event_id: str,
    operation: str,
    scope: str,
    client: str,
    session_id: str | None = None,
    turn_id: str | None = None,
    retrieval_run_id: str | None = None,
    component_values: dict[str, str] | None = None,
    component_metadata: dict[str, dict] | None = None,
    composed: ComposedContext | None = None,
    delivery: dict | None = None,
    render_version: str = HOOK_CONTEXT_RENDER_VERSION,
) -> dict:
    """Capture one exact model-visible context plus its deterministic measurements."""
    if composed is not None and context != composed.content:
        raise ValueError("measured context must equal the composed founder brief")
    estimated_tokens, characters, utf8_bytes = estimated_tokens_for_text(context)
    components: dict[str, int] = {}
    allowed = {
        "instructions",
        "manual",
        "profile",
        "plans",
        "tasks",
        "handoffs",
        "memories",
        "retrieval",
        "artifacts",
        "health",
        "overhead",
    }
    composed_metadata: dict[str, dict] = {}
    if composed is not None:
        for component_delivery in composed.manifest.components:
            if component_delivery.component not in allowed:
                continue
            components[component_delivery.component] = (
                component_delivery.emitted_measurement.utf8_bytes
            )
            composed_metadata[component_delivery.component] = {
                "item_count": component_delivery.emitted,
                "candidate_item_count": component_delivery.produced,
                "partial_item_count": component_delivery.partial,
                "omitted_item_count": component_delivery.omitted,
                "references": list(component_delivery.emitted_references),
                "omitted_references": list(component_delivery.omitted_references),
            }
    else:
        for name, value in (component_values or {}).items():
            if name in allowed:
                components[name] = components.get(name, 0) + len(value.encode("utf-8"))
    accounted = sum(components.values())
    if accounted > utf8_bytes:
        components = {"overhead": utf8_bytes}
    elif accounted < utf8_bytes:
        components["overhead"] = components.get("overhead", 0) + utf8_bytes - accounted
    manifest = []
    metadata = composed_metadata if composed is not None else component_metadata or {}
    for name, byte_count in components.items():
        item = {
            "name": name,
            "utf8_bytes": byte_count,
            "estimated_tokens": estimated_tokens_for_bytes(byte_count),
        }
        details = metadata.get(name) or {}
        if isinstance(details.get("item_count"), int):
            item["item_count"] = max(0, details["item_count"])
        for key in (
            "candidate_item_count",
            "partial_item_count",
            "omitted_item_count",
        ):
            if isinstance(details.get(key), int):
                item[key] = max(0, details[key])
        references = details.get("references")
        if isinstance(references, list):
            item["references"] = list(
                dict.fromkeys(str(value) for value in references if str(value))
            )[:1_000]
        omitted_references = details.get("omitted_references")
        if isinstance(omitted_references, list):
            item["omitted_references"] = list(
                dict.fromkeys(str(value) for value in omitted_references if str(value))
            )[:1_000]
        manifest.append(item)
    encoded = context.encode("utf-8")
    observation = {
        "event_id": event_id,
        "operation": operation,
        "scope": scope,
        "client": client,
        "session_id": session_id,
        "turn_id": turn_id,
        "retrieval_run_id": retrieval_run_id,
        "characters": characters,
        "utf8_bytes": utf8_bytes,
        "estimated_tokens": estimated_tokens,
        "estimator_version": ESTIMATOR_VERSION,
        "component_bytes": components,
        "components": manifest,
        "content": context,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "producer_version": __version__,
        "render_version": render_version,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    if composed is not None:
        observation["budget"] = {
            "limit_characters": composed.manifest.budget,
            "client_character_units": composed.measurement.budget_units,
            "candidate_characters": composed.produced_measurement.characters,
            "candidate_utf8_bytes": composed.produced_measurement.utf8_bytes,
            "candidate_estimated_tokens": estimated_tokens_for_bytes(
                composed.produced_measurement.utf8_bytes
            ),
            "avoided_characters": max(
                composed.produced_measurement.characters - composed.measurement.characters,
                0,
            ),
            "avoided_utf8_bytes": max(
                composed.produced_measurement.utf8_bytes - composed.measurement.utf8_bytes,
                0,
            ),
            "avoided_estimated_tokens": estimated_tokens_for_bytes(
                max(
                    composed.produced_measurement.utf8_bytes - composed.measurement.utf8_bytes,
                    0,
                )
            ),
            "included_items": composed.manifest.emitted,
            "partial_items": composed.manifest.partial,
            "omitted_items": composed.manifest.omitted,
            "outcome": composed.manifest.status,
            "delivery_expectation": "inline_expected",
        }
    if delivery is not None:
        observation["delivery"] = delivery
    return observation


def automatic_context_event_id(
    operation: str,
    *,
    session_id: str,
    turn_id: str | None = None,
    render_version: str = HOOK_CONTEXT_RENDER_VERSION,
    content: str | None = None,
    occurrence_id: str | None = None,
) -> str:
    """Return the same delivery id when one lifecycle render is replayed."""
    lifecycle_id = occurrence_id or turn_id or session_id
    value = f"dduo:{operation}:{lifecycle_id}:{render_version}"
    if content is not None:
        value += f":{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    return f"hook-{uuid.uuid5(uuid.NAMESPACE_URL, value)}"


def stage_context_observation(state: dict, observation: dict) -> None:
    pending = state.get("pending_context_observations")
    pending = (
        [item for item in pending if isinstance(item, dict)] if isinstance(pending, list) else []
    )
    if any(
        item.get("event_id") == observation.get("event_id")
        and item.get("content_sha256") == observation.get("content_sha256")
        for item in pending
    ):
        return
    pending.append(observation)
    state["pending_context_observations"] = pending[-100:]


def briefing_component_values(briefing: dict) -> dict[str, str]:
    categories = {
        "project": "profile",
        "operational_manual": "manual",
        "plans": "plans",
        "tasks": "tasks",
        "task_counts": "tasks",
        "task_indexing_pending": "health",
        "task_index_status": "health",
        "recent_handoffs": "handoffs",
        "memories": "memories",
        "retrieval": "retrieval",
        "artifacts": "artifacts",
        "memory_status": "health",
        "sleep_recovery": "health",
        "turn_recovery": "health",
        "backup": "health",
    }
    values: dict[str, str] = {}
    for key, category in categories.items():
        if key in briefing:
            values[category] = values.get(category, "") + json.dumps(
                briefing[key], ensure_ascii=False
            )
    return values


def briefing_component_metadata(briefing: dict) -> dict[str, dict]:
    categories = {
        "project": "profile",
        "operational_manual": "manual",
        "plans": "plans",
        "tasks": "tasks",
        "task_counts": "tasks",
        "task_indexing_pending": "health",
        "task_index_status": "health",
        "recent_handoffs": "handoffs",
        "memories": "memories",
        "retrieval": "retrieval",
        "artifacts": "artifacts",
        "memory_status": "health",
        "sleep_recovery": "health",
        "turn_recovery": "health",
        "backup": "health",
    }
    metadata: dict[str, dict] = {}
    for key, category in categories.items():
        if key not in briefing:
            continue
        value = briefing[key]
        values = value if isinstance(value, list) else [value]
        references = [
            str(item["id"])
            for item in values
            if isinstance(item, dict) and item.get("id") is not None
        ]
        current = metadata.setdefault(category, {"item_count": 0, "references": []})
        current["item_count"] += len(values) if value not in ({}, None) else 0
        current["references"].extend(references)
    return metadata


def merge_component_values(*groups: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for group in groups:
        for category, value in group.items():
            merged[category] = merged.get(category, "") + value
    return merged


def session_start() -> None:
    payload = read_input()
    root = project_root(payload)
    client = client_name(payload)
    external = external_session_id(payload)
    # SessionStart may run more than once while resuming the same external
    # session. Each real emission is a distinct delivery; persistence retries
    # reuse the event already staged in the local state file.
    occurrence_id = uuid.uuid4().hex
    fallback_message = (
        "dDuo Solo Founder memory is temporarily unavailable. Continue working normally; ask once whether "
        "the founder wants it repaired."
    )
    fallback_composed = compose_unavailable_context(fallback_message)
    fallback_context = fallback_composed.content
    binding: ProjectBinding | None = None
    session_reason = _session_start_reason(payload)
    resume_requested = session_reason == "resume"
    config_path = root / ".dduo-solo-founder/project.toml"
    if not config_path.exists():
        if setup_declined(root):
            emit_no_context()
            return
        emit(
            f"dDuo Solo Founder is available for {root.name}. If the current request is a self-contained "
            "remote project invitation, follow that invitation and never initialize a local fallback. Otherwise ask "
            "one concise question: activate dDuo Solo Founder locally for this folder? "
            "If declined, call decline_setup once and continue without memory or repeated setup questions. "
            "After approval, say that dDuo Setup is opening and ask the founder to complete only the actions shown "
            "there, then reply 'fatto' so you can verify it. Call open_setup without showing commands, URLs, or "
            "setup internals. On that reply, call check_setup with the current client. If it is ready, state only "
            "that a new chat in this folder is required. If it is not ready, state only the next action, reopen "
            "Setup, and wait for another 'fatto'.",
            "SessionStart",
        )
        return
    try:
        import subprocess

        project = load_project(root)
        binding = _binding_for_project(root, project)
        path = state_path(project["id"], client, external)
        if not resume_requested:
            try:
                _invalidate_delivery_baseline_file(path)
            except Exception:
                # A rehydration boundary still emits a complete snapshot. A
                # failed private-state write may only cause later
                # over-delivery, never block work.
                pass
        if not binding.remote:
            subprocess.run(
                ["dduo-solo-founder", "start", "--project-root", str(root)],
                timeout=190,
                check=True,
                capture_output=True,
            )
        transport = _project_client(binding, "hook", external)
        session = _session(transport, project, client, external)
        # Client-side usage and guard observations are a private, content-free
        # outbox.  A blocked prompt deliberately performs no network work, so
        # SessionStart is the earliest reliable opportunity to deliver any
        # observations left behind by that hard gate.
        flush_client_telemetry(transport, str(project["id"]))
        state = read_state(path)
        state_before = deepcopy(state)
        state.update(
            {
                "project_id": project["id"],
                "api": binding.api_url,
                "binding_id": binding.binding_id,
                "session_id": session["id"],
                "session_external_id": external,
                "session_off_record": bool(session.get("off_record")),
            }
        )
        flush_pending_commits(
            transport,
            state,
            binding.binding_id,
            allow_legacy=not binding.remote,
        )
        try:
            merge_state_update(path, state_before, state)
        except Exception:
            # A valid founder briefing is more important than optional local
            # outbox maintenance; the hook remains fail-open.
            pass
        flush_spooled_turns(
            project,
            transport,
            client,
            binding.binding_id,
            allow_legacy=not binding.remote,
        )
        state = read_state(path)
        state_before = deepcopy(state)
        briefing_options: dict = {"timeout": 10}
        if client in {"codex", "claude"}:
            briefing_options["params"] = {"client": client}
        response = _request(
            transport,
            "GET",
            f"/projects/{project['id']}/briefing",
            **briefing_options,
        )
        response.raise_for_status()
        briefing = response.json()
        if binding.remote:
            try:
                store_verified_manual(binding, briefing.get("operational_manual"))
            except Exception:
                # The cache is optional and must never suppress live context.
                pass
        setup_notice = ""
        memory_status = _scope_memory_status(briefing, client)
        if memory_status.get("state") != "connection_required":
            clear_setup_notice(str(project["id"]), client)
        onboarding = ""
        if briefing.get("onboarding_required"):
            onboarding = (
                "dDuo is active. Ask in one message what this project is for, its objectives, principles, "
                "current state, and main activities; then save the confirmed profile. "
            )
        memory_notice = _memory_notice(briefing, remote=binding.remote)
        full_compact_briefing = _context_for_model(briefing)
        full_compact_briefing["dashboard_url"] = binding.dashboard_link("tasks")
        baseline = (
            _delivery_baseline(
                state,
                binding_id=binding.binding_id,
                external_session_id=external,
            )
            if resume_requested
            else None
        )
        compact_briefing = deepcopy(full_compact_briefing)
        current_foundation_hash = foundation_context_hash(full_compact_briefing)
        current_work_hash = work_context_hash(full_compact_briefing)
        if baseline is None:
            foundation_changed = True
            work_changed = True
            delivery_kind = "snapshot"
            delivery_reason = (
                "session_unknown"
                if external == "default"
                else "state_missing"
                if resume_requested
                else session_reason
            )
            standing_contracts = COFOUNDER_CONTRACT + TASK_CONTRACT
        else:
            foundation_changed = baseline["foundation_hash"] != current_foundation_hash
            work_changed = baseline["work_hash"] != current_work_hash
            delivery_kind = "delta"
            delivery_reason = _context_delivery_reason(
                foundation_changed=foundation_changed,
                work_changed=work_changed,
            )
            if not foundation_changed:
                compact_briefing.pop("project", None)
                compact_briefing.pop("operational_manual", None)
            if not work_changed:
                _filter_undelivered_prompt_work(
                    compact_briefing,
                    prompt="",
                    baseline=baseline,
                )
            standing_contracts = (
                COFOUNDER_CONTRACT + TASK_CONTRACT if foundation_changed else ""
            )
        composed = compose_founder_context(
            compact_briefing,
            contracts=SESSION_REFRESH_CONTRACT if baseline is not None else "",
            standing_contracts=standing_contracts,
            onboarding=onboarding,
            setup_notice=setup_notice,
            memory_notice=memory_notice,
            session_id=session["id"],
            session_off_record=bool(session.get("off_record")),
        )
        session_context = composed.content
        reused = _context_reuse_measurement(
            baseline=baseline,
            foundation_changed=foundation_changed,
            work_changed=work_changed,
        )
        if composed.fallback_used:
            delivery_kind = "fallback"
            delivery_reason = "composition_failed"
            foundation_changed = False
            work_changed = False
            reused = None
        emit(session_context, "SessionStart")
        try:
            _store_delivery_baseline(
                state,
                context=full_compact_briefing,
                composed=composed,
                binding_id=binding.binding_id,
                previous=baseline,
                replace_foundation=baseline is None or foundation_changed,
                replace_work=baseline is None or work_changed,
            )
            stage_context_observation(
                state,
                measured_context(
                    session_context,
                    event_id=automatic_context_event_id(
                        "context.session_start",
                        session_id=external,
                        content=session_context,
                        occurrence_id=occurrence_id,
                    ),
                    operation="context.session_start",
                    scope="automatic",
                    client=client,
                    session_id=session["id"],
                    composed=composed,
                    delivery=_delivery_observation(
                        kind=delivery_kind,
                        reason=delivery_reason,
                        foundation_changed=foundation_changed,
                        work_changed=work_changed,
                        reused=reused,
                    ),
                ),
            )
            merge_state_update(path, state_before, state)
        except Exception:
            # Observability must never suppress a valid project briefing.
            pass
    except HookEmissionError:
        raise
    except Exception as error:
        upgrade_composed = client_upgrade_notice(error)
        fallback_reason = "memory_unavailable"
        if upgrade_composed is not None:
            fallback_composed = upgrade_composed
            fallback_context = upgrade_composed.content
            fallback_reason = "client_upgrade_required"
        fallback_observation: dict | None = None
        fallback_observation_path: Path | None = None
        try:
            project = load_project(root)
            fallback_binding = _binding_for_project(root, project, require_approval=False)
            cache_binding = binding
            if cache_binding is None:
                try:
                    cache_binding = _binding_for_project(root, project)
                except Exception:
                    cache_binding = None
            cached_composed = (
                compose_remote_offline_context(
                    cache_binding,
                    contracts="",
                    standing_contracts=COFOUNDER_CONTRACT + TASK_CONTRACT,
                )
                if upgrade_composed is None and remote_manual_cache_allowed(error)
                else None
            )
            if cached_composed is not None:
                fallback_composed = cached_composed
                fallback_context = cached_composed.content
                fallback_reason = "remote_offline"
            path = state_path(project["id"], client, external)
            try:
                state = _invalidate_delivery_baseline_file(path)
            except Exception:
                state = read_state(path)
                _invalidate_delivery_baseline(state)
            state_before = deepcopy(state)
            state.setdefault("binding_id", fallback_binding.binding_id)
            internal_session_id = str(state.get("session_id") or "") or None
            fallback_observation = measured_context(
                fallback_context,
                event_id=automatic_context_event_id(
                    "context.session_start",
                    session_id=external,
                    render_version=HOOK_FALLBACK_RENDER_VERSION,
                    content=fallback_context,
                    occurrence_id=occurrence_id,
                ),
                operation="context.session_start",
                scope="automatic",
                client=client,
                session_id=internal_session_id,
                component_values={"health": fallback_context},
                composed=fallback_composed,
                delivery=_delivery_observation(
                    kind="fallback",
                    reason=fallback_reason,
                ),
                render_version=HOOK_FALLBACK_RENDER_VERSION,
            )
            fallback_observation_path = path
            merge_state_update(path, state_before, state)
        except Exception:
            pass
        emit(
            fallback_context,
            "SessionStart",
            warn=True,
        )
        if fallback_observation is not None and fallback_observation_path is not None:
            try:
                state = read_state(fallback_observation_path)
                state_before = deepcopy(state)
                stage_context_observation(state, fallback_observation)
                merge_state_update(fallback_observation_path, state_before, state)
            except Exception:
                # The fallback was delivered; telemetry may retry only when its
                # private state can be updated safely.
                pass


def user_prompt_submit() -> None:
    payload = read_input()
    client = client_name(payload)
    prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
    external = external_session_id(payload)
    # A message_id identifies one prompt fragment, not its enclosing model
    # interaction. Clients without a native turn_id reuse the locally active
    # turn until Stop; treating message_id as a turn would split steering.
    declared_turn = payload.get("turn_id")
    # Supported Codex turn hooks expose turn_id. Claude prompts without a stable
    # turn identifier are separate turns; a fresh UUID avoids collapsing two
    # identical prompts into one historical record.
    external_turn = str(declared_turn or uuid.uuid4())
    prompt_delivery_id = prompt_event_id(
        payload,
        session_id=external,
        turn_id=external_turn,
        prompt=prompt,
    )
    fallback_message = (
        "dDuo could not reach local memory for this turn. Continue normally; this turn is "
        "queued locally and will be recovered automatically."
    )
    fallback_composed = compose_unavailable_context(fallback_message)
    fallback_context = fallback_composed.content
    internal_session_id: str | None = None
    internal_turn_id: str | None = None
    session_off_record: bool | None = None
    binding: ProjectBinding | None = None
    try:
        root = project_root(payload)
        try:
            project = load_project(root)
        except FileNotFoundError:
            # An unconfigured folder is not a memory outage. In particular,
            # never promise a replay when no project spool can exist.
            emit_no_context()
            return
        binding = _binding_for_project(root, project)
        transport = _project_client(binding, "hook", external)
        flush_client_telemetry(transport, str(project["id"]))
        session = _session(transport, project, client, external)
        internal_session_id = session["id"]
        session_off_record = bool(session.get("off_record"))
        path = state_path(project["id"], client, external)
        state = read_state(path)
        if declared_turn is None:
            active = state.get("spooled_active")
            active_external_turn = (
                state.get("external_turn_id")
                if state.get("turn_id")
                else active.get("external_turn_id")
                if isinstance(active, dict)
                else None
            )
            if isinstance(active_external_turn, str) and active_external_turn:
                external_turn = active_external_turn
        state_before = deepcopy(state)
        stage_codex_transcript_cursor(
            payload,
            state,
            external_session=external,
            external_turn=external_turn,
        )
        flush_pending_commits(
            transport,
            state,
            binding.binding_id,
            allow_legacy=not binding.remote,
        )
        # Persist the pending-commit outbox before the cross-session spool
        # flusher updates state files, then reload so a delivered offline turn
        # cannot be resurrected by a stale in-memory copy.
        merge_state_update(path, state_before, state)
        flush_spooled_turns(
            project,
            transport,
            client,
            binding.binding_id,
            allow_legacy=not binding.remote,
        )
        state = read_state(path)
        state_before = deepcopy(state)
        state["session_off_record"] = session_off_record
        flush_spooled_active_prompts(
            project,
            transport,
            state,
            session_id=session["id"],
            session_external_id=external,
            external_turn_id=external_turn,
        )
        response = _request(
            transport,
            "POST",
            f"/projects/{project['id']}/turns/begin",
            json={
                "session_id": session["id"],
                "external_id": external_turn,
                "prompt_event_id": prompt_delivery_id,
                "user_prompt": prompt,
                "off_record": session_off_record,
            },
            timeout=30,
        )
        response.raise_for_status()
        context = response.json()
        if binding.remote:
            try:
                store_verified_manual(binding, context.get("operational_manual"))
            except Exception:
                # The cache is optional and must never suppress live context.
                pass
        memory_status = _scope_memory_status(context, client)
        turn = context["turn"]
        internal_turn_id = turn["id"]
        register_prompt_artifacts(transport, project["id"], turn["id"], payload)
        state.update(
            {
                "turn_id": turn["id"],
                "session_id": session["id"],
                "session_external_id": external,
                "project_id": project["id"],
                "api": binding.api_url,
                "binding_id": binding.binding_id,
                "external_turn_id": external_turn,
                "prompt_event_id": prompt_delivery_id,
                "session_off_record": session_off_record,
            }
        )
        state.pop("spooled_active", None)
        full_compact = {
            "project": _project_context_for_model(context.get("project", {})),
            "operational_manual": context.get("operational_manual"),
            "plans": context.get("plans", []),
            "tasks": context.get("tasks", []),
            "task_counts": context.get("task_counts", {}),
            "task_indexing_pending": bool(context.get("task_indexing_pending")),
            "task_index_status": context.get("task_index_status", "ready"),
            "recent_handoffs": context.get("recent_handoffs", []),
            "memories": context.get("memories", []),
            "retrieval": {
                "status": (context.get("retrieval") or {}).get("status"),
                "metadata": {
                    "selection_mode": ((context.get("retrieval") or {}).get("metadata") or {}).get(
                        "selection_mode"
                    ),
                    "selected_reasons": (
                        (context.get("retrieval") or {}).get("metadata") or {}
                    ).get("selected_reasons", {}),
                },
            },
            "memory_status": context.get("memory_status", {}),
            "dashboard_url": binding.dashboard_link("tasks"),
        }
        baseline = _delivery_baseline(
            state,
            binding_id=binding.binding_id,
            external_session_id=external,
        )
        compact = deepcopy(full_compact)
        current_foundation_hash = foundation_context_hash(full_compact)
        current_work_hash = work_context_hash(full_compact)
        if baseline is None:
            foundation_changed = True
            work_changed = True
            delivery_kind = "snapshot"
            delivery_reason = "session_unknown" if external == "default" else "state_missing"
            standing_contracts = COFOUNDER_CONTRACT + TASK_CONTRACT
        else:
            foundation_changed = baseline["foundation_hash"] != current_foundation_hash
            work_changed = baseline["work_hash"] != current_work_hash
            delivery_kind = "delta"
            delivery_reason = _context_delivery_reason(
                foundation_changed=foundation_changed,
                work_changed=work_changed,
            )
            if not foundation_changed:
                compact.pop("project", None)
                compact.pop("operational_manual", None)
            if not work_changed:
                _filter_undelivered_prompt_work(
                    compact,
                    prompt=prompt,
                    baseline=baseline,
                )
            standing_contracts = (
                COFOUNDER_CONTRACT + TASK_CONTRACT if foundation_changed else ""
            )
        setup_notice = ""
        if memory_status.get("state") != "connection_required":
            clear_setup_notice(str(project["id"]), client)
        memory_notice = _memory_notice(context, remote=binding.remote)
        composed = compose_founder_context(
            compact,
            contracts=TURN_CONTRACT,
            standing_contracts=standing_contracts,
            prompt=prompt,
            setup_notice=setup_notice,
            memory_notice=memory_notice,
            session_id=session["id"],
            session_off_record=bool(session.get("off_record")),
        )
        turn_context = composed.content
        reused = _context_reuse_measurement(
            baseline=baseline,
            foundation_changed=foundation_changed,
            work_changed=work_changed,
        )
        if composed.fallback_used:
            delivery_kind = "fallback"
            delivery_reason = "composition_failed"
            foundation_changed = False
            work_changed = False
            reused = None
        try:
            # Persist the active turn before handing control back to the client.
            # The delivery baseline is deliberately excluded until stdout has
            # been produced and flushed, preventing false in-session reuse.
            merge_state_update(path, state_before, state)
            state = read_state(path)
            state_before = deepcopy(state)
        except Exception:
            pass
        emit(turn_context, "UserPromptSubmit")
        try:
            _store_delivery_baseline(
                state,
                context=full_compact,
                composed=composed,
                binding_id=binding.binding_id,
                previous=baseline,
                replace_foundation=baseline is None or foundation_changed,
                replace_work=baseline is None or work_changed,
            )
            stage_context_observation(
                state,
                measured_context(
                    turn_context,
                    event_id=automatic_context_event_id(
                        "context.turn_injection",
                        session_id=session["id"],
                        turn_id=turn["id"],
                        content=turn_context,
                        occurrence_id=prompt_delivery_id,
                    ),
                    operation="context.turn_injection",
                    scope="automatic",
                    client=client,
                    session_id=session["id"],
                    turn_id=turn["id"],
                    retrieval_run_id=(
                        (context.get("retrieval") or {}).get("retrieval_run_id")
                        or turn.get("retrieval_run_id")
                    ),
                    composed=composed,
                    delivery=_delivery_observation(
                        kind=delivery_kind,
                        reason=delivery_reason,
                        foundation_changed=foundation_changed,
                        work_changed=work_changed,
                        reused=reused,
                    ),
                ),
            )
            merge_state_update(path, state_before, state)
        except Exception:
            # The context has already been delivered. Missing private state can
            # only force a future full snapshot; it must never fail this turn.
            pass
    except HookEmissionError:
        raise
    except Exception as error:
        fallback_delivery_reason = "memory_unavailable"
        prompt_rejected = (
            isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409
        )
        if prompt_rejected:
            fallback_composed = compose_unavailable_context(
                "dDuo rejected a late or conflicting prompt fragment for a turn that is already "
                "closed. Continue normally in a new turn; this fragment was not queued."
            )
            fallback_context = fallback_composed.content
        upgrade_composed = client_upgrade_notice(error)
        if upgrade_composed is not None:
            fallback_composed = upgrade_composed
            fallback_context = upgrade_composed.content
            fallback_delivery_reason = "client_upgrade_required"
        fallback_observation: dict | None = None
        fallback_observation_path: Path | None = None
        prompt_spooled = False
        spool_persisted = False
        try:
            root = project_root(payload)
            project = load_project(root)
            fallback_binding = _binding_for_project(root, project, require_approval=False)
            path = state_path(project["id"], client, external)
            state = read_state(path)
            state_before = deepcopy(state)
            stage_codex_transcript_cursor(
                payload,
                state,
                external_session=external,
                external_turn=external_turn,
            )
            cache_binding = binding
            if cache_binding is None:
                try:
                    cache_binding = _binding_for_project(root, project)
                except Exception:
                    cache_binding = None
            has_live_baseline = (
                _delivery_baseline(
                    state,
                    binding_id=fallback_binding.binding_id,
                    external_session_id=external,
                )
                is not None
            )
            allow_remote_recovery = upgrade_composed is None and remote_manual_cache_allowed(error)
            cached_composed = (
                compose_remote_offline_context(
                    cache_binding,
                    contracts=TURN_CONTRACT,
                    standing_contracts=(
                        "" if has_live_baseline else COFOUNDER_CONTRACT + TASK_CONTRACT
                    ),
                    prompt=prompt,
                    include_manual=not has_live_baseline,
                )
                if allow_remote_recovery
                else None
            )
            if cached_composed is not None:
                fallback_composed = cached_composed
                fallback_context = cached_composed.content
                fallback_delivery_reason = "remote_offline"
            if not prompt_rejected:
                state.pop("turn_id", None)
            internal_session_id = internal_session_id or (
                str(state.get("session_id") or "") or None
            )
            if not prompt_rejected and (not fallback_binding.remote or allow_remote_recovery):
                cached_off_record = state.get("session_off_record")
                captured_off_record = (
                    session_off_record
                    if session_off_record is not None
                    else cached_off_record
                    if isinstance(cached_off_record, bool)
                    else True
                )
                stage_spooled_prompt(
                    state,
                    session_external_id=external,
                    external_turn_id=external_turn,
                    prompt_event_id=prompt_delivery_id,
                    user_prompt=prompt,
                    binding_id=fallback_binding.binding_id,
                    off_record=captured_off_record,
                )
                prompt_spooled = True
            elif not prompt_rejected:
                state.pop("spooled_active", None)
            # A rejected late/conflicting fragment was never delivered as dDuo
            # context for an open turn. Do not leave an orphan observation that
            # a later Stop could attach to another turn.
            if not prompt_rejected:
                fallback_observation = measured_context(
                    fallback_context,
                    event_id=automatic_context_event_id(
                        "context.turn_injection",
                        session_id=external,
                        turn_id=internal_turn_id or external_turn,
                        render_version=HOOK_FALLBACK_RENDER_VERSION,
                        content=fallback_context,
                        occurrence_id=prompt_delivery_id,
                    ),
                    operation="context.turn_injection",
                    scope="automatic",
                    client=client,
                    session_id=internal_session_id,
                    turn_id=internal_turn_id,
                    component_values={"health": fallback_context},
                    composed=fallback_composed,
                    delivery=_delivery_observation(
                        kind="fallback",
                        reason=fallback_delivery_reason,
                    ),
                    render_version=HOOK_FALLBACK_RENDER_VERSION,
                )
                fallback_observation_path = path
            merge_state_update(path, state_before, state)
            spool_persisted = prompt_spooled
        except Exception:
            pass
        if not spool_persisted and fallback_context == compose_unavailable_context(fallback_message).content:
            fallback_context = compose_unavailable_context(
                "dDuo memory is unavailable for this turn. Continue normally. "
                "Local recovery could not be confirmed; this turn may not be saved in memory."
            ).content
            # Do not attribute the abandoned, un-emitted fallback to this turn.
            fallback_observation = None
        emit(
            fallback_context,
            "UserPromptSubmit",
            warn=True,
        )
        if fallback_observation is not None and fallback_observation_path is not None:
            try:
                state = read_state(fallback_observation_path)
                state_before = deepcopy(state)
                stage_context_observation(state, fallback_observation)
                merge_state_update(fallback_observation_path, state_before, state)
            except Exception:
                # The fallback was delivered; telemetry remains best effort.
                pass


def stop() -> None:
    payload = read_input()
    client = client_name(payload)
    assistant_response = str(
        payload.get("last_assistant_message") or payload.get("assistant_response") or ""
    )
    try:
        root = project_root(payload)
        project = load_project(root)
        binding = _binding_for_project(root, project)
        path = state_path(project["id"], client, external_session_id(payload))
        state = read_state(path)
        state_before = deepcopy(state)
        if state.get("binding_id") not in {None, binding.binding_id}:
            raise RuntimeError("project binding changed while this hook session was open")
        declared_turn = payload.get("turn_id")
        active_spooled = state.get("spooled_active")
        active_external_turn = (
            state.get("external_turn_id")
            if state.get("turn_id")
            else active_spooled.get("external_turn_id")
            if isinstance(active_spooled, dict)
            else None
        )
        if (
            declared_turn is not None
            and isinstance(active_external_turn, str)
            and active_external_turn
            and str(declared_turn) != active_external_turn
        ):
            # A late Stop for turn A must never close a newer turn B that has
            # already become active in the same client session. The older
            # hook is safely obsolete; leave B and all of its observations
            # untouched and let its own Stop commit them.
            print(
                json.dumps(
                    {"suppressOutput": True} if client == "claude" else {"continue": True}
                )
            )
            return
        legacy = state.get("binding_id") is None and not binding.remote
        transport: str | ProjectHttpClient = (
            str(state.get("api") or binding.api_url)
            if legacy
            else _project_client(binding, "hook", external_session_id(payload))
        )
        turn_id = str(state.get("turn_id") or "")
        external_session = external_session_id(payload)
        telemetry_state = dict(state)
        telemetry_source = codex_telemetry_source(
            payload,
            telemetry_state,
            external_session=external_session,
        )
        if telemetry_source is not None:
            pending_sources = state.get("pending_codex_usage")
            pending_sources = (
                [dict(item) for item in pending_sources if isinstance(item, dict)]
                if isinstance(pending_sources, list)
                else []
            )
            source_id = telemetry_source["source_id"]
            if not any(item.get("source_id") == source_id for item in pending_sources):
                pending_sources.append(telemetry_source)
            state["pending_codex_usage"] = pending_sources
        # The cursor becomes a durable pending source in the same atomic state
        # merge that closes the memory turn. There is no crash window in which
        # memory is committed but the only telemetry recovery source is lost.
        state.pop("codex_transcript_cursor", None)
        pending_observations = state.get("pending_context_observations")
        observations = (
            [item for item in pending_observations if isinstance(item, dict)][:100]
            if isinstance(pending_observations, list)
            else []
        )
        if turn_id:
            body = {"assistant_response": assistant_response}
            if observations:
                body["context_observations"] = observations
            try:
                response = _request(
                    transport, "POST", f"/turns/{turn_id}/stop-check", json=body, timeout=10
                )
                response.raise_for_status()
                state.pop("turn_id", None)
            except Exception:
                queue_pending_commit(state, turn_id, body, None if legacy else binding.binding_id)
                state.pop("turn_id", None)
            state.pop("pending_context_observations", None)
        elif isinstance(state.get("spooled_active"), dict):
            item = dict(state.pop("spooled_active"))
            item["assistant_response"] = assistant_response
            if not legacy:
                item["binding_id"] = binding.binding_id
            if observations:
                item["context_observations"] = observations
            state["spooled_turns"] = [*_spooled_turns(state), item]
            state.pop("pending_context_observations", None)
        merge_state_update(path, state_before, state)
        # Only after memory and the retry source are safe do we parse usage.
        # Normal work is enqueued first so this single upload includes it. If a
        # full spool blocks a source, the same single upload frees space and a
        # second local-only drain stages the source for the next delivery.
        try:
            delivery_state = read_state(path)
            delivery_before = deepcopy(delivery_state)
            _, capacity_blocked = drain_pending_codex_usage(
                delivery_state,
                project_id=str(project["id"]),
            )
            flush_client_telemetry(transport, str(project["id"]))
            if capacity_blocked:
                drain_pending_codex_usage(
                    delivery_state,
                    project_id=str(project["id"]),
                )
            merge_state_update(path, delivery_before, delivery_state)
        except Exception:
            pass
        print(json.dumps({"suppressOutput": True} if client == "claude" else {"continue": True}))
    except Exception:
        print(json.dumps({"suppressOutput": True} if client == "claude" else {"continue": True}))
