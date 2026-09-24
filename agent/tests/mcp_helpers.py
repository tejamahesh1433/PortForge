"""Shared helpers for Phase 15 MCP pytest suite."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

CENTRAL_PATCH_TARGET = "portforge_agent.mcp.context.CentralClient"

from portforge_agent.mcp.errors import McpToolError, error_payload
from portforge_agent.mcp.tools import call_tool

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_STACK = _REPO_ROOT / "fixtures" / "sample-stack"

EXPECTED_TOOL_NAMES = frozenset(
    {
        "portforge_capabilities",
        "portforge_project_inspect",
        "portforge_project_plan",
        "portforge_project_provision",
        "portforge_project_verify",
        "portforge_project_rollback",
        "portforge_allocation_recommend",
        "portforge_allocation_create",
        "portforge_allocation_release",
    }
)

FORBIDDEN_TOOL_SUBSTRINGS = ("shell", "execute", "run", "bash", "cmd", "powershell", "http", "sql")

MUTATE_TOOLS = (
    "portforge_project_provision",
    "portforge_project_rollback",
    "portforge_allocation_create",
    "portforge_allocation_release",
)

_HOSTS = {"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "workstation"}]}

ALLOCATION = {
    "allocation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "project": "sample-stack",
    "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "workstation"},
    "status": "active",
    "allocations": [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3100, "reservation_id": "r1"},
        {"name": "api", "purpose": "api", "protocol": "tcp", "port": 30080, "reservation_id": "r2"},
        {"name": "postgres", "purpose": "postgres", "protocol": "tcp", "port": 5433, "reservation_id": "r3"},
        {"name": "redis", "purpose": "redis", "protocol": "tcp", "port": 6380, "reservation_id": "r4"},
        {"name": "metrics", "purpose": "generic", "protocol": "tcp", "port": 9100, "reservation_id": "r5"},
    ],
}

FIXTURE_SECRETS = {
    "enrollment_token": "enroll-tok-abc123xyz",
    "authorization": "Bearer secret-admin-token-xyz",
    "admin_bootstrap": "bootstrap-super-secret",
    "admin_token": "admin-token-value",
}


def copy_sample_stack(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "stack"
    shutil.copytree(FIXTURE_STACK, root)
    return root, root / "portforge.yml"


def file_hashes(root: Path, relative_paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in relative_paths:
        path = root / rel
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
    return out


def central_mock(*, health_ok: bool = True, health_error: str | None = None) -> MagicMock:
    instance = MagicMock()
    instance.health.return_value = MagicMock(success=health_ok, error=health_error)
    instance.list_hosts.return_value = MagicMock(success=True, data=_HOSTS)
    ports_by_purpose = {
        "frontend": 3100,
        "api": 30080,
        "postgres": 5433,
        "redis": 6380,
        "generic": 9100,
    }

    def _recommend(_host_id, purpose, _protocol):
        return MagicMock(success=True, data={"recommended_port": ports_by_purpose.get(purpose, 8100)})

    instance.get_recommendation.side_effect = _recommend
    instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data=ALLOCATION.copy())
    instance.get_allocation.return_value = MagicMock(success=True, data=ALLOCATION.copy())
    instance.list_allocations.return_value = MagicMock(success=True, data={"items": [ALLOCATION.copy()]})
    instance.verify_allocation.return_value = MagicMock(success=True, data={"status": "active", **ALLOCATION})
    instance.release_allocation.return_value = MagicMock(
        success=True,
        data={**ALLOCATION, "status": "released", "allocations": []},
    )
    return instance


def invoke_tool(
    name: str,
    arguments: dict | None = None,
    server_defaults: dict | None = None,
) -> tuple[dict, bool]:
    defaults = server_defaults if server_defaults is not None else {"central_url": "http://central.example"}
    try:
        return call_tool(name, arguments or {}, defaults), False
    except McpToolError as exc:
        return error_payload(exc), True


def parse_mcp_tool_payload(response: dict) -> tuple[dict, bool]:
    result = response["result"]
    payload = json.loads(result["content"][0]["text"])
    return payload, bool(result.get("isError"))


def mcp_exchange(
    requests: list[dict],
    *,
    central_url: str | None = "http://central.example",
    log_level: str = "WARNING",
    timeout: float = 15.0,
    central_client: MagicMock | None = None,
    patch_central: bool = True,
) -> tuple[list[dict], str]:
    from portforge_agent.mcp.server import run_stdio_server

    stdin_buf = io.StringIO("\n".join(json.dumps(r, separators=(",", ":")) for r in requests) + "\n")
    stdout_buf = io.StringIO()
    done = threading.Event()
    mock_client = central_client or central_mock()

    def run() -> None:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = stdin_buf
        sys.stdout = stdout_buf
        try:
            if patch_central:
                with patch(CENTRAL_PATCH_TARGET) as mock_cls:
                    mock_cls.return_value = mock_client
                    run_stdio_server(central_url=central_url, log_level=log_level)
            else:
                run_stdio_server(central_url=central_url, log_level=log_level)
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    done.wait(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError("MCP server thread did not exit")

    raw_stdout = stdout_buf.getvalue()
    responses = [json.loads(line) for line in raw_stdout.splitlines() if line.strip()]
    return responses, raw_stdout


def mcp_exchange_eof_after(request: dict, *, central_url: str | None = "http://central.example") -> None:
    from portforge_agent.mcp.server import run_stdio_server

    stdin_buf = io.StringIO(json.dumps(request, separators=(",", ":")) + "\n")
    stdout_buf = io.StringIO()
    done = threading.Event()

    def run() -> None:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = stdin_buf
        sys.stdout = stdout_buf
        try:
            run_stdio_server(central_url=central_url, log_level="WARNING")
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    done.wait(timeout=5.0)
    assert not thread.is_alive(), "server should exit cleanly on EOF"


def mcp_subprocess_exchange(request_line: str, *, central_url: str = "http://central.example") -> subprocess.CompletedProcess[str]:
    agent_dir = Path(__file__).resolve().parents[1]
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(agent_dir) + (__import__("os").pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, "-m", "portforge_agent", "mcp", "serve", "--url", central_url],
        input=request_line,
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        cwd=str(agent_dir),
    )


def tool_error_code(payload: dict) -> str:
    return payload["error"]["code"]
