"""MCP surface for workspace discovery and planning."""
from __future__ import annotations

import json

import pytest

from portforge_agent.cli import main
from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import (
    EXPECTED_TOOL_NAMES,
    FORBIDDEN_TOOL_SUBSTRINGS,
    MUTATE_TOOLS,
    discovery_service_ports,
    invoke_tool,
    mcp_exchange,
    normalize_discovery_payload,
    parse_mcp_tool_payload,
    tool_error_code,
    workspace_fixture_path,
)


def test_tools_list_includes_workspace_discover_and_phase15_tools():
    responses, _ = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == EXPECTED_TOOL_NAMES
    assert "portforge_workspace_discover" in names
    assert len(names) == 14


def test_mcp_exchange_workspace_discover():
    root = workspace_fixture_path("A")
    responses, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "portforge_workspace_discover",
                    "arguments": {
                        "project_root": str(root),
                        "include_local_runtime": False,
                        "central_url": "http://central.example",
                    },
                },
            }
        ]
    )
    payload, is_error = parse_mcp_tool_payload(responses[0])
    assert is_error is False
    assert payload["services"]
    assert payload["workspace_fingerprint"]


def test_cli_mcp_semantic_parity_on_fixture_a(capsys):
    root = workspace_fixture_path("A")
    mcp_payload, is_error = invoke_tool(
        "portforge_workspace_discover",
        {
            "project_root": str(root),
            "include_local_runtime": False,
            "central_url": "http://central.example",
        },
    )
    assert is_error is False

    exit_code = main(
        [
            "project",
            "discover",
            str(root),
            "--json",
            "--no-local-runtime",
            "--url",
            "http://central.example",
        ]
    )
    assert exit_code == 0
    cli_payload = json.loads(capsys.readouterr().out)

    assert discovery_service_ports(normalize_discovery_payload(mcp_payload)) == discovery_service_ports(
        normalize_discovery_payload(cli_payload)
    )


@pytest.mark.parametrize("tool_name", MUTATE_TOOLS)
def test_mutation_not_approved_unchanged(tool_name):
    args = {"request_id": "x"} if "provision" in tool_name or "create" in tool_name else {}
    if "rollback" in tool_name:
        args = {"mutation_id": "m1"}
    if "release" in tool_name:
        args = {"allocation_id": "a1"}
    payload, is_error = invoke_tool(tool_name, args)
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_no_shell_tools_in_tool_specs():
    for spec in TOOL_SPECS:
        lower = spec["name"].lower()
        assert not any(token in lower for token in FORBIDDEN_TOOL_SUBSTRINGS)


def test_capabilities_workspace_discover_true():
    payload, is_error = invoke_tool("portforge_capabilities", {"central_url": "http://central.example"})
    assert is_error is False
    assert payload["capabilities"]["workspace_discover"] is True
    assert payload["capabilities"]["workspace_plan"] is True
