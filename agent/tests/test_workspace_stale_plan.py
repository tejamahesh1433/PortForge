"""Workspace plan fingerprint staleness."""
from __future__ import annotations

import shutil

import pytest

from portforge_agent.mcp.errors import McpToolError
from portforge_agent.mcp.plans import verify_plan_fresh

from .mcp_helpers import invoke_tool, tool_error_code, workspace_fixture_path


def _copy_fixture(tmp_path, fixture_id: str):
    src = workspace_fixture_path(fixture_id)
    dst = tmp_path / fixture_id
    shutil.copytree(src, dst)
    return dst


def _workspace_plan(root):
    payload, is_error = invoke_tool(
        "portforge_project_plan",
        {
            "project_root": str(root),
            "workspace": True,
            "central_url": "http://central.example",
        },
    )
    assert is_error is False
    assert payload["plan_id"]
    assert payload["mode"] == "workspace"
    return payload


def test_workspace_plan_returns_plan_id(tmp_path):
    root = _copy_fixture(tmp_path, "A")
    payload = _workspace_plan(root)
    assert payload["plan_hash"]
    assert payload["discovery"]["services"]


def test_verify_plan_fresh_rejects_changed_fingerprint_input(tmp_path):
    root = _copy_fixture(tmp_path, "A")
    payload = _workspace_plan(root)
    plan_id = payload["plan_id"]
    env_path = root / ".env"
    env_path.write_text(env_path.read_text(encoding="utf-8") + "EXTRA_PORT=9999\n", encoding="utf-8")

    with pytest.raises(McpToolError) as exc_info:
        verify_plan_fresh(root, plan_id, manifest=None)

    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_PLAN"
    assert exc_info.value.details[0]["changed_paths"]


def test_provision_with_stale_workspace_plan_rejects(tmp_path, mock_central_client):
    root = _copy_fixture(tmp_path, "G")
    payload = _workspace_plan(root)
    plan_id = payload["plan_id"]
    compose = root / "docker-compose.yml"
    compose.write_text(
        compose.read_text(encoding="utf-8").replace("3000:3000", "3001:3000"),
        encoding="utf-8",
    )

    provision_payload, is_error = invoke_tool(
        "portforge_project_provision",
        {
            "project_root": str(root),
            "manifest_path": str(root / "portforge.yml"),
            "request_id": "stale-workspace-plan",
            "confirm_mutate": True,
            "plan_id": plan_id,
            "central_url": "http://central.example",
        },
    )
    assert is_error is True
    assert tool_error_code(provision_payload) == "CONFIG_CHANGED_SINCE_PLAN"
    assert provision_payload["error"]["details"][0]["changed_paths"]


def test_multi_file_change_only_one_still_rejects(tmp_path):
    root = _copy_fixture(tmp_path, "B")
    payload = _workspace_plan(root)
    plan_id = payload["plan_id"]
    env_path = root / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace("FRONTEND_PORT=3000", "FRONTEND_PORT=3001"),
        encoding="utf-8",
    )

    with pytest.raises(McpToolError) as exc_info:
        verify_plan_fresh(root, plan_id, manifest=None)

    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_PLAN"
    changed = exc_info.value.details[0]["changed_paths"]
    assert ".env" in changed
