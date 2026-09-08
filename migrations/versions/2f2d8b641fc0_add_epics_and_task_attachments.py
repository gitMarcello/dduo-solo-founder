"""add epics and task attachments

Revision ID: 2f2d8b641fc0
Revises: 91c4f1a8e2b7
"""

from alembic import op
import sqlalchemy as sa


revision = "2f2d8b641fc0"
down_revision = "91c4f1a8e2b7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tasks",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="task"),
    )
    op.add_column("tasks", sa.Column("epic_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_tasks_epic_id_tasks",
        "tasks",
        "tasks",
        ["epic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tasks_project_kind_status",
        "tasks",
        ["project_id", "kind", "status"],
    )
    op.create_table(
        "task_artifacts",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "artifact_id"),
    )


def downgrade():
    op.drop_table("task_artifacts")
    op.drop_index("ix_tasks_project_kind_status", table_name="tasks")
    op.drop_constraint("fk_tasks_epic_id_tasks", "tasks", type_="foreignkey")
    op.drop_column("tasks", "epic_id")
    op.drop_column("tasks", "kind")
