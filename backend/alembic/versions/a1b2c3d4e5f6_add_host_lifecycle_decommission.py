"""add host lifecycle decommission columns

Revision ID: a1b2c3d4e5f6
Revises: 038acf539985
Create Date: 2026-09-23 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "038acf539985"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hosts",
        sa.Column(
            "lifecycle_state",
            sa.String(length=32),
            nullable=False,
            server_default="ACTIVE",
        ),
    )
    op.add_column(
        "hosts",
        sa.Column("decommissioned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hosts",
        sa.Column("decommission_reason", sa.String(length=1024), nullable=True),
    )
    op.create_index("ix_hosts_lifecycle_state", "hosts", ["lifecycle_state"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_hosts_lifecycle_state", table_name="hosts")
    op.drop_column("hosts", "decommission_reason")
    op.drop_column("hosts", "decommissioned_at")
    op.drop_column("hosts", "lifecycle_state")
