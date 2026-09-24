"""MCP concurrent allocation_create and idempotency."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from .mcp_helpers import ALLOCATION, CENTRAL_PATCH_TARGET, central_mock, copy_sample_stack, invoke_tool


def _create_allocation_side_effect(ports_by_request: dict):
    def _create(**kwargs):
        request_id = kwargs.get("request_id")
        port = ports_by_request[request_id]
        data = {
            **ALLOCATION,
            "allocation_id": f"alloc-{request_id}",
            "allocations": [
                {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": port, "reservation_id": "r1"}
            ],
        }
        return MagicMock(success=True, status_code=201, data=data)

    return _create


def test_concurrent_allocation_create_distinct_ports(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    ports_map = {"conc-a": 4100, "conc-b": 4200}
    results: list[tuple[str, int]] = []
    lock = threading.Lock()

    def worker(request_id: str) -> None:
        client = central_mock()
        client.create_allocation.side_effect = _create_allocation_side_effect(ports_map)
        with patch(CENTRAL_PATCH_TARGET) as mock_cls:
            mock_cls.return_value = client
            payload, is_error = invoke_tool(
                "portforge_allocation_create",
                {
                    "project_root": str(root),
                    "manifest_path": str(manifest),
                    "central_url": "http://central.example",
                    "request_id": request_id,
                    "confirm_mutate": True,
                },
            )
        assert is_error is False
        port = payload["ports"]["frontend"]
        with lock:
            results.append((request_id, port))

    threads = [threading.Thread(target=worker, args=(rid,)) for rid in ports_map]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    allocated_ports = [port for _, port in results]
    assert len(allocated_ports) == 2
    assert len(set(allocated_ports)) == 2


def test_same_request_id_idempotent(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    args = {
        "project_root": str(root),
        "manifest_path": str(manifest),
        "central_url": "http://central.example",
        "request_id": "idem-conc-1",
        "confirm_mutate": True,
    }
    first, err1 = invoke_tool("portforge_allocation_create", args)
    second, err2 = invoke_tool("portforge_allocation_create", args)
    assert err1 is False and err2 is False
    assert first["allocation_id"] == second["allocation_id"]
    assert first["ports"] == second["ports"]
