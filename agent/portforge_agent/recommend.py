"""Deterministic port recommendation engine.

Every recommendation must pass three layers, always in this order (see
agent/README.md "Recommendation algorithm" for the full write-up):

    1. Fresh discovery (native + Docker)  -- the candidate must be FREE
    2. Reservation / exclusion evaluation  -- no reservation, not excluded
    3. Real socket bind probe               -- must actually be bindable

A port is only ever recommended if it clears all three. Candidate order
within the configured range is pluggable (see RecommendationStrategy) so
future strategies (project-affinity, an explicit preferred-ports list,
...) can be added without touching the validation pipeline itself. Phase 4
implements one strategy, `sequential`: walk the configured range from
`start` to `end` in order. This already satisfies "try common defaults
first" for the default ranges (3000 before 3001 for frontend, 8000 before
8001 for api, ...) without needing a second, separately-maintained list of
preferred ports that would just duplicate the range's own starting point.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from . import platform as pf
from .bindprobe import probe_bind
from .config import PortForgeConfig, load_config
from .discovery import discover_all_ports
from .evaluate import find_discovered, find_reservation
from .models import DiscoveredPort, Protocol
from .paths import lock_path as default_lock_path
from .paths import reservations_path as default_reservations_path
from .reservations.lock import reservation_lock
from .reservations.models import Reservation
from .reservations.storage import ReservationStore


class RecommendationStrategy(ABC):
    """Produces the order in which candidate ports within a range are tried."""

    name = "abstract"

    @abstractmethod
    def candidates(self, start: int, end: int) -> Iterator[int]:
        raise NotImplementedError


class SequentialStrategy(RecommendationStrategy):
    """Walk the range in order -- see module docstring for why this alone
    already satisfies "try common defaults first" for the default ranges.
    """

    name = "sequential"

    def candidates(self, start: int, end: int) -> Iterator[int]:
        yield from range(start, end + 1)


@dataclass
class ValidationStep:
    name: str
    passed: bool
    detail: str


@dataclass
class RecommendationResult:
    service_type: str
    protocol: Protocol
    address: str
    recommended_port: Optional[int]
    steps: List[ValidationStep] = field(default_factory=list)
    candidates_tried: int = 0
    unknown_service_type: bool = False

    @property
    def exhausted(self) -> bool:
        return self.recommended_port is None and not self.unknown_service_type

    def to_dict(self) -> dict:
        return {
            "service_type": self.service_type,
            "protocol": self.protocol.value,
            "address": self.address,
            "recommended_port": self.recommended_port,
            "steps": [{"name": s.name, "passed": s.passed, "detail": s.detail} for s in self.steps],
            "candidates_tried": self.candidates_tried,
            "unknown_service_type": self.unknown_service_type,
        }


def recommend_port(
    service_type: str,
    protocol: Protocol = Protocol.TCP,
    address: str = "0.0.0.0",
    config: Optional[PortForgeConfig] = None,
    reservations: Optional[List[Reservation]] = None,
    discovered: Optional[List[DiscoveredPort]] = None,
    strategy: Optional[RecommendationStrategy] = None,
    host_id: Optional[str] = None,
) -> RecommendationResult:
    """Find the first candidate port in `service_type`'s configured range
    that passes all three validation layers.

    `reservations`/`discovered` can be pre-fetched by the caller (used by
    recommend_and_reserve() to avoid re-running expensive discovery twice);
    when omitted, both are fetched fresh here.
    """
    config = config if config is not None else load_config()
    strategy = strategy or SequentialStrategy()
    host_id = host_id or pf.get_host_id()

    port_range = config.range_for(service_type)
    if port_range is None:
        return RecommendationResult(
            service_type, protocol, address, None, unknown_service_type=True
        )

    if reservations is None:
        reservations = ReservationStore(default_reservations_path()).load()
    if discovered is None:
        discovered = discover_all_ports()

    tried = 0
    for candidate in strategy.candidates(port_range.start, port_range.end):
        tried += 1
        steps: List[ValidationStep] = []

        if config.is_excluded(candidate):
            steps.append(ValidationStep("exclusion", False, "port is excluded by configuration"))
            continue

        existing = find_discovered(discovered, candidate, protocol)
        if existing is not None:
            owner = existing.container_name or existing.process_name or "unknown owner"
            steps.append(ValidationStep("discovery", False, f"active listener present ({owner})"))
            continue
        steps.append(ValidationStep("discovery", True, "no active listener"))

        reservation = find_reservation(reservations, host_id, candidate, protocol)
        if reservation is not None:
            steps.append(ValidationStep("reservation", False, f"reserved by '{reservation.project}'"))
            continue
        steps.append(ValidationStep("reservation", True, "no reservation"))
        steps.append(ValidationStep("exclusion", True, "not excluded"))

        probe = probe_bind(candidate, protocol, address)
        if not probe.available:
            steps.append(ValidationStep("bind_probe", False, probe.reason or "bind failed"))
            continue
        steps.append(ValidationStep("bind_probe", True, "bind succeeded"))

        return RecommendationResult(service_type, protocol, address, candidate, steps, tried)

    return RecommendationResult(service_type, protocol, address, None, [], tried)


def recommend_and_reserve(
    service_type: str,
    project: str,
    service: Optional[str] = None,
    purpose: Optional[str] = None,
    protocol: Protocol = Protocol.TCP,
    address: str = "0.0.0.0",
    notes: Optional[str] = None,
    config: Optional[PortForgeConfig] = None,
    strategy: Optional[RecommendationStrategy] = None,
    lock_timeout: Optional[float] = None,
) -> tuple:
    """Recommend a port AND reserve it, as one safe operation.

    Concurrency: two PortForge processes calling this at the same moment
    for the same range must never receive the same port. The expensive
    part (native + Docker discovery) runs once, *before* the lock is
    acquired -- live listeners can't change because of a reservation race,
    so there's no correctness reason to hold the lock during it. Everything
    that actually depends on other processes' reservations happens *inside*
    the lock: reservations are reloaded fresh, and the full candidate scan
    (reservation lookup + bind probe, both cheap) is redone from scratch.
    This means a second process that loses the race simply sees the first
    process's freshly-written reservation and correctly skips that
    candidate for the next one -- no special-case retry logic needed; see
    agent/README.md "Concurrency / locking" for the full reasoning, and
    tests/test_recommend_concurrency.py for a concurrent-thread proof.

    Returns (RecommendationResult, Optional[Reservation]) -- the
    reservation is None if no candidate passed validation.
    """
    config = config if config is not None else load_config()
    host_id = pf.get_host_id()
    store = ReservationStore(default_reservations_path())
    lock_file = default_lock_path()

    discovered = discover_all_ports()  # expensive; done outside the lock

    lock_kwargs = {} if lock_timeout is None else {"timeout": lock_timeout}
    with reservation_lock(lock_file, **lock_kwargs):
        reservations = store.load()  # fresh, inside the lock
        result = recommend_port(
            service_type,
            protocol=protocol,
            address=address,
            config=config,
            reservations=reservations,
            discovered=discovered,
            strategy=strategy,
            host_id=host_id,
        )

        if result.recommended_port is None:
            return result, None

        new_reservation = Reservation.create(
            host_id=host_id,
            port=result.recommended_port,
            project=project,
            protocol=protocol,
            service=service,
            purpose=purpose,
            notes=notes,
        )
        reservations.append(new_reservation)
        store.save(reservations)

        return result, new_reservation
