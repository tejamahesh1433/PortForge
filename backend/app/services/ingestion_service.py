"""Snapshot ingestion: the transactional heart of the central registry.

## Full-snapshot semantics

Each `/api/agent/observations` submission is treated as **authoritative
for the entire host's port state at that instant** -- exactly what the
agent's own `discover_all_ports()` already produces (a complete scan, not
an incremental delta). Concretely: any `current_port_observations` row for
that host that is NOT present in the submitted set is considered gone and
is removed from current state (with a "disappeared" history event
recorded). An agent that wants to report "still nothing changed" simply
re-submits its full current list, exactly as `portforge scan` would
produce right now.

## Current state vs. history (the strategy asked for in the project brief)

Two tables, not one growing-forever log:

- `current_port_observations`: exactly one row per (host, port, protocol,
  bind_address) believed active right now. Every accepted snapshot
  upserts this table to match reality.
- `port_observation_events`: append-only, but only for *meaningful
  changes* -- a binding appearing, disappearing, or its owner/state
  changing. An identical re-confirmation (the agent scans again 30 seconds
  later and finds the exact same process on the exact same port) updates
  `last_seen`/`observed_at` on the current row and writes **no** new
  event row. This is what keeps the history table from growing by one row
  per port per scan forever, while still answering "when was port 8000
  last used" (the most recent "disappeared" event's `occurred_at`, or the
  current row's `last_seen` if it's still active).

"Meaningfully changed" is defined narrowly on purpose: `state`,
`process_name`, `container_id`, `project_name`, and `purpose`. Anything
else changing (e.g. a PID, since processes restart with new PIDs
constantly) does NOT by itself count as a change worth a history row --
see `_meaningfully_changed()`.

## Snapshot identity & staleness

`scan_id` (a UUID the agent generates once per scan) makes a *repeated*
submission of the same scan idempotent: if a `Scan` row with that id
already exists, ingestion returns success immediately without
reprocessing (matches `SnapshotResult.accepted=True`, all counts 0).

Staleness is rejected using `observed_at`: if a host has already accepted
a scan with a later (or equal) `observed_at`, an incoming submission with
an *older* `observed_at` is rejected outright (its data is never applied)
-- this protects against an out-of-order delayed submission (e.g. a retry
that finally lands after a newer scan already went through) overwriting
newer current state with stale data. A `sequence` counter was considered
and intentionally not used (see the project brief's "if appropriate"): it
would require new persistent agent-side state purely for ordering, when
`observed_at` (which every scan already carries) is sufficient here.

## Transactionality

Everything below -- host update, current-row upserts/deletes, event
inserts, the Scan row itself -- happens in one SQLAlchemy session and is
committed exactly once at the end. Any exception before that point leaves
the session uncommitted; the caller (api/agents.py) lets it propagate,
FastAPI's request-scoped session is rolled back on the way out, and
nothing is left half-applied. See tests/test_ingestion_service.py for a
rollback test that simulates a failure partway through.

## Duplicate-binding canonicalization

`current_port_observations` has exactly one row per (host, port,
protocol, bind_address) -- `uq_current_port_binding`. A single incoming
snapshot can legitimately contain more than one raw observation for that
same identity (physically reproduced on NTMKEYA: two distinct processes
both bound to UDP 5353/`::` for mDNS via SO_REUSEADDR -- entirely valid
at the OS level, but the schema can only persist one current row for it).
Before this identity is used for anything else, `_canonicalize_observations()`
collapses each group of same-identity observations into one canonical
observation via `_merge_observation_group()`, deterministically and
independent of input order (see `_observation_sort_key()`): source
authority mirrors the agent's own established `merge_native_and_docker()`
precedent (docker > process/system), then detection confidence
(`Confidence.HIGH > ... > UNKNOWN`), then richness, then a fully
content-derived tiebreak. Remaining metadata is backfilled from runner-up
observations in three COHERENT groups (process identity, container
identity, detection result) rather than field-by-field, so a process's
pid is never paired with a different process's container id.

This is a genuine, documented information reduction, not a workaround:
when two truly different physical listeners share one binding identity,
only the winning observation's process/container metadata survives as
the current row -- the schema has no way to represent two owners for one
(host, port, protocol, bind_address) row. `IngestionResult.duplicates_merged`
reports how many raw observations were absorbed this way, so a caller can
tell "N raw physical observations, M canonical binding identities" apart
from actual data loss.

## Concurrent same-host ingestion

Two overlapping requests for the SAME host both reading `current_rows`
under READ COMMITTED isolation, before either commits, could previously
both decide the same binding was "new" and collide on
`uq_current_port_binding` at commit -- a second, distinct bug from the
same-request duplicate problem above (that one reproduces with zero
concurrency; this one only reproduces with two truly overlapping
transactions). `ingest_snapshot()` now opens with a PostgreSQL
transaction-scoped advisory lock keyed by `host_id`
(`_acquire_host_ingestion_lock()`) -- automatically released on
commit/rollback, no manual unlock, and scoped per-host so concurrent
traffic for *different* hosts is entirely unaffected (no new QueuePool
pressure). The second overlapping request simply waits for the first to
finish, then re-reads fully-committed state, so the existing
`observed_at` staleness check above -- unchanged -- correctly decides
which snapshot wins instead of both racing to insert.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models.port_observation import CurrentPortObservation, PortObservationEvent
from ..models.scan import Scan
from ..repositories.host_repository import HostRepository
from ..repositories.port_repository import PortRepository
from ..repositories.activity_repository import ActivityRepository
from ..models.activity import ActivityEvent
from ..schemas.agent import ObservationIn


class SnapshotRejectedError(Exception):
    """The snapshot was validated but rejected for a domain reason (stale,
    batch too large, unknown host) -- distinct from a schema validation
    error (which FastAPI/Pydantic already rejects with 422 before this
    service ever runs).
    """


class StaleSnapshotError(SnapshotRejectedError):
    pass


class BatchTooLargeError(SnapshotRejectedError):
    pass


_BindingKey = Tuple[int, str, str]  # (port, protocol, bind_address)

# Fields whose change is meaningful enough to record a history event.
# Everything else (pid, paths, timestamps, ...) updates the current row
# silently -- see module docstring "Current state vs. history".
_CHANGE_FIELDS = ("state", "process_name", "container_id", "project_name", "purpose")


@dataclass
class IngestionResult:
    scan_id: uuid.UUID
    accepted: bool
    reason: Optional[str]
    observations_processed: int
    appeared: int
    changed: int
    disappeared: int
    duplicates_merged: int = 0


def _binding_key(obs) -> _BindingKey:
    return (obs.port, obs.protocol if isinstance(obs.protocol, str) else obs.protocol.value, obs.bind_address)


def _meaningfully_changed(current: CurrentPortObservation, incoming: ObservationIn) -> bool:
    for field in _CHANGE_FIELDS:
        current_value = getattr(current, field)
        incoming_value = getattr(incoming, field)
        current_value = current_value.value if hasattr(current_value, "value") else current_value
        if current_value != incoming_value:
            return True
    return False


# --- Duplicate-binding canonicalization -- see module docstring ------------

_SOURCE_RANK = {"docker": 2, "process": 1, "system": 1, "reservation": 0}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

# Coherent field groups backfilled together (never field-by-field) so a
# runner-up observation's process identity is never mixed with a
# different runner-up's container identity into one incoherent record.
_PROCESS_FIELDS: Tuple[str, ...] = ("pid", "process_name", "process_path", "working_directory")
_CONTAINER_FIELDS: Tuple[str, ...] = (
    "container_id",
    "container_name",
    "container_image",
    "container_port",
    "docker_compose_project",
    "service_name",
)
_DETECTION_FIELDS: Tuple[str, ...] = ("purpose", "category", "detection_confidence")
_INDEPENDENT_BACKFILL_FIELDS: Tuple[str, ...] = ("project_name",)


def _source_rank(source) -> int:
    value = source.value if hasattr(source, "value") else source
    return _SOURCE_RANK.get(value, 0)


def _confidence_rank(confidence) -> int:
    if not confidence:
        return 0
    value = confidence.value if hasattr(confidence, "value") else confidence
    return _CONFIDENCE_RANK.get(str(value).lower(), 0)


def _observation_sort_key(obs: ObservationIn) -> tuple:
    """Higher is "more authoritative" -- picks which of several incoming
    observations for the SAME Central binding identity becomes canonical.
    Every component is derived from the observation's own content, never
    its position in the incoming list, so the result never depends on
    submission order. See the module docstring's "Duplicate-binding
    canonicalization" section for the full rationale.
    """
    return (
        _source_rank(obs.source),
        _confidence_rank(obs.detection_confidence),
        1 if obs.process_name else 0,
        1 if obs.container_id else 0,
        1 if obs.project_name else 0,
        1 if obs.purpose else 0,
        # Final tiebreak: content only, never list position -- guarantees
        # the same result regardless of incoming observation order.
        obs.process_name or "",
        obs.container_id or "",
        obs.container_name or "",
        obs.project_name or "",
        obs.category or "",
        obs.pid or 0,
    )


def _group_has_value(obs: ObservationIn, fields: Tuple[str, ...]) -> bool:
    return any(getattr(obs, f, None) for f in fields)


def _copy_field_group(target: ObservationIn, source: ObservationIn, fields: Tuple[str, ...]) -> None:
    for f in fields:
        setattr(target, f, getattr(source, f, None))


def _merge_observation_group(group: List[ObservationIn]) -> ObservationIn:
    """Collapses >=2 incoming observations that share one Central binding
    identity into a single canonical observation. See the module
    docstring's "Duplicate-binding canonicalization" section.
    """
    ranked = sorted(group, key=_observation_sort_key, reverse=True)
    primary = ranked[0]
    merged = primary.model_copy(deep=True)

    if not _group_has_value(merged, _PROCESS_FIELDS):
        for candidate in ranked[1:]:
            if _group_has_value(candidate, _PROCESS_FIELDS):
                _copy_field_group(merged, candidate, _PROCESS_FIELDS)
                break

    if not _group_has_value(merged, _CONTAINER_FIELDS):
        for candidate in ranked[1:]:
            if _group_has_value(candidate, _CONTAINER_FIELDS):
                _copy_field_group(merged, candidate, _CONTAINER_FIELDS)
                break

    if not _group_has_value(merged, _DETECTION_FIELDS):
        for candidate in ranked[1:]:
            if _group_has_value(candidate, _DETECTION_FIELDS):
                _copy_field_group(merged, candidate, _DETECTION_FIELDS)
                break

    for f in _INDEPENDENT_BACKFILL_FIELDS:
        if not getattr(merged, f, None):
            for candidate in ranked[1:]:
                value = getattr(candidate, f, None)
                if value:
                    setattr(merged, f, value)
                    break

    merged.first_seen = min(o.first_seen for o in group)
    merged.last_seen = max(o.last_seen for o in group)

    return merged


def _canonicalize_observations(observations: List[ObservationIn]) -> "Tuple[List[ObservationIn], int]":
    """Collapses the incoming observation list to exactly one entry per
    Central binding identity (host+port+protocol+bind_address). Returns
    (canonical_list, duplicates_merged); duplicates_merged is how many raw
    observations were absorbed into an existing canonical entry (0 when
    every incoming binding identity was already unique).
    """
    groups: Dict[_BindingKey, List[ObservationIn]] = {}
    order: List[_BindingKey] = []
    for obs in observations:
        key = _binding_key(obs)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(obs)

    canonical: List[ObservationIn] = [
        groups[key][0] if len(groups[key]) == 1 else _merge_observation_group(groups[key]) for key in order
    ]
    return canonical, len(observations) - len(canonical)


def _acquire_host_ingestion_lock(db: Session, host_id: uuid.UUID) -> None:
    """Serializes concurrent ingest_snapshot() calls for the SAME host_id
    via a transaction-scoped PostgreSQL advisory lock. See the module
    docstring's "Concurrent same-host ingestion" section.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:host_id))"), {"host_id": str(host_id)})


