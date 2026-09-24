"""Phase 18.6: port_recheck module unit tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock

import pytest

from portforge_agent.deployment.port_recheck import (
    PortConflictError,
    PortStateUnknownError,
    PortRecheckVerdict,
    assert_ports_clear_for_apply,
    recheck_host_ports,
    required_host_ports_from_ports_json,
)
from portforge_agent.models import Protocol, Source


# ---------------------------------------------------------------------------
# Minimal DiscoveredPort-like stub (duck-typed)
# ---------------------------------------------------------------------------

@dataclass
class _FakePort:
    """Minimal DiscoveredPort-like object for tests."""
    port: int
    protocol: Protocol = Protocol.TCP
    source: Source = Source.PROCESS
    host_port: Optional[int] = None
    docker_compose_project: Optional[str] = None
    process_name: Optional[str] = None

    def __post_init__(self):
        if self.host_port is None:
            self.host_port = self.port


def _docker_port(
    host_port: int,
    *,
    protocol: Protocol = Protocol.TCP,
    compose_project: Optional[str] = None,
) -> _FakePort:
    return _FakePort(
        port=host_port,
        host_port=host_port,
        protocol=protocol,
        source=Source.DOCKER,
        docker_compose_project=compose_project,
    )


def _native_port(
    port: int,
    *,
    protocol: Protocol = Protocol.TCP,
    process_name: str = "nginx",
) -> _FakePort:
    return _FakePort(
        port=port,
        host_port=port,
        protocol=protocol,
        source=Source.PROCESS,
        process_name=process_name,
    )


# ---------------------------------------------------------------------------
# required_host_ports_from_ports_json
# ---------------------------------------------------------------------------

def test_required_ports_extracts_host_ports():
    ports_json = {
        "services": [
            {"name": "api", "internal_port": 8000, "host_port": 18000, "protocol": "tcp"},
            {"name": "db", "internal_port": 5432, "host_port": 15432, "protocol": "tcp"},
        ]
    }
    result = required_host_ports_from_ports_json(ports_json)
    assert result == [("api", "tcp", 18000), ("db", "tcp", 15432)]


def test_required_ports_skips_missing_host_port():
    ports_json = {
        "services": [
            {"name": "internal", "internal_port": 8080},  # no host_port — skipped
            {"name": "exposed", "host_port": 9090},       # has host_port — included
        ]
    }
    result = required_host_ports_from_ports_json(ports_json)
    assert result == [("exposed", "tcp", 9090)]


def test_required_ports_empty_on_none():
    assert required_host_ports_from_ports_json(None) == []


def test_required_ports_empty_on_empty_dict():
    assert required_host_ports_from_ports_json({}) == []


# ---------------------------------------------------------------------------
# recheck_host_ports — FREE when no listeners
# ---------------------------------------------------------------------------

def test_free_when_no_listeners():
    required = [("api", "tcp", 18000)]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=[],
        native_ports=[],
    )
    assert len(results) == 1
    assert results[0].verdict == PortRecheckVerdict.FREE
    assert results[0].host_port == 18000


# ---------------------------------------------------------------------------
# recheck_host_ports — EXPECTED_EXISTING_DEPLOYMENT
# ---------------------------------------------------------------------------

def test_expected_when_same_compose_project():
    project = "pf-myapp-staging-abc123"
    required = [("api", "tcp", 18000)]
    docker_ports = [_docker_port(18000, compose_project=project)]
    results = recheck_host_ports(
        required,
        expected_compose_project=project,
        docker_ports=docker_ports,
        native_ports=[],
    )
    assert results[0].verdict == PortRecheckVerdict.EXPECTED_EXISTING_DEPLOYMENT


# ---------------------------------------------------------------------------
# recheck_host_ports — UNEXPECTED_OCCUPANT (wrong Compose project)
# ---------------------------------------------------------------------------

def test_unexpected_for_wrong_compose_project():
    required = [("api", "tcp", 18000)]
    docker_ports = [_docker_port(18000, compose_project="some-other-project")]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=docker_ports,
        native_ports=[],
    )
    assert results[0].verdict == PortRecheckVerdict.UNEXPECTED_OCCUPANT


def test_unexpected_for_docker_port_without_compose_project():
    required = [("api", "tcp", 18000)]
    docker_ports = [_docker_port(18000, compose_project=None)]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=docker_ports,
        native_ports=[],
    )
    # No compose project → not our deployment → unexpected
    assert results[0].verdict == PortRecheckVerdict.UNEXPECTED_OCCUPANT


# ---------------------------------------------------------------------------
# recheck_host_ports — UNEXPECTED_OCCUPANT (native listener)
# ---------------------------------------------------------------------------

def test_unexpected_for_native_listener():
    required = [("api", "tcp", 18000)]
    native_ports = [_native_port(18000, process_name="nginx")]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=[],
        native_ports=native_ports,
    )
    assert results[0].verdict == PortRecheckVerdict.UNEXPECTED_OCCUPANT
    assert "nginx" in results[0].reason


def test_docker_source_ports_ignored_in_native_check():
    """A docker-sourced entry in native_ports is skipped; treated as FREE."""
    required = [("api", "tcp", 18000)]
    # A DiscoveredPort with source=DOCKER passed in native_ports should be skipped.
    docker_as_native = _FakePort(
        port=18000, host_port=18000, protocol=Protocol.TCP,
        source=Source.DOCKER, docker_compose_project="other-project",
    )
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=[],       # no docker check
        native_ports=[docker_as_native],
    )
    # Docker-sourced entries in native_ports are filtered out; port → FREE.
    assert results[0].verdict == PortRecheckVerdict.FREE


# ---------------------------------------------------------------------------
# recheck_host_ports — UNKNOWN when discovery_ok=False
# ---------------------------------------------------------------------------

def test_unknown_when_discovery_unavailable():
    required = [("api", "tcp", 18000), ("db", "tcp", 15432)]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=None,
        native_ports=None,
        discovery_ok=False,
    )
    assert all(r.verdict == PortRecheckVerdict.UNKNOWN for r in results)


# ---------------------------------------------------------------------------
# assert_ports_clear_for_apply
# ---------------------------------------------------------------------------

def test_assert_clear_raises_conflict_on_unexpected():
    required = [("api", "tcp", 18000)]
    docker_ports = [_docker_port(18000, compose_project="someone-else")]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=docker_ports,
        native_ports=[],
    )
    with pytest.raises(PortConflictError) as exc_info:
        assert_ports_clear_for_apply(results)
    assert exc_info.value.conflicts[0]["host_port"] == 18000


def test_assert_clear_raises_unknown_on_unknown():
    required = [("api", "tcp", 18000)]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        discovery_ok=False,
    )
    with pytest.raises(PortStateUnknownError):
        assert_ports_clear_for_apply(results)


def test_assert_clear_passes_for_free():
    required = [("api", "tcp", 18000)]
    results = recheck_host_ports(
        required,
        expected_compose_project="pf-myapp-staging-abc123",
        docker_ports=[],
        native_ports=[],
    )
    assert_ports_clear_for_apply(results)  # must not raise


def test_assert_clear_passes_for_expected():
    project = "pf-myapp-staging-abc123"
    required = [("api", "tcp", 18000)]
    results = recheck_host_ports(
        required,
        expected_compose_project=project,
        docker_ports=[_docker_port(18000, compose_project=project)],
        native_ports=[],
    )
    assert_ports_clear_for_apply(results)  # must not raise


def test_conflict_error_has_failure_code():
    assert PortConflictError.failure_code == "DEPLOYMENT_PORT_CONFLICT"


def test_unknown_error_has_failure_code():
    assert PortStateUnknownError.failure_code == "DEPLOYMENT_PORT_STATE_UNKNOWN"
