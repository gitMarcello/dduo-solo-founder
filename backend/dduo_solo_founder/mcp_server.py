from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import json
import mimetypes
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import anyio
import httpx
import mcp.types as mcp_types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, Field

from dduo_solo_founder import __version__
from dduo_solo_founder.client_binding import ProjectBinding, binding_from_project
from dduo_solo_founder.client_http import ProjectHttpClient
from dduo_solo_founder.client_support import (
    UnsupportedClientError,
    require_supported_client,
)
from dduo_solo_founder.manual_cache import load_verified_manual, store_verified_manual
from dduo_solo_founder.memory_connection import MemoryConnectionChecks
from dduo_solo_founder.connection_health import (
    CONFIGURATION_CHOICE_INSTRUCTION,
    combine_connection_checks,
    sleep_connection_notice,
)
from dduo_solo_founder.observability import ESTIMATOR_VERSION, estimated_tokens_for_text
from dduo_solo_founder.operating_contract import HUMAN_WORK_RESPONSE_INSTRUCTION
from dduo_solo_founder.launcher import get_setup_status as read_setup_status
from dduo_solo_founder.launcher import open_setup
from dduo_solo_founder.project_config import (
    dashboard_item_url,
    find_workspace_root,
    load_project,
    portable_file_lock,
    validate_project_registration,
)
from dduo_solo_founder.schemas import (
    ArtifactCreate,
    MemoryForget,
    PlanCreate,
    PlanUpdate,
    ProjectUpdate,
    SessionPrivacyUpdate,
    SleepRequest,
    SprintArchive,
    SprintCreate,
    SprintHistoryCreate,
    SprintTransition,
    SprintUpdate,
    TaskCreate,
    TaskKind,
    TaskPlacement,
    TaskStatus,
    TaskUpdate,
)

MCP_OBSERVABILITY_DIR = Path.home() / ".config" / "dduo-solo-founder" / "mcp-observability"
MCP_CONTEXT_RENDER_VERSION = "mcp-result-v3"
MCP_CLIENT_ENV = "DDUO_SOLO_FOUNDER_CLIENT"
MCP_PROJECT_ROOT_ENV = "DDUO_SOLO_FOUNDER_PROJECT_ROOT"
MCP_WORKSPACE_ARGUMENT = "workspace_root"
MCP_WARNING_ACK_ARGUMENT = "memory_warning_ack"
_MEMORY_CONNECTION_CHECKS = MemoryConnectionChecks()
_CONNECTION_EXEMPT_TOOLS = {
    "check_memory_connection", "check_setup", "open_setup", "decline_setup", "health",
    "get_memory_status", "list_sleep_jobs",
    # Privacy requests must never wait for a memory-health choice.
    "set_off_record",
}

_ACTIVE_MCP_CLIENT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dduo_active_mcp_client",
    default=None,
)


class MCPContextError(ValueError):
    """An MCP request cannot be bound to one supported client and project."""


@dataclass(frozen=True)
class MCPToolRuntime:
    """One immutable, per-call binding resolved from the adapter context."""

    client: str
    project_root: Path
    project_id: str | None
    api: "ApiEndpoint"
    dashboard_url: str | None
    plans_dashboard_url: str | None
    binding: ProjectBinding | None


class EmptyInput(BaseModel):
    pass


class SetupCheckInput(BaseModel):
    provider: Literal["claude", "codex"] | None = None


class SearchInput(BaseModel):
    query: str
    limit: int = 8


class TaskListInput(BaseModel):
    detail: Literal["compact", "full"] = "compact"
    scope: Literal["active", "completed", "all"] = "active"
    status: TaskStatus | None = None
    kind: TaskKind | None = None
    epic_id: str | None = None
    sprint_id: str | None = None
    placement: TaskPlacement = "all"
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    q: str | None = Field(default=None, max_length=1_000)
    label: str | None = None
    known_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class TaskGetInput(BaseModel):
    task_id: str
    detail: Literal["compact", "working", "full"] = "working"
    known_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class TaskSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    scope: Literal["active", "completed", "all"] = "active"
    limit: int = Field(default=8, ge=1, le=100)
    cursor: str | None = None
    status: TaskStatus | None = None
    kind: TaskKind | None = None
    epic_id: str | None = None
    sprint_id: str | None = None
    placement: TaskPlacement = "all"
    label: str | None = None


class SprintListInput(BaseModel):
    status: Literal["planned", "active", "archived"] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class SprintGetInput(BaseModel):
    sprint_id: str


class SprintHistoryInput(SprintGetInput):
    closure_version: int | None = Field(default=None, ge=1)
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    q: str | None = Field(default=None, max_length=1_000)


class SprintUpdateInput(SprintUpdate):
    sprint_id: str


class SprintTransitionInput(SprintTransition):
    sprint_id: str


class SprintArchiveInput(SprintArchive):
    sprint_id: str


class PlanListInput(BaseModel):
    detail: Literal["compact", "full"] = "compact"
    status: Literal["draft", "decided", "executing", "completed", "superseded"] | None = None
    q: str | None = Field(default=None, max_length=1_000)
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class TaskUpdateInput(TaskUpdate):
    task_id: str
    expected_version: int = Field(ge=1)


class PlanUpdateInput(PlanUpdate):
    plan_id: str
    expected_version: int = Field(ge=1)


class ProjectProfileUpdateInput(ProjectUpdate):
    expected_version: int = Field(ge=1)


class ActivityInput(BaseModel):
    limit: int = 100


class MemoryInput(BaseModel):
    memory_id: str


class RetrySleepInput(BaseModel):
    job_id: str


class ActivateTaskInput(BaseModel):
    task_id: str
    known_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class ActivatePlanInput(BaseModel):
    plan_id: str


class OperationalManualUpdateInput(BaseModel):
    content: str = Field(max_length=100_000)
    expected_version: int = Field(ge=0)


class SetOffRecordInput(SessionPrivacyUpdate):
    session_id: str


class ArtifactInput(BaseModel):
    path: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    kind: str = "file"
    filename: str = ""
    mime_type: str = "application/octet-stream"
    source_uri: str = ""
    content_base64: str | None = None
    summary: str = ""
    extracted_text: str = ""
    metadata: dict = Field(default_factory=dict)


