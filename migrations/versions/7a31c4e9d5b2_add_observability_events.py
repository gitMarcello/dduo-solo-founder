"""add local observability events

Revision ID: 7a31c4e9d5b2
Revises: c4e98b1f0a22
"""

from alembic import op
import sqlalchemy as sa


revision = "7a31c4e9d5b2"
down_revision = "c4e98b1f0a22"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "observability_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("retrieval_run_id", sa.String(length=36), nullable=True),
        sa.Column("sleep_job_id", sa.String(length=36), nullable=True),
        sa.Column("outbox_event_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("measurement_source", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reported_total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("characters", sa.BigInteger(), nullable=True),
        sa.Column("utf8_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("provider_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("vector_store_duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("selected_count", sa.Integer(), nullable=True),
        sa.Column("dropped_count", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=20, scale=12), nullable=True),
        sa.Column("unit_price_usd_per_million", sa.Numeric(precision=20, scale=12), nullable=True),
        sa.Column("pricing_version", sa.String(length=80), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 1", name="ck_observability_attempt_nonnegative"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_observability_input_tokens"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_observability_output_tokens"
        ),
        sa.CheckConstraint("utf8_bytes IS NULL OR utf8_bytes >= 0", name="ck_observability_bytes"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_observability_duration"
        ),
        sa.CheckConstraint(
            "(cached_input_tokens IS NULL OR cached_input_tokens >= 0) "
            "AND (cache_write_input_tokens IS NULL OR cache_write_input_tokens >= 0) "
            "AND (reasoning_tokens IS NULL OR reasoning_tokens >= 0) "
            "AND (reported_total_tokens IS NULL OR reported_total_tokens >= 0) "
            "AND (characters IS NULL OR characters >= 0) "
            "AND (provider_duration_ms IS NULL OR provider_duration_ms >= 0) "
            "AND (vector_store_duration_ms IS NULL OR vector_store_duration_ms >= 0) "
            "AND (request_count IS NULL OR request_count >= 0) "
            "AND (item_count IS NULL OR item_count >= 0) "
            "AND (candidate_count IS NULL OR candidate_count >= 0) "
            "AND (selected_count IS NULL OR selected_count >= 0) "
            "AND (dropped_count IS NULL OR dropped_count >= 0) "
            "AND (cost_usd IS NULL OR cost_usd >= 0) "
            "AND (unit_price_usd_per_million IS NULL OR unit_price_usd_per_million >= 0)",
            name="ck_observability_metrics_nonnegative",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retrieval_run_id"], ["retrieval_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sleep_job_id"], ["sleep_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outbox_event_id"], ["outbox_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_observability_project_key"),
    )
    op.create_index(
        "ix_observability_project_occurred", "observability_events", ["project_id", "occurred_at"]
    )
    op.create_index(
        "ix_observability_project_category_occurred",
        "observability_events",
        ["project_id", "category", "occurred_at"],
    )
    op.create_index("ix_observability_sleep_job", "observability_events", ["sleep_job_id"])
    op.create_index("ix_observability_retrieval_run", "observability_events", ["retrieval_run_id"])


def downgrade():
    op.drop_index("ix_observability_retrieval_run", table_name="observability_events")
    op.drop_index("ix_observability_sleep_job", table_name="observability_events")
    op.drop_index("ix_observability_project_category_occurred", table_name="observability_events")
    op.drop_index("ix_observability_project_occurred", table_name="observability_events")
    op.drop_table("observability_events")
