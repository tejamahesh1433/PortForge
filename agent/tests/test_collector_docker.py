"""Tests for the Docker collector.

Docker discovery is optional. These tests cover the "Docker isn't usable
right now" paths (missing CLI, daemon down, timeout, permission denied,
malformed output) as well as the metadata-parsing paths, all against
mocked `run_command` output -- no real `docker` invocation.

`_resolve_docker_executable()` (which real `_run_docker()` calls before
`run_command`) is mocked to a fixed fake path via the autouse fixture
below, for every test in this file -- otherwise these tests would depend
on whether `docker` actually happens to be on the machine running them
(see test_docker_executable_resolution.py for the tests that exercise
resolution itself; those intentionally don't use this fixture).
"""
import json

import pytest

from portforge_agent.collectors import docker as docker_module
from portforge_agent.collectors.base import CollectorError
from portforge_agent.collectors.docker import (
    DockerCollector,
    DockerUnavailableError,
    container_record_to_ports,
)
from portforge_agent.models import Protocol, Source


@pytest.fixture(autouse=True)
def _fixed_docker_executable(monkeypatch):
    monkeypatch.setattr(docker_module, "_resolve_docker_executable", lambda: "/usr/bin/docker")


def _record(
    container_id="abc123def4567890",
    name="/my-app",
    image="my-app:latest",
    status="running",
    labels=None,
    networks=None,
    ports=None,
    path=None,
    args=None,
    cmd=None,
):
    config = {"Image": image, "Labels": labels or {}}
    if cmd is not None:
        config["Cmd"] = cmd
    record = {
        "Id": container_id,
        "Name": name,
        "Config": config,
        "State": {"Status": status},
        "NetworkSettings": {
            "Networks": networks or {"bridge": {}},
            "Ports": ports or {},
        },
    }
    if path is not None:
        record["Path"] = path
    if args is not None:
        record["Args"] = args
    return record


# ---------------------------------------------------------------------------
# Docker unavailable / degraded environments
# ---------------------------------------------------------------------------


def test_collect_returns_empty_when_docker_cli_missing(monkeypatch):
    def _fail(args, timeout):
        raise CollectorError("Required command not found: docker")

    monkeypatch.setattr(docker_module, "run_command", _fail)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_returns_empty_when_daemon_unavailable(monkeypatch):
    def _fail(args, timeout):
        raise CollectorError("Command 'docker ps -q' failed (exit 1): Cannot connect to the Docker daemon")

    monkeypatch.setattr(docker_module, "run_command", _fail)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_raises_on_timeout(monkeypatch):
    def _fail(args, timeout):
        raise CollectorError("Command timed out: docker ps -q")

    monkeypatch.setattr(docker_module, "run_command", _fail)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_raises_on_permission_denied(monkeypatch):
    def _fail(args, timeout):
        raise CollectorError(
            "Command 'docker ps -q' failed (exit 1): permission denied while trying to connect to the Docker daemon socket"
        )

    monkeypatch.setattr(docker_module, "run_command", _fail)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_returns_empty_when_no_containers_running(monkeypatch):
    monkeypatch.setattr(docker_module, "run_command", lambda args, timeout: "")

    ports = DockerCollector().collect()
    assert ports == []


def test_is_docker_available_true(monkeypatch):
    monkeypatch.setattr(docker_module, "run_command", lambda args, timeout: "29.6.1\n")
    assert docker_module.is_docker_available() is True


def test_is_docker_available_false(monkeypatch):
    def _fail(args, timeout):
        raise CollectorError("not found")

    monkeypatch.setattr(docker_module, "run_command", _fail)
    assert docker_module.is_docker_available() is False


def test_collect_raises_on_malformed_json(monkeypatch):
    def _fake(args, timeout):
        if "ps" in args:
            return "abc123\n"
        return "not valid json {{{"

    monkeypatch.setattr(docker_module, "run_command", _fake)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_raises_when_inspect_output_is_not_a_list(monkeypatch):
    def _fake(args, timeout):
        if "ps" in args:
            return "abc123\n"
        return json.dumps({"not": "a list"})

    monkeypatch.setattr(docker_module, "run_command", _fake)

    with pytest.raises(DockerUnavailableError):
        DockerCollector().collect()


def test_collect_skips_malformed_individual_container_record(monkeypatch):
    good = _record(
        container_id="good1234567890ab",
        name="/good",
        ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
    )
    bad = {"Id": "bad", "NetworkSettings": "not-a-dict"}  # malformed shape

    def _fake(args, timeout):
        if "ps" in args:
            return "good\nbad\n"
        return json.dumps([bad, good])

    monkeypatch.setattr(docker_module, "run_command", _fake)

    ports = DockerCollector().collect()
    assert len(ports) == 1
    assert ports[0].container_name == "good"


# ---------------------------------------------------------------------------
# container_record_to_ports: metadata parsing
# ---------------------------------------------------------------------------


