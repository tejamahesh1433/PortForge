"""Kubernetes safety flags for environment-target phase."""
from __future__ import annotations

from portforge_agent.agent_contract import build_contract

from .mcp_helpers import invoke_tool


def test_capabilities_environment_targets_does_not_enable_container_port_auto():
    contract = build_contract()
    assert contract["capabilities"]["environment_targets"] is True
    assert contract["capabilities"]["kubernetes_containerPort_auto"] is False


def test_mcp_capabilities_kubernetes_container_port_auto_still_false():
    payload, is_error = invoke_tool("portforge_capabilities", {"central_url": "http://central.example"})
    assert is_error is False
    assert payload["capabilities"]["environment_targets"] is True
    assert payload["capabilities"]["ingress_plan"] is True
    assert payload["capabilities"]["kubernetes_containerPort_auto"] is False
