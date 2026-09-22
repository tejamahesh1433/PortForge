"""v1.1-B: remote bind-probe lifecycle -- queue, deliver, complete, classify.

See docs/v1.1/remote-probe-design.md for the full architecture this
implements. Central NEVER performs a real socket bind for any host itself
(unchanged honesty discipline, docs/phase8a_allocation_audit.md §7) -- this
module only tracks the request/result round-trip with the target host's
own agent, delivered over the existing authenticated heartbeat channel
(schemas/agent.py::HeartbeatResponse.pending_probes), answered via one new
endpoint (api/agents.py's probe-result route).

Nothing here ever blocks a caller waiting for a probe to be answered --
`queue_probe` fires-and-forgets a request; a caller (recommendation/
allocation) only ever *consumes* a result that already exists by the time
it looks, via `classify_bind_probe`. See services/recommendation_service.py
and services/allocation_service.py for the two consumers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, FrozenSet, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host import Host
from ..models.host_probe import HostProbe
from ..repositories.probe_repository import ProbeRepository
from ..schemas.health_status import HostHealthState, derive_health_state

NOT_REMOTE_CAPABLE = "not_remote_capable"
VERIFIED_FREE = "verified_free"
VERIFIED_OCCUPIED = "verified_occupied"
EXPIRED = "expired"
UNAVAILABLE = "unavailable"

# Bounded: at most this many DB-only candidates get probe-checked (and,
# if occupied, retried) per resolution call -- a plain read per attempt,
# never a wait (task §11: "do not make recommendation wait forever"; task
# §12: the same bound applies to allocation's own candidate resolution).
MAX_PROBE_SKIP_ATTEMPTS = 5


class ProbeOwnershipError(Exception):
    """Raised when a probe result submission's authenticated host_id does
    not match the probe's own host_id -- see task §8: a host must never be
    able to claim or complete another host's probe. api/agents.py maps
    this to 403, mirroring every other cross-host-mismatch check already
    established for heartbeat/observations.
    """


def queue_probe(
    db: Session, host_id: uuid.UUID, port: int, protocol: str, bind_address: str = "0.0.0.0"
) -> Optional[HostProbe]:
    """Fire-and-forget: creates a PENDING probe for the target host's next
    heartbeat to pick up. Returns None (queues nothing, never raises) if:
    - the host doesn't exist at all -- `suggest_port`/`find_available_port`
      are deliberately usable for a not-yet-enrolled host_id (an empty
      range is "free" -- see test_recommendation_unknown_host), so probe
      queuing must degrade the same way rather than hitting a foreign-key
      violation; or
    - that host already has `max_pending_probes_per_host` active probes
      outstanding -- a graceful bound (task §23: "no unbounded queue
      growth"), not an error a caller needs to handle.

    If an active (PENDING/DELIVERED, unexpired) probe already exists for
    this EXACT binding, that existing probe is returned unchanged instead
    of creating a second one -- avoids duplicate-probe pileup for the same
    (host, port, protocol, bind_address), which would otherwise make
    "the latest probe for this binding" ambiguous between two probes
    created at nearly the same instant (a real ordering hazard this
    increment's own test suite caught).

    Deliberately FLUSHES, never commits: this is called from inside other
    services' own transactions (recommendation_service.suggest_port,
    allocation_service._resolve_candidate) that must control their own
    commit/rollback boundary -- a stray commit here previously caused a
    real, caught-by-test regression (allocation's host-scoped advisory
    lock is transaction-scoped, so committing mid-allocation released it
    early, letting two concurrent allocations both win the same port; see
    docs/v1.1/v1.1-b-implementation.md "Known limitations"). The caller's
    own eventual commit (or rollback) covers this row like any other
    change in that same transaction; if that transaction rolls back, an
    already-queued probe is lost too -- acceptable, since this is
    explicitly a best-effort optimization, not a correctness-critical
    write (a lost probe just means it gets queued again on the next call).
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    repo = ProbeRepository(db)

    host = db.get(Host, host_id)
    if host is None:
        return None

    # Task §15: "do not queue endless work for an offline host." A
    # STALE/OFFLINE host may never heartbeat again in time to claim this
    # probe before it expires anyway -- queuing for it would just grow the
    # table with work that's already known to be unlikely to complete.
    state, _reason, _age = derive_health_state(
        host.last_seen, settings.host_stale_after_seconds, settings.host_offline_after_seconds, now=now
    )
    if state != HostHealthState.HEALTHY:
        return None

    existing = repo.find_latest_for_binding(host_id, port, protocol, bind_address)
    if existing is not None and existing.status in ("PENDING", "DELIVERED") and existing.expires_at >= now:
        return existing

    if repo.count_active_for_host(host_id, now) >= settings.max_pending_probes_per_host:
        return None

    probe = HostProbe(
        host_id=host_id,
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        status="PENDING",
        expires_at=now + timedelta(seconds=settings.remote_probe_ttl_seconds),
    )
    repo.add(probe)
    return probe


def claim_pending_probes(db: Session, host_id: uuid.UUID) -> list[HostProbe]:
    """Called once per heartbeat (api/agents.py): marks up to
    `max_probes_delivered_per_heartbeat` PENDING probes for this host as
    DELIVERED and returns them for inclusion in the heartbeat response.
    Delivery is a one-way status transition -- a probe already DELIVERED
    on a prior heartbeat (the agent hasn't answered yet) is NOT
    re-delivered here; it simply sits until the agent answers or it
    expires. This keeps "how many times was this probe handed to the
    agent" unambiguous or -- for repeat-delivery designs -- explicitly out
    of v1.1-B's smallest-necessary scope.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    repo = ProbeRepository(db)

    pending = repo.list_pending_for_host(host_id, now, settings.max_probes_delivered_per_heartbeat)
    for probe in pending:
        probe.status = "DELIVERED"
        probe.delivered_at = now
    if pending:
        db.commit()
    return pending


def submit_result(
    db: Session, authenticated_host_id: uuid.UUID, probe_id: uuid.UUID, available: Optional[bool], reason: Optional[str]
) -> HostProbe:
    """`available=None` means the agent's own probe ATTEMPT failed (e.g.
    could not create a socket) -- recorded as FAILED, distinct from a
    successful attempt that found the port occupied (available=False,
    recorded as COMPLETED with result_available=False).

    Idempotent: resubmitting a result for an already-COMPLETED/FAILED
    probe is a safe no-op that returns the ALREADY-recorded state
    unchanged (task §4: "duplicate result submission must be idempotent
    or safely rejected") -- a second submission can never overwrite a
    first one, whether it agrees or disagrees with it.
    """
    repo = ProbeRepository(db)
    probe = repo.get(probe_id)
    if probe is None:
        raise LookupError(f"No probe with id '{probe_id}'.")

    if probe.host_id != authenticated_host_id:
        raise ProbeOwnershipError(
            f"Probe {probe_id} belongs to a different host than the authenticated credential."
        )

    if probe.status in ("COMPLETED", "FAILED"):
        return probe  # idempotent -- already has a recorded result

    now = datetime.now(timezone.utc)
    probe.completed_at = now
    probe.result_reason = reason
    if available is None:
        probe.status = "FAILED"
        probe.result_available = None
    else:
        probe.status = "COMPLETED"
        probe.result_available = available

    db.commit()
    db.refresh(probe)
    return probe


@dataclass(frozen=True)
class ProbeEvidence:
    bind_probe: str
    probe: Optional[HostProbe]


def classify_bind_probe(probe: Optional[HostProbe], now: Optional[datetime] = None) -> str:
    """Pure classification -- see module docstring's 5-value contract.
    `expires_at` is checked BEFORE `status`, so a COMPLETED-but-old probe
    is correctly reported "expired", never silently trusted as fresh no
    matter how old (task §9: "Do not convert missing evidence into free").
    """
    if probe is None:
        return NOT_REMOTE_CAPABLE
    now = now or datetime.now(timezone.utc)

    if probe.status == "FAILED":
        return UNAVAILABLE
    if probe.status not in ("PENDING", "DELIVERED", "COMPLETED"):  # pragma: no cover - defensive
        return NOT_REMOTE_CAPABLE

    if probe.expires_at < now:
        return EXPIRED if probe.status == "COMPLETED" else NOT_REMOTE_CAPABLE

    if probe.status != "COMPLETED":
        return NOT_REMOTE_CAPABLE  # still in flight, no result yet -- not evidence either way

    return VERIFIED_FREE if probe.result_available else VERIFIED_OCCUPIED


def get_probe_evidence(
    db: Session, host_id: uuid.UUID, port: int, protocol: str, bind_address: str = "0.0.0.0"
) -> ProbeEvidence:
    repo = ProbeRepository(db)
    probe = repo.find_latest_for_binding(host_id, port, protocol, bind_address)
    return ProbeEvidence(bind_probe=classify_bind_probe(probe), probe=probe)


def resolve_with_probe_awareness(
    db: Session,
    host_id: uuid.UUID,
    protocol: str,
    initial_result,
    refine: Callable[[FrozenSet[int]], object],
) -> Tuple[object, str]:
    """Shared, bounded probe-aware refinement of an already-computed
    DB-only candidate search result -- used identically by
    recommendation_service.suggest_port and
    allocation_service._resolve_candidate so there is exactly ONE
    "prefer a candidate a fresh probe didn't just rule out" loop, not two
    (task §6: "do not create conflicting definitions of free").

    `initial_result` and `refine(...)`'s return value must both expose a
    `.port: Optional[int]` attribute (both callers pass
    recommendation_service.CandidateSearchResult instances) -- a callback
    is used instead of importing `find_available_port` directly here to
    avoid a circular import (recommendation_service already imports THIS
    module for the exact same reason).

    If the winning candidate has a fresh `verified_occupied` probe, it is
    excluded and `refine()` is called again with the growing exclude set,
    up to MAX_PROBE_SKIP_ATTEMPTS times. Returns `(final_result, bind_probe)`.
    """
    exclude: set = set()
    result = initial_result
    bind_probe = NOT_REMOTE_CAPABLE

    for _ in range(MAX_PROBE_SKIP_ATTEMPTS):
        if result.port is None:
            break
        evidence = get_probe_evidence(db, host_id, result.port, protocol)
        if evidence.bind_probe == VERIFIED_OCCUPIED:
            exclude.add(result.port)
            result = refine(frozenset(exclude))
            continue
        if evidence.bind_probe == VERIFIED_FREE:
            bind_probe = VERIFIED_FREE
        else:
            # No fresh evidence yet -- queue one for a LATER call to
            # benefit from, and stay honest about this one right now.
            queue_probe(db, host_id, result.port, protocol)
            bind_probe = evidence.bind_probe
        break

    return result, bind_probe
