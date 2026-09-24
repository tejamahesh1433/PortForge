"""The central Host entity.

Primary key is the agent's own persistent UUID (see
agent/portforge_agent/identity.py) -- the server never mints its own,
separate host identifier. `hostname` is deliberately NOT unique: two
different physical machines can legitimately share a hostname (a common
default like "localhost", a VM template, a renamed clone, ...), and
identity must never be inferred from it.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Host(Base, TimestampMixin):
    __tablename__ = "hosts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

    hostname: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    operating_system: Mapped[str] = mapped_column(String(32))
    os_version: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    architecture: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    docker_available: Mapped[bool] = mapped_column(Boolean, default=False)

    # v1.1-D: the raw protocol_version an agent last reported on enroll/
    # heartbeat (see services/compatibility_service.py). v1.1-A deliberately
    # did NOT persist this -- see that module's docstring -- but Host
    # Compatibility display (v1.1-D task Sec7) has no way to survive between
    # requests without it. Only the raw integer is stored; compatibility
    # itself (compatible/warning/unknown) is always recomputed live via the
    # existing evaluate_protocol_compatibility(), never a stored verdict
    # that could go stale if Central's own PROTOCOL_VERSION ever changes.
    protocol_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="online")

    # Phase 8: operator lifecycle, independent of health/last_seen.
    # ACTIVE | DECOMMISSIONED — plain string like Allocation.status.
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="ACTIVE", server_default="ACTIVE")
    decommissioned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decommission_reason: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # Staleness protection for snapshot ingestion (see services/ingestion_service.py
    # and Scan model) -- the most recent *accepted* scan's own observed_at,
    # so an old, delayed submission can be rejected deterministically.
    last_scan_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 9: fleet intelligence fields (all nullable; populated from agent reports)
    contract_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    python_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    port_observations: Mapped[list["CurrentPortObservation"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
    reservations: Mapped[list["CentralReservation"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
    upgrades: Mapped[list["HostUpgrade"]] = relationship(
        "HostUpgrade", back_populates="host", cascade="all, delete-orphan"
    )
    deployments: Mapped[list["HostDeployment"]] = relationship(
        "HostDeployment", back_populates="host", cascade="all, delete-orphan"
    )
    deployment_revisions: Mapped[list["DeploymentRevision"]] = relationship(
        "DeploymentRevision", back_populates="host", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_hosts_hostname", "hostname"),
        Index("ix_hosts_lifecycle_state", "lifecycle_state"),
    )
