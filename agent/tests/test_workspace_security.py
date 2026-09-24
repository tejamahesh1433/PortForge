"""Workspace discovery security: secret absence and MCP scrub."""
from __future__ import annotations

import json

from portforge_agent.mcp.scrub import scrub_secrets
from portforge_agent.mcp.tools import TOOL_SPECS
from portforge_agent.workspace.discover import discover_workspace

from .mcp_helpers import (
    FORBIDDEN_TOOL_SUBSTRINGS,
    WORKSPACE_FIXTURE_SECRETS,
    invoke_tool,
    workspace_fixture_path,
)


def _discover(path):
    return discover_workspace(path, include_local_runtime=False)


def test_secret_leak_scan_fixture_a():
    model = _discover(workspace_fixture_path("A"))
    serialized = json.dumps(model.to_dict())
    for secret in WORKSPACE_FIXTURE_SECRETS["A"]:
        assert secret not in serialized


def test_secret_leak_scan_fixture_d():
    model = _discover(workspace_fixture_path("D"))
    serialized = json.dumps(model.to_dict())
    for secret in WORKSPACE_FIXTURE_SECRETS["D"]:
        assert secret not in serialized


def test_mcp_discover_output_scrubbed():
    root = workspace_fixture_path("A")
    payload, is_error = invoke_tool(
        "portforge_workspace_discover",
        {
            "project_root": str(root),
            "include_local_runtime": False,
            "central_url": "http://central.example",
        },
    )
    assert is_error is False
    serialized = json.dumps(payload)
    for secret in WORKSPACE_FIXTURE_SECRETS["A"]:
        assert secret not in serialized


def test_generic_shell_absent_from_mcp_tools():
    names = [spec["name"] for spec in TOOL_SPECS]
    for name in names:
        lower = name.lower()
        assert "shell" not in lower
        assert "execute" not in lower
        assert "bash" not in lower
        assert "powershell" not in lower
        assert not any(token in lower for token in FORBIDDEN_TOOL_SUBSTRINGS if token not in {"run", "cmd"})


def test_scrub_still_works_on_workspace_like_payload():
    polluted = {
        "services": [{"name": "api"}],
        "authorization": "Bearer secret-admin-token-xyz",
        "admin_token": "admin-token-value",
    }
    scrubbed = scrub_secrets(polluted)
    serialized = json.dumps(scrubbed)
    assert "secret-admin-token-xyz" not in serialized
    assert "admin-token-value" not in serialized
    assert "[REDACTED]" in serialized
