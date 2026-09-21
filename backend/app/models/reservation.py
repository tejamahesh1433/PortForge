"""Central reservation persistence.

Mirrors the agent's local Reservation fields (see
agent/portforge_agent/reservations/models.py) but is a genuinely separate
representation for a separate bounded context -- see models/__init__.py's
docstring. The same (host_id, port, protocol) is NOT globally unique here:
two different hosts reserving the same port for two different projects is
valid and expected (see services/reservation_service.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Protocol, TimestampMixin, new_uuid
from .port_observation import _ProtocolColumn


class CentralReservation(Base, TimestampMixin):
    __tablename__ = "central_reservations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    port: Mapped[int]
    protocol: Mapped[Protocol] = mapped_column(_ProtocolColumn)
    bind_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    project: Mapped[str] = mapped_column(String(255))
    service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # The agent-local reservation_id this row was synchronized from -- lets
    # a repeat sync from the same agent upsert instead of duplicating (see
    # services/reservation_service.py "Synchronization strategy").
    local_reservation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Phase 8A: set only for reservations created through an atomic
    # allocation bundle (services/allocation_service.py) -- NULL for every
    # other reservation (manual dashboard/agent reservations, unchanged).
    # ON DELETE SET NULL rather than CASCADE: releasing/deleting an
    # Allocation row must never silently delete a still-active reservation
    # out from under a project.
    allocation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("allocations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The caller-chosen bundle key this reservation was requested under
    # (e.g. "frontend", "database") -- distinct from `service`/`purpose`,
    # which are separate existing concepts. Only meaningful alongside
    # allocation_id; NULL otherwise. Lets GET /api/allocations/{id} and the
    # CLI's `--format env` reconstruct the original request->port mapping
    # without a second lookup table.
    request_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    host: Mapped["Host"] = relationship(back_populates="reservations")  # noqa: F821
    allocation: Mapped[Optional["Allocation"]] = relationship(back_populates="reservations")  # noqa: F821

    __table_args__ = (
        UniqueConstraint("host_id", "port", "protocol", "bind_address", name="uq_central_reservation_binding"),
    )
