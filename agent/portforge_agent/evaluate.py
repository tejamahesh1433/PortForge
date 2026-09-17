"""Combine live discovery with reservations into a final evaluated port state.

This is where PortForge answers "is port X available", "is it reserved",
and "is something conflicting with a reservation" -- Phases 1-3 only ever
answered "what's listening right now."

State semantics (see agent/README.md "State evaluation" for the full
rationale and worked examples):

    FREE      no listener, no reservation
    ACTIVE    a listener, no reservation -- OR a listener whose detected
              project matches the reservation's project (reservation kept
              as metadata, not lost)
    RESERVED  no listener, but a reservation exists
    CONFLICT  a listener exists whose project does NOT match the
              reservation's project -- including when the listener's
              project is simply UNKNOWN. Unknown ownership is deliberately
              never treated as "probably fine": see _project_matches().
    SYSTEM    the listener itself is OS/system-owned (DiscoveredPort.source
              == Source.SYSTEM) -- e.g. Windows' "System" process, or a PID
              discovery couldn't attribute to any process at all. This is
              never inferred merely from a low port number; it only ever
              reflects what discovery already determined.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models import DiscoveredPort, PortState, Protocol, Source
from .reservations.models import Reservation


def _project_matches(discovered_project: Optional[str], reservation_project: str) -> bool:
    """Conservative by design: an unknown/undetected project on the
    listener side is NEVER treated as a match. Guessing "probably the same
    project" from weak or absent evidence is exactly what Phase 3 already
    refuses to do for purpose/project detection, and the same rule applies
    here for a stronger reason -- getting this wrong hides a real conflict.
    """
    if not discovered_project:
        return False
    return discovered_project.strip().lower() == reservation_project.strip().lower()


def find_reservation(
    reservations: List[Reservation],
    host_id: str,
    port: int,
    protocol: Protocol,
    bind_address: Optional[str] = None,
) -> Optional[Reservation]:
    """Find a reservation for (host, port, protocol).

    A reservation with `bind_address=None` ("any address") matches
    regardless of the address being checked -- most reservations are
    address-agnostic. A reservation scoped to a specific bind_address only
    matches that same address (or a lookup that also omits an address,
    i.e. "does any reservation exist for this port/protocol at all").
    """
    for reservation in reservations:
        if (
            reservation.host_id == host_id
            and reservation.port == port
            and reservation.protocol == protocol
        ):
            if reservation.bind_address is None or bind_address is None:
                return reservation
            if reservation.bind_address == bind_address:
                return reservation
    return None


def find_discovered(
    discovered: List[DiscoveredPort], port: int, protocol: Protocol
) -> Optional[DiscoveredPort]:
    """First matching discovered binding for (port, protocol), across
    whichever address(es) it's listening on. See evaluate.py's module
    docstring: reservations/conflicts operate at (protocol, port)
    granularity, not per-address -- `scan`/`inspect` remain address-precise.
    """
    for port_obj in discovered:
        host_port = port_obj.host_port if port_obj.host_port is not None else port_obj.port
        if host_port == port and port_obj.protocol == protocol:
            return port_obj
    return None


@dataclass
class EvaluatedPort:
    """The Phase 4 unit of output for check/reservations/conflicts: a
    (host, protocol, port) "slot", with whatever combination of a live
    discovery record and/or a reservation applies, and the final state.
    """

    host_id: str
    port: int
    protocol: Protocol
    state: PortState
    discovered: Optional[DiscoveredPort] = None
    reservation: Optional[Reservation] = None
    conflict_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_id": self.host_id,
            "port": self.port,
            "protocol": self.protocol.value,
            "state": self.state.value,
            "discovered": self.discovered.to_dict() if self.discovered else None,
            "reservation": self.reservation.to_dict() if self.reservation else None,
            "conflict_reason": self.conflict_reason,
        }


def evaluate_port(
    host_id: str,
    port: int,
    protocol: Protocol,
    discovered: Optional[DiscoveredPort],
    reservation: Optional[Reservation],
) -> EvaluatedPort:
    if discovered is None and reservation is None:
        return EvaluatedPort(host_id, port, protocol, PortState.FREE)

    if discovered is None and reservation is not None:
        return EvaluatedPort(host_id, port, protocol, PortState.RESERVED, reservation=reservation)

    assert discovered is not None
    if discovered.source == Source.SYSTEM:
        return EvaluatedPort(
            host_id, port, protocol, PortState.SYSTEM, discovered=discovered, reservation=reservation
        )

    if reservation is None:
        return EvaluatedPort(host_id, port, protocol, PortState.ACTIVE, discovered=discovered)

    if _project_matches(discovered.project_name, reservation.project):
        return EvaluatedPort(
            host_id, port, protocol, PortState.ACTIVE, discovered=discovered, reservation=reservation
        )

    owner_desc = discovered.project_name or discovered.container_name or discovered.process_name or "an unrecognized owner"
    reason = (
        f"reserved for '{reservation.project}'"
        + (f"/{reservation.service}" if reservation.service else "")
        + f", but currently used by {owner_desc}"
    )
    return EvaluatedPort(
        host_id,
        port,
        protocol,
        PortState.CONFLICT,
        discovered=discovered,
        reservation=reservation,
        conflict_reason=reason,
    )


def evaluate_all(
    host_id: str,
    discovered: List[DiscoveredPort],
    reservations: List[Reservation],
) -> List[EvaluatedPort]:
    """Evaluate every (protocol, port) that appears in either discovery or
    the reservation list, so a purely-reserved (nothing listening) port is
    never dropped just because discovery didn't see it.
    """
    keys = set()
    for port_obj in discovered:
        host_port = port_obj.host_port if port_obj.host_port is not None else port_obj.port
        keys.add((port_obj.protocol, host_port))
    for reservation in reservations:
        if reservation.host_id == host_id:
            keys.add((reservation.protocol, reservation.port))

    results = []
    for protocol, port in sorted(keys, key=lambda k: (k[1], k[0].value)):
        d = find_discovered(discovered, port, protocol)
        r = find_reservation(reservations, host_id, port, protocol)
        results.append(evaluate_port(host_id, port, protocol, d, r))
    return results


def evaluate_physical(
    host_id: str,
    discovered: DiscoveredPort,
    reservations: List[Reservation],
) -> EvaluatedPort:
    """Evaluate a specific physical discovered binding against reservations.
    Unlike evaluate_all(), this does not collapse logically identical
    (protocol, port) bindings. Used for Phase 6 physical snapshots where
    every unique address/binding must be preserved.
    """
    logical_port = discovered.host_port if discovered.host_port is not None else discovered.port
    reservation = find_reservation(
        reservations, host_id, logical_port, discovered.protocol, discovered.bind_address
    )
    return evaluate_port(host_id, logical_port, discovered.protocol, discovered, reservation)

