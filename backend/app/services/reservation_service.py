"""Central reservation management.

## Synchronization strategy (local Phase 4 <-> central Phase 5)

The agent's **local** reservation file remains authoritative for local
allocation safety -- `portforge reserve`/`next --reserve` never consult the
central server and never will, by design (see agent/README.md "Offline
behavior"). The central registry is a synchronized *mirror*, useful for
answering "where else is this port reserved" across a user's machines.

Conflict rule: `local_reservation_id` (the agent's own reservation UUID)
identifies a central row that was synced from a specific local
reservation. A sync upsert keyed on `local_reservation_id` for the same
host always wins over whatever is centrally stored for that same
(host, port, protocol, bind_address) binding -- the local file is the
source of truth for *that specific reservation's* content. If a
*different* local reservation (different `local_reservation_id`) already
occupies that exact binding centrally, the sync is refused rather than
silently overwritten (mirrors the agent's own "never silently overwrite
another project's reservation" rule, applied to the central mirror too) --
this can only happen if two different local reservation files on two
different hosts ever converged on identical central IDs, which shouldn't
happen in normal operation, but the guard costs nothing and prevents a
confusing data-loss surprise if it ever does.

No last-write-wins-by-timestamp-only logic is used for the *content* of a
given reservation_id -- an upsert by `local_reservation_id` always applies
the agent's latest values, since the agent is authoritative for its own
reservations by definition. Timestamps (`updated_at`) are kept for
observability, not for conflict arbitration.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models.reservation import CentralReservation
from ..models.activity import ActivityEvent
from ..repositories.reservation_repository import ReservationRepository
from ..repositories.activity_repository import ActivityRepository
from datetime import datetime, timezone


class ReservationConflictError(Exception):
    """A different local reservation already occupies this exact binding
    centrally -- see module docstring.
    """


def create_reservation(
    db: Session,
    host_id: uuid.UUID,
    port: int,
    protocol: str,
    bind_address: Optional[str],
    project: str,
    service: Optional[str],
    purpose: Optional[str],
    notes: Optional[str],
    local_reservation_id: Optional[str],
) -> CentralReservation:
    repo = ReservationRepository(db)

    existing_binding = repo.get_by_binding(host_id, port, protocol, bind_address)
    if existing_binding is not None:
        if local_reservation_id and existing_binding.local_reservation_id == local_reservation_id:
            # Idempotent re-sync of the exact same local reservation.
            existing_binding.project = project
            existing_binding.service = service
            existing_binding.purpose = purpose
            existing_binding.notes = notes
            db.commit()
            db.refresh(existing_binding)
            return existing_binding
        raise ReservationConflictError(
            f"Port {port}/{protocol} is already reserved centrally for this host by a different reservation."
        )

    reservation = CentralReservation(
        host_id=host_id,
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        project=project,
        service=service,
        purpose=purpose,
        notes=notes,
        local_reservation_id=local_reservation_id,
    )
    repo.add(reservation)
    activity_repo = ActivityRepository(db)
    activity_repo.add(
        ActivityEvent(
            host_id=host_id,
            timestamp=datetime.now(timezone.utc),
            event_type="RESERVATION_CREATED",
            port=port,
            protocol=protocol,
            bind_address=bind_address,
            reservation_id=reservation.id,
            identity_context=project,
            summary=f"Port {port}/{protocol} reserved for project '{project}'",
        )
    )

    db.commit()
    db.refresh(reservation)
    return reservation


def delete_reservation(db: Session, host_id: uuid.UUID, reservation_id: uuid.UUID) -> bool:
    """Returns True if deleted, False if it didn't exist (or belongs to a
    different host -- treated the same as "not found" so one host can
    never even discover another host's reservation IDs by probing delete).
    """
    repo = ReservationRepository(db)
    reservation = repo.get(reservation_id)
    if reservation is None or reservation.host_id != host_id:
        return False
    repo.delete(reservation)
    
    activity_repo = ActivityRepository(db)
    activity_repo.add(
        ActivityEvent(
            host_id=host_id,
            timestamp=datetime.now(timezone.utc),
            event_type="RESERVATION_RELEASED",
            port=reservation.port,
            protocol=reservation.protocol.value if hasattr(reservation.protocol, "value") else reservation.protocol,
            bind_address=reservation.bind_address,
            reservation_id=reservation.id,
            identity_context=reservation.project,
            summary=f"Reservation released for port {reservation.port}/{reservation.protocol.value if hasattr(reservation.protocol, 'value') else reservation.protocol} (project '{reservation.project}')",
        )
    )

    db.commit()
    return True


def list_reservations(
    db: Session,
    host_id: Optional[uuid.UUID] = None,
    port: Optional[int] = None,
    project: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[CentralReservation], int]:
    repo = ReservationRepository(db)
    rows, total = repo.list(host_id=host_id, port=port, project=project, limit=limit, offset=offset)
    return list(rows), total
