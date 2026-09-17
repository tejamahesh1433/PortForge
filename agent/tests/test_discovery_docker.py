"""Tests for Docker-aware discovery: discover_docker_ports() and the
native+Docker merger.

Per project convention, the merger itself is tested directly with
controlled DiscoveredPort lists rather than mocking away the OS collector --
this exercises the actual merge/identity logic, not a stand-in for it.
"""
from portforge_agent import discovery
from portforge_agent.collectors.base import CollectorError
from portforge_agent.models import DiscoveredPort, Protocol, Source


def _native(port=8001, pid=4242, process_name="com.docker.backend.exe", bind_address="0.0.0.0", protocol=Protocol.TCP):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="windows",
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        source=Source.PROCESS,
        pid=pid,
        process_name=process_name,
        process_path=r"C:\Program Files\Docker\resources\com.docker.backend.exe",
        raw_state="LISTEN",
    )


def _docker(
    host_port=8001,
    container_port=8000,
    bind_address="0.0.0.0",
    protocol=Protocol.TCP,
    project="ocrforge",
    service="api",
    name="ocrforge-api",
):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="windows",
        port=host_port,
        protocol=protocol,
        bind_address=bind_address,
        source=Source.DOCKER,
        container_id="1671fd4e6fa2",
        container_name=name,
        docker_compose_project=project,
        service_name=service,
        host_port=host_port,
        container_port=container_port,
        container_status="running",
        raw_state="running",
    )


# ---------------------------------------------------------------------------
# discover_docker_ports(): optionality
# ---------------------------------------------------------------------------


class _FakeCollector:
    def __init__(self, ports=None, error=None):
        self._ports = ports or []
        self._error = error

    def collect(self):
        if self._error:
            raise self._error
        return self._ports


def test_discover_docker_ports_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setattr(
        discovery, "DockerCollector", lambda: _FakeCollector(error=CollectorError("no docker"))
    )
    assert discovery.discover_docker_ports() == []


def test_discover_docker_ports_returns_ports_when_available(monkeypatch):
    monkeypatch.setattr(discovery, "DockerCollector", lambda: _FakeCollector(ports=[_docker()]))
    ports = discovery.discover_docker_ports()
    assert len(ports) == 1
    assert ports[0].source == Source.DOCKER


def test_discover_all_ports_never_raises_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(discovery, "get_collector_for", lambda os: _FakeCollector(ports=[_native()]))
    monkeypatch.setattr(
        discovery, "DockerCollector", lambda: _FakeCollector(error=CollectorError("no docker"))
    )

    ports = discovery.discover_all_ports(discovery.pf.OperatingSystem.WINDOWS)
    assert len(ports) == 1
    assert ports[0].source == Source.PROCESS


# ---------------------------------------------------------------------------
# merge_native_and_docker(): the actual identity/merge logic
# ---------------------------------------------------------------------------


def test_merge_combines_matching_native_and_docker_into_one_record():
    native = [_native()]
    docker = [_docker()]

    merged = discovery.merge_native_and_docker(native, docker)

    assert len(merged) == 1  # not shown as two unrelated occupied ports
    record = merged[0]
    assert record.source == Source.DOCKER
    assert record.container_name == "ocrforge-api"
    assert record.docker_compose_project == "ocrforge"
    assert record.service_name == "api"
    assert record.host_port == 8001
    assert record.container_port == 8000


def test_merge_preserves_native_process_metadata_as_secondary_info():
    native = [_native(pid=4242, process_name="com.docker.backend.exe")]
    docker = [_docker()]

    merged = discovery.merge_native_and_docker(native, docker)

    record = merged[0]
    assert record.pid == 4242
    assert record.process_name == "com.docker.backend.exe"
    assert record.process_path == r"C:\Program Files\Docker\resources\com.docker.backend.exe"
    # Docker ownership metadata still wins for identity fields.
    assert record.container_name == "ocrforge-api"


def test_merge_prefers_native_raw_state_over_container_status():
    native = [_native()]
    docker = [_docker()]

    merged = discovery.merge_native_and_docker(native, docker)
    assert merged[0].raw_state == "LISTEN"  # not "running"


def test_merge_keeps_docker_only_port_with_no_pid():
    # Docker publishes it, but native OS-level discovery didn't surface it
    # (e.g. a permission gap).
    merged = discovery.merge_native_and_docker([], [_docker()])

    assert len(merged) == 1
    assert merged[0].source == Source.DOCKER
    assert merged[0].pid is None
    assert merged[0].process_name is None


def test_merge_keeps_native_only_port_unchanged():
    merged = discovery.merge_native_and_docker([_native(port=3389, process_name="svchost.exe")], [])

    assert len(merged) == 1
    assert merged[0].source == Source.PROCESS
    assert merged[0].process_name == "svchost.exe"


def test_merge_does_not_collapse_same_port_different_bind_addresses():
    native = [
        _native(bind_address="0.0.0.0"),
        _native(bind_address="::"),
    ]
    docker = [
        _docker(bind_address="0.0.0.0"),
        _docker(bind_address="::"),
    ]

    merged = discovery.merge_native_and_docker(native, docker)

    assert len(merged) == 2
    addresses = {p.bind_address for p in merged}
    assert addresses == {"0.0.0.0", "::"}
    assert all(p.source == Source.DOCKER for p in merged)  # each matched its own binding


def test_merge_does_not_confuse_loopback_lan_and_ipv6_as_the_same_binding():
    docker = [
        _docker(host_port=8000, bind_address="127.0.0.1"),
        _docker(host_port=8000, bind_address="192.168.1.50"),
        _docker(host_port=8000, bind_address="::1"),
    ]

    merged = discovery.merge_native_and_docker([], docker)

    assert len(merged) == 3
    assert {p.bind_address for p in merged} == {"127.0.0.1", "192.168.1.50", "::1"}


def test_merge_matches_by_binding_not_by_pid():
    # Docker's own record has no pid; native record does. They must still
    # merge because identity is (protocol, bind_address, host_port).
    native = [_native(pid=9999)]
    docker_port = _docker()
    assert docker_port.pid is None

    merged = discovery.merge_native_and_docker(native, [docker_port])

    assert len(merged) == 1
    assert merged[0].pid == 9999


def test_merge_handles_udp_mapping():
    native = [_native(port=5300, protocol=Protocol.UDP, process_name="com.docker.backend.exe")]
    docker = [_docker(host_port=5300, container_port=53, protocol=Protocol.UDP)]

    merged = discovery.merge_native_and_docker(native, docker)
    assert len(merged) == 1
    assert merged[0].protocol == Protocol.UDP
    assert merged[0].container_port == 53


def test_discover_docker_ports_deduplicates_repeated_observations(monkeypatch):
    # Identity for discover_docker_ports()'s own dedupe includes container_id,
    # so two identical observations of the same container+binding collapse,
    # while two different containers publishing the same host binding (a
    # real conflict, left to Phase 4) are both kept visible.
    same_container = _docker()
    monkeypatch.setattr(
        discovery, "DockerCollector", lambda: _FakeCollector(ports=[same_container, same_container])
    )

    ports = discovery.discover_docker_ports()
    assert len(ports) == 1
