"""restore the dDuo sleep-driven memory engine

Revision ID: 91c4f1a8e2b7
Revises: 4f9d45f8796c
"""

from alembic import op
import sqlalchemy as sa


revision = "91c4f1a8e2b7"
down_revision = "4f9d45f8796c"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve alpha data. Existing memories become one-record revision chains;
    # their original source turns and queued vector writes remain usable. This
    # migration predates a stable release, so keeping it additive also protects
    # installations that upgrade directly from any early alpha snapshot.

    op.add_column(
        "agent_sessions",
        sa.Column("off_record", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "segments",
        sa.Column("sleep_status", sa.String(length=30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "turns", sa.Column("off_record", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "turns",
        sa.Column("sleep_status", sa.String(length=30), nullable=False, server_default="open"),
    )
    op.add_column("turns", sa.Column("retrieval_run_id", sa.String(length=36), nullable=True))
    op.add_column(
        "turns", sa.Column("retrieved_memory_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "turns", sa.Column("used_memory_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column("turns", sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("memories", sa.Column("memory_group_id", sa.String(length=36), nullable=True))
    op.add_column("memories", sa.Column("superseded_by_id", sa.String(length=36), nullable=True))
    op.add_column(
        "memories", sa.Column("source_message_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "memories", sa.Column("source_node_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "memories", sa.Column("source_artifact_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column("memories", sa.Column("sleep_job_id", sa.String(length=36), nullable=True))
    op.add_column(
        "memories",
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("memories", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    # A legacy memory is its own first revision. Use its existing primary key
    # rather than manufacturing a value in migration SQL so PostgreSQL and
    # SQLite-compatible test snapshots retain the exact original identity.
    op.execute("UPDATE memories SET memory_group_id = id WHERE memory_group_id IS NULL")
    op.alter_column("memories", "memory_group_id", nullable=False)
    op.create_index(
        "ix_memories_project_status_type", "memories", ["project_id", "status", "node_type"]
    )
    op.create_index("ix_memories_project_key", "memories", ["project_id", "node_type", "node_key"])
    op.create_unique_constraint(
        "uq_memory_group_revision", "memories", ["memory_group_id", "revision"]
    )

    op.create_table(
        "task_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(length=50), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version"),
    )
    op.create_table(
        "retrieval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("latest_query_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("selected_memory_ids", sa.JSON(), nullable=False),
        sa.Column("selected_scores", sa.JSON(), nullable=False),
        sa.Column("dropped_memory_ids", sa.JSON(), nullable=False),
        sa.Column("degraded_reasons", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sleep_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("input_turn_ids", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_sleep_jobs_ready", "sleep_jobs", ["status", "not_before"])
    op.create_table(
        "sleep_topics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("topic_id", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("source_turn_ids", sa.JSON(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("related_memory_ids", sa.JSON(), nullable=False),
        sa.Column("planned_actions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["sleep_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "content_hash"),
    )
    op.create_table(
        "turn_artifacts",
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("turn_id", "artifact_id"),
    )


def downgrade():
    op.drop_table("turn_artifacts")
    op.drop_table("artifacts")
    op.drop_table("sleep_topics")
    op.drop_index("ix_sleep_jobs_ready", table_name="sleep_jobs")
    op.drop_table("sleep_jobs")
    op.drop_table("retrieval_runs")
    op.drop_table("task_revisions")
    op.drop_constraint("uq_memory_group_revision", "memories", type_="unique")
    op.drop_index("ix_memories_project_key", table_name="memories")
    op.drop_index("ix_memories_project_status_type", table_name="memories")
    for column in (
        "valid_to",
        "valid_from",
        "sleep_job_id",
        "source_artifact_ids",
        "source_node_ids",
        "source_message_ids",
        "superseded_by_id",
        "memory_group_id",
    ):
        op.drop_column("memories", column)
    for column in (
        "committed_at",
        "used_memory_ids",
        "retrieved_memory_ids",
        "retrieval_run_id",
        "sleep_status",
        "off_record",
    ):
        op.drop_column("turns", column)
    op.drop_column("segments", "sleep_status")
    op.drop_column("agent_sessions", "last_activity_at")
    op.drop_column("agent_sessions", "off_record")