def test_simple_tcp_mapping():
    record = _record(ports={"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5444"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")

    assert len(ports) == 1
    p = ports[0]
    assert p.protocol == Protocol.TCP
    assert p.host_port == 5444
    assert p.container_port == 5432
    assert p.port == 5444  # host port, never the container port
    assert p.bind_address == "0.0.0.0"
    assert p.source == Source.DOCKER


def test_udp_mapping():
    record = _record(ports={"53/udp": [{"HostIp": "0.0.0.0", "HostPort": "5300"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")

    assert len(ports) == 1
    assert ports[0].protocol == Protocol.UDP
    assert ports[0].host_port == 5300
    assert ports[0].container_port == 53


def test_container_without_published_ports_yields_nothing():
    record = _record(ports={})
    assert container_record_to_ports(record, "host", "host", "linux") == []


def test_expose_without_publication_is_not_reported():
    # NetworkSettings.Ports value is null (None) for EXPOSE-only ports.
    record = _record(ports={"8001/tcp": None})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports == []


def test_multiple_published_ports_on_one_container():
    record = _record(
        ports={
            "9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9000"}],
            "9001/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9001"}],
        }
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert {p.host_port for p in ports} == {9000, 9001}
    assert {p.container_port for p in ports} == {9000, 9001}


def test_multiple_host_bindings_for_one_container_port():
    # Docker Desktop dual-stack publish: one container port -> two host bindings.
    record = _record(
        ports={
            "5173/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "5173"},
                {"HostIp": "::", "HostPort": "5173"},
            ]
        }
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert len(ports) == 2
    addresses = {p.bind_address for p in ports}
    assert addresses == {"0.0.0.0", "::"}
    assert all(p.host_port == 5173 and p.container_port == 5173 for p in ports)


def test_localhost_only_mapping():
    record = _record(ports={"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert len(ports) == 1
    assert ports[0].bind_address == "127.0.0.1"


def test_ipv6_mapping():
    record = _record(ports={"8000/tcp": [{"HostIp": "::1", "HostPort": "8000"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert len(ports) == 1
    assert ports[0].bind_address == "::1"


def test_empty_host_ip_normalizes_to_wildcard():
    record = _record(ports={"80/tcp": [{"HostIp": "", "HostPort": "8080"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].bind_address == "0.0.0.0"


def test_compose_project_and_service_labels():
    record = _record(
        labels={
            "com.docker.compose.project": "ocrforge",
            "com.docker.compose.service": "api",
        },
        ports={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8001"}]},
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].docker_compose_project == "ocrforge"
    assert ports[0].service_name == "api"


def test_non_compose_container_has_no_compose_metadata():
    record = _record(
        labels={"some.other.label": "value"},
        ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].docker_compose_project is None
    assert ports[0].service_name is None


def test_container_name_strips_leading_slash():
    record = _record(name="/ocrforge-api", ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].container_name == "ocrforge-api"


def test_container_image_status_and_networks_captured():
    record = _record(
        image="ocrforge-api:latest",
        status="running",
        networks={"ocrforge_net": {}},
        ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    p = ports[0]
    assert p.container_image == "ocrforge-api:latest"
    assert p.container_status == "running"
    assert p.docker_networks == ["ocrforge_net"]


def test_malformed_port_key_is_skipped():
    record = _record(ports={"not-a-port": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
    assert container_record_to_ports(record, "host", "host", "linux") == []


def test_unknown_protocol_in_port_key_is_skipped():
    record = _record(ports={"80/sctp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]})
    assert container_record_to_ports(record, "host", "host", "linux") == []


def test_malformed_host_port_is_skipped():
    record = _record(ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "not-a-number"}]})
    assert container_record_to_ports(record, "host", "host", "linux") == []


def test_full_collect_end_to_end(monkeypatch):
    record = _record(
        container_id="1671fd4e6fa2aabbccddeeff00112233",
        name="/ocrforge-api",
        image="ocrforge-api",
        labels={
            "com.docker.compose.project": "ocrforge",
            "com.docker.compose.service": "api",
        },
        ports={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8001"}]},
    )

    def _fake(args, timeout):
        if "ps" in args:
            return "1671fd4e6fa2\n"
        return json.dumps([record])

    monkeypatch.setattr(docker_module, "run_command", _fake)

    ports = DockerCollector().collect()
    assert len(ports) == 1
    p = ports[0]
    assert p.host_port == 8001
    assert p.container_port == 8000
    assert p.container_name == "ocrforge-api"
    assert p.docker_compose_project == "ocrforge"
    assert p.service_name == "api"
    assert p.container_id == "1671fd4e6fa2"


def test_container_command_from_path_and_args():
    record = _record(
        path="uvicorn",
        args=["main:app", "--host", "0.0.0.0", "--port", "8000"],
        ports={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8001"}]},
    )
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].container_command == ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


def test_container_command_falls_back_to_config_cmd_when_no_path():
    record = _record(cmd=["nginx", "-g", "daemon off;"], ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8090"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].container_command == ["nginx", "-g", "daemon off;"]


def test_container_command_none_when_neither_available():
    record = _record(ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8090"}]})
    ports = container_record_to_ports(record, "host", "host", "linux")
    assert ports[0].container_command is None
