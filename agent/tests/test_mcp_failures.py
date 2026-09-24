"""MCP structured error codes under Central and workflow failures."""
from __future__ import annotations

from unittest.mock import MagicMock

from portforge_agent.config_files import ConfigPathError
from portforge_agent.mcp.errors import map_exception

from .mcp_helpers import copy_sample_stack, invoke_tool, tool_error_code


def _stack_args(root, manifest):
    return {
        "project_root": str(root),
        "manifest_path": str(manifest),
        "central_url": "http://central.example",
    }


def test_central_unavailable_no_url_configured(monkeypatch):
    from portforge_agent.mcp import context
    from portforge_agent.mcp.errors import McpToolError

    monkeypatch.delenv("PORTFORGE_CENTRAL_URL", raising=False)
    monkeypatch.setattr(
        context,
        "resolve_central_url",
        lambda _url: (_ for _ in ()).throw(
            McpToolError("CENTRAL_UNAVAILABLE", "No Central server URL configured.")
        ),
    )
    payload, is_error = invoke_tool(
        "portforge_allocation_release",
        {"allocation_id": "x", "confirm_mutate": True},
        server_defaults={},
    )
    assert is_error is True
    assert tool_error_code(payload) == "CENTRAL_UNAVAILABLE"


def test_host_decommissioned_on_create_allocation(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    mock_central_client.create_allocation.return_value = MagicMock(
        success=False,
        data={"error": {"code": "HOST_DECOMMISSIONED", "message": "Host removed", "details": []}},
    )
    payload, is_error = invoke_tool(
        "portforge_allocation_create",
        {**_stack_args(root, manifest), "request_id": "host-decom-1", "confirm_mutate": True},
    )
    assert is_error is True
    assert tool_error_code(payload) == "HOST_DECOMMISSIONED"


def test_host_offline_on_create_allocation(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    mock_central_client.create_allocation.return_value = MagicMock(
        success=False,
        data={"error": {"code": "HOST_OFFLINE", "message": "Host offline", "details": []}},
    )
    payload, is_error = invoke_tool(
        "portforge_allocation_create",
        {**_stack_args(root, manifest), "request_id": "host-off-1", "confirm_mutate": True},
    )
    assert is_error is True
    assert tool_error_code(payload) == "HOST_OFFLINE"


def test_allocation_unavailable_no_free_ports(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    mock_central_client.create_allocation.return_value = MagicMock(
        success=False,
        data={"error": {"code": "ALLOCATION_UNAVAILABLE", "message": "No ports", "details": []}},
    )
    payload, is_error = invoke_tool(
        "portforge_project_provision",
        {**_stack_args(root, manifest), "request_id": "no-ports-1", "confirm_mutate": True},
    )
    assert is_error is True
    assert tool_error_code(payload) == "ALLOCATION_UNAVAILABLE"


def test_invalid_project_manifest_bad_yaml(tmp_path):
    bad = tmp_path / "portforge.yml"
    bad.write_text("project: [unclosed\n", encoding="utf-8")
    payload, is_error = invoke_tool(
        "portforge_project_inspect",
        {"manifest_path": str(bad), "central_url": "http://central.example"},
    )
    assert is_error is True
    assert tool_error_code(payload) == "INVALID_PROJECT_MANIFEST"


def test_config_changed_since_plan(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    plan, _ = invoke_tool("portforge_project_plan", _stack_args(root, manifest))
    (root / ".env").write_text((root / ".env").read_text() + "\n# edit\n", encoding="utf-8")
    payload, is_error = invoke_tool(
        "portforge_project_provision",
        {
            **_stack_args(root, manifest),
            "request_id": "stale-2",
            "confirm_mutate": True,
            "plan_id": plan["plan_id"],
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "CONFIG_CHANGED_SINCE_PLAN"


def test_mutation_not_approved():
    payload, is_error = invoke_tool(
        "portforge_allocation_create",
        {"request_id": "x", "central_url": "http://central.example"},
    )
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_path_outside_project_via_map_exception():
    mapped = map_exception(ConfigPathError("outside"))
    assert mapped.code == "PATH_OUTSIDE_PROJECT"


def test_duplicate_request_id_idempotent_replay(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    args = {**_stack_args(root, manifest), "request_id": "idem-1", "confirm_mutate": True}
    first, err1 = invoke_tool("portforge_project_provision", args)
    assert err1 is False
    assert first["status"] == "APPLIED"
    second, err2 = invoke_tool("portforge_project_provision", args)
    assert err2 is False
    assert second["status"] == "APPLIED"
    assert mock_central_client.create_allocation.call_count == 1


def test_rollback_failure_unknown_mutation(tmp_path):
    root, _manifest = copy_sample_stack(tmp_path)
    payload, is_error = invoke_tool(
        "portforge_project_rollback",
        {"project_root": str(root), "mutation_id": "nonexistent-mutation", "confirm_mutate": True},
    )
    assert is_error is True
    assert tool_error_code(payload) in (
        "CONFIG_ROLLBACK_FAILED",
        "MUTATION_NOT_FOUND",
        "CONFIG_NOT_FOUND",
        "CONFIG_MUTATION_NOT_FOUND",
    )


def test_central_health_failure_surfaces_in_capabilities(mock_central_client):
    mock_central_client.health.return_value = MagicMock(success=False, error="connection refused")
    payload, is_error = invoke_tool("portforge_capabilities", {})
    assert is_error is False
    assert payload["central_available"] is False
