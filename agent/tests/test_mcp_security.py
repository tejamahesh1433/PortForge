"""MCP security: tool absence, secret scrubbing, path confinement, approval gates."""
from __future__ import annotations

import json
import os
import platform
from unittest.mock import MagicMock

import pytest

from portforge_agent.config_files import ConfigPathError, resolve_within_root
from portforge_agent.mcp.errors import map_exception
from portforge_agent.mcp.scrub import scrub_secrets
from portforge_agent.mcp.tools import TOOL_SPECS, list_tool_definitions

from .mcp_helpers import (
    FIXTURE_SECRETS,
    MUTATE_TOOLS,
    copy_sample_stack,
    invoke_tool,
    parse_mcp_tool_payload,
    mcp_exchange,
    tool_error_code,
)


def test_no_generic_shell_or_command_tools():
    names = [spec["name"] for spec in TOOL_SPECS]
    for name in names:
        lower = name.lower()
        assert "shell" not in lower
        assert "execute" not in lower
        assert "run" not in lower
        assert "bash" not in lower
        assert "cmd" not in lower
        assert "powershell" not in lower


def test_no_arbitrary_http_sql_filesystem_tools():
    for tool in list_tool_definitions():
        blob = f"{tool['name']} {tool.get('description', '')}".lower()
        assert "http" not in blob
        assert "sql" not in blob
        assert "kubectl" not in blob
        assert "docker exec" not in blob


def test_scrub_secrets_redacts_known_keys_and_bearer():
    payload = {
        "enrollment_token": FIXTURE_SECRETS["enrollment_token"],
        "authorization": FIXTURE_SECRETS["authorization"],
        "admin_token": FIXTURE_SECRETS["admin_token"],
        "nested": {"note": "Bearer secret-admin-token-xyz"},
    }
    scrubbed = scrub_secrets(payload)
    serialized = json.dumps(scrubbed)
    assert FIXTURE_SECRETS["enrollment_token"] not in serialized
    assert FIXTURE_SECRETS["authorization"] not in serialized
    assert FIXTURE_SECRETS["admin_token"] not in serialized
    assert "Bearer secret-admin-token-xyz" not in serialized
    assert "[REDACTED]" in serialized


def test_call_tool_scrubs_injected_central_secrets(mock_central_client):
    mock_central_client.release_allocation.return_value = MagicMock(
        success=True,
        data={
            "status": "released",
            "admin_bootstrap": FIXTURE_SECRETS["admin_bootstrap"],
            "enrollment_token": FIXTURE_SECRETS["enrollment_token"],
        },
    )
    payload, is_error = invoke_tool(
        "portforge_allocation_release",
        {
            "allocation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "confirm_mutate": True,
            "central_url": "http://central.example",
        },
    )
    assert is_error is False
    serialized = json.dumps(payload)
    assert FIXTURE_SECRETS["admin_bootstrap"] not in serialized
    assert FIXTURE_SECRETS["enrollment_token"] not in serialized
    assert "[REDACTED]" in serialized


def test_map_exception_config_path_error_to_path_outside_project():
    mapped = map_exception(ConfigPathError("'../outside.env' resolves outside root."))
    assert mapped.code == "PATH_OUTSIDE_PROJECT"
    assert mapped.details[0]["code"] == "CONFIG_PATH_OUTSIDE_PROJECT"


def test_path_traversal_dotenv_rejected_via_provision(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    bad_yaml = manifest.read_text(encoding="utf-8").replace("file: .env", "file: ../escape.env")
    manifest.write_text(bad_yaml, encoding="utf-8")

    payload, is_error = invoke_tool(
        "portforge_project_provision",
        {
            "confirm_mutate": True,
            "request_id": "path-escape-1",
            "project_root": str(root),
            "manifest_path": str(manifest),
            "central_url": "http://central.example",
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "CONFIG_PATH_OUTSIDE_PROJECT"


def test_resolve_within_root_rejects_parent_traversal(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("SECRET=1\n")
    with pytest.raises(ConfigPathError):
        resolve_within_root(project_dir, "../outside.env")


@pytest.mark.skipif(platform.system() == "Windows", reason="symlink creation may require elevation")
def test_resolve_within_root_rejects_symlink_escape(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.env").write_text("SECRET=1\n")
    os.symlink(str(outside_dir / "secret.env"), str(project_dir / ".env"))
    with pytest.raises(ConfigPathError):
        resolve_within_root(project_dir, ".env")


@pytest.mark.parametrize("tool_name", MUTATE_TOOLS)
def test_mutation_not_approved_without_confirm_mutate(tool_name):
    args = {"request_id": "x"} if "provision" in tool_name or "create" in tool_name else {}
    if "rollback" in tool_name:
        args = {"mutation_id": "m1"}
    if "release" in tool_name:
        args = {"allocation_id": "a1"}
    payload, is_error = invoke_tool(tool_name, args)
    assert is_error is True
    assert tool_error_code(payload) == "MUTATION_NOT_APPROVED"


def test_admin_bootstrap_scrubbed_from_capabilities_response():
    from unittest.mock import patch

    polluted_contract = {
        "contract_version": 1,
        "capabilities": {"mcp": True},
        "admin_bootstrap": FIXTURE_SECRETS["admin_bootstrap"],
    }
    with patch("portforge_agent.mcp.tools.build_contract", return_value=polluted_contract):
        responses, raw = mcp_exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "portforge_capabilities", "arguments": {}},
                }
            ]
        )
    payload, _ = parse_mcp_tool_payload(responses[0])
    serialized = raw + json.dumps(payload)
    assert FIXTURE_SECRETS["admin_bootstrap"] not in serialized
    assert payload.get("admin_bootstrap") == "[REDACTED]"
