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
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional

from sqlalchemy.orm import Session

from ..repositories.port_repository import PortRepository
from ..repositories.reservation_repository import ReservationRepository
from ..schemas.recommendation import CentralRecommendationOut
from . import probe_service

# Mirrors agent/portforge_agent/config.py's DEFAULT_RANGES -- kept as a
# small, independent copy rather than an import specifically because this
# server has no runtime dependency on the agent's config module (only on
# its pure value-type enums, see models/base.py); duplicating six
# (name, start, end) tuples is a far smaller, lower-risk surface than
# adding a second cross-package dependency for it. If these ever need to
# diverge or be centrally configurable, that's a natural Phase 6 follow-up.
#
# Reused as-is (not duplicated) by Phase 8A's allocation_service.py --
# see docs/phase8a_allocation_audit.md §1. Exported (not prefixed `_`)
# specifically so allocation_service.py can validate a request's `purpose`
# against the same known-purpose set without a second list to keep in sync.
DEFAULT_RANGES = {
    "frontend": (3000, 3999),
    "api": (8000, 8999),
    "postgres": (5432, 5499),
    "mysql": (3306, 3399),
    "redis": (6379, 6399),
    "generic": (10000, 19999),
}


@dataclass
class CandidateSearchResult:
    port: Optional[int]
    candidates_considered: int
    excluded: List[int] = field(default_factory=list)


def find_available_port(
    db: Session,
    host_id: uuid.UUID,
    service_type: str,
    protocol: str = "tcp",
    exclude_ports: FrozenSet[int] = frozenset(),
) -> CandidateSearchResult:
    """The one candidate-search implementation shared by the recommendation
    endpoint (`suggest_port` below) and Phase 8A's allocation bundle
    (`services/allocation_service.py`) -- see
    docs/phase8a_allocation_audit.md §1 for why this must not be
    duplicated. Excludes ports currently occupied (`CurrentPortObservation`)
    or already reserved (`CentralReservation`) for this host, plus any
    caller-supplied `exclude_ports` (allocation uses this for ports already
    claimed by an earlier item in the same in-progress bundle, which are
    not yet committed/visible to a fresh query).
    """
    port_range = DEFAULT_RANGES.get(service_type)
    if port_range is None:
        return CandidateSearchResult(port=None, candidates_considered=0, excluded=[])

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

    unavailable = occupied_ports | reserved_ports | exclude_ports
    excluded = sorted(occupied_ports | reserved_ports)

    recommended: Optional[int] = None
    considered = 0
    for candidate in range(start, end + 1):
        considered += 1
        if candidate not in unavailable:
            recommended = candidate
            break

    return CandidateSearchResult(port=recommended, candidates_considered=considered, excluded=excluded)


def suggest_port(
    db: Session, host_id: uuid.UUID, service_type: str, protocol: str = "tcp"
) -> CentralRecommendationOut:
    if service_type not in DEFAULT_RANGES:
        return CentralRecommendationOut(
            service_type=service_type,
            protocol=protocol,
            recommended_port=None,
            verification="central_suggestion",
            basis=f"Unknown service type '{service_type}'.",
            candidates_considered=0,
            known_conflicts_excluded=[],
        )

    # v1.1-B: layer fresh remote probe evidence (if any) on top of the
    # existing reservation/observation-based candidate search -- a
    # candidate with a FRESH verified_occupied probe is skipped even
    # though nothing in the reservation table knows about it. Shared,
    # bounded logic (see probe_service.resolve_with_probe_awareness's own
    # docstring for why this isn't duplicated per-caller).
    result = find_available_port(db, host_id, service_type, protocol)
    result, bind_probe = probe_service.resolve_with_probe_awareness(
        db, host_id, protocol, result, lambda exclude: find_available_port(db, host_id, service_type, protocol, exclude_ports=exclude)
    )
    verification = "locally_verified" if bind_probe == probe_service.VERIFIED_FREE else "central_suggestion"

    basis = (
        "Based on the most recently reported state and reservations on file for this host in the central "
        "registry -- NOT a live check. The target host must still perform fresh discovery, a local "
        "reservation check, and a real socket bind probe before actually using this port "
        "(see `portforge check <port>` on that host)."
    )
    if bind_probe == probe_service.VERIFIED_FREE:
        basis = (
            "A fresh, authoritative bind probe performed BY THE TARGET HOST'S OWN AGENT confirmed this port "
            "was free. This is still a point-in-time observation, not a lock -- another process could still "
            "bind it before you do (see `portforge check <port>` on that host)."
        )

    # suggest_port owns its own transaction boundary (api/recommendations.py's
    # handler never commits) -- this persists any probe queued above.
    # Harmless no-op when nothing was queued (the common case).
    db.commit()

    return CentralRecommendationOut(
        service_type=service_type,
        protocol=protocol,
        recommended_port=result.port,
        verification=verification,
        bind_probe=bind_probe,
        basis=basis,
        candidates_considered=result.candidates_considered,
        known_conflicts_excluded=result.excluded,
    )
