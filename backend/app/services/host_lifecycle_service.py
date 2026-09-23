"""Permanent host Decommission / Reactivate lifecycle (Phase 8).

Distinct from v1.3 Remove Record (hard purge). See
docs/design/host-decommission-lifecycle.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.activity import ActivityEvent
from ..models.host import Host
from ..repositories.activity_repository import ActivityRepository
from ..repositories.agent_repository import AgentRepository
from ..repositories.allocation_repository import AllocationRepository
from ..repositories.host_repository import HostRepository
from ..repositories.reservation_repository import ReservationRepository

LIFECYCLE_ACTIVE = "ACTIVE"
LIFECYCLE_DECOMMISSIONED = "DECOMMISSIONED"


class HostLifecycleError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class HostNotFoundError(HostLifecycleError):
    def __init__(self, message: str = "Host not found."):
        super().__init__(message, status_code=404)


class HostAlreadyActiveError(HostLifecycleError):
    def __init__(self, message: str = "Host is already active."):
        super().__init__(message, status_code=409)


def is_decommissioned(host: Host) -> bool:
    return (host.lifecycle_state or LIFECYCLE_ACTIVE) == LIFECYCLE_DECOMMISSIONED


def _release_active_allocations_flush_only(db: Session, host_id: uuid.UUID, now: datetime) -> None:
    """Release active allocations without committing (caller owns the transaction)."""
    alloc_repo = AllocationRepository(db)
    reservation_repo = ReservationRepository(db)
    activity_repo = ActivityRepository(db)
    active_rows, _ = alloc_repo.list(host_id=host_id, status="active", limit=1000)
    rows, _ = reservation_repo.list(host_id=host_id, limit=1000)

    for allocation in active_rows:
        for reservation in rows:
            if reservation.allocation_id != allocation.id:
                continue
            reservation_repo.delete(reservation)
            protocol = (
                reservation.protocol.value
                if hasattr(reservation.protocol, "value")
                else reservation.protocol
            )
            activity_repo.add(
                ActivityEvent(
                    host_id=host_id,
                    timestamp=now,
                    event_type="RESERVATION_RELEASED",
                    port=reservation.port,
                    protocol=protocol,
                    bind_address=reservation.bind_address,
                    reservation_id=reservation.id,
                    identity_context=reservation.project,
                    summary=(
                        f"Reservation released for port {reservation.port}/{protocol} "
                        f"(project '{reservation.project}')"
                    ),
                )
            )
        allocation.status = "released"
        allocation.released_at = now


def decommission_host(
    db: Session,
    host_id: uuid.UUID,
    reason: Optional[str] = None,
) -> Host:
    """ACTIVE -> DECOMMISSIONED. Idempotent if already decommissioned."""
    host_repo = HostRepository(db)
    host = host_repo.get(host_id)
    if host is None:
        raise HostNotFoundError()

    now = datetime.now(timezone.utc)
    cleaned_reason = reason.strip() if reason and reason.strip() else None

    if is_decommissioned(host):
        # Idempotent: do not rewrite timestamp or emit a second activity event.
        return host

    host.lifecycle_state = LIFECYCLE_DECOMMISSIONED
    host.decommissioned_at = now
    host.decommission_reason = cleaned_reason

    AgentRepository(db).revoke_active_credential_for_host(host_id, revoked_at=now)
    _release_active_allocations_flush_only(db, host_id, now)

    # Cancel any in-flight upgrade for this host (Phase 10)
    from ..services.upgrade_service import fail_active_upgrades_for_host
    fail_active_upgrades_for_host(db, host_id, "host_decommissioned", now)

    summary = f"Host '{host.hostname}' decommissioned"
    if cleaned_reason:
        summary = f"{summary}: {cleaned_reason[:200]}"

    ActivityRepository(db).add(
        ActivityEvent(
            host_id=host_id,
            timestamp=now,
            event_type="HOST_DECOMMISSIONED",
            identity_context=host.hostname,
            summary=summary,
        )
    )

    db.flush()
    return host


def reactivate_host(db: Session, host_id: uuid.UUID) -> Host:
    """DECOMMISSIONED -> ACTIVE. Does not issue an agent credential."""
    host_repo = HostRepository(db)
    host = host_repo.get(host_id)
    if host is None:
        raise HostNotFoundError()

    if not is_decommissioned(host):
        raise HostAlreadyActiveError()

    now = datetime.now(timezone.utc)
    host.lifecycle_state = LIFECYCLE_ACTIVE
    host.decommissioned_at = None
    host.decommission_reason = None

    ActivityRepository(db).add(
        ActivityEvent(
            host_id=host_id,
            timestamp=now,
            event_type="HOST_REACTIVATED",
            identity_context=host.hostname,
            summary=f"Host '{host.hostname}' reactivated; new enrollment still required",
        )
    )

    db.flush()
    return host
