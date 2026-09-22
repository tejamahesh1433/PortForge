"""v1.1-B: authoritative remote bind-probe requests/results.

See docs/v1.1/remote-probe-design.md for the full architecture. Central
never performs a real socket bind for a remote host itself (that honesty
discipline is unchanged, see docs/phase8a_allocation_audit.md §7) -- a
HostProbe row is Central's record of asking the TARGET host's own agent to
do that real check and report back. Delivered on the agent's existing
heartbeat channel (no new transport), answered via one new authenticated
endpoint.

Lifecycle: PENDING -> DELIVERED -> COMPLETED (or FAILED, if the agent's
own probe attempt errored). "Expired" is deliberately NOT a stored status
-- it is always derived at read time by comparing `expires_at` to now
(see services/probe_service.py::classify_bind_probe), so a probe that sits
PENDING/DELIVERED past its TTL is correctly treated as stale evidence
without needing a background sweeper to flip a column first.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Protocol, TimestampMixin, new_uuid
from .port_observation import _ProtocolColumn


class HostProbe(Base, TimestampMixin):
    __tablename__ = "host_probes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)

    port: Mapped[int]
    protocol: Mapped[Protocol] = mapped_column(_ProtocolColumn)
    bind_address: Mapped[str] = mapped_column(String(64), default="0.0.0.0")

    # "PENDING" | "DELIVERED" | "COMPLETED" | "FAILED" -- a plain string,
    # not a DB enum, matching Allocation.status/Host.status's existing
    # convention in this codebase.
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)

    # Populated only once status == "COMPLETED": True = the target agent's
    # real local bind succeeded (port was free at that moment); False = it
    # failed with EADDRINUSE or equivalent (port was occupied). NULL for
    # every other status.
    result_available: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    result_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # TTL cutoff, set at creation time (now + settings.remote_probe_ttl_seconds).
    # A result reported after this must never be trusted as fresh -- see
    # classify_bind_probe.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    host: Mapped["Host"] = relationship()  # noqa: F821
