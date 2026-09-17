"""Port observation persistence: CURRENT state + bounded HISTORY.

Two tables, deliberately not one growing-forever log (see
services/ingestion_service.py "Current state vs. history" for the full
strategy this implements):

- `current_port_observations`: exactly one row per (host, port, protocol,
  bind_address) that is currently believed active -- upserted on every
  snapshot, deleted when a port disappears from a later complete snapshot.
- `port_observation_events`: an append-only log of *meaningful changes*
  only (a port appearing, disappearing, or changing owner/state) -- never
  one row per identical heartbeat. This is what answers "when was port
  8000 last used" without the table growing unboundedly from routine
  re-confirmations of unchanged state.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, PortState, Protocol, TimestampMixin, new_uuid

_ProtocolColumn = SAEnum(Protocol, name="protocol", values_callable=lambda enum_cls: [e.value for e in enum_cls])
_StateColumn = SAEnum(PortState, name="port_state", values_callable=lambda enum_cls: [e.value for e in enum_cls])


class CurrentPortObservation(Base, TimestampMixin):
    __tablename__ = "current_port_observations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[Protocol] = mapped_column(_ProtocolColumn)
    bind_address: Mapped[str] = mapped_column(String(64))
    state: Mapped[PortState] = mapped_column(_StateColumn)
    source: Mapped[str] = mapped_column(String(32))

    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    process_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    process_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    working_directory: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    container_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    container_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    container_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    container_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    docker_compose_project: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    service_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    project_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detection_confidence: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))

    host: Mapped["Host"] = relationship(back_populates="port_observations")  # noqa: F821

    __table_args__ = (
        UniqueConstraint("host_id", "port", "protocol", "bind_address", name="uq_current_port_binding"),
        Index("ix_current_port_observations_port", "port"),
        Index("ix_current_port_observations_project", "project_name"),
    )


class PortObservationEvent(Base):
    __tablename__ = "port_observation_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[Protocol] = mapped_column(_ProtocolColumn)
    bind_address: Mapped[str] = mapped_column(String(64))

    event_type: Mapped[str] = mapped_column(String(16))  # "appeared" | "changed" | "disappeared"
    state: Mapped[Optional[PortState]] = mapped_column(_StateColumn, nullable=True)
    process_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    project_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    scan_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_port_observation_events_host_port", "host_id", "port", "protocol"),)