TOOL_MODELS = {
    "health": ("Check dDuo Solo Founder health", EmptyInput),
    "decline_setup": (
        "Remember the user's explicit choice not to activate memory for this unconfigured folder",
        EmptyInput,
    ),
    "open_setup": (
        "Open local Setup only for a local binding; for a remote binding return safe remote-access guidance without starting Docker",
        EmptyInput,
    ),
    "check_setup": (
        "Verify local Setup or the configured remote memory binding; return only the next safe action",
        SetupCheckInput,
    ),
    "check_memory_connection": (
        "Check local automatic-memory hook authorization before project work in each chat, "
        "and recheck after the user fixes permission; does not start services or repair anything",
        EmptyInput,
    ),
    "get_project_briefing": (
        "Refresh purpose, objectives, active work and onboarding status only when the automatic Founder Brief is absent, degraded, explicitly requested, or stale; otherwise this duplicates current context",
        EmptyInput,
    ),
    "get_project_manual": (
        "Load the complete project operating manual before proposing or publishing a durable rule",
        EmptyInput,
    ),
    "update_project_manual": (
        "Publish a confirmed operating-manual revision; call get_project_manual first and use its version. Only use after the authorized user directly requested the update or explicitly approved the proposed durable rule",
        OperationalManualUpdateInput,
    ),
    "search_memory": ("Search consolidated project memory", SearchInput),
    "get_memory_status": ("Inspect sleep, retrieval, and memory availability", EmptyInput),
    "request_sleep": (
        "Queue pending turns for consolidation after a topic boundary or when the user asks",
        SleepRequest,
    ),
    "list_sleep_jobs": ("List memory consolidation jobs and failures", ActivityInput),
    "retry_sleep_job": ("Retry a waiting memory consolidation job", RetrySleepInput),
    "explain_memory": ("Show a memory's complete revision and source provenance", MemoryInput),
    "list_memory_revisions": ("List every revision of one memory", MemoryInput),
    "forget_memory": ("Remove a memory group from active recall", MemoryForget),
    "set_off_record": ("Enable or disable memory capture for one session", SetOffRecordInput),
    "rebuild_vector_index": ("Rebuild Qdrant from authoritative PostgreSQL memory", EmptyInput),
    "register_artifact": (
        "Attach a safe project file, URL, or summary to a turn, task, or plan",
        ArtifactInput,
    ),
    "list_artifacts": ("List project artifacts available as memory sources", ActivityInput),
    "list_tasks": (
        "List project tasks compactly by default; request full only when all task content is needed",
        TaskListInput,
    ),
    "get_task": (
        "Load one identified task directly; reuse an in-context result, and pass its snapshot hash only for a genuine reread while that content is still available",
        TaskGetInput,
    ),
    "search_tasks": (
        "Resolve an ambiguous task once by exact identity or semantic meaning; after selecting an ID, call get_task directly and do not repeat the same search in the current context",
        TaskSearchInput,
    ),
    "list_plans": ("List paginated project plans and their linked work", PlanListInput),
    "get_plan": ("Load one plan by exact ID, including archived/completed plans", ActivatePlanInput),
    "list_sprints": ("List project sprint windows, independently of task execution status", SprintListInput),
    "get_sprint": ("Load one sprint by exact ID", SprintGetInput),
    "create_sprint": ("Create a planned sprint without changing task status", SprintCreate),
    "update_sprint": ("Edit a planned or active sprint using its latest version", SprintUpdateInput),
    "start_sprint": ("Start a planned sprint; each project has at most one active sprint", SprintTransitionInput),
    "preview_sprint_close": ("Read counts and current version before explicitly choosing carryover destination", SprintGetInput),
    "archive_sprint": ("Atomically archive a sprint with immutable outcomes and explicit unfinished-work destination", SprintArchiveInput),
    "reopen_sprint": ("Reopen an archived sprint as planned while retaining closure history", SprintTransitionInput),
    "list_sprint_history": ("Read paginated immutable task snapshots for a sprint closure", SprintHistoryInput),
    "create_historical_sprint": ("Explicitly group selected completed unsprinted tasks in a historical sprint", SprintHistoryCreate),
    "activate_task_context": (
        "Load an existing task as the working context for this turn",
        ActivateTaskInput,
    ),
    "activate_plan_context": (
        "Load an existing plan as the planning context for this turn",
        ActivatePlanInput,
    ),
    "create_task": ("Create a project task and return its dashboard handoff", TaskCreate),
    "update_task": (
        "Update or complete a task using the version from its latest context; on conflict reload it once",
        TaskUpdateInput,
    ),
    "create_plan": ("Create a versioned project plan and return its dashboard handoff", PlanCreate),
    "update_plan": (
        "Update a project plan using its latest version; on conflict reload it once",
        PlanUpdateInput,
    ),
    "get_activity": ("Get the project activity log", ActivityInput),
    "update_project_profile": (
        "Update cause, principles, objectives or initial context",
        ProjectProfileUpdateInput,
    ),
}


class ApiEndpoint(str):
    """String-compatible endpoint carrying its authenticated transport."""

    client: ProjectHttpClient | None
    binding_id: str | None

    def __new__(
        cls,
        value: str,
        client: ProjectHttpClient | None = None,
        binding_id: str | None = None,
    ):
        instance = str.__new__(cls, value)
        instance.client = client
        instance.binding_id = binding_id
        return instance


def _observability_queue_path(project_id: str, binding_id: str | None = None) -> Path:
    if binding_id:
        scope = hashlib.sha256(f"{project_id}\0{binding_id}".encode("utf-8")).hexdigest()
        return MCP_OBSERVABILITY_DIR / f"{scope}.json"
    return MCP_OBSERVABILITY_DIR / f"{project_id}.json"


