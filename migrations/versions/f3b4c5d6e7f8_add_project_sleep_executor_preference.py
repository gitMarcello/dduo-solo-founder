"""Select a preferred subscription per project while retaining actual job provenance.

Revision ID: f3b4c5d6e7f8
Revises: e72b1d4c9a60
"""

from alembic import op
import sqlalchemy as sa


revision = "f3b4c5d6e7f8"
down_revision = "e72b1d4c9a60"
branch_labels = None
depends_on = None


def upgrade():
    # Existing projects intentionally stay neutral.  Their next supported
    # session establishes the real preference; pending jobs retain their own
    # historical executor below, so migration never rewrites work in flight.
    op.add_column(
        "projects",
        sa.Column("sleep_executor_preference", sa.String(length=20), nullable=True),
    )
    with op.batch_alter_table("projects") as batch:
        batch.create_check_constraint(
            "ck_projects_sleep_executor_preference",
            "sleep_executor_preference IS NULL OR "
            "sleep_executor_preference IN ('codex', 'claude')",
        )

    op.add_column(
        "sleep_jobs",
        sa.Column("executor_preference", sa.String(length=20), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE sleep_jobs SET executor_preference = provider "
            "WHERE executor_preference IS NULL"
        )
    )


def downgrade():
    op.drop_column("sleep_jobs", "executor_preference")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("ck_projects_sleep_executor_preference", type_="check")
    op.drop_column("projects", "sleep_executor_preference")
