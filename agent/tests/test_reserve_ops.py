"""Tests for high-level reserve/release operations: ownership rules,
idempotency, and .portforge.yml sync.
"""
import pytest

from portforge_agent import reserve_ops
from portforge_agent.models import DiscoveredPort, Protocol, Source
from portforge_agent.reservations.storage import ReservationStore


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    reservations_path = tmp_path / "reservations.json"
    lock_path = tmp_path / "reservations.lock"
    monkeypatch.setattr(reserve_ops, "default_reservations_path", lambda: reservations_path)
    monkeypatch.setattr(reserve_ops, "default_lock_path", lambda: lock_path)
    monkeypatch.setattr(reserve_ops.pf, "get_host_id", lambda: "host-a")
    return reservations_path


def _discovered(port=8003, project_name=None, process_name="app.exe", source=Source.PROCESS):
    return DiscoveredPort(
        hostname="h",
        host_id="host-a",
        operating_system="linux",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=source,
        process_name=process_name,
        project_name=project_name,
    )


def test_reserve_creates_new_reservation_when_free(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    result = reserve_ops.reserve(8003, "deeptrace", service="api", purpose="api")

    assert result.success
    assert result.outcome == reserve_ops.ReserveOutcome.CREATED
    assert result.reservation.project == "deeptrace"
    assert result.reservation.service == "api"


def test_reserve_idempotent_for_same_project(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    first = reserve_ops.reserve(8003, "deeptrace", service="api")
    second = reserve_ops.reserve(8003, "deeptrace", service="api-v2")

    assert second.success
    assert second.outcome == reserve_ops.ReserveOutcome.UPDATED_SAME_PROJECT
    assert second.reservation.reservation_id == first.reservation.reservation_id
    assert second.reservation.service == "api-v2"  # refreshed


def test_reserve_refuses_when_reserved_by_another_project(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    reserve_ops.reserve(8003, "deeptrace")
    result = reserve_ops.reserve(8003, "another-project")

    assert not result.success
    assert result.outcome == reserve_ops.ReserveOutcome.REFUSED_OTHER_PROJECT_RESERVATION
    assert result.reservation.project == "deeptrace"


def test_reserve_refuses_actively_used_port_different_owner(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [_discovered(project_name="other-project")])
    result = reserve_ops.reserve(8003, "deeptrace")

    assert not result.success
    assert result.outcome == reserve_ops.ReserveOutcome.REFUSED_ACTIVE_OTHER_OWNER


def test_reserve_refuses_actively_used_port_unknown_owner(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [_discovered(project_name=None)])
    result = reserve_ops.reserve(8003, "deeptrace")

    assert not result.success
    assert result.outcome == reserve_ops.ReserveOutcome.REFUSED_ACTIVE_OTHER_OWNER


def test_reserve_adopts_active_port_when_same_project(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [_discovered(project_name="DeepTrace")])
    result = reserve_ops.reserve(8003, "deeptrace")

    assert result.success
    assert result.outcome == reserve_ops.ReserveOutcome.ADOPTED_ACTIVE_SAME_PROJECT
    assert result.reservation is not None


def test_reserve_does_not_overwrite_silently(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    reserve_ops.reserve(8003, "deeptrace")
    reserve_ops.reserve(8003, "someone-else")  # refused

    saved = ReservationStore(_isolated_storage).load()
    assert len(saved) == 1
    assert saved[0].project == "deeptrace"


def test_tcp_and_udp_reservations_are_independent(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    tcp_result = reserve_ops.reserve(53, "project-a", protocol=Protocol.TCP)
    udp_result = reserve_ops.reserve(53, "project-b", protocol=Protocol.UDP)

    assert tcp_result.success
    assert udp_result.success  # different protocol -- not a conflict with the TCP one


# ---------------------------------------------------------------------------
# release / release_by_id
# ---------------------------------------------------------------------------


def test_release_removes_reservation(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    reserve_ops.reserve(8003, "deeptrace")

    result = reserve_ops.release(8003, "deeptrace")
    assert result.success
    assert result.outcome == reserve_ops.ReleaseOutcome.RELEASED
    assert ReservationStore(_isolated_storage).load() == []


def test_release_wrong_project_is_refused(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    reserve_ops.reserve(8003, "deeptrace")

    result = reserve_ops.release(8003, "someone-else")
    assert not result.success
    assert result.outcome == reserve_ops.ReleaseOutcome.REFUSED_OTHER_PROJECT
    assert len(ReservationStore(_isolated_storage).load()) == 1  # untouched


def test_release_is_idempotent_when_already_absent():
    result = reserve_ops.release(9999, "deeptrace")
    assert result.success
    assert result.outcome == reserve_ops.ReleaseOutcome.ALREADY_ABSENT


def test_release_by_id(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    created = reserve_ops.reserve(8003, "deeptrace")

    result = reserve_ops.release_by_id(created.reservation.reservation_id)
    assert result.success
    assert ReservationStore(_isolated_storage).load() == []


def test_release_by_id_with_project_check(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    created = reserve_ops.reserve(8003, "deeptrace")

    refused = reserve_ops.release_by_id(created.reservation.reservation_id, project="someone-else")
    assert not refused.success

    released = reserve_ops.release_by_id(created.reservation.reservation_id, project="deeptrace")
    assert released.success


def test_release_by_id_idempotent_when_unknown_id():
    result = reserve_ops.release_by_id("does-not-exist")
    assert result.success
    assert result.outcome == reserve_ops.ReleaseOutcome.ALREADY_ABSENT


# ---------------------------------------------------------------------------
# sync_project_reservations
# ---------------------------------------------------------------------------


def test_sync_project_reservations_creates_each_port(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    project_config = {
        "project": "deeptrace",
        "ports": [
            {"port": 8003, "service": "api", "purpose": "api"},
            {"port": 3002, "service": "frontend"},
            {"port": 5436, "service": "postgres", "protocol": "tcp"},
        ],
    }

    results = reserve_ops.sync_project_reservations(project_config)
    assert len(results) == 3
    assert all(r.success for r in results)

    saved = ReservationStore(_isolated_storage).load()
    assert {r.port for r in saved} == {8003, 3002, 5436}
    assert all(r.project == "deeptrace" for r in saved)


def test_sync_project_reservations_requires_project_name(monkeypatch):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    with pytest.raises(ValueError):
        reserve_ops.sync_project_reservations({"ports": [{"port": 8003}]})


def test_sync_project_reservations_skips_malformed_entries(monkeypatch, _isolated_storage):
    monkeypatch.setattr(reserve_ops, "discover_all_ports", lambda: [])
    project_config = {
        "project": "deeptrace",
        "ports": [{"port": 8003}, {"service": "no-port-field"}, "not-a-dict", {"port": "not-an-int"}],
    }
    results = reserve_ops.sync_project_reservations(project_config)
    assert len(results) == 1
    assert results[0].reservation.port == 8003


def test_sync_project_reservations_does_not_run_implicitly():
    # sync_project_reservations is only ever invoked by the explicit
    # `sync-reservations` CLI command; nothing in discovery/scan calls it.
    import portforge_agent.discovery as discovery_module

    assert "sync_project_reservations" not in dir(discovery_module)
