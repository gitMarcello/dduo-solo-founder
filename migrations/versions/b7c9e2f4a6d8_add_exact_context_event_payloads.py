"""add exact context event payloads

Revision ID: b7c9e2f4a6d8
Revises: 6d2f4c8a1b3e
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c9e2f4a6d8"
down_revision = "6d2f4c8a1b3e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "context_event_payloads",
        sa.Column("observability_event_id", sa.String(length=36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("producer_version", sa.String(length=80), nullable=False),
        sa.Column("render_version", sa.String(length=80), nullable=False),
        sa.Column("estimator_version", sa.String(length=80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="ck_context_event_payload_sha256_length"
        ),
        sa.ForeignKeyConstraint(
            ["observability_event_id"], ["observability_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("observability_event_id"),
    )


def downgrade():
    op.drop_table("context_event_payloads")
