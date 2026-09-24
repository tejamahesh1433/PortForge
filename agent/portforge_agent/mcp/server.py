from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional

from ..version import get_portforge_version
from . import MCP_PROTOCOL_VERSION, MCP_SCHEMA_VERSION, MCP_SERVER_NAME
from .errors import McpToolError, error_payload
from .protocol import read_message, write_message
from .tools import call_tool, list_tool_definitions


def _tool_result_content(payload: dict, is_error: bool) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, separators=(",", ":"))}],
        "isError": is_error,
    }


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle_request(msg: dict, server_defaults: dict) -> Optional[dict]:
    method = msg.get("method")
    params = msg.get("params") or {}
    request_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": MCP_SERVER_NAME,
                    "version": get_portforge_version(),
                    "mcp_schema_version": MCP_SCHEMA_VERSION,
                },
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list_tool_definitions()}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments, server_defaults)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result_content(result, is_error=False),
            }
        except McpToolError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _tool_result_content(error_payload(exc), is_error=True),
            }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if request_id is not None:
        return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")
    return None


def run_stdio_server(central_url: Optional[str] = None, log_level: str = "WARNING") -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.WARNING),
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    server_defaults = {"central_url": central_url}

    while True:
        try:
            msg = read_message(sys.stdin)
        except json.JSONDecodeError as exc:
            logging.getLogger("portforge_agent.mcp").warning("Malformed JSON on stdin: %s", exc)
            write_message(sys.stdout, _jsonrpc_error(None, -32700, f"Parse error: {exc}"))
            continue

        if msg is None:
            break

        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            write_message(sys.stdout, _jsonrpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request"))
            continue

        request_id = msg.get("id")
        try:
            response = _handle_request(msg, server_defaults)
        except Exception as exc:
            logging.getLogger("portforge_agent.mcp").exception("Unhandled MCP server error")
            if request_id is not None:
                write_message(sys.stdout, _jsonrpc_error(request_id, -32603, str(exc)))
            continue

        if response is not None:
            write_message(sys.stdout, response)
