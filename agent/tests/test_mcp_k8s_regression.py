"""Kubernetes mutation scope regression for MCP surface."""
from __future__ import annotations

from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import copy_sample_stack, invoke_tool


def test_unsupported_automatic_mutations_include_container_and_service_ports():
    caps, err = invoke_tool("portforge_capabilities", {})
    assert err is False
    unsupported = caps["unsupported_automatic_mutations"]
    assert "containerPort" in unsupported
    assert "Service port" in unsupported
    assert "targetPort" in unsupported


def test_no_rewrite_container_port_tool():
    for spec in TOOL_SPECS:
        assert "rewrite_container_port" not in spec["name"].lower()
        assert "container_port" not in spec["name"].lower()


def test_sample_stack_plan_kubernetes_hostport_nodeport_only(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    plan, err = invoke_tool(
        "portforge_project_plan",
        {
            "project_root": str(root),
            "manifest_path": str(manifest),
            "central_url": "http://central.example",
        },
    )
    assert err is False
    k8s = [f for f in plan["fields_affected"] if f["type"] == "kubernetes"]
    assert k8s, "sample-stack declares kubernetes hostPort/nodePort mappings"
    assert k8s[0]["host_ports"] >= 1
    assert k8s[0]["node_ports"] >= 1
    assert "containerPort" not in str(plan["fields_affected"])
