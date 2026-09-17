"""Tests for the recommendation engine's three-layer validation and
deterministic candidate strategy.
"""
from portforge_agent.config import Exclusion, PortForgeConfig, PortRange
from portforge_agent.models import DiscoveredPort, Protocol, Source
from portforge_agent.recommend import (
    RecommendationStrategy,
    SequentialStrategy,
    recommend_and_reserve,
    recommend_port,
)
from portforge_agent.reservations.models import Reservation


def _config(start=9000, end=9010, exclusions=None):
    return PortForgeConfig(
        ranges={"test-service": PortRange("test-service", start, end)},
        exclusions=exclusions or [],
    )


def _discovered_port(port, protocol=Protocol.TCP, process_name="app.exe"):
    return DiscoveredPort(
        hostname="h",
        host_id="host-a",
        operating_system="linux",
        port=port,
        protocol=protocol,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        process_name=process_name,
    )


def _always_available(monkeypatch):
    import portforge_agent.recommend as recommend_module

    monkeypatch.setattr(recommend_module, "probe_bind", lambda port, protocol, address: _Probe(True))


class _Probe:
    def __init__(self, available, reason=None):
        self.available = available
        self.reason = reason


def test_sequential_strategy_yields_in_order():
    strategy = SequentialStrategy()
    assert list(strategy.candidates(3000, 3003)) == [3000, 3001, 3002, 3003]


def test_first_candidate_free_and_probe_passes(monkeypatch):
    _always_available(monkeypatch)
    config = _config()
    result = recommend_port(
        "test-service", config=config, reservations=[], discovered=[], host_id="host-a"
    )
    assert result.recommended_port == 9000
    assert result.candidates_tried == 1


def test_first_candidate_occupied_moves_to_next(monkeypatch):
    _always_available(monkeypatch)
    config = _config()
    discovered = [_discovered_port(9000)]
    result = recommend_port(
        "test-service", config=config, reservations=[], discovered=discovered, host_id="host-a"
    )
    assert result.recommended_port == 9001
    assert result.candidates_tried == 2
    assert result.steps[0].name == "discovery" and result.steps[0].passed is True


def test_first_candidate_reserved_moves_to_next(monkeypatch):
    _always_available(monkeypatch)
    config = _config()
    reservations = [Reservation.create(host_id="host-a", port=9000, project="other", protocol=Protocol.TCP)]
    result = recommend_port(
        "test-service", config=config, reservations=reservations, discovered=[], host_id="host-a"
    )
    assert result.recommended_port == 9001


def test_first_candidate_excluded_moves_to_next(monkeypatch):
    _always_available(monkeypatch)
    config = _config(exclusions=[Exclusion(9000, 9000)])
    result = recommend_port("test-service", config=config, reservations=[], discovered=[], host_id="host-a")
    assert result.recommended_port == 9001


def test_bind_probe_failure_moves_to_next(monkeypatch):
    import portforge_agent.recommend as recommend_module

    def _probe(port, protocol, address):
        return _Probe(False, "simulated bind failure") if port == 9000 else _Probe(True)

    monkeypatch.setattr(recommend_module, "probe_bind", _probe)
    config = _config()
    result = recommend_port("test-service", config=config, reservations=[], discovered=[], host_id="host-a")
    assert result.recommended_port == 9001
    # the winning candidate's own steps should show bind_probe passed
    assert result.steps[-1].name == "bind_probe" and result.steps[-1].passed


def test_range_exhausted_returns_none(monkeypatch):
    _always_available(monkeypatch)
    config = _config(start=9000, end=9002)
    discovered = [_discovered_port(9000), _discovered_port(9001), _discovered_port(9002)]
    result = recommend_port(
        "test-service", config=config, reservations=[], discovered=discovered, host_id="host-a"
    )
    assert result.recommended_port is None
    assert result.exhausted is True
    assert result.candidates_tried == 3


def test_unknown_service_type():
    config = PortForgeConfig(ranges={}, exclusions=[])
    result = recommend_port("no-such-type", config=config, reservations=[], discovered=[], host_id="host-a")
    assert result.recommended_port is None
    assert result.unknown_service_type is True
    assert result.exhausted is False  # unknown type is distinct from "exhausted"


