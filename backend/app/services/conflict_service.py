"""Central conflict detection -- the same conservative rule as the agent's
local evaluate.py (see agent/README.md "State evaluation"), applied across
whatever the central registry currently has on file. Like the
recommendation service, this is a query over cached data, not a live
re-check of any specific host.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models.host import Host
from ..repositories.port_repository import PortRepository
from ..repositories.reservation_repository import ReservationRepository
from ..schemas.conflict import ConflictOut


def _project_matches(observed: Optional[str], reserved: str) -> bool:
    if not observed:
        return False  # unknown ownership is never assumed to match -- same rule as the agent
    return observed.strip().lower() == reserved.strip().lower()


def list_conflicts(db: Session, host_id: Optional[uuid.UUID] = None) -> List[ConflictOut]:
    reservation_repo = ReservationRepository(db)
    port_repo = PortRepository(db)

    reservations, _ = reservation_repo.list(host_id=host_id, limit=10_000)

    conflicts: List[ConflictOut] = []
    for reservation in reservations:
        host = db.get(Host, reservation.host_id)
        if host is None:
            continue

        current = port_repo.get_current(
            reservation.host_id,
            reservation.port,
            reservation.protocol.value,
            reservation.bind_address or "0.0.0.0",
        )
        if current is None:
            continue  # RESERVED, not active -- not a conflict

        if _project_matches(current.project_name, reservation.project):
            continue  # same project -- ACTIVE, not a conflict

        owner = current.project_name or current.container_name or current.process_name or "an unrecognized owner"
        conflicts.append(
            ConflictOut(
                host_id=host.id,
                hostname=host.hostname,
                port=reservation.port,
                protocol=reservation.protocol.value,
                reserved_for_project=reservation.project,
                reserved_for_service=reservation.service,
                actual_project=current.project_name,
                actual_process_name=current.process_name,
                actual_container_name=current.container_name,
                reason=f"reserved for '{reservation.project}', but currently used by {owner}",
            )
        )

    return conflicts
