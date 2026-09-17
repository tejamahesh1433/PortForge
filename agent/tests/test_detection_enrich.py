"""Tests for the detection orchestrator (enrich_port/enrich_ports).

Focuses on the parts that are specific to combining project+purpose
detection into one DiscoveredPort: raw facts must be preserved untouched,
Docker Compose metadata must short-circuit native filesystem detection, a
non-Compose container's name must never become a project name, and the
combined confidence/evidence must behave as documented.
"""
import json

from portforge_agent.detection import enrich_port, enrich_ports
from portforge_agent.detection.models import Confidence
from portforge_agent.models import DiscoveredPort, PortState, Protocol, Source


def _docker_port(compose_project=None, compose_service=None, image=None, name=None):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="linux",
        port=8001,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.DOCKER,
        container_id="abc123",
        container_name=name,
        docker_compose_project=compose_project,
        service_name=compose_service,
        container_image=image,
        host_port=8001,
        container_port=8000,
    )


def _native_port(process_name=None, working_directory=None, pid=1234, command_line=None):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="linux",
        port=8000,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        pid=pid,
        process_name=process_name,
        working_directory=working_directory,
        command_line=command_line,
    )


def test_docker_compose_project_is_authoritative(tmp_path):
    port = _docker_port(compose_project="ocrforge", compose_service="api", image="ocrforge-api")
    enriched = enrich_port(port)

    assert enriched.project_name == "ocrforge"
    assert enriched.detection.confidence == Confidence.HIGH
    assert enriched.detection.method.startswith("docker_compose")
    assert "com.docker.compose.project=ocrforge" in enriched.detection.evidence


def test_non_compose_container_name_is_not_used_as_project_name():
    port = _docker_port(compose_project=None, compose_service=None, name="my-random-container")
    enriched = enrich_port(port)

    assert enriched.project_name is None


def test_native_process_uses_filesystem_detection(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "my-dashboard"}), encoding="utf-8")
    port = _native_port(process_name="node.exe", working_directory=str(tmp_path))

    enriched = enrich_port(port)

    assert enriched.project_name == "my-dashboard"


def test_raw_facts_are_never_mutated_by_enrichment(tmp_path):
    port = _native_port(process_name="mysqld.exe", working_directory=str(tmp_path), pid=999)

    enriched = enrich_port(port)

    # Enrichment must never alter raw facts -- only add inferred fields.
    assert enriched.process_name == "mysqld.exe"
    assert enriched.working_directory == str(tmp_path)
    assert enriched.pid == 999
    assert enriched.purpose == "mysql"
    assert enriched.category == "database"


def test_enrich_port_does_not_mutate_the_input(tmp_path):
    port = _native_port(process_name="mysqld.exe", working_directory=str(tmp_path))
    original_purpose = port.purpose

    enrich_port(port)

    assert port.purpose == original_purpose  # input object untouched


def test_unknown_project_does_not_drag_down_known_purpose_confidence():
    # No working_directory at all -> project stays unknown, but the purpose
    # (mysqld -> mysql) is still confidently HIGH -- matches the project
    # brief's own worked example (port 3306: Purpose: mysql / Confidence: high,
    # with no Project line shown at all).
    port = _native_port(process_name="mysqld.exe", working_directory=None)

    enriched = enrich_port(port)

    assert enriched.project_name is None
    assert enriched.purpose == "mysql"
    assert enriched.detection.confidence == Confidence.HIGH


def test_combined_evidence_includes_both_project_and_purpose(tmp_path):
    port = _docker_port(compose_project="ocrforge", compose_service="api", image="ocrforge-api")
    enriched = enrich_port(port)

    evidence = enriched.detection.evidence
    assert any("compose.project" in e for e in evidence)
    assert any("api" in e.lower() for e in evidence)


def test_enrich_ports_enriches_every_port_and_shares_one_cache(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "shared-project"}), encoding="utf-8")
    port_a = _native_port(process_name="node.exe", working_directory=str(tmp_path), pid=1)
    port_b = _native_port(process_name="node.exe", working_directory=str(tmp_path), pid=2)

    enriched = enrich_ports([port_a, port_b])

    assert len(enriched) == 2
    assert all(p.project_name == "shared-project" for p in enriched)


def test_to_dict_serializes_detection_block():
    port = _docker_port(compose_project="ocrforge", compose_service="api")
    enriched = enrich_port(port)

    data = enriched.to_dict()
    assert data["detection"]["confidence"] == "high"
    assert isinstance(data["detection"]["evidence"], list)
    assert data["project_name"] == "ocrforge"


def test_unrecognized_native_process_stays_unknown_not_invented(tmp_path):
    empty_dir = tmp_path / "no-markers-here"
    empty_dir.mkdir()
    port = _native_port(process_name="some_custom_tool.exe", working_directory=str(empty_dir))

    enriched = enrich_port(port)

    assert enriched.project_name is None
    assert enriched.purpose is None
    assert enriched.category == "unknown"
    assert enriched.detection.confidence == Confidence.UNKNOWN
