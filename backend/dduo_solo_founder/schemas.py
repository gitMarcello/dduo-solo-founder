from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskStatus = Literal["todo", "in_progress", "blocked", "done", "cancelled"]
TaskPriority = Literal["low", "medium", "high", "critical"]
TaskKind = Literal["task", "epic"]
PlanStatus = Literal["draft", "decided", "executing", "completed", "superseded"]
SprintStatus = Literal["planned", "active", "archived"]
TaskPlacement = Literal["all", "current", "backlog", "archive"]
MemoryLanguage = Literal["it", "en"]
TeamCapability = Literal["infrastructure_manager", "project_member"]


class ProjectCreate(BaseModel):
    id: str
    name: str
    root_path: str
    cause: str = ""
    principles: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    context: str = ""


class ProjectUpdate(BaseModel):
    cause: str | None = None
    principles: list[str] | None = None
    objectives: list[str] | None = None
    context: str | None = None
    rationale: str = ""
    expected_version: int | None = Field(default=None, ge=1)


class SessionStart(BaseModel):
    client: Literal["codex", "claude", "other"]
    external_id: str


class TeamBootstrap(BaseModel):
    """Create the first infrastructure manager from the trusted local control plane."""

    display_name: str = Field(min_length=1, max_length=200)
    device_id: str = Field(min_length=1, max_length=100)
    device_label: str = Field(default="", max_length=200)
    device_token: str = Field(
        min_length=52,
        max_length=128,
        pattern=r"^dduo_dev_[A-Za-z0-9_-]{43,}$",
    )


class TeamInviteCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    expires_in_hours: int = Field(default=24, ge=1, le=168)
    language: Literal["en", "it"] = "it"
    api_url: str | None = Field(default=None, max_length=2_000)
    dashboard_url: str | None = Field(default=None, max_length=2_000)


class TeamDeviceTokenCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)
    device_label: str = Field(default="", max_length=200)
    device_token: str = Field(
        min_length=52,
        max_length=128,
        pattern=r"^dduo_dev_[A-Za-z0-9_-]{43,}$",
    )


class TeamInviteExchange(BaseModel):
    invitation_code: str = Field(
        min_length=52,
        max_length=128,
        pattern=r"^dduo_inv_[A-Za-z0-9_-]{43,}$",
    )
    device_id: str = Field(min_length=1, max_length=100)
    device_label: str = Field(default="", max_length=200)
    # Generated and retained by the client before exchange. This makes a lost
    # HTTP response retry-safe while the server persists only a one-way hash.
    device_token: str = Field(
        min_length=52,
        max_length=128,
        pattern=r"^dduo_dev_[A-Za-z0-9_-]{43,}$",
    )


class BrowserSessionExchange(BaseModel):
    ticket: str = Field(
        min_length=52,
        max_length=128,
        pattern=r"^dduo_web_[A-Za-z0-9_-]{43,}$",
    )


class AuthorityNodeRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    expected_generation: int = Field(default=1, ge=1)


class AuthorityRecoveryRequest(AuthorityNodeRequest):
    """Explicit disaster-recovery takeover after the old node is confirmed unavailable."""

    old_node_unreachable: Literal[True]


class AuthorityFinalizeRequest(BaseModel):
    activation_receipt: str = Field(
        min_length=64,
        max_length=2_048,
        pattern=r"^dduo_authority_v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    )


class AuthorityCompleteRequest(AuthorityNodeRequest):
    finalization_receipt: str = Field(
        min_length=64,
        max_length=2_048,
        pattern=r"^dduo_authority_v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",
    )


class OperationalManualUpdate(BaseModel):
    content: str = Field(max_length=100_000)
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)


class OperationalManualCompactDraft(BaseModel):
    expected_version: int = Field(ge=0)


class TurnBegin(BaseModel):
    session_id: str
    external_id: str
    # One interactive turn may receive more than one user prompt (for example,
    # Codex steering while the agent is still working).  This identifier makes
    # every delivered prompt fragment independently idempotent without adding a
    # second Turn or changing the database schema.
    prompt_event_id: str | None = Field(default=None, min_length=1, max_length=500)
    user_prompt: str
    limit: int = Field(default=8, ge=1, le=20)
    off_record: bool | None = None


