from datetime import datetime

from portforge_agent.models import DiscoveredPort, PortState, Protocol, Source


def _make_port(**overrides) -> DiscoveredPort:
    defaults = dict(
        hostname="test-host",
        host_id="test-host",
        operating_system="linux",
        port=8000,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        pid=1234,
        process_name="python",
    )
    defaults.update(overrides)
    return DiscoveredPort(**defaults)


def test_defaults():
    port = _make_port()
    assert port.state == PortState.ACTIVE
    assert isinstance(port.first_seen, datetime)
    assert isinstance(port.last_seen, datetime)
    assert port.container_id is None
    assert port.docker_compose_project is None


def test_to_dict_serializes_enums_and_timestamps():
    port = _make_port()
    data = port.to_dict()

    assert data["protocol"] == "tcp"
    assert data["source"] == "process"
    assert data["state"] == "ACTIVE"
    assert isinstance(data["first_seen"], str)
    assert isinstance(data["last_seen"], str)
    assert data["port"] == 8000


def test_dedupe_key_distinguishes_protocol_address_port_pid():
    a = _make_port(port=3000, protocol=Protocol.TCP, bind_address="0.0.0.0", pid=1)
    b = _make_port(port=3000, protocol=Protocol.UDP, bind_address="0.0.0.0", pid=1)
    c = _make_port(port=3000, protocol=Protocol.TCP, bind_address="127.0.0.1", pid=1)

    assert a.dedupe_key() != b.dedupe_key()
    assert a.dedupe_key() != c.dedupe_key()


def test_dedupe_key_equal_for_identical_observation():
    a = _make_port(port=3000, pid=42)
    b = _make_port(port=3000, pid=42)
    assert a.dedupe_key() == b.dedupe_key()
