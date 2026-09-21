"""Global activity history persistence.

An append-only timeline of operational events (ports appearing/disappearing,
reservations created, hosts going online). Used for the dashboard's Activity feed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, new_uuid
from .port_observation import _ProtocolColumn, Protocol


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)

    # Optional routing / binding context
    port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    protocol: Mapped[Optional[Protocol]] = mapped_column(_ProtocolColumn, nullable=True)
    bind_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    identity_context: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    reservation_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    summary: Mapped[str] = mapped_column(String(2048))
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    host: Mapped["Host"] = relationship()  # noqa: F821

    __table_args__ = (Index("ix_activity_events_timestamp_desc", "timestamp", postgresql_using="btree"),)
