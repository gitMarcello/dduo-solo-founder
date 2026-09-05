from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(Text)
    cause: Mapped[str] = mapped_column(Text, default="")
    principles: Mapped[list] = mapped_column(JSON, default=list)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[str] = mapped_column(Text, default="")
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    backup_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_generation: Mapped[int] = mapped_column(Integer, default=1)
    last_backed_up_generation: Mapped[int] = mapped_column(Integer, default=0)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_index_reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    task_index_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    task_index_epoch: Mapped[str | None] = mapped_column(String(36), nullable=True)
    authority_node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authority_target_node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authority_transfer_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authority_generation: Mapped[int] = mapped_column(Integer, default=1)
    authority_state: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("authority_generation >= 1", name="ck_projects_authority_generation"),
        CheckConstraint(
            "authority_state IN ('active', 'transfer_pending', 'transferred')",
            name="ck_projects_authority_state",
        ),
        CheckConstraint(
            "authority_transfer_nonce IS NULL OR length(authority_transfer_nonce) = 64",
            name="ck_projects_authority_transfer_nonce",
        ),
    )


class TeamMember(Base):
    """One auditable human identity inside a project-scoped team."""

    __tablename__ = "team_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(200))
    capability: Mapped[str] = mapped_column(String(40), default="project_member")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_team_members_project_status", "project_id", "status"),
        CheckConstraint(
            "capability IN ('infrastructure_manager', 'project_member')",
            name="ck_team_members_capability",
        ),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_team_members_status"),
    )


class TeamAccessToken(Base):
    """A revocable device credential; plaintext is never persisted server-side."""

    __tablename__ = "team_access_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    member_id: Mapped[str] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[str] = mapped_column(String(100))
    device_label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("member_id", "device_id", name="uq_team_token_member_device"),
        Index("ix_team_tokens_project_member", "project_id", "member_id"),
        CheckConstraint("length(token_hash) = 64", name="ck_team_token_hash_length"),
    )


class TeamInvitation(Base):
    """A short-lived, one-use invitation into exactly one project."""

    __tablename__ = "team_invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    invited_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    consumed_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_access_tokens.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_team_invitations_project_expiry", "project_id", "expires_at"),
        CheckConstraint("length(token_hash) = 64", name="ck_team_invite_hash_length"),
    )


class BrowserAuthCredential(Base):
    """One-time browser ticket or revocable server-side browser session."""

    __tablename__ = "browser_auth_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    member_id: Mapped[str] = mapped_column(ForeignKey("team_members.id", ondelete="CASCADE"))
    access_token_id: Mapped[str] = mapped_column(
        ForeignKey("team_access_tokens.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_browser_auth_project_member", "project_id", "member_id"),
        Index("ix_browser_auth_project_expiry", "project_id", "expires_at"),
        CheckConstraint("kind IN ('ticket', 'session')", name="ck_browser_auth_kind"),
        CheckConstraint("length(secret_hash) = 64", name="ck_browser_auth_hash_length"),
        CheckConstraint(
            "(kind = 'ticket' AND csrf_secret_hash IS NULL) OR "
            "(kind = 'session' AND length(csrf_secret_hash) = 64)",
            name="ck_browser_auth_csrf_hash",
        ),
    )


class OperationalManual(Base):
    """The current project operating manual, edited only by infrastructure managers."""

    __tablename__ = "operational_manuals"
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_by_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("version >= 0", name="ck_operational_manual_version"),)


class OperationalManualRevision(Base):
    """Immutable manual snapshots and their idempotent mutation identity."""

    __tablename__ = "operational_manual_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_manual_project_version"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_manual_project_idempotency"),
        CheckConstraint("version >= 1", name="ck_manual_revision_version"),
        CheckConstraint("length(content_sha256) = 64", name="ck_manual_content_hash_length"),
    )


