"""host_deployments + deployment_revisions tables

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a1b2
Create Date: 2026-09-24 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c3d4e5f6a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_STATES_WHERE = "state NOT IN ('SUCCEEDED', 'FAILED', 'ROLLED_BACK')"


def upgrade() -> None:
    # 1. host_deployments without revision FKs (columns present, constraints added later)
    op.create_table(
        "host_deployments",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("host_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("project", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("target_alias", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("package_uri", sa.String(length=2048), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("package_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_revision_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("rollback_revision_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("ports_json", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ingress_json", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("health_json", JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.String(length=1024), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_agent_update_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host_id", "request_id", name="uq_host_deployments_host_request"),
    )
    op.create_index(
        "ix_host_deployments_host_state",
        "host_deployments",
        ["host_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_host_deployments_project_env_host",
        "host_deployments",
        ["project", "environment", "host_id"],
        unique=False,
    )

    # 2. Partial unique index — at most one non-terminal attempt per project/env/host
    op.create_index(
        "uq_host_deployments_active",
        "host_deployments",
        ["project", "environment", "host_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATES_WHERE),
    )

    # 3. deployment_revisions (source_deployment_id FK → host_deployments)
    op.create_table(
        "deployment_revisions",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("host_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("project", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("revision_id", sa.String(length=64), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("package_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_deployment_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_known_good",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_deployment_id"],
            ["host_deployments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "host_id",
            "revision_id",
            name="uq_deployment_revisions_host_revision",
        ),
    )
    op.create_index(
        "ix_deployment_revisions_project_env_host",
        "deployment_revisions",
        ["project", "environment", "host_id"],
        unique=False,
    )
    op.create_index(
        "ix_deployment_revisions_known_good",
        "deployment_revisions",
        ["host_id", "project", "environment"],
        unique=False,
        postgresql_where=sa.text("is_known_good"),
    )

    # 4. Circular FKs: host_deployments → deployment_revisions
    op.create_foreign_key(
        "fk_host_deployments_result_revision_id",
        "host_deployments",
        "deployment_revisions",
        ["result_revision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_host_deployments_rollback_revision_id",
        "host_deployments",
        "deployment_revisions",
        ["rollback_revision_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_host_deployments_rollback_revision_id",
        "host_deployments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_host_deployments_result_revision_id",
        "host_deployments",
        type_="foreignkey",
    )

    op.drop_index("ix_deployment_revisions_known_good", table_name="deployment_revisions")
    op.drop_index(
        "ix_deployment_revisions_project_env_host",
        table_name="deployment_revisions",
    )
    op.drop_table("deployment_revisions")

    op.drop_index("uq_host_deployments_active", table_name="host_deployments")
    op.drop_index("ix_host_deployments_project_env_host", table_name="host_deployments")
    op.drop_index("ix_host_deployments_host_state", table_name="host_deployments")
    op.drop_table("host_deployments")
