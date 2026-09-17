"""High-level reserve/release operations.

Encodes the ownership and idempotency rules from the Phase 4 brief in one
place, all performed under the reservation lock so two concurrent
`portforge reserve`/`release` invocations can't race each other (same
locking pattern as recommend.py's recommend_and_reserve()).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from . import platform as pf
from .discovery import discover_all_ports
from .evaluate import find_discovered
from .models import Protocol
from .paths import lock_path as default_lock_path
from .paths import reservations_path as default_reservations_path
from .reservations.lock import reservation_lock
from .reservations.models import Reservation
from .reservations.storage import ReservationStore


class ReserveOutcome(str, Enum):
    CREATED = "created"
    UPDATED_SAME_PROJECT = "updated_same_project"  # idempotent re-reserve, fields refreshed
    ADOPTED_ACTIVE_SAME_PROJECT = "adopted_active_same_project"
    REFUSED_OTHER_PROJECT_RESERVATION = "refused_other_project_reservation"
    REFUSED_ACTIVE_OTHER_OWNER = "refused_active_other_owner"


class ReleaseOutcome(str, Enum):
    RELEASED = "released"
    ALREADY_ABSENT = "already_absent"  # idempotent: nothing to release
    REFUSED_OTHER_PROJECT = "refused_other_project"


@dataclass
class ReserveResult:
    outcome: ReserveOutcome
    reservation: Optional[Reservation]
    message: str

    @property
    def success(self) -> bool:
        return self.outcome in (
            ReserveOutcome.CREATED,
            ReserveOutcome.UPDATED_SAME_PROJECT,
            ReserveOutcome.ADOPTED_ACTIVE_SAME_PROJECT,
        )


@dataclass
class ReleaseResult:
    outcome: ReleaseOutcome
    reservation: Optional[Reservation]
    message: str

    @property
    def success(self) -> bool:
        return self.outcome in (ReleaseOutcome.RELEASED, ReleaseOutcome.ALREADY_ABSENT)


def reserve(
    port: int,
    project: str,
    protocol: Protocol = Protocol.TCP,
    service: Optional[str] = None,
    purpose: Optional[str] = None,
    bind_address: Optional[str] = None,
    notes: Optional[str] = None,
    lock_timeout: Optional[float] = None,
) -> ReserveResult:
    """Create (or idempotently refresh) a reservation, honoring ownership rules:

    - An existing reservation for the same project is refreshed in place
      (service/purpose/notes updated, `updated_at` bumped) -- idempotent,
      not an error.
    - An existing reservation for a DIFFERENT project is refused outright;
      PortForge never silently overwrites another project's reservation.
    - A port with nothing reserved but an ACTIVE listener is only reserved
      if that listener's detected project confidently matches `project`
      (i.e. Phase 3's own conservative project detection already
      identified it as this project) -- "adopting" a port the project
      already legitimately occupies. Any other active owner (a different
      project, or one Phase 3 couldn't identify) refuses the reservation:
      unknown ownership is never treated as safe to claim.
    """
    host_id = pf.get_host_id()
    store = ReservationStore(default_reservations_path())
    discovered = discover_all_ports()  # fresh discovery, outside the lock (expensive part)

    lock_kwargs = {} if lock_timeout is None else {"timeout": lock_timeout}
    with reservation_lock(default_lock_path(), **lock_kwargs):
        reservations = store.load()

        existing = _find_exact(reservations, host_id, port, protocol)
        if existing is not None:
            if _same_project(existing.project, project):
                existing.service = service if service is not None else existing.service
                existing.purpose = purpose if purpose is not None else existing.purpose
                existing.notes = notes if notes is not None else existing.notes
                existing.bind_address = bind_address if bind_address is not None else existing.bind_address
                existing.updated_at = datetime.now(timezone.utc)
                store.save(reservations)
                return ReserveResult(
                    ReserveOutcome.UPDATED_SAME_PROJECT,
                    existing,
                    f"Port {port}/{protocol.value} was already reserved by '{project}' -- reservation refreshed.",
                )
            return ReserveResult(
                ReserveOutcome.REFUSED_OTHER_PROJECT_RESERVATION,
                existing,
                f"Port {port}/{protocol.value} is already reserved by '{existing.project}'"
                + (f"/{existing.service}" if existing.service else "")
                + f", not '{project}'. Refusing to overwrite another project's reservation.",
            )

        active = find_discovered(discovered, port, protocol)
        outcome = ReserveOutcome.CREATED
        if active is not None:
            if _same_project(active.project_name, project):
                outcome = ReserveOutcome.ADOPTED_ACTIVE_SAME_PROJECT
            else:
                owner_desc = (
                    active.project_name
                    or active.container_name
                    or active.process_name
                    or "an unrecognized owner"
                )
                return ReserveResult(
                    ReserveOutcome.REFUSED_ACTIVE_OTHER_OWNER,
                    None,
                    f"Port {port}/{protocol.value} is actively in use by {owner_desc}, "
                    f"not confidently '{project}'. Refusing to reserve it out from under an active listener.",
                )

        new_reservation = Reservation.create(
            host_id=host_id,
            port=port,
            project=project,
            protocol=protocol,
            service=service,
            purpose=purpose,
            bind_address=bind_address,
            notes=notes,
        )
        reservations.append(new_reservation)
        store.save(reservations)

        message = (
            f"Reservation created: {port}/{protocol.value} -> '{project}'."
            if outcome == ReserveOutcome.CREATED
            else f"Port {port}/{protocol.value} is actively used by '{project}' -- adopted as a reservation."
        )
        return ReserveResult(outcome, new_reservation, message)


def release(
    port: int,
    project: str,
    protocol: Protocol = Protocol.TCP,
    lock_timeout: Optional[float] = None,
) -> ReleaseResult:
    host_id = pf.get_host_id()
    store = ReservationStore(default_reservations_path())

    lock_kwargs = {} if lock_timeout is None else {"timeout": lock_timeout}
    with reservation_lock(default_lock_path(), **lock_kwargs):
        reservations = store.load()
        existing = _find_exact(reservations, host_id, port, protocol)

        if existing is None:
            return ReleaseResult(
                ReleaseOutcome.ALREADY_ABSENT,
                None,
                f"No reservation exists for {port}/{protocol.value} -- nothing to release.",
            )

        if not _same_project(existing.project, project):
            return ReleaseResult(
                ReleaseOutcome.REFUSED_OTHER_PROJECT,
                existing,
                f"Port {port}/{protocol.value} is reserved by '{existing.project}', not '{project}'. Refusing to release another project's reservation.",
            )

        reservations = [r for r in reservations if r.reservation_id != existing.reservation_id]
        store.save(reservations)
        return ReleaseResult(
            ReleaseOutcome.RELEASED, existing, f"Released reservation for {port}/{protocol.value} ('{project}')."
        )


def release_by_id(
    reservation_id: str, project: Optional[str] = None, lock_timeout: Optional[float] = None
) -> ReleaseResult:
    store = ReservationStore(default_reservations_path())

    lock_kwargs = {} if lock_timeout is None else {"timeout": lock_timeout}
    with reservation_lock(default_lock_path(), **lock_kwargs):
        reservations = store.load()
        existing = next((r for r in reservations if r.reservation_id == reservation_id), None)

        if existing is None:
            return ReleaseResult(
                ReleaseOutcome.ALREADY_ABSENT,
                None,
                f"No reservation with id {reservation_id} -- nothing to release.",
            )

        if project is not None and not _same_project(existing.project, project):
            return ReleaseResult(
                ReleaseOutcome.REFUSED_OTHER_PROJECT,
                existing,
                f"Reservation {reservation_id} belongs to '{existing.project}', not '{project}'. Refusing to release.",
            )

        reservations = [r for r in reservations if r.reservation_id != reservation_id]
        store.save(reservations)
        return ReleaseResult(
            ReleaseOutcome.RELEASED, existing, f"Released reservation {reservation_id} ('{existing.project}')."
        )


def sync_project_reservations(
    project_config: dict, lock_timeout: Optional[float] = None
) -> List[ReserveResult]:
    """Create/refresh local reservations from a parsed `.portforge.yml`'s
    `ports:` list. Never invoked implicitly by discovery/scan -- only by
    the explicit `sync-reservations` CLI command, per the project brief
    ("do not silently create reservations merely because a scan encounters
    a .portforge.yml").

    Each `ports` entry is `{port, service?, purpose?, protocol?}`; `project`
    (top-level in the config) is required. Ownership rules are identical to
    a normal `reserve()` call, applied independently per port.
    """
    project = project_config.get("project")
    if not project or not isinstance(project, str):
        raise ValueError("Project config has no 'project' name -- refusing to sync reservations.")

    entries = project_config.get("ports")
    if not isinstance(entries, list):
        return []

    results: List[ReserveResult] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        port = entry.get("port")
        if not isinstance(port, int):
            continue

        service = entry.get("service")
        purpose = entry.get("purpose")
        protocol_raw = entry.get("protocol")
        protocol = Protocol.UDP if str(protocol_raw).lower() == "udp" else Protocol.TCP

        result = reserve(
            port,
            project,
            protocol=protocol,
            service=str(service) if service is not None else None,
            purpose=str(purpose) if purpose is not None else None,
            lock_timeout=lock_timeout,
        )
        results.append(result)

    return results


def _same_project(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def _find_exact(
    reservations: List[Reservation], host_id: str, port: int, protocol: Protocol
) -> Optional[Reservation]:
    for r in reservations:
        if r.host_id == host_id and r.port == port and r.protocol == protocol:
            return r
    return None