class Session(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    client: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))
    member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    access_token_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_access_tokens.id", ondelete="SET NULL"), nullable=True
    )
    open_segment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    off_record: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("project_id", "client", "external_id"),)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="open")
    summary: Mapped[str] = mapped_column(Text, default="")
    sleep_status: Mapped[str] = mapped_column(String(30), default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200))
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id"), nullable=True)
    user_prompt: Mapped[str] = mapped_column(Text)
    assistant_response: Mapped[str] = mapped_column(Text, default="")
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    stop_attempts: Mapped[int] = mapped_column(Integer, default=0)
    semantic_commit: Mapped[dict] = mapped_column(JSON, default=dict)
    off_record: Mapped[bool] = mapped_column(Boolean, default=False)
    sleep_status: Mapped[str] = mapped_column(String(30), default="open")
    retrieval_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retrieved_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    used_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("session_id", "external_id"),)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    node_type: Mapped[str] = mapped_column(String(30))
    node_key: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    memory_group_id: Mapped[str] = mapped_column(String(36), default=uid)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_turn_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_artifact_ids: Mapped[list] = mapped_column(JSON, default=list)
    sleep_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_memories_project_status_type", "project_id", "status", "node_type"),
        Index("ix_memories_project_key", "project_id", "node_type", "node_key"),
        UniqueConstraint("memory_group_id", "revision", name="uq_memory_group_revision"),
    )


class Sprint(Base):
    """A small project-owned execution window; closing never removes work."""

    __tablename__ = "sprints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="planned")
    version: Mapped[int] = mapped_column(Integer, default=1)
    archive_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_sprints_project_id"),
        CheckConstraint("status IN ('planned', 'active', 'archived')", name="ck_sprints_status"),
        CheckConstraint("version >= 1", name="ck_sprints_version"),
        Index("ix_sprints_project_status", "project_id", "status"),
        Index(
            "uq_sprints_active_project", "project_id", unique=True,
            postgresql_where=text("status = 'active'"), sqlite_where=text("status = 'active'"),
        ),
    )


class SprintTaskSnapshot(Base):
    """Immutable membership and outcome at a particular sprint closure."""

    __tablename__ = "sprint_task_snapshots"
    sprint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    closure_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36))
    snapshot: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(30))
    destination_sprint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "sprint_id"], ["sprints.project_id", "sprints.id"],
            name="fk_sprint_snapshots_project_sprint",
        ),
        Index("ix_sprint_snapshots_project_task", "project_id", "task_id"),
    )


class SprintMutation(Base):
    """Project-scoped replay receipts prevent duplicate lifecycle transitions."""

    __tablename__ = "sprint_mutations"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20), default="task")
    epic_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    sprint_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="todo")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    completion_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_tasks_project_kind_status", "project_id", "kind", "status"),
        Index("ix_tasks_project_sprint_status", "project_id", "sprint_id", "status"),
        ForeignKeyConstraint(
            ["project_id", "sprint_id"], ["sprints.project_id", "sprints.id"],
            name="fk_tasks_project_sprint",
        ),
        CheckConstraint("kind != 'epic' OR sprint_id IS NULL", name="ck_tasks_epic_no_sprint"),
    )


class TaskRevision(Base):
    __tablename__ = "task_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(50), default="agent")
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("task_id", "version"),)


class Plan(Base):
    """A versioned planning document that can inform one or more work items."""

    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (Index("ix_plans_project_status", "project_id", "status"),)


class PlanRevision(Base):
    """Immutable snapshots make meaningful planning decisions auditable."""

    __tablename__ = "plan_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(50), default="agent")
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("plan_id", "version"),)


class PlanWorkItem(Base):
    """Many-to-many links keep a design independent from the execution hierarchy."""

    __tablename__ = "plan_work_items"
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        Index("ix_plan_work_items_task_id", "task_id"),
        UniqueConstraint("plan_id", "position", name="uq_plan_work_items_plan_position"),
        CheckConstraint("position >= 0", name="ck_plan_work_items_position_nonnegative"),
    )


class PlanArtifact(Base):
    """Files attached to a plan remain project-scoped reusable artifacts."""

    __tablename__ = "plan_artifacts"
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(30), default="attachment")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("agent_sessions.id"), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    query_text: Mapped[str] = mapped_column(Text)
    latest_query_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="context_ready")
    selected_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    selected_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    dropped_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    degraded_reasons: Mapped[list] = mapped_column(JSON, default=list)
    threshold: Mapped[float] = mapped_column(default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SleepJob(Base):
    __tablename__ = "sleep_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(20))
    source_client: Mapped[str | None] = mapped_column(String(30), nullable=True)
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    input_turn_ids: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_sleep_jobs_ready", "status", "not_before"),)


class SleepTopic(Base):
    __tablename__ = "sleep_topics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(ForeignKey("sleep_jobs.id", ondelete="CASCADE"))
    topic_id: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(300))
    query_text: Mapped[str] = mapped_column(Text)
    source_turn_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    related_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    planned_actions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30), default="file")
    filename: Mapped[str] = mapped_column(String(500), default="")
    mime_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    source_uri: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)


