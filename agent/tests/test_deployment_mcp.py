"""Phase 18: deployment MCP tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import (
    CENTRAL_PATCH_TARGET,
    EXPECTED_TOOL_NAMES,
    FORBIDDEN_TOOL_SUBSTRINGS,
    MUTATE_TOOLS,
    invoke_tool,
    mcp_exchange,
    tool_error_code,
)
from .targets_helpers import PLAN_CENTRAL_PATCH_TARGET, copy_targets_fixture, targets_central_mock


def test_deployment_tools_listed():
    responses, _ = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    for expected in (
        "portforge_deployment_plan",
        "portforge_deployment_apply",
        "portforge_deployment_status",
        "portforge_deployment_rollback",
    ):
        assert expected in names
    assert names == EXPECTED_TOOL_NAMES


def test_no_shell_tools_in_deployment_specs():
    deployment_names = {name for name in EXPECTED_TOOL_NAMES if "deployment" in name}
    for spec in TOOL_SPECS:
        if spec["name"] not in deployment_names:
            continue
        lower = spec["name"].lower()
        assert not any(token in lower for token in FORBIDDEN_TOOL_SUBSTRINGS)


def test_deployment_apply_requires_confirm_mutate(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload, is_error = invoke_tool(
        "portforge_deployment_apply",
        {
            "project_root": str(root),
            "environment": "production",
            "target": "lenovo-prod",
            "request_id": "deploy-1",
            "plan_hash": "c" * 64,
            "package_uri": "https://artifacts.example/pkg.tar.gz",
            "package_sha256": "a" * 64,
            "package_manifest_sha256": "b" * 64,
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_deployment_rollback_requires_confirm_mutate():
    payload, is_error = invoke_tool(
        "portforge_deployment_rollback",
        {
            "deployment_id": "11111111-1111-1111-1111-111111111111",
            "request_id": "rollback-1",
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_deployment_plan_mocked_central(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock()
    client.plan_deployment = MagicMock(
        return_value=MagicMock(
            success=True,
            data={
                "host_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "project": "sample-stack",
                "environment": "production",
                "plan_hash": "d" * 64,
                "services": [],
                "ingress_bindings": [],
            },
        )
    )
    with patch(CENTRAL_PATCH_TARGET) as mcp_cls, patch(PLAN_CENTRAL_PATCH_TARGET) as plan_cls:
        mcp_cls.return_value = client
        plan_cls.return_value = client
        payload, is_error = invoke_tool(
            "portforge_deployment_plan",
            {
                "project_root": str(root),
                "environment": "production",
                "target": "lenovo-prod",
                "central_url": "http://central.example",
            },
        )
    assert is_error is False
    assert payload["deployment_plan"]["plan_hash"] == "d" * 64
    assert payload["target_plan"]["environment"] == "production"
    client.plan_deployment.assert_called_once()


def test_mutate_tools_include_deployment():
    for name in ("portforge_deployment_apply", "portforge_deployment_rollback"):
        assert name in MUTATE_TOOLS