class RawEventCreate(BaseModel):
    session_id: str | None = None
    turn_id: str | None = None
    event_type: Literal[
        "tool_call",
        "tool_result",
        "user_prompt",
        "assistant_response",
        "system",
        "compaction_pre",
        "compaction_post",
    ]
    payload: dict = Field(default_factory=dict)
    actor: str = Field(default="agent", max_length=50)


class CompactionRecord(BaseModel):
    session_id: str
    phase: Literal["pre", "post"]
    trigger: Literal["manual", "auto", "unknown"] = "unknown"
    summary: str = Field(default="", max_length=100_000)


ContextOperation = Literal[
    "context.session_start",
    "context.turn_injection",
    "context.pre_tool_principles",
    "context.mcp_tool_result",
]
ContextScope = Literal["automatic", "requested"]

ContextDeliveryReason = Literal[
    "startup",
    "resume",
    "clear",
    "compact",
    "session_start",
    "session_unknown",
    "state_missing",
    "foundation_changed",
    "work_changed",
    "foundation_and_work_changed",
    "unchanged",
    "remote_offline",
    "memory_unavailable",
    "composition_failed",
    "client_upgrade_required",
]


class ContextComponent(BaseModel):
    """One numeric manifest entry for a slice of model-visible context."""

    name: str = Field(min_length=1, max_length=40)
    utf8_bytes: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    item_count: int | None = Field(default=None, ge=0)
    candidate_item_count: int | None = Field(default=None, ge=0)
    partial_item_count: int = Field(default=0, ge=0)
    omitted_item_count: int = Field(default=0, ge=0)
    references: list[str] = Field(default_factory=list, max_length=1_000)
    omitted_references: list[str] = Field(default_factory=list, max_length=1_000)

    @model_validator(mode="after")
    def validate_component(self):
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
            "result",
        }
        if self.name not in allowed:
            raise ValueError("component name is unsupported")
        if self.estimated_tokens != (self.utf8_bytes + 3) // 4:
            raise ValueError("component estimated_tokens does not match utf8_bytes")
        if any(not value or len(value) > 200 for value in self.references):
            raise ValueError("component references must contain bounded non-empty identifiers")
        if len(set(self.references)) != len(self.references):
            raise ValueError("component references must be unique")
        if any(not value or len(value) > 200 for value in self.omitted_references):
            raise ValueError(
                "component omitted_references must contain bounded non-empty identifiers"
            )
        if len(set(self.omitted_references)) != len(self.omitted_references):
            raise ValueError("component omitted_references must be unique")
        if set(self.references).intersection(self.omitted_references):
            raise ValueError("included and omitted component references must be disjoint")
        if self.item_count is not None and self.partial_item_count > self.item_count:
            raise ValueError("partial_item_count cannot exceed item_count")
        if self.candidate_item_count is not None:
            included = self.item_count or 0
            if self.candidate_item_count != included + self.omitted_item_count:
                raise ValueError("candidate_item_count must equal included and omitted items")
        return self


class ContextBudgetObservation(BaseModel):
    """Selection facts for one bounded automatic context render."""

    limit_characters: int = Field(ge=1, le=100_000)
    client_character_units: int = Field(ge=0)
    candidate_characters: int = Field(ge=0)
    candidate_utf8_bytes: int = Field(ge=0)
    candidate_estimated_tokens: int = Field(ge=0)
    avoided_characters: int = Field(ge=0)
    avoided_utf8_bytes: int = Field(ge=0)
    avoided_estimated_tokens: int = Field(ge=0)
    included_items: int = Field(ge=0)
    partial_items: int = Field(ge=0)
    omitted_items: int = Field(ge=0)
    outcome: Literal["within_budget", "budgeted", "fallback"]
    delivery_expectation: Literal["inline_expected"] = "inline_expected"

    @model_validator(mode="after")
    def validate_measurements(self):
        if self.client_character_units > self.limit_characters:
            raise ValueError("client_character_units exceeds the configured context budget")
        if self.candidate_estimated_tokens != (self.candidate_utf8_bytes + 3) // 4:
            raise ValueError("candidate_estimated_tokens does not match candidate_utf8_bytes")
        if self.avoided_estimated_tokens != (self.avoided_utf8_bytes + 3) // 4:
            raise ValueError("avoided_estimated_tokens does not match avoided_utf8_bytes")
        if self.partial_items > self.included_items:
            raise ValueError("partial_items cannot exceed included_items")
        if self.outcome == "within_budget" and (self.omitted_items or self.partial_items):
            raise ValueError("within_budget cannot report omitted or partial items")
        if self.outcome == "budgeted" and not (self.omitted_items or self.partial_items):
            raise ValueError("budgeted requires an omitted or partial item")
        return self


