"""Phase 8A: atomic multi-port allocation bundles.

See docs/phase8a_allocation_audit.md for the architectural reasoning this
implementation follows, and docs/phase8a_agent_allocation.md for the full
external contract. Summary of the guarantees this module provides:

- **Atomicity** (§4): a bundle is ALL-SUCCESS or NO-RESERVATIONS-CREATED.
  Every candidate port for every request in the bundle is resolved, and
  every reservation row inserted, inside one SQLAlchemy transaction; the
  first failure calls `db.rollback()` and raises before anything commits.
  Nothing is written until the single `db.commit()` at the very end.
- **Concurrency** (§5): the transaction opens by acquiring
  `services/host_lock.py`'s host-scoped PostgreSQL advisory lock for the
  target host_id, BEFORE reading any port/reservation state. A second,
  concurrent allocation (or snapshot ingestion) for the same host simply
  waits; allocations for *different* hosts never block each other.
- **Remote-host honesty** (§7/§8): Central never performs a real socket
  bind() for any host. `_build_validation()` reports the host's current
  health state and snapshot age, and `bind_probe` is always
  `"not_remote_capable"`. Hosts that are OFFLINE are refused outright;
  STALE hosts are refused too by default (see `_ensure_host_allocatable` --
  this is a policy choice, documented, not a certainty claim).
- **Idempotency** (§13): `request_id` (optional) is looked up before any
  mutation. An exact payload replay returns the existing allocation
  untouched; a reused key with a different payload raises
  IDEMPOTENCY_CONFLICT. Persisted in the `allocations` table, so this
  survives a Central restart (Phase 8A §32).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.allocation import Allocation
from ..models.reservation import CentralReservation
from ..repositories.allocation_repository import AllocationRepository
from ..repositories.host_repository import HostRepository
from ..repositories.reservation_repository import ReservationRepository
from ..repositories.port_repository import PortRepository
from ..schemas.allocation import (
    AllocationEntryOut,
    AllocationHostOut,
    AllocationIn,
    AllocationOut,
    AllocationRequestItem,
    AllocationValidationOut,
)
from ..schemas.health_status import HostHealthState, derive_health_state
from . import probe_service
from .host_lock import acquire_host_lock
from .recommendation_service import DEFAULT_RANGES, find_available_port
from .reservation_service import _insert_reservation


class AllocationError(Exception):
    """Machine-readable allocation failure -- see
    docs/phase8a_agent_allocation.md "Error contract". Caught by a FastAPI
    exception handler (api/allocations.py) and rendered as
    `{"error": {"code": ..., "message": ..., "details": [...]}}` with the
    given HTTP status code. Never carries a traceback or any secret.
    """

    def __init__(self, code: str, message: str, status_code: int, details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def _hash_payload(project: str, host_id: uuid.UUID, requests: List[AllocationRequestItem]) -> str:
    """Deterministic fingerprint of "the request that matters" for
    idempotency comparison -- request order is normalized (sorted by name,
    which is already required unique) so semantically-identical retries
    that happen to list requests in a different order still match.
    """
    canonical = {
        "project": project,
        "host_id": str(host_id),
        "requests": sorted(
            (
                {
                    "name": item.name,
                    "purpose": item.purpose,
                    "protocol": item.protocol,
                    "preferred_port": item.preferred_port,
                    "requested_range": item.requested_range,
                }
                for item in requests
            ),
            key=lambda item: item["name"],
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_validation(host, now: Optional[datetime] = None, bind_probe: str = "not_remote_capable") -> AllocationValidationOut:
    settings = get_settings()
    state, _reason, age_seconds = derive_health_state(
        host.last_seen, settings.host_stale_after_seconds, settings.host_offline_after_seconds, now=now
    )
    return AllocationValidationOut(
        snapshot_age_seconds=age_seconds,
        host_health_state=state.value,
        bind_probe=bind_probe,
    )


def _ensure_host_allocatable(host) -> AllocationValidationOut:
    """Policy decision (documented in docs/phase8a_agent_allocation.md
    "Remote-host validation limitations"): allocation is refused for a
    host Central cannot currently vouch for as HEALTHY. STALE and OFFLINE
    are both refused -- not just OFFLINE -- because "stale" already means
    Central's own port/reservation state for that host may not reflect
    reality, which is exactly the state allocation's atomicity guarantee
    depends on being trustworthy.
    """
    validation = _build_validation(host)
    if validation.host_health_state != HostHealthState.HEALTHY.value:
        code = "HOST_OFFLINE" if validation.host_health_state == HostHealthState.OFFLINE.value else "HOST_STALE"
        raise AllocationError(
            code=code,
            message=(
                f"Host '{host.hostname}' is {validation.host_health_state} "
                f"(last seen {validation.snapshot_age_seconds}s ago) -- refusing to allocate against "
                "state Central cannot currently vouch for."
            ),
            status_code=409,
            details=[{"host_id": str(host.id), "health_state": validation.host_health_state}],
        )
    return validation

def _build_allocation_out(
    db: Session, 
    allocation: Allocation, 
    bind_probe: str = "not_remote_capable",
    requests: Optional[List[AllocationRequestItem]] = None
) -> AllocationOut:
    host_repo = HostRepository(db)
    host = host_repo.get(allocation.host_id)
    reservation_repo = ReservationRepository(db)
    rows, _ = reservation_repo.list(host_id=allocation.host_id, limit=1000)
    own_rows = [r for r in rows if r.allocation_id == allocation.id]
    # v1.1-D: live per-entry evidence -- bounded by MAX_BUNDLE_SIZE (<=20),
    # not a list-page N+1 (see list_allocations below for the batched
    # equivalent used there).
    requests_by_name = {req.name: req for req in requests} if requests else {}

    entries = []
    for r in own_rows:
        protocol_value = r.protocol.value if hasattr(r.protocol, "value") else r.protocol
        evidence = probe_service.get_probe_evidence(db, allocation.host_id, r.port, protocol_value, r.bind_address or "0.0.0.0")
        
        req = requests_by_name.get(r.request_name)
        req_range = req.requested_range if req else None

        entries.append(
            AllocationEntryOut(
                name=r.request_name or "",
                purpose=r.purpose or "",
                protocol=protocol_value,
                port=r.port,
                reservation_id=r.id,
                bind_address=r.bind_address,
                bind_probe=evidence.bind_probe,
                requested_range=req_range,
            )
        )
    return AllocationOut(
        allocation_id=allocation.id,
        project=allocation.project,
        host=AllocationHostOut(id=host.id, hostname=host.hostname),
        status=allocation.status,
        allocations=entries,
        validation=_build_validation(host, bind_probe=bind_probe),
        created_at=allocation.created_at,
        released_at=allocation.released_at,
        request_id=allocation.request_id,
    )


def list_allocations(
    db: Session,
    host_id: Optional[uuid.UUID] = None,
    project: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> "tuple[List[AllocationOut], int]":
    """v1.1-D: the Allocations page's list source (task Sec2). Builds every
    row's entries/host/evidence from THREE total queries (allocations page,
    batch-resolved hosts, batch-resolved reservations) plus one batched
    probe-evidence query -- never one query per allocation (task Sec22).
    """
    allocation_repo = AllocationRepository(db)
    rows, total = allocation_repo.list(
        host_id=host_id, project=project, status=status, search=search, limit=limit, offset=offset
    )
    if not rows:
        return [], total

    host_ids = list({a.host_id for a in rows})
    hosts_by_id = {h.id: h for h in HostRepository(db).get_many(host_ids)}

    allocation_ids = [a.id for a in rows]
    reservation_rows = ReservationRepository(db).list_by_allocation_ids(allocation_ids)
    reservations_by_allocation: Dict[uuid.UUID, List] = {}
    for r in reservation_rows:
        if r.allocation_id is not None:
            reservations_by_allocation.setdefault(r.allocation_id, []).append(r)

    evidence_map = probe_service.get_probe_evidence_map(db, host_ids)

    results: List[AllocationOut] = []
    for allocation in rows:
        host = hosts_by_id.get(allocation.host_id)
        if host is None:  # pragma: no cover - defensive; a host is never deleted out from under its allocations
            continue
        entries = []
        for r in reservations_by_allocation.get(allocation.id, []):
            protocol_value = r.protocol.value if hasattr(r.protocol, "value") else r.protocol
            key = (allocation.host_id, r.port, protocol_value, r.bind_address or "0.0.0.0")
            evidence = evidence_map.get(key)
            entries.append(
                AllocationEntryOut(
                    name=r.request_name or "",
                    purpose=r.purpose or "",
                    protocol=protocol_value,
                    port=r.port,
                    reservation_id=r.id,
                    bind_address=r.bind_address,
                    bind_probe=evidence.bind_probe if evidence else probe_service.NOT_REMOTE_CAPABLE,
                )
            )
        results.append(
            AllocationOut(
                allocation_id=allocation.id,
                project=allocation.project,
                host=AllocationHostOut(id=host.id, hostname=host.hostname),
                status=allocation.status,
                allocations=entries,
                validation=_build_validation(host),
                created_at=allocation.created_at,
                released_at=allocation.released_at,
                request_id=allocation.request_id,
            )
        )
    return results, total


def _validate_purposes(requests: List[AllocationRequestItem]) -> None:
    unknown = [item.name for item in requests if item.purpose not in DEFAULT_RANGES]
    if unknown:
        raise AllocationError(
            code="INVALID_REQUEST",
            message=f"Unknown purpose for request(s): {', '.join(unknown)}. "
            f"Known purposes: {', '.join(sorted(DEFAULT_RANGES))}.",
            status_code=422,
            details=[{"name": name, "reason": "unknown purpose"} for name in unknown],
        )


def _port_is_free(db: Session, host_id: uuid.UUID, port: int, protocol: str, claimed: set) -> bool:
    if port in claimed:
        return False
    port_repo = PortRepository(db)
    reservation_repo = ReservationRepository(db)
    for row in port_repo.list_current_for_host(host_id):
        if row.port == port and row.protocol.value == protocol:
            return False
    rows, _ = reservation_repo.list(host_id=host_id, limit=10_000)
    for reservation in rows:
        if reservation.port == port and reservation.protocol.value == protocol:
            return False
    # v1.1-B: a fresh remote probe saying this EXACT binding is occupied
    # is real evidence a plain reservation-table read can't have -- reject
    # it even though nothing here has reserved it (task §11: "occupied
    # remote candidate is rejected"). Only a genuinely occupied result
    # rejects; missing/expired/unavailable evidence never does (task §9:
    # "do not convert missing evidence into free" -- the inverse also
    # holds: missing evidence must not manufacture an unavailable finding).
    evidence = probe_service.get_probe_evidence(db, host_id, port, protocol)
    if evidence.bind_probe == probe_service.VERIFIED_OCCUPIED:
        return False
    return True


def create_allocation(db: Session, payload: AllocationIn) -> AllocationOut:
    _validate_purposes(payload.requests)

    allocation_repo = AllocationRepository(db)
    payload_hash = _hash_payload(payload.project, payload.host_id, payload.requests)

    # --- Idempotency (checked before any mutation / lock) -----------------
    if payload.request_id:
        existing = allocation_repo.get_by_request_id(payload.request_id)
        if existing is not None:
            if existing.request_payload_hash == payload_hash:
                out = _build_allocation_out(db, existing, requests=payload.requests)
                out.idempotent_replay = True
                return out
            raise AllocationError(
                code="IDEMPOTENCY_CONFLICT",
                message=f"request_id '{payload.request_id}' was already used with a different request payload.",
                status_code=409,
                details=[{"request_id": payload.request_id}],
            )

    host_repo = HostRepository(db)
    host = host_repo.get(payload.host_id)
    if host is None:
        raise AllocationError(
            code="HOST_NOT_FOUND",
            message=f"No host with id '{payload.host_id}'.",
            status_code=404,
            details=[{"host_id": str(payload.host_id)}],
        )

    # --- Concurrency: host-scoped, transaction-scoped advisory lock -------
    # Acquired before any read of port/reservation state so a second,
    # overlapping allocation (or snapshot ingestion) for the SAME host
    # always waits and then sees this bundle's fully-committed result (or
    # this bundle's rollback, i.e. as if it never happened).
    acquire_host_lock(db, payload.host_id)

    # Re-check host freshness AFTER acquiring the lock (not before): a
    # concurrent ingestion could have been mid-flight and just committed,
    # so re-reading now (still pre-allocation) reflects the freshest state
    # this transaction will ever see.
    db.refresh(host)
    _ensure_host_allocatable(host)

    claimed: set = set()
    resolved: List[tuple] = []  # (request_item, port)
    # v1.1-B: the STRONGEST evidence seen across the whole bundle -- if
    # even one port was genuinely confirmed by a fresh remote probe, the
    # allocation's overall `validation.bind_probe` reports that (rather
    # than trying to represent N ports' worth of individually-differing
    # evidence in one field, which AllocationValidationOut's existing,
    # unchanged shape doesn't have room for).
    bundle_bind_probe = "not_remote_capable"
    for item in payload.requests:
        port, item_bind_probe = _resolve_candidate(db, payload.host_id, item, claimed)
        if port is None:
            db.rollback()
            raise AllocationError(
                code="ALLOCATION_UNAVAILABLE",
                message=f"No available port for request '{item.name}' (purpose '{item.purpose}').",
                status_code=409,
                details=[{"name": item.name, "purpose": item.purpose, "protocol": item.protocol}],
            )
        if item_bind_probe == "verified_free":
            bundle_bind_probe = "verified_free"
        resolved.append((item, port))
        claimed.add(port)

    allocation = Allocation(
        host_id=payload.host_id,
        project=payload.project,
        status="active",
        request_id=payload.request_id,
        request_payload_hash=payload_hash if payload.request_id else None,
    )
    allocation_repo.add(allocation)

    try:
        for item, port in resolved:
            _insert_reservation(
                db,
                host_id=payload.host_id,
                port=port,
                protocol=item.protocol,
                bind_address=None,
                project=payload.project,
                service=None,
                purpose=item.purpose,
                notes=None,
                local_reservation_id=None,
                allocation_id=allocation.id,
                request_name=item.name,
            )
    except IntegrityError:
        # Belt-and-suspenders: the advisory lock already prevents this for
        # allocation-vs-allocation and allocation-vs-ingestion races on the
        # SAME host, but the DB's own uq_central_reservation_binding
        # constraint (models/reservation.py) is the final, authoritative
        # guard -- see docs/phase8a_allocation_audit.md §3.
        db.rollback()
        raise AllocationError(
            code="ALLOCATION_UNAVAILABLE",
            message="One or more candidate ports were claimed by a concurrent request; no reservations were created.",
            status_code=409,
        )

    db.commit()
    db.refresh(allocation)
    return _build_allocation_out(db, allocation, bind_probe=bundle_bind_probe, requests=payload.requests)


def _resolve_candidate(
    db: Session, host_id: uuid.UUID, item: AllocationRequestItem, claimed: set
) -> "tuple[Optional[int], str]":
    """Returns `(port, bind_probe)`. `bind_probe` reflects the strongest
    evidence actually used to pick THIS port -- `verified_free` only when
    a fresh remote probe genuinely confirmed it, `not_remote_capable`
    otherwise (matches AllocationValidationOut's existing, unchanged
    default meaning -- see docs/v1.1/remote-probe-design.md's "bind_probe
    contract").
    """
    if item.preferred_port is not None and _port_is_free(db, host_id, item.preferred_port, item.protocol, claimed):
        evidence = probe_service.get_probe_evidence(db, host_id, item.preferred_port, item.protocol)
        if evidence.bind_probe == probe_service.VERIFIED_FREE:
            return item.preferred_port, probe_service.VERIFIED_FREE
        if evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE:
            # No evidence yet for this specific preferred port -- queue one
            # so a LATER allocation/recommendation call benefits, exactly
            # like the non-preferred path already does via
            # resolve_with_probe_awareness.
            probe_service.queue_probe(db, host_id, item.preferred_port, item.protocol)
        return item.preferred_port, probe_service.NOT_REMOTE_CAPABLE

    requested_range_tuple = None
    if item.requested_range:
        parts = item.requested_range.split("-")
        requested_range_tuple = (int(parts[0]), int(parts[1]))

    result = find_available_port(
        db, host_id, item.purpose, item.protocol, exclude_ports=frozenset(claimed), requested_range=requested_range_tuple
    )
    result, bind_probe = probe_service.resolve_with_probe_awareness(
        db,
        host_id,
        item.protocol,
        result,
        lambda exclude: find_available_port(
            db, host_id, item.purpose, item.protocol, exclude_ports=frozenset(claimed) | exclude, requested_range=requested_range_tuple
        ),
    )
    return result.port, bind_probe


def get_allocation(db: Session, allocation_id: uuid.UUID) -> AllocationOut:
    allocation = AllocationRepository(db).get(allocation_id)
    if allocation is None:
        raise AllocationError(
            code="ALLOCATION_NOT_FOUND",
            message=f"No allocation with id '{allocation_id}'.",
            status_code=404,
        )
    return _build_allocation_out(db, allocation)


def release_allocation(db: Session, allocation_id: uuid.UUID) -> AllocationOut:
    """Releases all still-active reservations belonging to this
    allocation. Idempotent: releasing an already-released allocation is a
    no-op success (returns its current, already-released state) rather
    than an error -- an agent retrying a release request after a dropped
    response must not get a confusing failure for work that already
    happened. One missing/already-gone reservation row does not abort the
    rest (Phase 8A §12).
    """
    allocation_repo = AllocationRepository(db)
    allocation = allocation_repo.get(allocation_id)
    if allocation is None:
        raise AllocationError(
            code="ALLOCATION_NOT_FOUND",
            message=f"No allocation with id '{allocation_id}'.",
            status_code=404,
        )

    if allocation.status == "released":
        return _build_allocation_out(db, allocation)

    acquire_host_lock(db, allocation.host_id)

    reservation_repo = ReservationRepository(db)
    rows, _ = reservation_repo.list(host_id=allocation.host_id, limit=1000)
    from ..models.activity import ActivityEvent
    from ..repositories.activity_repository import ActivityRepository

    activity_repo = ActivityRepository(db)
    for reservation in rows:
        if reservation.allocation_id != allocation.id:
            continue
        reservation_repo.delete(reservation)
        activity_repo.add(
            ActivityEvent(
                host_id=allocation.host_id,
                timestamp=datetime.now(timezone.utc),
                event_type="RESERVATION_RELEASED",
                port=reservation.port,
                protocol=reservation.protocol.value if hasattr(reservation.protocol, "value") else reservation.protocol,
                bind_address=reservation.bind_address,
                reservation_id=reservation.id,
                identity_context=reservation.project,
                summary=(
                    f"Reservation released for port {reservation.port}/"
                    f"{reservation.protocol.value if hasattr(reservation.protocol, 'value') else reservation.protocol} "
                    f"(project '{reservation.project}')"
                ),
            )
        )

    allocation.status = "released"
    allocation.released_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(allocation)
    return _build_allocation_out(db, allocation)
