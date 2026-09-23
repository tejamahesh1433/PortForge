"""HostUpgrade entity — tracks a single structured agent upgrade operation.

One row per upgrade attempt. Non-terminal upgrades (not SUCCEEDED/FAILED/
ROLLED_BACK) represent in-flight work; Central enforces at most one per host.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class HostUpgrade(Base, TimestampMixin):
    __tablename__ = "host_upgrades"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)

    target_version: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    previous_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    previous_artifact_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    previous_artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    failure_reason: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, default="admin", server_default="admin"
    )

    host: Mapped["Host"] = relationship("Host", back_populates="upgrades")  # type: ignore[name-defined]

    __table_args__ = (
        UniqueConstraint("host_id", "request_id", name="uq_host_upgrades_host_request"),
        Index("ix_host_upgrades_host_id_state", "host_id", "state"),
    )
