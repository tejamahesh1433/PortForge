"""MCP stdio protocol framing and JSON-RPC behavior."""
from __future__ import annotations

import io
import json
import logging
import sys
import threading
import pytest

from portforge_agent.mcp import MCP_PROTOCOL_VERSION, MCP_SCHEMA_VERSION, MCP_SERVER_NAME
from portforge_agent.mcp.server import run_stdio_server
from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import (
    EXPECTED_TOOL_NAMES,
    FORBIDDEN_TOOL_SUBSTRINGS,
    mcp_exchange,
    mcp_exchange_eof_after,
    mcp_subprocess_exchange,
    parse_mcp_tool_payload,
)


def test_initialize_handshake():
    responses, _ = mcp_exchange([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    assert len(responses) == 1
    result = responses[0]["result"]
    assert result["protocolVersion"] == MCP_PROTOCOL_VERSION == "2024-11-05"
    assert result["serverInfo"]["name"] == MCP_SERVER_NAME == "portforge"
    assert result["serverInfo"]["mcp_schema_version"] == MCP_SCHEMA_VERSION == 1


def test_tools_list_exactly_ten_tools():
    responses, _ = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
    )
    tools = responses[1]["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == EXPECTED_TOOL_NAMES
    assert len(tools) == 14
    for name in names:
        lower = name.lower()
        assert not any(token in lower for token in FORBIDDEN_TOOL_SUBSTRINGS)


def test_tools_call_capabilities_returns_json_text():
    responses, _ = mcp_exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "portforge_capabilities", "arguments": {}}},
        ]
    )
    payload, is_error = parse_mcp_tool_payload(responses[0])
    assert is_error is False
    assert "capabilities" in payload
    assert payload["mcp_schema_version"] == 1


def test_malformed_json_returns_parse_error():
    from portforge_agent.mcp.server import run_stdio_server

    stdin_buf = io.StringIO("{not-json\n")
    stdout_buf = io.StringIO()
    done = threading.Event()

    def run() -> None:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = stdin_buf, stdout_buf
        try:
            run_stdio_server(central_url="http://central.example", log_level="WARNING")
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    done.wait(timeout=5)
    response = json.loads(stdout_buf.getvalue().strip())
    assert response["error"]["code"] == -32700


def test_unknown_method_returns_not_found():
    responses, _ = mcp_exchange([{"jsonrpc": "2.0", "id": 99, "method": "does/not/exist", "params": {}}])
    assert responses[0]["error"]["code"] == -32601


def test_unknown_tool_is_error_invalid_tool():
    responses, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "portforge_run_shell", "arguments": {}},
            }
        ]
    )
    payload, is_error = parse_mcp_tool_payload(responses[0])
    assert is_error is True
    assert payload["error"]["code"] == "INVALID_TOOL"


def test_invalid_params_provision_without_request_id():
    responses, _ = mcp_exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "portforge_project_provision",
                    "arguments": {"confirm_mutate": True},
                },
            }
        ]
    )
    payload, is_error = parse_mcp_tool_payload(responses[0])
    assert is_error is True
    assert payload["error"]["code"] == "INVALID_PARAMS"


def test_client_disconnect_exits_cleanly():
    mcp_exchange_eof_after({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})


def test_logging_goes_to_stderr_not_stdout(caplog):
    caplog.set_level(logging.WARNING)
    responses, raw_stdout = mcp_exchange(
        [{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}],
        log_level="DEBUG",
    )
    assert len(responses) == 1
    for line in raw_stdout.splitlines():
        if line.strip():
            parsed = json.loads(line)
            assert parsed.get("jsonrpc") == "2.0"


def test_subprocess_initialize_black_box():
    proc = mcp_subprocess_exchange(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    )
    assert proc.returncode == 0
    line = proc.stdout.strip().splitlines()[0]
    result = json.loads(line)["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "portforge"
    assert result["serverInfo"]["mcp_schema_version"] == 1


def test_tool_specs_count_matches_list():
    assert len(TOOL_SPECS) == 14