def ingest_snapshot(
    db: Session,
    host_id: uuid.UUID,
    scan_id: uuid.UUID,
    observed_at: datetime,
    observations: List[ObservationIn],
    max_batch_size: int,
) -> IngestionResult:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    if len(observations) > max_batch_size:
        raise BatchTooLargeError(
            f"Snapshot has {len(observations)} observations, exceeding the configured limit of {max_batch_size}."
        )

    # Serializes concurrent submissions for THIS host only -- see
    # "Concurrent same-host ingestion" above. Acquired before any read so
    # a second overlapping request always sees fully-committed state.
    _acquire_host_ingestion_lock(db, host_id)

    # Idempotent replay: this exact scan was already accepted.
    existing_scan = db.get(Scan, scan_id)
    if existing_scan is not None:
        return IngestionResult(scan_id, True, "duplicate scan_id (already processed)", 0, 0, 0, 0, 0)

    host_repo = HostRepository(db)
    host = host_repo.get(host_id)
    if host is None:
        raise SnapshotRejectedError(f"Unknown host_id {host_id}; enroll before submitting observations.")

    if host.last_scan_observed_at is not None and observed_at <= host.last_scan_observed_at:
        raise StaleSnapshotError(
            f"Snapshot observed_at={observed_at.isoformat()} is not newer than the last accepted "
            f"scan's observed_at={host.last_scan_observed_at.isoformat()}; rejected as stale/out-of-order."
        )

    # Collapse any duplicate binding identities WITHIN this snapshot before
    # touching the DB at all -- see "Duplicate-binding canonicalization"
    # above. Everything below operates on the canonical list only.
    canonical_observations, duplicates_merged = _canonicalize_observations(observations)

    port_repo = PortRepository(db)
    activity_repo = ActivityRepository(db)
    current_rows = {
        (row.port, row.protocol.value, row.bind_address): row for row in port_repo.list_current_for_host(host_id)
    }
    incoming_keys = {_binding_key(obs) for obs in canonical_observations}
    
    is_baseline = host.last_scan_observed_at is None

    appeared = changed = disappeared = 0

    for obs in canonical_observations:
        key = _binding_key(obs)
        existing = current_rows.get(key)

        if existing is None:
            row = CurrentPortObservation(
                host_id=host_id,
                port=obs.port,
                protocol=obs.protocol,
                bind_address=obs.bind_address,
                state=obs.state,
                source=obs.source,
                pid=obs.pid,
                process_name=obs.process_name,
                process_path=obs.process_path,
                working_directory=obs.working_directory,
                container_id=obs.container_id,
                container_name=obs.container_name,
                container_image=obs.container_image,
                container_port=obs.container_port,
                docker_compose_project=obs.docker_compose_project,
                service_name=obs.service_name,
                project_name=obs.project_name,
                purpose=obs.purpose,
                category=obs.category,
                detection_confidence=obs.detection_confidence,
                first_seen=obs.first_seen,
                last_seen=obs.last_seen,
                observed_at=observed_at,
                scan_id=scan_id,
            )
            port_repo.upsert_current(row)
            port_repo.add_event(
                PortObservationEvent(
                    host_id=host_id,
                    port=obs.port,
                    protocol=obs.protocol,
                    bind_address=obs.bind_address,
                    event_type="appeared",
                    state=obs.state,
                    process_name=obs.process_name,
                    project_name=obs.project_name,
                    purpose=obs.purpose,
                    scan_id=scan_id,
                    occurred_at=observed_at,
                )
            )
            if not is_baseline:
                activity_repo.add(
                    ActivityEvent(
                        host_id=host_id,
                        timestamp=observed_at,
                        event_type="PORT_APPEARED",
                        port=obs.port,
                        protocol=obs.protocol,
                        bind_address=obs.bind_address,
                        source=obs.source.value if hasattr(obs.source, "value") else obs.source,
                        identity_context=obs.process_name or obs.container_name or obs.project_name or "unknown",
                        metadata_json={"project_name": obs.project_name} if obs.project_name else None,
                        summary=f"Port {obs.port}/{obs.protocol} appeared (source: {obs.source.value if hasattr(obs.source, 'value') else obs.source})",
                    )
                )
            appeared += 1
        else:
            if _meaningfully_changed(existing, obs):
                port_repo.add_event(
                    PortObservationEvent(
                        host_id=host_id,
                        port=obs.port,
                        protocol=obs.protocol,
                        bind_address=obs.bind_address,
                        event_type="changed",
                        state=obs.state,
                        process_name=obs.process_name,
                        project_name=obs.project_name,
                        purpose=obs.purpose,
                        scan_id=scan_id,
                        occurred_at=observed_at,
                    )
                )
                changed += 1

            existing.state = obs.state
            existing.source = obs.source
            existing.pid = obs.pid
            existing.process_name = obs.process_name
            existing.process_path = obs.process_path
            existing.working_directory = obs.working_directory
            existing.container_id = obs.container_id
            existing.container_name = obs.container_name
            existing.container_image = obs.container_image
            existing.container_port = obs.container_port
            existing.docker_compose_project = obs.docker_compose_project
            existing.service_name = obs.service_name
            existing.project_name = obs.project_name
            existing.purpose = obs.purpose
            existing.category = obs.category
            existing.detection_confidence = obs.detection_confidence
            existing.last_seen = obs.last_seen
            existing.observed_at = observed_at
            existing.scan_id = scan_id

    for key, row in current_rows.items():
        if key not in incoming_keys:
            port_repo.add_event(
                PortObservationEvent(
                    host_id=host_id,
                    port=row.port,
                    protocol=row.protocol,
                    bind_address=row.bind_address,
                    event_type="disappeared",
                    state=row.state,
                    process_name=row.process_name,
                    project_name=row.project_name,
                    purpose=row.purpose,
                    scan_id=scan_id,
                    occurred_at=observed_at,
                )
            )
            activity_repo.add(
                ActivityEvent(
                    host_id=host_id,
                    timestamp=observed_at,
                    event_type="PORT_DISAPPEARED",
                    port=row.port,
                    protocol=row.protocol,
                    bind_address=row.bind_address,
                    source=row.source.value if hasattr(row.source, "value") else row.source,
                    identity_context=row.process_name or row.container_name or row.project_name or "unknown",
                    metadata_json={"project_name": row.project_name} if row.project_name else None,
                    summary=f"Port {row.port}/{row.protocol.value if hasattr(row.protocol, 'value') else row.protocol} disappeared",
                )
            )
            port_repo.delete_current(row)
            disappeared += 1

    db.add(
        Scan(
            id=scan_id,
            host_id=host_id,
            observed_at=observed_at,
            observation_count=len(observations),
            received_at=datetime.now(timezone.utc),
        )
    )
    host.last_scan_observed_at = observed_at
    host.last_seen = observed_at

    db.commit()

    return IngestionResult(
        scan_id=scan_id,
        accepted=True,
        reason=None,
        observations_processed=len(observations),
        appeared=appeared,
        changed=changed,
        disappeared=disappeared,
        duplicates_merged=duplicates_merged,
    )
