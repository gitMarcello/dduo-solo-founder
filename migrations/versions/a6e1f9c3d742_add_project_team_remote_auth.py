"""add project team and remote authentication

Revision ID: a6e1f9c3d742
Revises: d91f5a7c2e40
"""

from alembic import op
import sqlalchemy as sa


revision = "a6e1f9c3d742"
down_revision = "d91f5a7c2e40"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("authority_node_id", sa.String(length=100), nullable=True))
    op.add_column(
        "projects",
        sa.Column("authority_target_node_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("authority_transfer_nonce", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("authority_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "authority_state",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_check_constraint(
        "ck_projects_authority_generation", "projects", "authority_generation >= 1"
    )
    op.create_check_constraint(
        "ck_projects_authority_state",
        "projects",
        "authority_state IN ('active', 'transfer_pending', 'transferred')",
    )
    op.create_check_constraint(
        "ck_projects_authority_transfer_nonce",
        "projects",
        "authority_transfer_nonce IS NULL OR length(authority_transfer_nonce) = 64",
    )

    op.create_table(
        "team_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "capability",
            sa.String(length=40),
            nullable=False,
            server_default="project_member",
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capability IN ('infrastructure_manager', 'project_member')",
            name="ck_team_members_capability",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_team_members_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_team_members_project_status", "team_members", ["project_id", "status"])

    op.create_table(
        "team_access_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=100), nullable=False),
        sa.Column("device_label", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_team_token_hash_length"),
        sa.ForeignKeyConstraint(["member_id"], ["team_members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id", "device_id", name="uq_team_token_member_device"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_team_tokens_project_member",
        "team_access_tokens",
        ["project_id", "member_id"],
    )

    op.create_table(
        "team_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("invited_by_member_id", sa.String(length=36), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_member_id", sa.String(length=36), nullable=True),
        sa.Column("consumed_token_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_team_invite_hash_length"),
        sa.ForeignKeyConstraint(
            ["consumed_by_member_id"], ["team_members.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["consumed_token_id"], ["team_access_tokens.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["invited_by_member_id"], ["team_members.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_team_invitations_project_expiry",
        "team_invitations",
        ["project_id", "expires_at"],
    )

    op.create_table(
        "browser_auth_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("member_id", sa.String(length=36), nullable=False),
        sa.Column("access_token_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_secret_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('ticket', 'session')", name="ck_browser_auth_kind"),
        sa.CheckConstraint("length(secret_hash) = 64", name="ck_browser_auth_hash_length"),
        sa.CheckConstraint(
            "(kind = 'ticket' AND csrf_secret_hash IS NULL) OR "
            "(kind = 'session' AND length(csrf_secret_hash) = 64)",
            name="ck_browser_auth_csrf_hash",
        ),
        sa.ForeignKeyConstraint(["access_token_id"], ["team_access_tokens.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["team_members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_hash"),
    )
    op.create_index(
        "ix_browser_auth_project_member",
        "browser_auth_credentials",
        ["project_id", "member_id"],
    )
    op.create_index(
        "ix_browser_auth_project_expiry",
        "browser_auth_credentials",
        ["project_id", "expires_at"],
    )

    op.create_table(
        "operational_manuals",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by_member_id", sa.String(length=36), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 0", name="ck_operational_manual_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_member_id"], ["team_members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "operational_manual_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("actor_member_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_manual_revision_version"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_manual_content_hash_length"),
        sa.ForeignKeyConstraint(["actor_member_id"], ["team_members.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_manual_project_idempotency"),
        sa.UniqueConstraint("project_id", "version", name="uq_manual_project_version"),
    )

    _actor_columns = (
        ("task_revisions", "actor_member_id", "fk_task_revisions_actor_member"),
        ("plan_revisions", "actor_member_id", "fk_plan_revisions_actor_member"),
        ("activities", "actor_member_id", "fk_activities_actor_member"),
        ("profile_revisions", "actor_member_id", "fk_profile_revisions_actor_member"),
        ("raw_events", "actor_member_id", "fk_raw_events_actor_member"),
        ("observability_events", "actor_member_id", "fk_observability_actor_member"),
    )
    for table, column, constraint in _actor_columns:
        op.add_column(table, sa.Column(column, sa.String(length=36), nullable=True))
        op.create_foreign_key(
            constraint,
            table,
            "team_members",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_observability_project_actor_occurred",
        "observability_events",
        ["project_id", "actor_member_id", "occurred_at"],
    )

    op.add_column("agent_sessions", sa.Column("member_id", sa.String(length=36), nullable=True))
    op.add_column(
        "agent_sessions", sa.Column("access_token_id", sa.String(length=36), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_sessions_member",
        "agent_sessions",
        "team_members",
        ["member_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_sessions_access_token",
        "agent_sessions",
        "team_access_tokens",
        ["access_token_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("sleep_jobs", sa.Column("source_client", sa.String(length=30), nullable=True))
    op.add_column("sleep_jobs", sa.Column("actor_member_id", sa.String(length=36), nullable=True))
    # Historical jobs used the interactive client as their executor. Preserve
    # that provenance while converging every upgraded stack on the server-owned
    # default; runtime configuration can subsequently choose another executor.
    op.execute(
        "UPDATE sleep_jobs AS job SET source_client = session.client, provider = 'codex' "
        "FROM agent_sessions AS session "
        "WHERE job.session_id = session.id AND job.source_client IS NULL"
    )
    op.create_foreign_key(
        "fk_sleep_jobs_actor_member",
        "sleep_jobs",
        "team_members",
        ["actor_member_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.execute(
        "UPDATE sleep_jobs SET provider = source_client WHERE source_client IS NOT NULL"
    )
    op.drop_constraint("fk_sleep_jobs_actor_member", "sleep_jobs", type_="foreignkey")
    op.drop_column("sleep_jobs", "actor_member_id")
    op.drop_column("sleep_jobs", "source_client")
    op.drop_constraint("fk_agent_sessions_access_token", "agent_sessions", type_="foreignkey")
    op.drop_constraint("fk_agent_sessions_member", "agent_sessions", type_="foreignkey")
    op.drop_column("agent_sessions", "access_token_id")
    op.drop_column("agent_sessions", "member_id")

    op.drop_index("ix_observability_project_actor_occurred", table_name="observability_events")
    for table, column, constraint in reversed(
        (
            ("task_revisions", "actor_member_id", "fk_task_revisions_actor_member"),
            ("plan_revisions", "actor_member_id", "fk_plan_revisions_actor_member"),
            ("activities", "actor_member_id", "fk_activities_actor_member"),
            ("profile_revisions", "actor_member_id", "fk_profile_revisions_actor_member"),
            ("raw_events", "actor_member_id", "fk_raw_events_actor_member"),
            ("observability_events", "actor_member_id", "fk_observability_actor_member"),
        )
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.drop_column(table, column)

    op.drop_table("operational_manual_revisions")
    op.drop_table("operational_manuals")
    op.drop_index("ix_browser_auth_project_expiry", table_name="browser_auth_credentials")
    op.drop_index("ix_browser_auth_project_member", table_name="browser_auth_credentials")
    op.drop_table("browser_auth_credentials")
    op.drop_index("ix_team_invitations_project_expiry", table_name="team_invitations")
    op.drop_table("team_invitations")
    op.drop_index("ix_team_tokens_project_member", table_name="team_access_tokens")
    op.drop_table("team_access_tokens")
    op.drop_index("ix_team_members_project_status", table_name="team_members")
    op.drop_table("team_members")

    op.drop_constraint("ck_projects_authority_state", "projects", type_="check")
    op.drop_constraint(
        "ck_projects_authority_transfer_nonce", "projects", type_="check"
    )
    op.drop_constraint("ck_projects_authority_generation", "projects", type_="check")
    op.drop_column("projects", "authority_state")
    op.drop_column("projects", "authority_generation")
    op.drop_column("projects", "authority_target_node_id")
    op.drop_column("projects", "authority_transfer_nonce")
    op.drop_column("projects", "authority_node_id")
