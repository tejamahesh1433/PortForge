"""POST /api/allocations/batch is an alias of POST /api/allocations."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_batch_alias_matches_primary_route(client: TestClient, enrolled_host):
    requests = [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
        {"name": "api", "purpose": "api", "protocol": "tcp"},
    ]
    body = {"project": "jarvis", "host_id": str(enrolled_host.id), "requests": requests, "request_id": "batch-alias-1"}

    primary = client.post("/api/allocations", json=body)
    assert primary.status_code == 201, primary.text

    replay = client.post("/api/allocations/batch", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["allocation_id"] == primary.json()["allocation_id"]
    assert replay.json()["idempotent_replay"] is True
