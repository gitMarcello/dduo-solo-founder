"""add explicit plan work item positions

Revision ID: 6d2f4c8a1b3e
Revises: 7a31c4e9d5b2
"""

from alembic import op
import sqlalchemy as sa


revision = "6d2f4c8a1b3e"
down_revision = "7a31c4e9d5b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plan_work_items", sa.Column("position", sa.Integer(), nullable=True))
    # Preserve the only deterministic order exposed before positions existed.
    # The correlated count is portable across PostgreSQL and SQLite.
    op.execute(
        """
        UPDATE plan_work_items
        SET position = (
            SELECT COUNT(*) - 1
            FROM plan_work_items AS preceding
            WHERE preceding.plan_id = plan_work_items.plan_id
              AND (
                  preceding.created_at < plan_work_items.created_at
                  OR (
                      preceding.created_at = plan_work_items.created_at
                      AND preceding.task_id <= plan_work_items.task_id
                  )
              )
        )
        """
    )
    with op.batch_alter_table("plan_work_items") as batch_op:
        batch_op.alter_column("position", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_plan_work_items_plan_position", ["plan_id", "position"]
        )
        batch_op.create_check_constraint(
            "ck_plan_work_items_position_nonnegative", "position >= 0"
        )


def downgrade():
    with op.batch_alter_table("plan_work_items") as batch_op:
        batch_op.drop_constraint("ck_plan_work_items_position_nonnegative", type_="check")
        batch_op.drop_constraint("uq_plan_work_items_plan_position", type_="unique")
        batch_op.drop_column("position")
