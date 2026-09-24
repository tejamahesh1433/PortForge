"""Kubernetes discovery safety: report-only container/service ports."""
from __future__ import annotations

from portforge_agent.workspace.discover import discover_workspace

from .mcp_helpers import invoke_tool, workspace_fixture_path


def _discover(path):
    return discover_workspace(path, include_local_runtime=False)


def test_discovery_reports_container_port_non_mutable():
    model = _discover(workspace_fixture_path("E"))
    container_ports = [
        req
        for svc in model.services
        for req in svc.port_requirements
        if req.role == "container" and req.port == 8080
    ]
    assert container_ports
    assert all(not req.mutable for req in container_ports)
    assert all(req.classification == "UNSUPPORTED" for req in container_ports)


def test_capabilities_kubernetes_container_port_auto_false():
    payload, is_error = invoke_tool("portforge_capabilities", {"central_url": "http://central.example"})
    assert is_error is False
    assert payload["capabilities"]["kubernetes_containerPort_auto"] is False
    assert payload["capabilities"]["kubernetes_service_port_auto"] is False
    assert payload["capabilities"]["kubernetes_targetPort_auto"] is False


def test_no_automatic_mutation_of_service_port_or_target_port():
    model = _discover(workspace_fixture_path("E"))
    service_ports = [
        req
        for svc in model.services
        for req in svc.port_requirements
        if "Service port" in req.evidence[0].detail or "targetPort" in req.evidence[0].detail
    ]
    assert service_ports
    assert all(not req.mutable for req in service_ports)
