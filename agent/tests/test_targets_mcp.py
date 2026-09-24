"""MCP surface for environment-target planning and provisioning."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from portforge_agent.mcp.errors import McpToolError
from portforge_agent.mcp.plans import verify_plan_fresh
from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import (
    CENTRAL_PATCH_TARGET,
    EXPECTED_TOOL_NAMES,
    FORBIDDEN_TOOL_SUBSTRINGS,
    invoke_tool,
    mcp_exchange,
    tool_error_code,
)
from .targets_helpers import (
    HOST_HP,
    LENOVO_RECOMMENDATIONS,
    PLAN_CENTRAL_PATCH_TARGET,
    copy_targets_fixture,
    lenovo_conflict_allocations,
    targets_central_mock,
)


def _target_plan_args(root, **overrides):
    args = {
        "project_root": str(root),
        "environment": "production",
        "target": "lenovo-prod",
        "central_url": "http://central.example",
        "request_id": "mcp-deploy-1",
    }
    args.update(overrides)
    return args


def test_portforge_project_plan_with_environment_target_mocked_central(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock(
        allocation_items=lenovo_conflict_allocations(),
        recommendation_map=LENOVO_RECOMMENDATIONS,
    )
    with patch(CENTRAL_PATCH_TARGET) as mcp_cls, patch(PLAN_CENTRAL_PATCH_TARGET) as plan_cls:
        mcp_cls.return_value = client
        plan_cls.return_value = client
        payload, is_error = invoke_tool("portforge_project_plan", _target_plan_args(root))
    assert is_error is False
    assert payload["environment"] == "production"
    assert payload["target"] == "lenovo-prod"
    assert payload["plan_id"]
    assert payload["services"]


def test_provision_without_confirm_mutate_mutation_not_approved(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload, is_error = invoke_tool(
        "portforge_project_provision",
        {
            **_target_plan_args(root),
            "manifest_path": str(root / "portforge.yml"),
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_provision_plan_target_mismatch(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock()
    with patch(CENTRAL_PATCH_TARGET) as mcp_cls, patch(PLAN_CENTRAL_PATCH_TARGET) as plan_cls:
        mcp_cls.return_value = client
        plan_cls.return_value = client
        plan_payload, plan_err = invoke_tool("portforge_project_plan", _target_plan_args(root))
        assert plan_err is False
        plan_id = plan_payload["plan_id"]

        provision_payload, is_error = invoke_tool(
            "portforge_project_provision",
            {
                **_target_plan_args(root, target="hp-prod", target_host_id=HOST_HP),
                "manifest_path": str(root / "portforge.yml"),
                "plan_id": plan_id,
                "confirm_mutate": True,
            },
        )
    assert is_error is True
    assert tool_error_code(provision_payload) == "PLAN_TARGET_MISMATCH"


def test_phase15_16_tools_still_listed():
    responses, _ = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == EXPECTED_TOOL_NAMES
    assert "portforge_workspace_discover" in names


def test_no_shell_tools_in_specs():
    for spec in TOOL_SPECS:
        lower = spec["name"].lower()
        assert not any(token in lower for token in FORBIDDEN_TOOL_SUBSTRINGS)


def test_verify_plan_fresh_plan_target_mismatch_direct(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock()
    with patch(CENTRAL_PATCH_TARGET) as mcp_cls, patch(PLAN_CENTRAL_PATCH_TARGET) as plan_cls:
        mcp_cls.return_value = client
        plan_cls.return_value = client
        payload, _ = invoke_tool("portforge_project_plan", _target_plan_args(root))
    manifest = __import__("portforge_agent.manifest", fromlist=["load_and_validate_manifest"]).load_and_validate_manifest(
        root / "portforge.yml"
    )
    with pytest.raises(McpToolError) as exc_info:
        verify_plan_fresh(
            root,
            payload["plan_id"],
            manifest,
            environment="production",
            host_id=HOST_HP,
        )
    assert exc_info.value.code == "PLAN_TARGET_MISMATCH"