class TurnArtifact(Base):
    __tablename__ = "turn_artifacts"
    turn_id: Mapped[str] = mapped_column(
        ForeignKey("turns.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(30), default="source")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(30), default="attachment")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(50), default="agent")
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProfileRevision(Base):
    __tablename__ = "profile_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, default="")
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("project_id", "version"),)


class RawEvent(Base):
    __tablename__ = "raw_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("agent_sessions.id"), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("turns.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(50), default="agent")
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivery_priority: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index(
            "ix_outbox_events_ready",
            "delivery_priority",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
            sqlite_where=text("status IN ('pending', 'failed')"),
        ),
        Index(
            "ix_outbox_events_project_type_status",
            "project_id",
            "event_type",
            "status",
            "next_attempt_at",
        ),
    )


class ObservabilityEvent(Base):
    """An append-only, content-free record of local dDuo resource usage."""

    __tablename__ = "observability_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True
    )
    turn_id: Mapped[str | None] = mapped_column(
        ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    retrieval_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="SET NULL"), nullable=True
    )
    sleep_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("sleep_jobs.id", ondelete="SET NULL"), nullable=True
    )
    outbox_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("outbox_events.id", ondelete="SET NULL"), nullable=True
    )
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(80))
    scope: Mapped[str] = mapped_column(String(24), default="")
    status: Mapped[str] = mapped_column(String(24), default="success")
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    measurement_source: Mapped[str] = mapped_column(String(32), default="unavailable")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_write_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_total_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    characters: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    utf8_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vector_store_duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dropped_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12), nullable=True)
    unit_price_usd_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 12), nullable=True
    )
    pricing_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_observability_project_key"),
        Index("ix_observability_project_occurred", "project_id", "occurred_at"),
        Index(
            "ix_observability_project_category_occurred", "project_id", "category", "occurred_at"
        ),
        Index(
            "ix_observability_project_actor_occurred",
            "project_id",
            "actor_member_id",
            "occurred_at",
        ),
        Index("ix_observability_sleep_job", "sleep_job_id"),
        Index("ix_observability_retrieval_run", "retrieval_run_id"),
        CheckConstraint("attempt >= 1", name="ck_observability_attempt_nonnegative"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_observability_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_observability_output_tokens"
        ),
        CheckConstraint("utf8_bytes IS NULL OR utf8_bytes >= 0", name="ck_observability_bytes"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_observability_duration"
        ),
        CheckConstraint(
            " AND ".join(
                [
                    "(cached_input_tokens IS NULL OR cached_input_tokens >= 0)",
                    "(cache_write_input_tokens IS NULL OR cache_write_input_tokens >= 0)",
                    "(reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
                    "(reported_total_tokens IS NULL OR reported_total_tokens >= 0)",
                    "(characters IS NULL OR characters >= 0)",
                    "(provider_duration_ms IS NULL OR provider_duration_ms >= 0)",
                    "(vector_store_duration_ms IS NULL OR vector_store_duration_ms >= 0)",
                    "(request_count IS NULL OR request_count >= 0)",
                    "(item_count IS NULL OR item_count >= 0)",
                    "(candidate_count IS NULL OR candidate_count >= 0)",
                    "(selected_count IS NULL OR selected_count >= 0)",
                    "(dropped_count IS NULL OR dropped_count >= 0)",
                    "(cost_usd IS NULL OR cost_usd >= 0)",
                    "(unit_price_usd_per_million IS NULL OR unit_price_usd_per_million >= 0)",
                ]
            ),
            name="ck_observability_metrics_nonnegative",
        ),
    )


class ContextEventPayload(Base):
    """The immutable, exact model-visible payload for one context event."""

    __tablename__ = "context_event_payloads"

    observability_event_id: Mapped[str] = mapped_column(
        ForeignKey("observability_events.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    producer_version: Mapped[str] = mapped_column(String(80))
    render_version: Mapped[str] = mapped_column(String(80))
    estimator_version: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    components: Mapped[list] = mapped_column(JSON, default=list)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (
        CheckConstraint(
            "length(content_sha256) = 64", name="ck_context_event_payload_sha256_length"
        ),
    )


class BackupRecord(Base):
    __tablename__ = "backup_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    archive_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    includes_qdrant: Mapped[bool] = mapped_column(Boolean, default=False)
    retained: Mapped[bool] = mapped_column(Boolean, default=True)
    source_generation: Mapped[int] = mapped_column(Integer, default=0)
    manifest_json: Mapped[dict] = mapped_column("manifest", JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