def _observability_credential_scope(api: str) -> str | None:
    """Isolate a remote spool without persisting its bearer credential."""
    client = getattr(api, "client", None)
    if not isinstance(client, ProjectHttpClient) or not client.binding.remote:
        return None
    token = client.binding.bearer_token
    if not token:
        return "remote-credential-unavailable"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_observability_queue(project_id: str, binding_id: str | None = None) -> list[dict]:
    try:
        payload = json.loads(
            _observability_queue_path(project_id, binding_id).read_text(encoding="utf-8")
        )
        return (
            [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, list)
            else []
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []


def _write_observability_queue(
    project_id: str, items: list[dict], binding_id: str | None = None
) -> None:
    path = _observability_queue_path(project_id, binding_id)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(items[-100:]), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass


def flush_observability_queue(
    api: str,
    project_id: str,
    pending: list[dict] | None = None,
    binding_id: str | None = None,
) -> None:
    binding_id = binding_id or getattr(api, "binding_id", None)
    credential_scope = _observability_credential_scope(api)
    try:
        queue_path = _observability_queue_path(project_id, binding_id)
        with portable_file_lock(queue_path, timeout=3):
            queued = [
                item
                for item in _read_observability_queue(project_id, binding_id)
                if item.get("_credential_scope") == credential_scope
            ]
            new_items = [dict(item) for item in (pending or [])]
            if credential_scope is not None:
                new_items = [
                    {**item, "_credential_scope": credential_scope} for item in new_items
                ]
            items = [*queued, *new_items][-100:]
            if binding_id:
                items = [{**item, "binding_id": binding_id} for item in items]
            if not items:
                queue_path.unlink(missing_ok=True)
                return
            try:
                wire_items = [
                    {key: value for key, value in item.items() if key != "_credential_scope"}
                    for item in items
                ]
                response = _raw_response(
                    api,
                    "POST",
                    f"/projects/{project_id}/observability/context-events/batch",
                    json={"items": wire_items},
                    timeout=2,
                )
                response.raise_for_status()
            except Exception:
                _write_observability_queue(project_id, items, binding_id)
                return
            queue_path.unlink(missing_ok=True)
    except Exception:
        # Telemetry must never interrupt an MCP tool. A contended lock means
        # another process is already preserving this project's queue.
        pass


def _task_observability_references(value: object, tool_name: str) -> list[str]:
    """Describe task payloads without adding bytes outside the exact MCP result."""
    default_detail = {
        "list_tasks": "compact",
        "get_task": "working",
        "search_tasks": "compact",
        "activate_task_context": "working",
        "create_task": "compact",
        "update_task": "compact",
    }.get(tool_name)
    if default_detail is None or not isinstance(value, dict):
        return []
    detail = value.get("detail")
    if detail not in {"compact", "working", "full"}:
        detail = default_detail
    if tool_name in {"list_tasks", "search_tasks"}:
        candidates = value.get("items", [])
    elif "task" in value:
        candidates = [value.get("task")]
    else:
        candidates = [value]
    references: list[str] = []
    for task in candidates if isinstance(candidates, list) else []:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        version = task.get("version")
        if not isinstance(task_id, str) or not task_id or not isinstance(version, int):
            continue
        reference = f"task:{task_id}@v{version}:{detail}"
        if len(reference) <= 200 and reference not in references:
            references.append(reference)
    return references[:1_000]


def result(
    value: object,
    *,
    api: str | None = None,
    project_id: str | None = None,
    tool_name: str | None = None,
) -> dict:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if api and project_id and tool_name:
        try:
            estimated_tokens, characters, utf8_bytes = estimated_tokens_for_text(text)
            encoded = text.encode("utf-8")
            references = _task_observability_references(value, tool_name)
            flush_observability_queue(
                api,
                project_id,
                [
                    {
                        "event_id": f"mcp-{uuid.uuid4()}",
                        "operation": "context.mcp_tool_result",
                        "scope": "requested",
                        "client": current_setup_provider() or "other",
                        "characters": characters,
                        "utf8_bytes": utf8_bytes,
                        "estimated_tokens": estimated_tokens,
                        "estimator_version": ESTIMATOR_VERSION,
                        "component_bytes": {"result": utf8_bytes},
                        "components": [
                            {
                                "name": "result",
                                "utf8_bytes": utf8_bytes,
                                "estimated_tokens": estimated_tokens,
                                "item_count": (
                                    len(references)
                                    if tool_name
                                    in {
                                        "list_tasks",
                                        "get_task",
                                        "search_tasks",
                                        "activate_task_context",
                                        "create_task",
                                        "update_task",
                                    }
                                    else 1
                                ),
                                "references": references,
                            }
                        ],
                        "content": text,
                        "content_sha256": hashlib.sha256(encoded).hexdigest(),
                        "producer_version": __version__,
                        "render_version": MCP_CONTEXT_RENDER_VERSION,
                        "tool_name": tool_name,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            )
        except Exception:
            pass
    return {"content": [{"type": "text", "text": text}]}


def _raw_response(api: str, method: str, path: str, **kwargs):
    client = getattr(api, "client", None)
    if isinstance(client, ProjectHttpClient):
        return client.request(method, path, **kwargs)
    url = str(api).rstrip("/") + "/" + path.lstrip("/")
    caller = getattr(httpx, method.lower(), httpx.request)
    if caller is httpx.request:
        return caller(method, url, **kwargs)
    return caller(url, **kwargs)


def request(api: str, method: str, path: str, body=None):
    if isinstance(getattr(api, "client", None), ProjectHttpClient):
        response = _raw_response(api, method, path, json=body, timeout=30)
    else:
        response = httpx.request(
            method,
            str(api).rstrip("/") + "/" + path.lstrip("/"),
            json=body,
            timeout=30,
        )
    response.raise_for_status()
    return response.json()


def _path_with_query(path: str, params: dict[str, object | None]) -> str:
    query = urlencode(
        [(key, str(value)) for key, value in params.items() if value is not None]
    )
    return f"{path}?{query}" if query else path


def _work_item_url(
    dashboard_url: str | None,
    *,
    work_id: str | None = None,
    plan_id: str | None = None,
) -> str | None:
    if dashboard_url is None:
        return None
    try:
        return dashboard_item_url(dashboard_url, work_id=work_id, plan_id=plan_id)
    except ValueError:
        # A missing dashboard must never make the underlying Work operation fail.
        return None


def _decorate_work_item(
    item: dict,
    dashboard_url: str | None,
    *,
    plan: bool = False,
) -> dict:
    item_id = str(item.get("id") or "").strip()
    url = _work_item_url(
        dashboard_url,
        plan_id=item_id if plan else None,
        work_id=None if plan else item_id,
    ) if item_id else None
    return {**item, **({"url": url} if url else {})}


def _decorate_work_collection(
    payload: dict,
    dashboard_url: str | None,
    *,
    plan: bool = False,
) -> dict:
    result = dict(payload)
    items = result.get("items")
    if isinstance(items, list):
        result["items"] = [
            _decorate_work_item(item, dashboard_url, plan=plan)
            if isinstance(item, dict)
            else item
            for item in items
        ]
    if dashboard_url is not None:
        result["dashboard_url"] = dashboard_url
    result["response_instruction"] = HUMAN_WORK_RESPONSE_INSTRUCTION
    return result


def _decorate_single_work_result(
    payload: dict,
    dashboard_url: str | None,
    *,
    item_id: str,
    key: str,
    plan: bool = False,
    changed: bool = False,
) -> dict:
    result = dict(payload)
    item = result.get(key)
    if isinstance(item, dict):
        result[key] = _decorate_work_item(item, dashboard_url, plan=plan)
    exact_url = _work_item_url(
        dashboard_url,
        plan_id=item_id if plan else None,
        work_id=None if plan else item_id,
    )
    if exact_url:
        result["item_url"] = exact_url
    if dashboard_url is not None:
        result["dashboard_url"] = dashboard_url
    prefix = "Briefly summarize the confirmed changes. " if changed else ""
    result["response_instruction"] = prefix + HUMAN_WORK_RESPONSE_INSTRUCTION
    return result


def task_mutation_result(task: dict, dashboard_url: str | None) -> dict:
    """Attach one human, item-specific handoff after a task mutation."""
    return _decorate_single_work_result(
        {"task": task},
        dashboard_url,
        item_id=str(task.get("id") or ""),
        key="task",
        changed=True,
    )


def plan_mutation_result(plan: dict, dashboard_url: str | None) -> dict:
    """Attach one human, item-specific handoff after a plan mutation."""
    return _decorate_single_work_result(
        {"plan": plan},
        dashboard_url,
        item_id=str(plan.get("id") or ""),
        key="plan",
        plan=True,
        changed=True,
    )


def briefing_result(
    payload: dict,
    dashboard_url: str | None,
    plans_dashboard_url: str | None,
) -> dict:
    """Keep the technical identity private while exposing exact human Work links."""
    result = dict(payload)
    tasks = result.get("tasks")
    if isinstance(tasks, list):
        result["tasks"] = [
            _decorate_work_item(item, dashboard_url) if isinstance(item, dict) else item
            for item in tasks
        ]
    plans = result.get("plans")
    if isinstance(plans, list):
        result["plans"] = [
            _decorate_work_item(item, plans_dashboard_url, plan=True)
            if isinstance(item, dict)
            else item
            for item in plans
        ]
    if dashboard_url is not None:
        result["dashboard_url"] = dashboard_url
    result["response_instruction"] = HUMAN_WORK_RESPONSE_INSTRUCTION
    return result


def manual_result(
    payload: dict,
    dashboard_url: str | None,
    *,
    changed: bool = False,
) -> dict:
    result = dict(payload)
    if dashboard_url is not None:
        result["dashboard_url"] = dashboard_url
    if changed:
        result["response_instruction"] = (
            "Briefly state which durable project rules changed, then link `Operational manual` "
            "to dashboard_url when available. Do not expose internal identifiers."
        )
    elif payload.get("idempotent") is True:
        result["response_instruction"] = (
            "No manual revision was created. Treat the returned manual as current and authoritative; "
            "do not claim that the requested text was published. If it differs from the request, a "
            "later revision superseded that replay. Link `Operational manual` to dashboard_url when available."
        )
    return result


def current_setup_provider() -> str | None:
    """Return the request-scoped client, with an explicit adapter-only fallback."""
    active = _ACTIVE_MCP_CLIENT.get()
    if active:
        return active
    declared = os.getenv(MCP_CLIENT_ENV)
    if not declared:
        return None
    try:
        # Without MCP clientInfo this is useful only to direct local Setup. It
        # never authorizes an MCP tool request; the request handler does that.
        return require_supported_client(declared)
    except UnsupportedClientError:
        return None


def _mcp_client_name(context: ServerRequestContext[Any, Any]) -> str | None:
    params = context.session.client_params
    client_info = params.client_info if params is not None else None
    return client_info.name if client_info is not None else None


def _supported_request_client(context: ServerRequestContext[Any, Any]) -> str:
    """Authorize only a real Codex/Claude MCP client for this request."""
    declared = os.getenv(MCP_CLIENT_ENV) if MCP_CLIENT_ENV in os.environ else None
    return require_supported_client(
        _mcp_client_name(context),
        declared_client=declared,
    )


def _canonical_adapter_root(value: object, *, source: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MCPContextError(f"{source} must be a non-empty absolute project directory")
    if "\x00" in value:
        raise MCPContextError(f"{source} is invalid")
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        raise MCPContextError(f"{source} must be an absolute project directory")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MCPContextError(f"{source} does not identify an available project directory") from exc
    if not resolved.is_dir():
        raise MCPContextError(f"{source} must identify a project directory")
    discovered = find_workspace_root(resolved).resolve()
    if discovered != resolved:
        raise MCPContextError(
            f"{source} must identify the project root, not a parent or nested directory"
        )
    return resolved


def _request_project_root(arguments: dict[str, object]) -> Path:
    """Resolve one root from this call; never use cwd or a previous request."""
    argument_present = MCP_WORKSPACE_ARGUMENT in arguments
    argument_value = arguments.pop(MCP_WORKSPACE_ARGUMENT, None)
    environment_present = MCP_PROJECT_ROOT_ENV in os.environ
    environment_value = os.environ.get(MCP_PROJECT_ROOT_ENV)

    argument_root = (
        _canonical_adapter_root(argument_value, source=MCP_WORKSPACE_ARGUMENT)
        if argument_present
        else None
    )
    environment_root = (
        _canonical_adapter_root(environment_value, source=MCP_PROJECT_ROOT_ENV)
        if environment_present
        else None
    )
    if argument_root is not None and environment_root is not None:
        if argument_root != environment_root:
            raise MCPContextError(
                "workspace_root does not match the project root supplied by the client adapter"
            )
    selected = environment_root or argument_root
    if selected is None:
        raise MCPContextError(
            "workspace_root is required when the client adapter does not supply a project root"
        )
    return selected


def _resolve_tool_runtime(client: str, arguments: dict[str, object]) -> MCPToolRuntime:
    """Reload and validate the exact project binding for one tool call."""
    project_root = _request_project_root(arguments)
    try:
        config = load_project(project_root)
    except FileNotFoundError:
        config = None

    binding: ProjectBinding | None = None
    if config is not None:
        local = str(config.get("binding") or "local").strip().lower() == "local"
        if local:
            validate_project_registration(str(config.get("id") or ""), project_root)
        try:
            candidate = binding_from_project(
                project_root,
                config,
                enforce_project_claim=False,
            )
        except Exception:
            # Setup remains available for incomplete configurations. Project
            # tools will fail closed because no project id or endpoint exists.
            candidate = None
        if candidate is not None:
            if candidate.root_path.resolve() != project_root:
                raise MCPContextError(
                    "the resolved project binding does not match the client adapter root"
                )
            binding = candidate

    if binding is None:
        api = ApiEndpoint("")
        return MCPToolRuntime(client, project_root, None, api, None, None, None)

    http_client = ProjectHttpClient(binding, component="mcp")
    api = ApiEndpoint(binding.api_url, http_client, binding.binding_id)
    return MCPToolRuntime(
        client=client,
        project_root=project_root,
        project_id=binding.project_id,
        api=api,
        dashboard_url=binding.dashboard_link("tasks"),
        plans_dashboard_url=binding.dashboard_link("tasks", "plans"),
        binding=binding,
    )


def _setup_check(status: dict, provider: str | None) -> dict:
    """Turn private host-agent state into the one action a founder must take next."""
    remaining: list[dict[str, str]] = []
    health = status.get("memory_status")
    executor = health.get("executor_provider") if isinstance(health, dict) else None
    login_provider = executor if executor in {"codex", "claude"} else provider

    if not bool((status.get("docker") or {}).get("ready")):
        remaining.append(
            {
                "id": "docker",
                "title": "Start Docker Desktop",
                "detail": "Start Docker Desktop from Setup and wait until it shows Connected.",
            }
        )
    if not bool((status.get("embeddings") or {}).get("ready")):
        remaining.append(
            {
                "id": "embeddings",
                "title": "Add the embeddings key",
                "detail": "Save the OpenAI embeddings key in Setup.",
            }
        )

    if login_provider:
        client = (status.get("clients") or {}).get(login_provider) or {}
        if not bool(client.get("ready")):
            waiting = client.get("setup_state") == "waiting"
            connect_action = "Reconnect" if client.get("reason") == "auth_required" else "Connect"
            remaining.append(
                {
                    "id": f"{login_provider}_login",
                    "title": f"{connect_action} {login_provider.title()}",
                    "detail": (
                        f"Complete the official {login_provider.title()} sign-in already open in Setup."
                        if waiting
                        else f"Select {connect_action} for {login_provider.title()} in Setup and complete the official sign-in."
                    ),
                }
            )
        if provider == "codex" and not bool((status.get("codex_hooks") or {}).get("ready")):
            hook_reason = str((status.get("codex_hooks") or {}).get("reason") or "")
            repair_required = hook_reason in {
                "hooks_missing",
                "hooks_incomplete",
                "hooks_disabled",
                "check_failed",
            }
            remaining.append(
                {
                    "id": "codex_hooks",
                    "title": (
                        "Repair the Codex plugin"
                        if repair_required
                        else "Approve Codex lifecycle permission"
                    ),
                    "detail": (
                        "Update or reinstall dDuo Solo Founder, fully restart Codex, then "
                        "open Setup again."
                        if repair_required
                        else "Open Codex Settings > Hooks, select dDuo Solo Founder, then choose "
                        "Review and Trust all. This approval cannot be completed from the chat prompt."
                    ),
                }
            )

    if not bool((status.get("project") or {}).get("ready")):
        remaining.append(
            {
                "id": "project",
                "title": "Activate the project",
                "detail": "Select Activate project in Setup.",
            }
        )

    issue = sleep_connection_notice(health if isinstance(health, dict) else {"state": "unknown"})
    if issue is not None and bool((status.get("project") or {}).get("ready")):
        remaining.append({"id": issue["reason"], "title": "Check project memory", "detail": issue["message"] + " " + issue["next_action"]})
    ready = not remaining
    primary_issue = issue is not None and bool(remaining) and remaining[0]["id"] == issue["reason"]
    return {
        "ready": ready,
        "requires_choice": issue["requires_choice"] if primary_issue else not ready,
        "memory_state": health.get("state") if isinstance(health, dict) else None,
        "provider": provider,
        "next_action": remaining[0] if remaining else None,
        "remaining_actions": remaining,
        "response_instruction": (
            "Setup is verified. If this was installation, update, or first activation, tell the founder in one "
            "short sentence to open a new chat in this same folder. Otherwise report that setup checks passed. "
            "If memory_state is updating, say consolidation is queued, not recovered. "
            "A stored login is not proof of successful consolidation; verify an actual sleep result."
            if ready
            else issue["response_instruction"]
            if primary_issue
            else "Do not claim Setup is complete. " + CONFIGURATION_CHOICE_INSTRUCTION
            + "Use next_action.detail for the proposed repair. If setup or repair is already requested, "
            "call open_setup once and guide only that next action; ask the founder to reply 'fatto', "
            "then verify again. Do not force the user to finish setup if they choose to defer."
        ),
    }


def check_setup(project_root: Path | None, provider: str | None) -> dict:
    """Check the setup agent without leaking its token, URL, or raw credentials."""
    if project_root is None:
        raise ValueError("the client adapter did not supply a project root")
    root = project_root
    try:
        return _setup_check(read_setup_status(root), provider)
    except Exception:
        return {
            "ready": False,
            "requires_choice": True,
            "provider": provider,
            "next_action": {
                "id": "setup_unavailable",
                "title": "Reconnect Setup",
                "detail": "The local Setup check is unavailable. Open Setup again and wait for it to load.",
            },
            "remaining_actions": [],
            "response_instruction": (
                "Do not claim Setup is complete. " + CONFIGURATION_CHOICE_INSTRUCTION
                + "Offer to reopen local configuration with open_setup. After consent, open it once "
                "and ask the founder to reply 'fatto' when the page has loaded."
            ),
        }


def _remote_setup_context(
    project_root: Path | None, binding: ProjectBinding | None
) -> tuple[bool, ProjectBinding | None]:
    """Detect a declared remote binding without resolving it into a local fallback."""
    if binding is not None:
        return binding.remote, binding if binding.remote else None
    if project_root is None:
        return False, None
    try:
        project = load_project(project_root)
    except Exception:
        return False, None
    return str(project.get("binding") or "local").strip().lower() == "remote", None


def _open_remote_setup(binding: ProjectBinding | None) -> dict:
    if binding is None:
        return {
            "opened": False,
            "mode": "remote",
            "binding_ready": False,
            "docker_required": False,
            "local_setup_required": False,
            "next_action": {
                "id": "remote_binding_required",
                "title": "Reconnect remote project access",
                "detail": (
                    "This checkout declares shared remote memory, but its project-scoped device "
                    "binding is incomplete. Re-run the self-contained invitation or ask the "
                    "Gestore dell'infrastruttura for a new one."
                ),
            },
            "response_instruction": (
                "Do not open local Setup or start Docker. " + CONFIGURATION_CHOICE_INSTRUCTION
                + "Offer help restoring access using the project invitation or requesting a new one "
                "from the infrastructure manager. Do not contact anyone without consent. "
                "If deferred, continue project work without remote memory, not by creating a local fallback."
            ),
        }
    return {
        "opened": False,
        "mode": "remote",
        "binding_ready": True,
        "docker_required": False,
        "local_setup_required": False,
        "project_id": binding.project_id,
        "dashboard_url": binding.dashboard_link("team"),
        "next_action": {
            "id": "check_remote_memory",
            "title": "Verify shared remote memory",
            "detail": "The project already uses shared remote memory; verify its connection now.",
        },
        "response_instruction": (
            "Do not open local Setup or start Docker. Tell the founder this checkout already uses "
            "shared remote memory, then call check_setup to verify the connection."
        ),
    }


def _check_remote_setup(api, binding: ProjectBinding | None, provider: str | None) -> dict:
    if binding is None:
        payload = _open_remote_setup(None)
        return {
            **payload,
            "ready": False,
            "provider": provider,
            "remaining_actions": [payload["next_action"]],
        }
    try:
        team = request(api, "GET", f"/projects/{binding.project_id}/team")
        reachable = isinstance(team, dict)
    except Exception:
        reachable = False
    if not reachable:
        action = {
            "id": "remote_memory_unavailable",
            "title": "Shared memory is unavailable",
            "detail": (
                "The shared remote memory cannot be reached right now. Offer to help restore access "
                "with the Gestore dell'infrastruttura. Alternatively the user can choose to work "
                "without current memory, using the last verified operational manual when available."
            ),
        }
        return {
            "ready": False,
            "mode": "remote",
            "provider": provider,
            "project_id": binding.project_id,
            "requires_choice": True,
            "docker_required": False,
            "local_setup_required": False,
            "next_action": action,
            "remaining_actions": [action],
            "response_instruction": (
                "Do not open local Setup or start Docker. " + CONFIGURATION_CHOICE_INSTRUCTION
                + "Use next_action.detail; do not contact anyone without consent."
            ),
        }
    try:
        health = request(api, "GET", f"/projects/{binding.project_id}/memory-status")
    except Exception:
        health = {"state": "unknown"}
    issue = sleep_connection_notice(health if isinstance(health, dict) else {}, remote=True)
    if issue:
        action = {"id": issue["reason"], "title": "Check shared memory", "detail": issue["message"] + " " + issue["next_action"]}
        return {**issue, "mode": "remote", "provider": provider, "project_id": binding.project_id,
                "docker_required": False, "local_setup_required": False,
                "next_action": action, "remaining_actions": [action]}
    return {
        "ready": True,
        "mode": "remote",
        "provider": provider,
        "project_id": binding.project_id,
        "docker_required": False,
        "local_setup_required": False,
        "next_action": None,
        "remaining_actions": [],
        "response_instruction": (
            "Shared remote memory is reachable. Do not open local Setup or start Docker. "
            "If consolidation is pending, report it as pending rather than verified recovery."
        ),
    }


def _artifact_body(args: dict, project_root: Path) -> dict:
    path_value = args.pop("path", None)
    body = dict(args)
    if not path_value:
        return ArtifactCreate.model_validate(body).model_dump()
    path = Path(path_value)
    path = (project_root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("artifact path must stay inside the project directory") from exc
    lowered = path.name.lower()
    if lowered.startswith(".env") or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
        raise ValueError("secret and credential files cannot be registered as artifacts")
    content = path.read_bytes()
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("artifact is larger than 10 MiB")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not body.get("extracted_text") and (
        mime_type.startswith("text/")
        or path.suffix.lower() in {".md", ".json", ".toml", ".yaml", ".yml"}
    ):
        body["extracted_text"] = content.decode("utf-8", errors="replace")
    body.update(
        {
            "filename": path.name,
            "mime_type": mime_type,
            "source_uri": str(path.relative_to(project_root.resolve())),
            "content_base64": base64.b64encode(content).decode(),
        }
    )
    return ArtifactCreate.model_validate(body).model_dump()


def call(
    name: str,
    args: dict,
    project_id: str | None,
    api: str,
    project_root: Path | None = None,
    dashboard_url: str | None = None,
    plans_dashboard_url: str | None = None,
    binding: ProjectBinding | None = None,
):
    if name == "decline_setup":
        if project_root is None:
            raise ValueError("the client adapter did not supply a project root")
        if project_id is not None:
            raise ValueError("setup can only be declined for an unconfigured folder")
        from dduo_solo_founder.project_activation import decline_setup
        decline_setup(project_root)
        return {
            "status": "declined",
            "instruction": "No project memory was initialized. Setup will not be suggested again for this folder unless explicitly requested.",
        }
    if name == "health":
        remote_setup, remote_binding = _remote_setup_context(project_root, binding)
        if remote_setup and remote_binding is None:
            return _open_remote_setup(None)
        if not project_id:
            from dduo_solo_founder.project_activation import setup_declined
            if project_root is not None and setup_declined(project_root):
                return {
                    "status": "declined",
                    "instruction": "Memory is not active for this folder by the user's choice. Do not offer setup again unless explicitly requested.",
                }
            return {
                "status": "unconfigured",
                "instruction": (
                    "Briefly offer to open configuration to give this project memory and organized Work, "
                    "with continuing without memory as the explicit alternative. After consent, use open_setup; "
                    "if declined, use decline_setup once and do not offer again unless explicitly requested."
                ),
            }
        return request(api, "GET", "/health")
    if name == "open_setup":
        remote_setup, remote_binding = _remote_setup_context(project_root, binding)
        if remote_setup:
            return _open_remote_setup(remote_binding)
        if project_root is None:
            raise ValueError("the client adapter did not supply a project root")
        root = project_root
        open_setup(root)
        return {
            "opened": True,
            "response_instruction": (
                "Tell the founder in one short sentence: the local Setup page is open; complete only the items "
                "that ask for action, then reply 'fatto' and dDuo will verify it. Do not show commands, URLs, "
                "or secrets in chat. Stop and wait for that reply."
            ),
        }
    if name == "check_setup":
        remote_setup, remote_binding = _remote_setup_context(project_root, binding)
        if remote_setup:
            return _check_remote_setup(
                api, remote_binding, args.get("provider") or current_setup_provider()
            )
        return check_setup(project_root, args.get("provider") or current_setup_provider())
    if not project_id:
        raise ValueError(
            "dDuo Solo Founder is not configured for this project; run `dduo-solo-founder init` first"
        )
    team_dashboard_url = binding.dashboard_link("team") if binding is not None else None
    if name == "get_project_briefing":
        path = _path_with_query(
            f"/projects/{project_id}/briefing",
            {"client": current_setup_provider()},
        )
        briefing = request(api, "GET", path)
        if binding is not None and binding.remote:
            try:
                store_verified_manual(binding, briefing.get("operational_manual"))
            except Exception:
                pass
        return briefing_result(briefing, dashboard_url, plans_dashboard_url)
    if name == "get_project_manual":
        try:
            response = request(api, "GET", f"/projects/{project_id}/team/manual")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
            cached = load_verified_manual(binding) if binding is not None else None
            if cached is None:
                raise
            return manual_result(
                {
                    "manual": cached,
                    "offline": True,
                    "source": "last_verified_cache",
                    "response_instruction": (
                        "Use this last verified manual, state only if relevant that remote memory is "
                        "currently unavailable, and do not start local dDuo Docker for this project."
                    ),
                },
                team_dashboard_url,
            )
        except httpx.RequestError:
            cached = load_verified_manual(binding) if binding is not None else None
            if cached is None:
                raise
            return manual_result(
                {
                    "manual": cached,
                    "offline": True,
                    "source": "last_verified_cache",
                    "response_instruction": (
                        "Use this last verified manual, state only if relevant that remote memory is "
                        "currently unavailable, and do not start local dDuo Docker for this project."
                    ),
                },
                team_dashboard_url,
            )
        if binding is not None and binding.remote:
            try:
                store_verified_manual(binding, response.get("manual"))
            except Exception:
                pass
        return manual_result(response, team_dashboard_url)
    if name == "update_project_manual":
        content = str(args.get("content") or "").strip()
        expected_version = int(args["expected_version"])
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        response = request(
            api,
            "PATCH",
            f"/projects/{project_id}/team/manual",
            {
                "content": content,
                "expected_version": expected_version,
                "idempotency_key": f"mcp-manual-v{expected_version}-{content_hash[:40]}",
            },
        )
        if binding is not None and binding.remote:
            try:
                store_verified_manual(binding, response.get("manual"))
            except Exception:
                pass
        return manual_result(
            response,
            team_dashboard_url,
            changed=response.get("idempotent") is not True,
        )
    if name == "search_memory":
        response = _raw_response(
            api,
            "GET",
            f"/projects/{project_id}/memories/search",
            params={"q": args["query"], "limit": args.get("limit", 8)},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    if name == "get_memory_status":
        path = _path_with_query(
            f"/projects/{project_id}/memory-status",
            {"client": current_setup_provider()},
        )
        return request(api, "GET", path)
    if name == "request_sleep":
        return request(api, "POST", f"/projects/{project_id}/sleep", args)
    if name == "list_sleep_jobs":
        return request(
            api, "GET", f"/projects/{project_id}/sleep-jobs?limit={args.get('limit', 100)}"
        )
    if name == "retry_sleep_job":
        return request(
            api,
            "POST",
            f"/projects/{project_id}/sleep-jobs/{args['job_id']}/retry",
        )
    if name == "explain_memory":
        return request(
            api, "GET", f"/projects/{project_id}/memories/{args['memory_id']}/provenance"
        )
    if name == "list_memory_revisions":
        return request(api, "GET", f"/projects/{project_id}/memories/{args['memory_id']}/revisions")
    if name == "forget_memory":
        return request(api, "POST", f"/projects/{project_id}/memories/forget", args)
    if name == "set_off_record":
        body = dict(args)
        session_id = body.pop("session_id", None)
        if not session_id:
            raise ValueError("set_off_record requires session_id")
        return request(
            api,
            "PATCH",
            f"/projects/{project_id}/sessions/{session_id}/privacy",
            body,
        )
    if name == "rebuild_vector_index":
        return request(api, "POST", f"/projects/{project_id}/memories/reindex")
    if name == "register_artifact":
        if not project_root:
            raise ValueError("project root is unavailable")
        artifact_args = dict(args)
        task_id = artifact_args.pop("task_id", None)
        plan_id = artifact_args.pop("plan_id", None)
        if task_id and plan_id:
            raise ValueError("register_artifact accepts either task_id or plan_id, not both")
        path = f"/projects/{project_id}/artifacts"
        if task_id:
            path = f"/projects/{project_id}/tasks/{task_id}/attachments"
        elif plan_id:
            path = f"/projects/{project_id}/plans/{plan_id}/attachments"
        return request(
            api,
            "POST",
            path,
            _artifact_body(artifact_args, project_root),
        )
    if name == "list_artifacts":
        return request(
            api, "GET", f"/projects/{project_id}/artifacts?limit={args.get('limit', 100)}"
        )
    if name == "list_tasks":
        path = _path_with_query(
            f"/projects/{project_id}/tasks",
            {
                "detail": args.get("detail", "compact"),
                "scope": args.get("scope", "all" if args.get("placement") == "archive" else "active"),
                "status": args.get("status"),
                "kind": args.get("kind"),
                "epic_id": args.get("epic_id"),
                "sprint_id": args.get("sprint_id"),
                "placement": args.get("placement"),
                "limit": args.get("limit"),
                "offset": args.get("offset"),
                "q": args.get("q"),
                "label": args.get("label"),
                "known_snapshot_hash": args.get("known_snapshot_hash"),
            },
        )
        return _decorate_work_collection(request(api, "GET", path), dashboard_url)
    if name == "get_task":
        path = _path_with_query(
            f"/projects/{project_id}/tasks/{args['task_id']}",
            {
                "detail": args.get("detail", "working"),
                "known_snapshot_hash": args.get("known_snapshot_hash"),
            },
        )
        return _decorate_single_work_result(
            request(api, "GET", path),
            dashboard_url,
            item_id=args["task_id"],
            key="task",
        )
    if name == "search_tasks":
        body = {
            "query": args["query"],
            "scope": args.get("scope", "all" if args.get("placement") == "archive" else "active"),
            "limit": args.get("limit", 8),
        }
        body.update(
            {
                key: args[key]
                for key in ("cursor", "status", "kind", "epic_id", "label", "sprint_id", "placement")
                if args.get(key) is not None
            }
        )
        return _decorate_work_collection(
            request(api, "POST", f"/projects/{project_id}/tasks/search", body),
            dashboard_url,
        )
    if name == "list_plans":
        return _decorate_work_collection(
            request(api, "GET", _path_with_query(f"/projects/{project_id}/plans", {"detail": "compact", **args})),
            plans_dashboard_url,
            plan=True,
        )
    if name == "activate_task_context":
        path = _path_with_query(
            f"/projects/{project_id}/tasks/{args['task_id']}",
            {
                "detail": "working",
                "known_snapshot_hash": args.get("known_snapshot_hash"),
            },
        )
        task_response = request(
            api,
            "GET",
            path,
        )
        if task_response.get("unchanged"):
            return _decorate_single_work_result(
                task_response,
                dashboard_url,
                item_id=args["task_id"],
                key="task",
            )
        activated = {"task": task_response["task"], "activated": True}
        if task_response.get("snapshot_hash"):
            activated["snapshot_hash"] = task_response["snapshot_hash"]
        return _decorate_single_work_result(
            activated,
            dashboard_url,
            item_id=args["task_id"],
            key="task",
        )
    if name in {"activate_plan_context", "get_plan"}:
        plan = request(api, "GET", f"/projects/{project_id}/plans/{args['plan_id']}")["plan"]
        return _decorate_single_work_result(
            {"plan": plan, **({"activated": True} if name == "activate_plan_context" else {})},
            plans_dashboard_url,
            item_id=args["plan_id"],
            key="plan",
            plan=True,
        )
    if name in {"list_sprints", "get_sprint", "preview_sprint_close", "list_sprint_history"}:
        query = dict(args)
        sprint_id = query.pop("sprint_id", None)
        path = f"/projects/{project_id}/sprints"
        if sprint_id is not None:
            path += f"/{sprint_id}"
        if name == "preview_sprint_close":
            path += "/close-preview"
        elif name == "list_sprint_history":
            path += "/tasks"
        return request(api, "GET", _path_with_query(path, query))
    if name in {"create_sprint", "create_historical_sprint", "update_sprint", "start_sprint", "archive_sprint", "reopen_sprint"}:
        body = dict(args)
        sprint_id = body.pop("sprint_id", None)
        path = f"/projects/{project_id}/sprints"
        if name == "create_historical_sprint":
            path += "/history"
        elif sprint_id is not None:
            path += f"/{sprint_id}"
            if name != "update_sprint":
                path += "/" + name.removesuffix("_sprint")
        return request(api, "PATCH" if name == "update_sprint" else "POST", path, body)
    if name == "create_task":
        task = request(api, "POST", f"/projects/{project_id}/tasks?detail=compact", args)
        return task_mutation_result(task, dashboard_url)
    if name == "update_task":
        body = dict(args)
        task_id = body.pop("task_id")
        task = request(
            api,
            "PATCH",
            f"/projects/{project_id}/tasks/{task_id}?detail=compact",
            body,
        )
        return task_mutation_result(task, dashboard_url)
    if name == "create_plan":
        plan = request(api, "POST", f"/projects/{project_id}/plans", args)
        return plan_mutation_result(plan, plans_dashboard_url)
    if name == "update_plan":
        body = dict(args)
        plan_id = body.pop("plan_id")
        plan = request(api, "PATCH", f"/projects/{project_id}/plans/{plan_id}", body)
        return plan_mutation_result(plan, plans_dashboard_url)
    if name == "get_activity":
        return request(
            api, "GET", f"/projects/{project_id}/activity?limit={args.get('limit', 100)}"
        )
    if name == "update_project_profile":
        return request(api, "PATCH", f"/projects/{project_id}", args)
    raise ValueError(f"unknown tool: {name}")


def _mcp_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Add the portable per-call root without changing the domain contracts."""
    schema = model.model_json_schema()
    properties = schema.setdefault("properties", {})
    properties[MCP_WARNING_ACK_ARGUMENT] = {
        "type": "string",
        "pattern": "^[0-9a-f]{32}$",
        "description": (
            "Current warning_id, only after the user explicitly chooses in THIS chat "
            "to continue without verified automatic memory. Never invent or reuse another chat's choice."
        ),
    }
    properties[MCP_WORKSPACE_ARGUMENT] = {
        "type": "string",
        "description": (
            "Absolute root of the current project. Required when the Codex or Claude "
            "adapter does not provide DDUO_SOLO_FOUNDER_PROJECT_ROOT."
        ),
    }
    if MCP_PROJECT_ROOT_ENV not in os.environ:
        required = schema.setdefault("required", [])
        if MCP_WORKSPACE_ARGUMENT not in required:
            required.append(MCP_WORKSPACE_ARGUMENT)
    return schema


def _sdk_tools() -> list[mcp_types.Tool]:
    return [
        mcp_types.Tool(
            name=name,
            description=description,
            inputSchema=_mcp_input_schema(model),
        )
        for name, (description, model) in TOOL_MODELS.items()
    ]


def _tool_error(message: str) -> mcp_types.CallToolResult:
    text = json.dumps({"error": message}, ensure_ascii=False)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        isError=True,
    )


def _safe_tool_error(exc: Exception) -> str:
    if isinstance(exc, (MCPContextError, UnsupportedClientError, ValueError)):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"dDuo memory returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return (
            "dDuo memory is currently unavailable. Offer to check_memory_connection to identify the "
            "repair first; alternatively the user can continue temporarily without memory. "
            "Respect a choice already made in this chat; do not start local Setup for remote memory."
        )
    return (
        "dDuo could not complete this memory operation. Explain the failed operation without assuming "
        "that all memory is disconnected. Offer a targeted check first, or let the user defer it. "
        "Do not blindly retry a mutation whose outcome is unknown."
    )


def _invoke_mcp_tool(
    name: str,
    arguments: dict[str, Any],
    runtime: MCPToolRuntime,
) -> tuple[object, dict]:
    value = call(
        name,
        arguments,
        runtime.project_id,
        runtime.api,
        runtime.project_root,
        runtime.dashboard_url,
        runtime.plans_dashboard_url,
        runtime.binding,
    )
    rendered = result(
        value,
        api=runtime.api if runtime.project_id else None,
        project_id=runtime.project_id,
        tool_name=name,
    )
    return value, rendered


def _read_connection_health(runtime: MCPToolRuntime) -> dict:
    """Read the existing project endpoint; never probe a paid model or change login."""
    try:
        response = _raw_response(
            runtime.api, "GET", f"/projects/{runtime.project_id}/memory-status", timeout=5
        )
        response.raise_for_status()
        value = response.json()
        if isinstance(value, dict) and isinstance(value.get("state"), str):
            return value
    except (httpx.HTTPError, ValueError, OSError):
        pass
    return {"state": "unavailable", "available": False}


def _connection_notice(
    name: str, runtime: MCPToolRuntime, acknowledgement: str | None
) -> dict | None:
    if name in _CONNECTION_EXEMPT_TOOLS and name != "check_memory_connection":
        return None
    if not runtime.project_id:
        from dduo_solo_founder.project_activation import setup_declined
        declined = runtime.project_root is not None and setup_declined(runtime.project_root)
        return (
            {"ready": None, "reason": "declined" if declined else "unconfigured", "requires_choice": False,
             "response_instruction": (
                 "Respect the user's choice: do not offer setup again unless explicitly requested."
                 if declined else
                 "Offer to open configuration to give this project memory and organized Work; "
                 "explicitly offer continuing without it as the alternative. Wait for consent. "
                 "Do not claim memory is active."
             )}
            if name == "check_memory_connection" else None
        )
    checked = _MEMORY_CONNECTION_CHECKS.check(
        runtime.client, runtime.project_root,
        runtime.binding.binding_id if runtime.binding else runtime.project_id,
        refresh=name == "check_memory_connection",
    )
    checked = combine_connection_checks(
        checked, _read_connection_health(runtime),
        binding_id=runtime.binding.binding_id if runtime.binding else runtime.project_id,
        remote=bool(runtime.binding and runtime.binding.remote),
    )
    if name == "check_memory_connection":
        return checked
    if checked["requires_choice"] and acknowledgement != checked.get("warning_id"):
        return {**checked, "tool_executed": False}
    return None


async def _list_mcp_tools(
    context: ServerRequestContext[Any, Any],
    _params: mcp_types.PaginatedRequestParams | None,
) -> mcp_types.ListToolsResult:
    try:
        _supported_request_client(context)
    except UnsupportedClientError:
        # Unsupported hosts can inspect the package, but dDuo exposes no
        # operational surface until a Codex or Claude adapter is active.
        return mcp_types.ListToolsResult(tools=[])
    return mcp_types.ListToolsResult(tools=_sdk_tools())


async def _call_mcp_tool(
    context: ServerRequestContext[Any, Any],
    params: mcp_types.CallToolRequestParams,
) -> mcp_types.CallToolResult:
    try:
        client = _supported_request_client(context)
    except UnsupportedClientError as exc:
        return _tool_error(str(exc))

    selected = TOOL_MODELS.get(params.name)
    if selected is None:
        return _tool_error(f"unknown tool: {params.name}")

    raw_arguments: dict[str, Any] = dict(params.arguments or {})
    try:
        acknowledgement = raw_arguments.pop(MCP_WARNING_ACK_ARGUMENT, None)
        if acknowledgement is not None and (
            not isinstance(acknowledgement, str)
            or len(acknowledgement) != 32
            or any(char not in "0123456789abcdef" for char in acknowledgement)
        ):
            raise ValueError("memory_warning_ack must be the current warning_id")
        runtime = _resolve_tool_runtime(client, raw_arguments)
        model = selected[1]
        validated = model.model_validate(raw_arguments)
        # Explicit null is a mutation (for example moving a task to backlog).
        # Only omitted fields may disappear from a validated tool request.
        arguments = validated.model_dump(mode="json", exclude_unset=True)
    except Exception as exc:
        return _tool_error(_safe_tool_error(exc))

    provider_token = _ACTIVE_MCP_CLIENT.set(client)
    try:
        notice = await anyio.to_thread.run_sync(
            functools.partial(_connection_notice, params.name, runtime, acknowledgement)
        )
        if notice is not None:
            # This is a local control response, not a delivered project payload.
            # Only read-only readiness checks ran; no task mutation or telemetry flush occurs.
            return mcp_types.CallToolResult(
                content=[mcp_types.TextContent(type="text", text=json.dumps(notice))],
                structuredContent=notice,
            )
        value, rendered = await anyio.to_thread.run_sync(
            functools.partial(_invoke_mcp_tool, params.name, arguments, runtime)
        )
    except Exception as exc:
        return _tool_error(_safe_tool_error(exc))
    finally:
        _ACTIVE_MCP_CLIENT.reset(provider_token)

    text = str(rendered["content"][0]["text"])
    # The text fallback and structured result deliberately derive from the
    # same serialization so clients never observe two different payloads.
    structured = json.loads(text)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        structuredContent=structured,
    )


def create_mcp_server() -> Server:
    """Build the official, dual-era MCP server with no process-global project."""
    return Server(
        "dduo-solo-founder",
        version=__version__,
        description="Project-isolated memory and work context for Codex and Claude Code",
        instructions=(
            "Use dDuo only from Codex or Claude Code. Every tool call is bound to the "
            "current project root and never falls back to another project. Before project "
            "work in each chat call check_memory_connection once, even if MCP tools work. "
            "If automatic memory needs attention, recommend the specific repair FIRST: offer "
            "open_setup for local sign-in, guided native permission review, or remote repair "
            "through the infrastructure manager. Explicitly offer continuing with the stated "
            "limitation as the alternative, never as the only question. Wait for the choice; "
            "do not claim capture is active just because MCP is connected. "
            "On 'done', recheck. After explicit consent to continue, use the returned "
            "warning_id as memory_warning_ack on calls in this chat only; ask again only "
            "for a changed warning. If the user declines dDuo for this project, do not activate it."
        ),
        on_list_tools=_list_mcp_tools,
        on_call_tool=_call_mcp_tool,
    )


async def _serve_stdio(server: Server | None = None) -> None:
    selected = server or create_mcp_server()
    initialization = selected.create_initialization_options(
        NotificationOptions(tools_changed=False)
    )
    async with stdio_server() as (read_stream, write_stream):
        await selected.run(read_stream, write_stream, initialization)


def main() -> None:
    """Run the SDK-managed stdio transport and protocol negotiation."""
    anyio.run(_serve_stdio)


if __name__ == "__main__":
    main()
