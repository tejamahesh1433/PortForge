"""Simulate an external coding agent speaking MCP JSON-RPC only."""
from __future__ import annotations

import json

from .mcp_helpers import EXPECTED_TOOL_NAMES, copy_sample_stack, mcp_exchange, parse_mcp_tool_payload


def test_external_coding_agent_mcp_only_workflow(tmp_path):
    """Task: Configure project ports via PortForge — MCP frames only."""
    root, manifest = copy_sample_stack(tmp_path)
    evidence: list[str] = []
    request_id = "coding-agent-mcp-e2e-1"

    init_and_caps = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "portforge_capabilities", "arguments": {"central_url": "http://central.example"}},
            },
        ]
    )
    assert init_and_caps[0][0]["result"]["serverInfo"]["name"] == "portforge"
    caps, caps_err = parse_mcp_tool_payload(init_and_caps[0][1])
    assert caps_err is False
    assert caps["capabilities"]["mcp"] is True
    evidence.append(f"capabilities.mcp={caps['capabilities']['mcp']}")

    inspect_resp, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "portforge_project_inspect",
                    "arguments": {
                        "project_root": str(root),
                        "manifest_path": str(manifest),
                        "central_url": "http://central.example",
                    },
                },
            }
        ]
    )
    inspect, inspect_err = parse_mcp_tool_payload(inspect_resp[0])
    assert inspect_err is False
    assert inspect["project"] == "sample-stack"
    evidence.append(f"inspect.services={len(inspect['services'])}")

    plan_resp, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "portforge_project_plan",
                    "arguments": {
                        "project_root": str(root),
                        "manifest_path": str(manifest),
                        "central_url": "http://central.example",
                    },
                },
            }
        ]
    )
    plan, plan_err = parse_mcp_tool_payload(plan_resp[0])
    assert plan_err is False
    assert plan["committed"] is False
    plan_id = plan["plan_id"]
    evidence.append(f"plan_id={plan_id}")

    review_blob = json.dumps({"plan_hash": plan["plan_hash"], "fields_affected": plan["fields_affected"]})
    assert "dotenv" in review_blob or "compose" in review_blob
    evidence.append("review=plan_hash_and_fields_recorded")

    provision_resp, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "portforge_project_provision",
                    "arguments": {
                        "project_root": str(root),
                        "manifest_path": str(manifest),
                        "central_url": "http://central.example",
                        "request_id": request_id,
                        "confirm_mutate": True,
                        "plan_id": plan_id,
                    },
                },
            }
        ]
    )
    provision, prov_err = parse_mcp_tool_payload(provision_resp[0])
    assert prov_err is False
    assert provision["status"] == "APPLIED"
    evidence.append(f"provision.status={provision['status']}")

    verify_resp, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "portforge_project_verify",
                    "arguments": {
                        "project_root": str(root),
                        "request_id": request_id,
                        "central_url": "http://central.example",
                    },
                },
            }
        ]
    )
    verify, verify_err = parse_mcp_tool_payload(verify_resp[0])
    assert verify_err is False
    assert verify["workflow"]["status"] == "APPLIED"
    evidence.append(f"verify.workflow={verify['workflow']['status']}")

    assert len(evidence) >= 5
    tools_list, _ = mcp_exchange([{"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}])
    names = {t["name"] for t in tools_list[0]["result"]["tools"]}
    assert names == EXPECTED_TOOL_NAMES
