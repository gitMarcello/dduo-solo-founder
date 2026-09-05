"""add typed sleep failure metadata

Revision ID: 8b78cc6a9f10
Revises: 2f2d8b641fc0
"""

from alembic import op
import sqlalchemy as sa


revision = "8b78cc6a9f10"
down_revision = "2f2d8b641fc0"
branch_labels = None
depends_on = None


def upgrade():
    # Additive migration: alpha job history and retry state remain intact.
    op.add_column("sleep_jobs", sa.Column("error_kind", sa.String(length=40), nullable=True))
    op.add_column("sleep_jobs", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sleep_jobs_error_kind", "sleep_jobs", ["error_kind"])


def downgrade():
    op.drop_index("ix_sleep_jobs_error_kind", table_name="sleep_jobs")
    op.drop_column("sleep_jobs", "retry_at")
    op.drop_column("sleep_jobs", "error_kind")
