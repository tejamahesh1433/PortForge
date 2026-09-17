"""Concurrency test: multiple simultaneous recommend_and_reserve() calls
against the same small range must never hand out the same port twice.

Uses real threads hitting the real file-based lock (reservations.lock),
which is the actual mechanism two separate PortForge *processes* would
also use -- see reservations/lock.py's docstring for why the file lock's
mutual exclusion holds correctly across threads within one process too
(each thread opens its own file descriptor/handle; flock/msvcrt locking
contend correctly across distinct descriptors from the same process, not
just across processes).
"""
import threading
from types import SimpleNamespace

from portforge_agent.config import PortForgeConfig, PortRange
from portforge_agent.recommend import recommend_and_reserve
from portforge_agent.reservations.storage import ReservationStore


def test_concurrent_recommend_and_reserve_never_double_allocates(tmp_path, monkeypatch):
    reservations_path = tmp_path / "reservations.json"
    lock_path = tmp_path / "reservations.lock"

    monkeypatch.setattr("portforge_agent.recommend.default_reservations_path", lambda: reservations_path)
    monkeypatch.setattr("portforge_agent.recommend.default_lock_path", lambda: lock_path)
    monkeypatch.setattr("portforge_agent.recommend.discover_all_ports", lambda: [])
    monkeypatch.setattr("portforge_agent.recommend.pf.get_host_id", lambda: "host-a")
    monkeypatch.setattr(
        "portforge_agent.recommend.probe_bind",
        lambda port, protocol, address: SimpleNamespace(available=True, reason=None),
    )

    # Small range: 5 slots, 8 competing workers -- guarantees contention
    # (more workers than available ports) so at least some must lose.
    config = PortForgeConfig(ranges={"test-service": PortRange("test-service", 9000, 9004)}, exclusions=[])

    results = []
    results_lock = threading.Lock()

    def worker(index: int):
        result, reservation = recommend_and_reserve(
            "test-service", project=f"project-{index}", config=config
        )
        with results_lock:
            results.append((index, result.recommended_port, reservation))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert all(not t.is_alive() for t in threads), "a worker thread hung -- possible deadlock"

    successful = [(idx, port, r) for idx, port, r in results if r is not None]
    ports_allocated = [port for _, port, _ in successful]

    # The core guarantee: no two successful allocations received the same port.
    assert len(ports_allocated) == len(set(ports_allocated)), f"duplicate allocation detected: {ports_allocated}"

    # At most 5 of the 8 workers could succeed (range has 5 slots).
    assert len(successful) <= 5

    # The reservation file on disk must agree exactly with what callers
    # were told succeeded -- no phantom or missing reservations.
    saved = ReservationStore(reservations_path).load()
    assert len(saved) == len(successful)
    assert {r.port for r in saved} == set(ports_allocated)
    assert {r.reservation_id for r in saved} == {res.reservation_id for _, _, res in successful}


def test_concurrent_reserve_same_port_same_project_is_idempotent_not_duplicated(tmp_path, monkeypatch):
    from portforge_agent import reserve_ops

    reservations_path = tmp_path / "reservations.json"
    lock_path = tmp_path / "reservations.lock"

    monkeypatch.setattr("portforge_agent.reserve_ops.default_reservations_path", lambda: reservations_path)
    monkeypatch.setattr("portforge_agent.reserve_ops.default_lock_path", lambda: lock_path)
    monkeypatch.setattr("portforge_agent.reserve_ops.discover_all_ports", lambda: [])
    monkeypatch.setattr("portforge_agent.reserve_ops.pf.get_host_id", lambda: "host-a")

    def worker():
        reserve_ops.reserve(9500, "deeptrace", service="api")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    saved = ReservationStore(reservations_path).load()
    # Repeated concurrent reserve() calls for the same project/port must
    # settle on exactly one reservation, never duplicates.
    assert len(saved) == 1
    assert saved[0].project == "deeptrace"
