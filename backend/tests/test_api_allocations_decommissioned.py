"""Phase 8A + host lifecycle: decommissioned hosts refuse new allocations."""
from __future__ import annotations

from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}


def _allocate(client: TestClient, host_id, requests=None):
    return client.post(
        "/api/allocations",
        json={
            "project": "jarvis",
            "host_id": str(host_id),
            "requests": requests or [{"name": "api", "purpose": "api", "protocol": "tcp"}],
        },
    )


def test_create_allocation_rejects_decommissioned_host(client: TestClient, enrolled_host):
    host_id = enrolled_host.id
    decom = client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "retired"})
    assert decom.status_code == 200, decom.text

    response = _allocate(client, host_id)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "HOST_DECOMMISSIONED"
    assert body["error"]["details"][0]["lifecycle_state"] == "DECOMMISSIONED"


def test_batch_alias_rejects_decommissioned_host(client: TestClient, enrolled_host):
    host_id = enrolled_host.id
    assert client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "retired"}).status_code == 200

    response = client.post(
        "/api/allocations/batch",
        json={
            "project": "jarvis",
            "host_id": str(host_id),
            "requests": [{"name": "frontend", "purpose": "frontend", "protocol": "tcp"}],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HOST_DECOMMISSIONED"