class ContextDeliveryObservation(BaseModel):
    """Why this hook emitted a full snapshot, a delta, or a safe fallback."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["snapshot", "delta", "fallback"]
    reason: ContextDeliveryReason
    foundation_changed: bool = False
    work_changed: bool = False
    reused_characters: int = Field(default=0, ge=0)
    reused_utf8_bytes: int = Field(default=0, ge=0)
    reused_estimated_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reuse(self):
        allowed_reasons = {
            "snapshot": {
                "startup",
                "resume",
                "clear",
                "compact",
                "session_start",
                "session_unknown",
                "state_missing",
            },
            "delta": {
                "foundation_changed",
                "work_changed",
                "foundation_and_work_changed",
                "unchanged",
            },
            "fallback": {
                "remote_offline",
                "memory_unavailable",
                "composition_failed",
                "client_upgrade_required",
            },
        }
        if self.reason not in allowed_reasons[self.kind]:
            raise ValueError(f"delivery reason {self.reason!r} is invalid for {self.kind}")
        expected_changes = {
            "foundation_changed": (True, False),
            "work_changed": (False, True),
            "foundation_and_work_changed": (True, True),
            "unchanged": (False, False),
        }
        if self.kind == "snapshot" and not (self.foundation_changed and self.work_changed):
            raise ValueError("snapshot delivery must replace foundation and Work")
        if self.kind == "fallback" and (self.foundation_changed or self.work_changed):
            raise ValueError("fallback delivery cannot advance foundation or Work")
        if self.kind == "delta" and (
            self.foundation_changed,
            self.work_changed,
        ) != expected_changes[self.reason]:
            raise ValueError("delta change flags do not match its delivery reason")
        if self.reused_characters > self.reused_utf8_bytes:
            raise ValueError("reused_characters cannot exceed reused_utf8_bytes")
        if self.reused_estimated_tokens != (self.reused_utf8_bytes + 3) // 4:
            raise ValueError("reused_estimated_tokens does not match reused_utf8_bytes")
        if self.kind != "delta" and any(
            (self.reused_characters, self.reused_utf8_bytes, self.reused_estimated_tokens)
        ):
            raise ValueError("only delta delivery can reuse live-session context")
        return self


class ContextObservation(BaseModel):
    """A measured context delivery with an optional exact immutable snapshot."""

    event_id: str = Field(
        min_length=1,
        max_length=192,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
    )
    operation: ContextOperation
    scope: ContextScope
    client: Literal["codex", "claude", "other"]
    session_id: str | None = Field(default=None, max_length=36)
    turn_id: str | None = Field(default=None, max_length=36)
    retrieval_run_id: str | None = Field(default=None, max_length=36)
    characters: int = Field(ge=0)
    utf8_bytes: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    estimator_version: Literal["utf8_bytes_div_4_v1"]
    budget: ContextBudgetObservation | None = None
    delivery: ContextDeliveryObservation | None = None
    component_bytes: dict[str, int] = Field(default_factory=dict)
    components: list[ContextComponent] = Field(default_factory=list, max_length=20)
    # No size limit here: capture happens after rendering, so validation must
    # never discard context that was already emitted to the model.
    content: str | None = None
    content_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    producer_version: str | None = Field(default=None, min_length=1, max_length=80)
    render_version: str | None = Field(default=None, min_length=1, max_length=80)
    tool_name: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def validate_numeric_components(self):
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
            "result",
        }
        if not set(self.component_bytes).issubset(allowed):
            raise ValueError("component_bytes contains an unsupported component")
        if any(value < 0 for value in self.component_bytes.values()):
            raise ValueError("component_bytes values must be non-negative")
        if self.characters > self.utf8_bytes:
            raise ValueError("characters cannot exceed utf8_bytes")
        if self.budget is not None:
            if self.scope != "automatic":
                raise ValueError("context budgets apply only to automatic context")
            if self.characters > self.budget.client_character_units:
                raise ValueError("characters cannot exceed client_character_units")
            if self.characters > self.budget.candidate_characters:
                raise ValueError("emitted characters cannot exceed candidate characters")
            if self.utf8_bytes > self.budget.candidate_utf8_bytes:
                raise ValueError("emitted bytes cannot exceed candidate bytes")
            if self.budget.avoided_characters != self.budget.candidate_characters - self.characters:
                raise ValueError("avoided_characters must equal candidate minus emitted")
            if self.budget.avoided_utf8_bytes != self.budget.candidate_utf8_bytes - self.utf8_bytes:
                raise ValueError("avoided_utf8_bytes must equal candidate minus emitted")
        if sum(self.component_bytes.values()) > self.utf8_bytes:
            raise ValueError("component_bytes cannot exceed utf8_bytes")
        component_names = [item.name for item in self.components]
        if len(set(component_names)) != len(component_names):
            raise ValueError("components must contain unique names")
        if self.components:
            manifest = {item.name: item.utf8_bytes for item in self.components}
            if manifest != self.component_bytes:
                raise ValueError("components must match component_bytes")
        if self.tool_name and not all(
            character.isalnum() or character in "._:-/" for character in self.tool_name
        ):
            raise ValueError("tool_name contains unsupported characters")
        if self.operation == "context.session_start" and self.turn_id is not None:
            raise ValueError("context.session_start must remain session-scoped")
        snapshot_metadata = (
            self.content_sha256,
            self.producer_version,
            self.render_version,
            self.occurred_at,
        )
        if self.content is None:
            if any(value is not None for value in snapshot_metadata):
                raise ValueError("snapshot metadata requires content")
            if self.components:
                raise ValueError("components require content")
            return self
        if any(value is None for value in snapshot_metadata):
            raise ValueError(
                "content requires content_sha256, producer_version, render_version, and occurred_at"
            )
        encoded = self.content.encode("utf-8")
        if len(self.content) != self.characters:
            raise ValueError("characters does not match content")
        if len(encoded) != self.utf8_bytes:
            raise ValueError("utf8_bytes does not match content")
        if self.budget is not None:
            actual_client_units = max(len(self.content), len(self.content.encode("utf-16-le")) // 2)
            if actual_client_units != self.budget.client_character_units:
                raise ValueError("client_character_units does not match content")
        if (len(encoded) + 3) // 4 != self.estimated_tokens:
            raise ValueError("estimated_tokens does not match content")
        if hashlib.sha256(encoded).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match content")
        if self.occurred_at is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone for content snapshots")
        if not self.components:
            raise ValueError("content snapshots require a component manifest")
        if sum(self.component_bytes.values()) != self.utf8_bytes:
            raise ValueError("content snapshot components must account for every UTF-8 byte")
        return self


class ContextObservationBatch(BaseModel):
    items: list[ContextObservation] = Field(min_length=1, max_length=100)


class AgentUsageObservation(BaseModel):
    """Content-free usage emitted by a supported interactive client adapter."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agent_usage"] = "agent_usage"
    event_id: str = Field(
        min_length=1,
        max_length=192,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
    )
    provider: Literal["codex", "claude"]
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    turn_id: str | None = Field(default=None, min_length=1, max_length=36)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:/+-]+$",
    )
    measurement_source: Literal["provider_reported", "local_estimate"]
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    reported_total_tokens: int | None = Field(default=None, ge=0)
    client_cost_usd: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        max_digits=20,
        decimal_places=12,
    )
    cost_source: Literal["claude_code_client_estimate"] | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_usage(self):
        token_values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.reported_total_tokens,
        )
        if not any(value is not None for value in token_values) and self.client_cost_usd is None:
            raise ValueError("agent usage requires tokens or a client cost")
        if self.client_cost_usd is not None:
            if (
                self.provider != "claude"
                or self.measurement_source != "local_estimate"
                or self.cost_source != "claude_code_client_estimate"
            ):
                raise ValueError("client cost is supported only for Claude Code client estimates")
        elif self.cost_source is not None:
            raise ValueError("cost_source requires client_cost_usd")
        if self.provider == "codex" and self.input_tokens is not None:
            cached = self.cached_input_tokens or 0
            cache_write = self.cache_write_input_tokens or 0
            if cached + cache_write > self.input_tokens:
                raise ValueError("Codex cached input classes cannot exceed input tokens")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class _RetiredUsageGuardTelemetryItem(BaseModel):
    """Validation-only compatibility shape for draining pre-retirement outboxes."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["usage_guard"]
    event_id: str = Field(
        min_length=1,
        max_length=192,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$",
    )
    provider: Literal["codex", "claude"]
    operation: Literal[
        "usage_guard.threshold_reached",
        "usage_guard.prompt_blocked",
        "usage_guard.resumed",
        "usage_guard.manual_pause",
        "usage_guard.telemetry_unavailable",
    ]
    measurement_source: Literal["provider_reported", "local_estimate", "unavailable"]
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_retired_transition(self):
        if self.operation == "usage_guard.telemetry_unavailable":
            if self.measurement_source != "unavailable":
                raise ValueError("telemetry_unavailable requires unavailable measurement source")
        elif self.operation == "usage_guard.threshold_reached":
            if self.measurement_source != "provider_reported":
                raise ValueError("threshold_reached requires provider-reported measurement")
        elif self.measurement_source == "unavailable":
            raise ValueError("only telemetry_unavailable can use unavailable measurement source")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class ClientTelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AgentUsageObservation] = Field(
        max_length=1_000,
        json_schema_extra={"minItems": 1},
    )

    @field_validator("items", mode="before")
    @classmethod
    def discard_retired_usage_guard_items(cls, value):
        if not isinstance(value, (list, tuple)):
            return value
        if not value:
            raise ValueError("client telemetry batches require at least 1 item")
        if len(value) > 1_000:
            raise ValueError("client telemetry batches may contain at most 1000 items")

        usage_items: list[AgentUsageObservation] = []
        event_ids: set[str] = set()
        for raw_item in value:
            if isinstance(raw_item, dict) and raw_item.get("kind") == "usage_guard":
                item = _RetiredUsageGuardTelemetryItem.model_validate(raw_item)
            else:
                item = AgentUsageObservation.model_validate(raw_item)
                usage_items.append(item)
            if item.event_id in event_ids:
                raise ValueError(
                    "client telemetry event_id values must be unique within a batch"
                )
            event_ids.add(item.event_id)
        return usage_items

    @model_validator(mode="after")
    def unique_event_ids(self):
        event_ids = [item.event_id for item in self.items]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("client telemetry event_id values must be unique within a batch")
        return self


class StopCheck(BaseModel):
    assistant_response: str = ""
    topic_changed: bool = False
    segment_summary: str = ""
    used_memory_ids: list[str] = Field(default_factory=list)
    receipt: str = ""
    context_observations: list[ContextObservation] = Field(default_factory=list, max_length=100)

    @field_validator("context_observations", mode="before")
    @classmethod
    def keep_valid_context_observations(cls, value):
        if not isinstance(value, list):
            return []
        observations: list[ContextObservation] = []
        for item in value:
            try:
                observations.append(ContextObservation.model_validate(item))
            except (TypeError, ValueError):
                continue
            if len(observations) == 100:
                break
        return observations

    @model_validator(mode="after")
    def validate_segment(self):
        if self.topic_changed and not self.segment_summary.strip():
            raise ValueError("topic_changed requires segment_summary")
        return self


class TaskAction(BaseModel):
    action: Literal["create", "update", "complete"]
    task_id: str | None = None
    kind: TaskKind | None = None
    epic_id: str | None = None
    sprint_id: str | None = None
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    labels: list[str] | None = None
    objective: str | None = None
    next_action: str | None = None
    dependencies: list[str] | None = None
    rationale: str | None = None
    completion_evidence: str | None = None
    due_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "create" and not (self.title and self.title.strip()):
            raise ValueError("create requires title")
        if self.action in {"update", "complete"} and not self.task_id:
            raise ValueError(f"{self.action} requires task_id")
        if self.kind == "epic" and self.epic_id:
            raise ValueError("an epic cannot belong to another epic")
        required = {"kind", "title", "description", "status", "priority", "labels", "dependencies"}
        invalid = sorted(
            field for field in required & self.model_fields_set if getattr(self, field) is None
        )
        if invalid:
            raise ValueError(f"task fields cannot be null: {', '.join(invalid)}")
        if "title" in self.model_fields_set and self.title is not None and not self.title.strip():
            raise ValueError("task title cannot be blank")
        return self


class TurnCommit(BaseModel):
    assistant_response: str
    topic_changed: bool = False
    segment_summary: str = ""
    task_actions: list[TaskAction] = Field(default_factory=list)
    profile_update: ProjectUpdate | None = None
    used_memory_ids: list[str] | None = None
    receipt: str = ""

    @model_validator(mode="after")
    def validate_segment(self):
        if self.topic_changed and not self.segment_summary.strip():
            raise ValueError("topic_changed requires segment_summary")
        return self


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: TaskKind = "task"
    epic_id: str | None = None
    sprint_id: str | None = None
    description: str = ""
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    labels: list[str] = Field(default_factory=list)
    objective: str | None = None
    next_action: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    rationale: str | None = None
    due_at: datetime | None = None

    @model_validator(mode="after")
    def validate_hierarchy(self):
        if not self.title.strip():
            raise ValueError("task title cannot be blank")
        if self.kind == "epic" and self.epic_id:
            raise ValueError("an epic cannot belong to another epic")
        return self


class TaskUpdate(BaseModel):
    kind: TaskKind | None = None
    epic_id: str | None = None
    sprint_id: str | None = None
    title: str | None = Field(default=None, max_length=300)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    labels: list[str] | None = None
    objective: str | None = None
    next_action: str | None = None
    dependencies: list[str] | None = None
    rationale: str | None = None
    completion_evidence: str | None = None
    due_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_non_nullable_fields(self):
        required = {"kind", "title", "description", "status", "priority", "labels", "dependencies"}
        invalid = sorted(
            field for field in required & self.model_fields_set if getattr(self, field) is None
        )
        if invalid:
            raise ValueError(f"task fields cannot be null: {', '.join(invalid)}")
        if "title" in self.model_fields_set and self.title is not None and not self.title.strip():
            raise ValueError("task title cannot be blank")
        if self.kind == "epic" and self.epic_id:
            raise ValueError("an epic cannot belong to another epic")
        return self


class TaskSearch(BaseModel):
    """A ranked task lookup; listing the complete archive remains a separate operation."""

    query: str = Field(min_length=1, max_length=10_000)
    scope: Literal["active", "completed", "all"] = "active"
    limit: int = Field(default=8, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=40)
    status: TaskStatus | None = None
    kind: TaskKind | None = None
    epic_id: str | None = None
    sprint_id: str | None = None
    placement: TaskPlacement = "all"
    label: str | None = Field(default=None, max_length=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("task search query cannot be blank")
        return normalized


class SprintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(default="", max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sprint title cannot be blank")
        return value


class SprintTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)


class SprintUpdate(SprintTransition):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def validate_changes(self):
        for key in {"title", "objective"} & self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(f"sprint {key} cannot be null")
        if self.title is not None and not self.title.strip():
            raise ValueError("sprint title cannot be blank")
        return self


class SprintArchive(SprintTransition):
    unfinished_destination: Literal["backlog", "sprint"]
    destination_sprint_id: str | None = None

    @model_validator(mode="after")
    def validate_destination(self):
        if (self.unfinished_destination == "sprint") != bool(self.destination_sprint_id):
            raise ValueError("choose backlog or supply a destination sprint")
        return self


class SprintHistoryCreate(SprintCreate):
    task_ids: list[str] = Field(min_length=1, max_length=1_000)

    @field_validator("task_ids")
    @classmethod
    def unique_task_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(not item.strip() for item in value):
            raise ValueError("historical task ids must be unique and non-empty")
        return value


class PlanCreate(BaseModel):
    """A broad design or decision record, optionally linked to existing work."""

    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(default="", max_length=20_000)
    content: str = Field(default="", max_length=500_000)
    status: PlanStatus = "draft"
    labels: list[str] = Field(default_factory=list, max_length=100)
    work_item_ids: list[str] = Field(default_factory=list, max_length=250)
    rationale: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def validate_plan(self):
        if not self.title.strip():
            raise ValueError("plan title cannot be blank")
        if any(not item.strip() for item in self.work_item_ids):
            raise ValueError("plan work item ids cannot be blank")
        if len(set(self.work_item_ids)) != len(self.work_item_ids):
            raise ValueError("plan work item ids must be unique")
        return self


class PlanUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    objective: str | None = Field(default=None, max_length=20_000)
    content: str | None = Field(default=None, max_length=500_000)
    status: PlanStatus | None = None
    labels: list[str] | None = Field(default=None, max_length=100)
    work_item_ids: list[str] | None = Field(default=None, max_length=250)
    rationale: str = Field(default="", max_length=2_000)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_plan(self):
        required = {"title", "objective", "content", "status", "labels", "work_item_ids"}
        invalid = sorted(
            field for field in required & self.model_fields_set if getattr(self, field) is None
        )
        if invalid:
            raise ValueError(f"plan fields cannot be null: {', '.join(invalid)}")
        if "title" in self.model_fields_set and self.title is not None and not self.title.strip():
            raise ValueError("plan title cannot be blank")
        if self.work_item_ids is not None:
            if any(not item.strip() for item in self.work_item_ids):
                raise ValueError("plan work item ids cannot be blank")
            if len(set(self.work_item_ids)) != len(self.work_item_ids):
                raise ValueError("plan work item ids must be unique")
        return self


class BackupRestoreRegister(BaseModel):
    archive_name: str = Field(min_length=1, max_length=500, pattern=r"^[^/\\]+\.dduobackup$")
    size_bytes: int = Field(ge=1)
    manifest: dict


class SessionPrivacyUpdate(BaseModel):
    off_record: bool


class SleepRequest(BaseModel):
    session_id: str | None = None
    provider: Literal["codex", "claude"] | None = None
    resume_auth: bool = False
    trigger: Literal[
        "manual",
        "session_start",
        "topic_boundary",
        "compaction",
        "idle",
        "threshold",
    ] = "manual"


class MemoryForget(BaseModel):
    memory_id: str
    rationale: str = Field(min_length=1, max_length=2_000)


class ArtifactCreate(BaseModel):
    turn_id: str | None = None
    kind: Literal["file", "image", "document", "url", "audio", "other"] = "file"
    filename: str = Field(default="", max_length=500)
    mime_type: str = Field(default="application/octet-stream", max_length=200)
    source_uri: str = Field(default="", max_length=4_000)
    content_base64: str | None = None
    extracted_text: str = Field(default="", max_length=500_000)
    summary: str = Field(default="", max_length=100_000)
    metadata: dict = Field(default_factory=dict)


class SleepTopicPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    query_text: str = Field(min_length=1, max_length=20_000)
    source_turn_ids: list[str] = Field(min_length=1, max_length=100)
    source_message_ids: list[str] = Field(default_factory=list, max_length=200)


class TopicSegmentationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[SleepTopicPayload] = Field(max_length=50)


class MemoryActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "replace_current", "set_status"]
    target_node_type: Literal["episode", "reusable_fact", "heuristic"]
    target_node_key: str = Field(min_length=1, max_length=500)
    text: str = Field(default="", max_length=200_000)
    status: Literal["active", "inactive", "superseded"] = "active"
    source_message_ids: list[str] = Field(default_factory=list, max_length=200)
    source_node_ids: list[str] = Field(default_factory=list, max_length=100)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_semantics(self):
        if self.action in {"create", "replace_current"} and not self.text.strip():
            raise ValueError(f"{self.action} requires non-empty text")
        if self.action == "set_status" and self.status == "active":
            raise ValueError("set_status must invalidate or supersede a memory")
        return self


class TopicMemoryPlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=1, max_length=100)
    memory_actions: list[MemoryActionPayload] = Field(max_length=100)


class BatchMemoryPlanningPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_plans: list[TopicMemoryPlanPayload] = Field(max_length=50)


class ConsolidatedTopicPayload(SleepTopicPayload):
    """A topic and its durable-memory decision from a single model call."""

    language: MemoryLanguage
    memory_actions: list[MemoryActionPayload] = Field(default_factory=list, max_length=100)


class MemoryConsolidationPayload(BaseModel):
    """The complete, one-pass output accepted from a sleep provider."""

    model_config = ConfigDict(extra="forbid")

    topics: list[ConsolidatedTopicPayload] = Field(max_length=50)
