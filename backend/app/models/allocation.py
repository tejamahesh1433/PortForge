"""Phase 8A: atomic multi-port allocation bundles.

Terminology (see docs/phase8a_allocation_audit.md §2 and
docs/phase8a_agent_allocation.md for the full contract):

- Recommendation: a non-binding suggestion (unchanged, services/recommendation_service.py).
- Reservation: one reserved binding (unchanged, models/reservation.py).
- Allocation: one atomic request containing one or more reservations, created
  and released as a single unit. This model is the "which bundle created
  these reservations" record -- it intentionally does NOT duplicate
  per-port data (port/protocol/bind_address live only on CentralReservation,
  referenced via `allocation_id`); this row exists to answer "what was
  requested, for which project/host, and is it still active" even after
  some or all of its reservations have been individually inspected or the
  whole bundle released.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class Allocation(Base, TimestampMixin):
    __tablename__ = "allocations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    project: Mapped[str] = mapped_column(String(255))

    # "active" (>=1 reservation still live) or "released" (all reservations
    # released via DELETE /api/allocations/{id}). A plain string, not a DB
    # enum, matching Host.status's existing convention in this codebase.
    status: Mapped[str] = mapped_column(String(32), default="active")
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Idempotency (Phase 8A §13). Both nullable: a caller may omit
    # request_id entirely, in which case no idempotency replay/conflict
    # checking applies to that call (a fresh allocation is always made).
    # Unique so `get_by_request_id` is a single indexed lookup, not a scan.
    request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    # SHA-256 hex digest of the exact request payload (project, host_id,
    # requests[]) this request_id was first submitted with -- see
    # services/allocation_service.py::_hash_payload. Compared on retry to
    # distinguish "the same call, retried" (return the existing allocation)
    # from "a different call reusing an old key" (IDEMPOTENCY_CONFLICT).
    request_payload_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    host: Mapped["Host"] = relationship()  # noqa: F821
    reservations: Mapped[list["CentralReservation"]] = relationship(back_populates="allocation")  # noqa: F821
