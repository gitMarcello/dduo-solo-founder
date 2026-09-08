"""add encrypted backup tracking

Revision ID: 4f9d45f8796c
Revises: b480c779500b
"""

from alembic import op
import sqlalchemy as sa


revision = "4f9d45f8796c"
down_revision = "b480c779500b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column("backup_dirty", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "projects", sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "projects",
        sa.Column("backup_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column("last_backed_up_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("archive_name", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("includes_qdrant", sa.Boolean(), nullable=False),
        sa.Column("retained", sa.Boolean(), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("backup_records")
    op.drop_column("projects", "last_backed_up_generation")
    op.drop_column("projects", "backup_generation")
    op.drop_column("projects", "last_backup_at")
    op.drop_column("projects", "backup_dirty")
