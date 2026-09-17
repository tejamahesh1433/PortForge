from portforge_agent import discovery
from portforge_agent import platform as pf
from portforge_agent.collectors.base import CollectorError
from portforge_agent.models import DiscoveredPort, Protocol, Source


def _port(port, pid=1):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="linux",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        pid=pid,
    )


class _FakeCollector:
    def __init__(self, ports=None, error=None):
        self._ports = ports or []
        self._error = error

    def collect(self):
        if self._error:
            raise self._error
        return self._ports


def test_discover_ports_selects_collector_for_current_os(monkeypatch):
    fake = _FakeCollector(ports=[_port(8000)])
    monkeypatch.setattr(discovery, "get_collector_for", lambda os: fake)

    ports = discovery.discover_ports(pf.OperatingSystem.LINUX)
    assert [p.port for p in ports] == [8000]


def test_discover_ports_returns_empty_for_unsupported_os():
    ports = discovery.discover_ports(pf.OperatingSystem.UNKNOWN)
    assert ports == []


def test_discover_ports_swallows_collector_error(monkeypatch):
    fake = _FakeCollector(error=CollectorError("boom"))
    monkeypatch.setattr(discovery, "get_collector_for", lambda os: fake)

    ports = discovery.discover_ports(pf.OperatingSystem.LINUX)
    assert ports == []


def test_discover_ports_dedupes_and_sorts(monkeypatch):
    fake = _FakeCollector(
        ports=[_port(9000, pid=1), _port(3000, pid=1), _port(9000, pid=1)]
    )
    monkeypatch.setattr(discovery, "get_collector_for", lambda os: fake)

    ports = discovery.discover_ports(pf.OperatingSystem.LINUX)
    assert [p.port for p in ports] == [3000, 9000]
