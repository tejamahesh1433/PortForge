"""A record of one accepted discovery snapshot submission.

Exists primarily so a duplicate submission (the agent retrying after a
network blip, for instance) can be recognized and treated idempotently --
`id` is the agent-generated `scan_id`, so re-submitting the same scan_id is
a simple primary-key lookup, not a semantic re-processing decision. See
services/ingestion_service.py "Snapshot identity & staleness".
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)  # the agent's scan_id
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
