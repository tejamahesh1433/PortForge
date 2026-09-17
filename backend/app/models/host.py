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

from sqlalchemy import Boolean, DateTime, Index, String
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

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="online")

    # Staleness protection for snapshot ingestion (see services/ingestion_service.py
    # and Scan model) -- the most recent *accepted* scan's own observed_at,
    # so an old, delayed submission can be rejected deterministically.
    last_scan_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    port_observations: Mapped[list["CurrentPortObservation"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
    reservations: Mapped[list["CentralReservation"]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_hosts_hostname", "hostname"),)
