"""Central recommendation ("suggestion") service.

See schemas/recommendation.py's module docstring for the critical
semantic this implements: a central suggestion is derived purely from
cached `current_port_observations`/`central_reservations` rows already on
file for a host -- it is NEVER labeled as verified availability, because
the central server cannot perform a fresh discovery scan, check the
agent's local reservation file, or run a real socket bind probe on that
machine. Only the target host's own agent can do that (see
agent/README.md "Three-layer validation").

This deliberately reuses the same DEFAULT_RANGES vocabulary the agent
uses (frontend/api/postgres/mysql/redis/generic) so a suggestion and a
local recommendation for the same service_type search the same numeric
space -- but it does NOT import the agent's `recommend.py` engine itself
(that engine's whole design assumes it can run a live bind probe, which is
exactly what the central server must not pretend to do).
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from ..repositories.port_repository import PortRepository
from ..repositories.reservation_repository import ReservationRepository
from ..schemas.recommendation import CentralRecommendationOut

# Mirrors agent/portforge_agent/config.py's DEFAULT_RANGES -- kept as a
# small, independent copy rather than an import specifically because this
# server has no runtime dependency on the agent's config module (only on
# its pure value-type enums, see models/base.py); duplicating six
# (name, start, end) tuples is a far smaller, lower-risk surface than
# adding a second cross-package dependency for it. If these ever need to
# diverge or be centrally configurable, that's a natural Phase 6 follow-up.
_DEFAULT_RANGES = {
    "frontend": (3000, 3999),
    "api": (8000, 8999),
    "postgres": (5432, 5499),
    "mysql": (3306, 3399),
    "redis": (6379, 6399),
    "generic": (10000, 19999),
}


def suggest_port(
    db: Session, host_id: uuid.UUID, service_type: str, protocol: str = "tcp"
) -> CentralRecommendationOut:
    port_range = _DEFAULT_RANGES.get(service_type)
    if port_range is None:
        return CentralRecommendationOut(
            service_type=service_type,
            protocol=protocol,
            recommended_port=None,
            verification="central_suggestion",
            basis=f"Unknown service type '{service_type}'.",
            candidates_considered=0,
            known_conflicts_excluded=[],
        )

    start, end = port_range
    port_repo = PortRepository(db)
    reservation_repo = ReservationRepository(db)

    occupied_ports = set()
    for row in port_repo.list_current_for_host(host_id):
        if start <= row.port <= end and row.protocol.value == protocol:
            occupied_ports.add(row.port)

    reserved_ports = set()
    rows, _ = reservation_repo.list(host_id=host_id, limit=10_000)
    for reservation in rows:
        if start <= reservation.port <= end and reservation.protocol.value == protocol:
            reserved_ports.add(reservation.port)

    excluded = sorted(occupied_ports | reserved_ports)

    recommended: Optional[int] = None
    considered = 0
    for candidate in range(start, end + 1):
        considered += 1
        if candidate not in occupied_ports and candidate not in reserved_ports:
            recommended = candidate
            break

    basis = (
        "Based on the most recently reported state and reservations on file for this host in the central "
        "registry -- NOT a live check. The target host must still perform fresh discovery, a local "
        "reservation check, and a real socket bind probe before actually using this port "
        "(see `portforge check <port>` on that host)."
    )

    return CentralRecommendationOut(
        service_type=service_type,
        protocol=protocol,
        recommended_port=recommended,
        verification="central_suggestion",
        basis=basis,
        candidates_considered=considered,
        known_conflicts_excluded=excluded,
    )
