"""Add project sprints and immutable closure snapshots without assigning old tasks.

Revision ID: e72b1d4c9a60
Revises: a6e1f9c3d742
"""

from alembic import op
import sqlalchemy as sa


revision = "e72b1d4c9a60"
down_revision = "a6e1f9c3d742"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sprints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archive_version", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "id", name="uq_sprints_project_id"),
        sa.CheckConstraint("status IN ('planned', 'active', 'archived')", name="ck_sprints_status"),
        sa.CheckConstraint("version >= 1", name="ck_sprints_version"),
    )
    op.create_index("ix_sprints_project_status", "sprints", ["project_id", "status"])
    op.create_index(
        "uq_sprints_active_project",
        "sprints",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("sprint_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_tasks_project_sprint", "sprints", ["project_id", "sprint_id"], ["project_id", "id"]
        )
        batch.create_check_constraint(
            "ck_tasks_epic_no_sprint", "kind != 'epic' OR sprint_id IS NULL"
        )
        batch.create_index("ix_tasks_project_sprint_status", ["project_id", "sprint_id", "status"])
    op.create_table(
        "sprint_task_snapshots",
        sa.Column("sprint_id", sa.String(36), primary_key=True),
        sa.Column("closure_version", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("destination_sprint_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "sprint_id"],
            ["sprints.project_id", "sprints.id"],
            name="fk_sprint_snapshots_project_sprint",
        ),
    )
    op.create_index(
        "ix_sprint_snapshots_project_task", "sprint_task_snapshots", ["project_id", "task_id"]
    )
    op.create_table(
        "sprint_mutations",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("idempotency_key", sa.String(100), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The new metadata is repaired by the existing SQL-backed bootstrap/outbox.
    op.execute(sa.text("UPDATE projects SET task_index_reconciled = false"))


def downgrade():
    op.drop_table("sprint_mutations")
    op.drop_table("sprint_task_snapshots")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_project_sprint", type_="foreignkey")
        batch.drop_constraint("ck_tasks_epic_no_sprint", type_="check")
        batch.drop_index("ix_tasks_project_sprint_status")
        batch.drop_column("sprint_id")
    op.drop_table("sprints")