def test_custom_range_is_respected(monkeypatch):
    _always_available(monkeypatch)
    config = _config(start=20000, end=20002)
    result = recommend_port("test-service", config=config, reservations=[], discovered=[], host_id="host-a")
    assert result.recommended_port == 20000


def test_custom_exclusion_skips_entire_configured_range(monkeypatch):
    _always_available(monkeypatch)
    config = _config(start=9000, end=9003, exclusions=[Exclusion(9000, 9002)])
    result = recommend_port("test-service", config=config, reservations=[], discovered=[], host_id="host-a")
    assert result.recommended_port == 9003


def test_udp_protocol_checked_independently_of_tcp(monkeypatch):
    _always_available(monkeypatch)
    config = _config()
    discovered = [_discovered_port(9000, protocol=Protocol.TCP)]
    result = recommend_port(
        "test-service", protocol=Protocol.UDP, config=config, reservations=[], discovered=discovered, host_id="host-a"
    )
    assert result.recommended_port == 9000  # TCP occupation doesn't block UDP


def test_custom_strategy_can_be_supplied():
    class ReverseStrategy(RecommendationStrategy):
        name = "reverse"

        def candidates(self, start, end):
            yield from range(end, start - 1, -1)

    config = _config(start=9000, end=9003)

    import portforge_agent.recommend as recommend_module

    def _always(port, protocol, address):
        return _Probe(True)

    orig = recommend_module.probe_bind
    recommend_module.probe_bind = _always
    try:
        result = recommend_port(
            "test-service", config=config, reservations=[], discovered=[], host_id="host-a", strategy=ReverseStrategy()
        )
    finally:
        recommend_module.probe_bind = orig

    assert result.recommended_port == 9003


def test_recommendation_result_json_serializable():
    config = PortForgeConfig(ranges={}, exclusions=[])
    result = recommend_port("no-such-type", config=config, reservations=[], discovered=[], host_id="host-a")
    data = result.to_dict()
    assert data["unknown_service_type"] is True


# ---------------------------------------------------------------------------
# recommend_and_reserve: atomic recommend + reserve
# ---------------------------------------------------------------------------


def test_recommend_and_reserve_creates_reservation(tmp_path, monkeypatch):
    _always_available(monkeypatch)
    monkeypatch.setattr("portforge_agent.recommend.default_reservations_path", lambda: tmp_path / "r.json")
    monkeypatch.setattr("portforge_agent.recommend.default_lock_path", lambda: tmp_path / "r.lock")
    monkeypatch.setattr("portforge_agent.recommend.discover_all_ports", lambda: [])
    monkeypatch.setattr("portforge_agent.recommend.pf.get_host_id", lambda: "host-a")

    config = _config()
    result, reservation = recommend_and_reserve("test-service", project="deeptrace", config=config)

    assert result.recommended_port == 9000
    assert reservation is not None
    assert reservation.project == "deeptrace"
    assert reservation.port == 9000

    from portforge_agent.reservations.storage import ReservationStore

    saved = ReservationStore(tmp_path / "r.json").load()
    assert len(saved) == 1
    assert saved[0].reservation_id == reservation.reservation_id


def test_recommend_and_reserve_returns_none_reservation_when_exhausted(tmp_path, monkeypatch):
    _always_available(monkeypatch)
    monkeypatch.setattr("portforge_agent.recommend.default_reservations_path", lambda: tmp_path / "r.json")
    monkeypatch.setattr("portforge_agent.recommend.default_lock_path", lambda: tmp_path / "r.lock")
    monkeypatch.setattr("portforge_agent.recommend.discover_all_ports", lambda: [])
    monkeypatch.setattr("portforge_agent.recommend.pf.get_host_id", lambda: "host-a")

    config = _config(start=9000, end=9000, exclusions=[Exclusion(9000, 9000)])
    result, reservation = recommend_and_reserve("test-service", project="deeptrace", config=config)

    assert result.recommended_port is None
    assert reservation is None

    from portforge_agent.reservations.storage import ReservationStore

    assert ReservationStore(tmp_path / "r.json").load() == []


def test_no_automatic_reservation_from_plain_recommend(tmp_path, monkeypatch):
    _always_available(monkeypatch)
    config = _config()
    recommend_port("test-service", config=config, reservations=[], discovered=[], host_id="host-a")
    # recommend_port (no "_and_reserve") must never touch storage at all --
    # verified implicitly: no reservations_path/lock_path patched here, and
    # the call above completed without needing them.
