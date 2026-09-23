"""fleet intelligence columns + host_upgrades table

Revision ID: c3d4e5f6a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-23 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision: str = "c3d4e5f6a1b2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 9: fleet intelligence columns on hosts (all nullable, additive)
    op.add_column("hosts", sa.Column("contract_version", sa.Integer(), nullable=True))
    op.add_column("hosts", sa.Column("python_version", sa.String(length=64), nullable=True))
    op.add_column("hosts", sa.Column("last_error", sa.String(length=1024), nullable=True))

    # Phase 10: host_upgrades table
    op.create_table(
        "host_upgrades",
        sa.Column("id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("host_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("target_version", sa.String(length=64), nullable=False),
        sa.Column("artifact_url", sa.String(length=2048), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_filename", sa.String(length=255), nullable=True),
        sa.Column("previous_version", sa.String(length=64), nullable=True),
        sa.Column("previous_artifact_url", sa.String(length=2048), nullable=True),
        sa.Column("previous_artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=1024), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True, server_default="admin"),
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
        sa.UniqueConstraint("host_id", "request_id", name="uq_host_upgrades_host_request"),
    )
    op.create_index("ix_host_upgrades_host_id", "host_upgrades", ["host_id"], unique=False)
    op.create_index(
        "ix_host_upgrades_host_id_state", "host_upgrades", ["host_id", "state"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_host_upgrades_host_id_state", table_name="host_upgrades")
    op.drop_index("ix_host_upgrades_host_id", table_name="host_upgrades")
    op.drop_table("host_upgrades")

    op.drop_column("hosts", "last_error")
    op.drop_column("hosts", "python_version")
    op.drop_column("hosts", "contract_version")
