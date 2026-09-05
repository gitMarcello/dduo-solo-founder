"""add task index outbox readiness

Revision ID: d91f5a7c2e40
Revises: b7c9e2f4a6d8
"""

from alembic import op
import sqlalchemy as sa


revision = "d91f5a7c2e40"
down_revision = "b7c9e2f4a6d8"
branch_labels = None
depends_on = None


def upgrade():
    # Task vectors remain a derived projection. The migration deliberately
    # performs no provider or Qdrant calls and does not mutate task content.
    op.add_column(
        "projects",
        sa.Column(
            "task_index_reconciled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "projects",
        sa.Column("task_index_epoch", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "outbox_events",
        sa.Column(
            "delivery_priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_outbox_events_ready",
        "outbox_events",
        ["delivery_priority", "next_attempt_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )
    op.create_index(
        "ix_outbox_events_project_type_status",
        "outbox_events",
        ["project_id", "event_type", "status", "next_attempt_at"],
        unique=False,
    )
    op.add_column(
        "projects",
        sa.Column(
            "task_index_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade():
    op.drop_index("ix_outbox_events_project_type_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_ready", table_name="outbox_events")
    op.drop_column("outbox_events", "delivery_priority")
    op.drop_column("projects", "task_index_epoch")
    op.drop_column("projects", "task_index_generation")
    op.drop_column("projects", "task_index_reconciled")
