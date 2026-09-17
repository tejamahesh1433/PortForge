"""Tests for state evaluation: combining discovery with reservations into
FREE/ACTIVE/RESERVED/CONFLICT/SYSTEM, including the conservative
unknown-ownership rule.
"""
from portforge_agent.evaluate import evaluate_all, evaluate_port, find_discovered, find_reservation
from portforge_agent.models import DiscoveredPort, PortState, Protocol, Source
from portforge_agent.reservations.models import Reservation


def _port(port=8003, protocol=Protocol.TCP, source=Source.PROCESS, project_name=None, process_name="app.exe"):
    return DiscoveredPort(
        hostname="h",
        host_id="host-a",
        operating_system="windows",
        port=port,
        protocol=protocol,
        bind_address="0.0.0.0",
        source=source,
        process_name=process_name,
        project_name=project_name,
    )


def _reservation(port=8003, protocol=Protocol.TCP, project="deeptrace"):
    return Reservation.create(host_id="host-a", port=port, project=project, protocol=protocol)


def test_free_no_listener_no_reservation():
    result = evaluate_port("host-a", 8003, Protocol.TCP, None, None)
    assert result.state == PortState.FREE
    assert result.discovered is None
    assert result.reservation is None


def test_active_listener_no_reservation():
    d = _port()
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, None)
    assert result.state == PortState.ACTIVE
    assert result.discovered is d


def test_reserved_no_listener():
    r = _reservation()
    result = evaluate_port("host-a", 8003, Protocol.TCP, None, r)
    assert result.state == PortState.RESERVED
    assert result.reservation is r


def test_active_same_project_as_reservation_stays_active_with_reservation_retained():
    d = _port(project_name="DeepTrace")  # case-insensitive match
    r = _reservation(project="deeptrace")
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, r)
    assert result.state == PortState.ACTIVE
    assert result.discovered is d
    assert result.reservation is r  # not lost


def test_active_different_known_project_is_conflict():
    d = _port(project_name="other-project")
    r = _reservation(project="deeptrace")
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, r)
    assert result.state == PortState.CONFLICT
    assert "other-project" in result.conflict_reason
    assert "deeptrace" in result.conflict_reason


def test_active_unknown_owner_is_conservatively_a_conflict():
    # Owner project could not be determined -- must NOT be assumed safe.
    d = _port(project_name=None, process_name="mystery.exe")
    r = _reservation(project="deeptrace")
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, r)
    assert result.state == PortState.CONFLICT
    assert "mystery.exe" in result.conflict_reason


def test_system_source_is_system_state_even_with_reservation():
    d = _port(source=Source.SYSTEM, process_name="System")
    r = _reservation()
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, r)
    assert result.state == PortState.SYSTEM


def test_system_source_without_reservation_is_system_state():
    d = _port(source=Source.SYSTEM, process_name="System")
    result = evaluate_port("host-a", 445, Protocol.TCP, d, None)
    assert result.state == PortState.SYSTEM


def test_project_match_is_case_insensitive_and_trims_whitespace():
    d = _port(project_name=" DeepTrace ")
    r = _reservation(project="deeptrace")
    result = evaluate_port("host-a", 8003, Protocol.TCP, d, r)
    assert result.state == PortState.ACTIVE


# ---------------------------------------------------------------------------
# find_discovered / find_reservation
# ---------------------------------------------------------------------------


def test_find_discovered_matches_by_protocol_and_host_port():
    ports = [_port(port=3000, protocol=Protocol.TCP), _port(port=3000, protocol=Protocol.UDP)]
    found = find_discovered(ports, 3000, Protocol.TCP)
    assert found is ports[0]


def test_find_discovered_none_when_absent():
    assert find_discovered([_port(port=3000)], 4000, Protocol.TCP) is None


def test_find_reservation_matches_host_port_protocol():
    reservations = [_reservation(port=8003, protocol=Protocol.TCP)]
    found = find_reservation(reservations, "host-a", 8003, Protocol.TCP)
    assert found is reservations[0]


def test_find_reservation_respects_host_id():
    r = Reservation.create(host_id="other-host", port=8003, project="x", protocol=Protocol.TCP)
    assert find_reservation([r], "host-a", 8003, Protocol.TCP) is None


def test_find_reservation_any_address_matches_specific_address_lookup():
    r = Reservation.create(host_id="host-a", port=8003, project="x", protocol=Protocol.TCP, bind_address=None)
    assert find_reservation([r], "host-a", 8003, Protocol.TCP, bind_address="127.0.0.1") is r


def test_find_reservation_specific_address_does_not_match_different_address():
    r = Reservation.create(
        host_id="host-a", port=8003, project="x", protocol=Protocol.TCP, bind_address="127.0.0.1"
    )
    assert find_reservation([r], "host-a", 8003, Protocol.TCP, bind_address="192.168.1.5") is None


def test_tcp_and_udp_reservations_are_distinct():
    tcp = _reservation(protocol=Protocol.TCP, project="tcp-owner")
    udp = _reservation(protocol=Protocol.UDP, project="udp-owner")
    assert find_reservation([tcp, udp], "host-a", 8003, Protocol.TCP).project == "tcp-owner"
    assert find_reservation([tcp, udp], "host-a", 8003, Protocol.UDP).project == "udp-owner"


# ---------------------------------------------------------------------------
# evaluate_all
# ---------------------------------------------------------------------------


def test_evaluate_all_includes_reserved_only_ports_not_in_discovery():
    reservations = [_reservation(port=9999)]
    results = evaluate_all("host-a", [], reservations)
    assert len(results) == 1
    assert results[0].state == PortState.RESERVED
    assert results[0].port == 9999


def test_evaluate_all_includes_discovered_only_ports_not_reserved():
    discovered = [_port(port=3000)]
    results = evaluate_all("host-a", discovered, [])
    assert len(results) == 1
    assert results[0].state == PortState.ACTIVE


def test_evaluate_all_deduplicates_same_protocol_port_once():
    discovered = [_port(port=3000, protocol=Protocol.TCP), _port(port=3000, protocol=Protocol.TCP)]
    results = evaluate_all("host-a", discovered, [])
    assert len(results) == 1


def test_evaluate_all_ignores_reservations_for_other_hosts():
    other_host_reservation = Reservation.create(host_id="other-host", port=1234, project="x", protocol=Protocol.TCP)
    results = evaluate_all("host-a", [], [other_host_reservation])
    assert results == []


def test_evaluate_all_sorted_by_port_then_protocol():
    discovered = [_port(port=9000, protocol=Protocol.UDP), _port(port=3000, protocol=Protocol.TCP)]
    results = evaluate_all("host-a", discovered, [])
    assert [(r.port, r.protocol) for r in results] == [(3000, Protocol.TCP), (9000, Protocol.UDP)]
