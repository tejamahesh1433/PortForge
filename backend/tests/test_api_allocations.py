"""HTTP-level tests for POST/GET/DELETE /api/allocations.

Uses the real `enrolled_host` fixture (a genuinely enrolled, HEALTHY host
via the actual enrollment flow, per conftest.py) so allocation's host
freshness policy (docs/phase8a_allocation_audit.md §7) passes naturally
without any test-only bypass.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.models.host import Host
from app.models.reservation import CentralReservation


def _allocate(client: TestClient, host_id, requests, project="jarvis", request_id=None):
    body = {"project": project, "host_id": str(host_id), "requests": requests}
    if request_id:
        body["request_id"] = request_id
    return client.post("/api/allocations", json=body)


def test_create_allocation_happy_path(client: TestClient, enrolled_host):
    response = _allocate(
        client,
        enrolled_host.id,
        [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
            {"name": "api", "purpose": "api", "protocol": "tcp"},
            {"name": "db", "purpose": "postgres", "protocol": "tcp"},
            {"name": "cache", "purpose": "redis", "protocol": "tcp"},
        ],
    )
    assert response.status_code == 201, response.text
    data = response.json()

    assert data["project"] == "jarvis"
    assert data["host"]["id"] == str(enrolled_host.id)
    assert data["status"] == "active"
    assert len(data["allocations"]) == 4
    names = {a["name"] for a in data["allocations"]}
    assert names == {"frontend", "api", "db", "cache"}
    ports = [a["port"] for a in data["allocations"]]
    assert len(set(ports)) == 4  # all unique
    for a in data["allocations"]:
        assert 1 <= a["port"] <= 65535
    assert data["validation"]["bind_probe"] == "not_remote_capable"
    assert data["validation"]["host_health_state"] == "HEALTHY"

    # Real ports must come from PortForge, not hardcoded -- confirm the
    # frontend port actually landed in the documented 3000-3999 range.
    frontend_entry = next(a for a in data["allocations"] if a["name"] == "frontend")
    assert 3000 <= frontend_entry["port"] <= 3999

    # Visible via the existing reservations list (Phase 8A §21).
    listed = client.get(f"/api/reservations?host_id={enrolled_host.id}").json()
    assert listed["total"] == 4


def test_create_allocation_respects_preferred_port_when_free(client: TestClient, enrolled_host):
    response = _allocate(
        client, enrolled_host.id, [{"name": "frontend", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3005}]
    )
    assert response.status_code == 201
    assert response.json()["allocations"][0]["port"] == 3005


def test_preferred_port_falls_back_when_occupied_not_forced(client: TestClient, enrolled_host, db_session):
    # Occupy 3005 with an unrelated reservation first.
    db_session.add(
        CentralReservation(host_id=enrolled_host.id, port=3005, protocol="tcp", project="someone-else")
    )
    db_session.commit()

    response = _allocate(
        client, enrolled_host.id, [{"name": "frontend", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3005}]
    )
    assert response.status_code == 201
    # Must NOT force 3005 (it's taken); must still succeed with a
    # different, valid port in range.
    port = response.json()["allocations"][0]["port"]
    assert port != 3005
    assert 3000 <= port <= 3999


def test_get_allocation_returns_current_state(client: TestClient, enrolled_host):
    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    fetched = client.get(f"/api/allocations/{created['allocation_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["allocation_id"] == created["allocation_id"]
    assert fetched.json()["status"] == "active"


def test_get_unknown_allocation_returns_structured_404(client: TestClient):
    response = client.get(f"/api/allocations/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "ALLOCATION_NOT_FOUND"


def test_release_allocation_removes_reservations_and_generates_activity(client: TestClient, enrolled_host):
    created = _allocate(
        client,
        enrolled_host.id,
        [{"name": "frontend", "purpose": "frontend", "protocol": "tcp"}, {"name": "api", "purpose": "api", "protocol": "tcp"}],
    ).json()

    released = client.delete(f"/api/allocations/{created['allocation_id']}")
    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert released.json()["allocations"] == []

    remaining = client.get(f"/api/reservations?host_id={enrolled_host.id}").json()
    assert remaining["total"] == 0

    events = client.get(f"/api/activity?host_id={enrolled_host.id}&limit=50").json()["events"]
    released_events = [e for e in events if e["event_type"] == "RESERVATION_RELEASED"]
    assert len(released_events) == 2


def test_release_allocation_is_idempotent(client: TestClient, enrolled_host):
    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    first = client.delete(f"/api/allocations/{created['allocation_id']}")
    second = client.delete(f"/api/allocations/{created['allocation_id']}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "released"


def test_unknown_host_returns_structured_error(client: TestClient):
    response = _allocate(client, uuid.uuid4(), [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "HOST_NOT_FOUND"
    assert "detail" not in body


def test_unknown_purpose_returns_structured_error(client: TestClient, enrolled_host):
    response = _allocate(client, enrolled_host.id, [{"name": "weird", "purpose": "not-a-real-purpose", "protocol": "tcp"}])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_duplicate_request_names_rejected_by_schema(client: TestClient, enrolled_host):
    response = _allocate(
        client,
        enrolled_host.id,
        [{"name": "x", "purpose": "api", "protocol": "tcp"}, {"name": "x", "purpose": "frontend", "protocol": "tcp"}],
    )
    assert response.status_code == 422  # standard FastAPI schema-validation shape, not AllocationError


def test_empty_request_list_rejected(client: TestClient, enrolled_host):
    response = _allocate(client, enrolled_host.id, [])
    assert response.status_code == 422


def test_bundle_size_over_max_rejected(client: TestClient, enrolled_host):
    requests = [{"name": f"svc-{i}", "purpose": "generic", "protocol": "tcp"} for i in range(21)]
    response = _allocate(client, enrolled_host.id, requests)
    assert response.status_code == 422


def test_invalid_protocol_rejected(client: TestClient, enrolled_host):
    response = _allocate(client, enrolled_host.id, [{"name": "x", "purpose": "api", "protocol": "sctp"}])
    assert response.status_code == 422


def test_invalid_preferred_port_rejected(client: TestClient, enrolled_host):
    response = _allocate(
        client, enrolled_host.id, [{"name": "x", "purpose": "api", "protocol": "tcp", "preferred_port": 99999}]
    )
    assert response.status_code == 422


def test_stale_host_refused(client: TestClient, db_session, enrolled_host):
    host = db_session.get(Host, enrolled_host.id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=200)  # > 120 stale threshold, < 300 offline
    db_session.commit()

    response = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HOST_STALE"


def test_offline_host_refused(client: TestClient, db_session, enrolled_host):
    host = db_session.get(Host, enrolled_host.id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=400)  # > 300 offline threshold
    db_session.commit()

    response = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HOST_OFFLINE"


def test_idempotent_replay_returns_existing_allocation(client: TestClient, enrolled_host):
    payload_requests = [{"name": "api", "purpose": "api", "protocol": "tcp"}]
    first = _allocate(client, enrolled_host.id, payload_requests, request_id="task-123")
    second = _allocate(client, enrolled_host.id, payload_requests, request_id="task-123")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["allocation_id"] == second.json()["allocation_id"]
    assert first.json()["allocations"][0]["port"] == second.json()["allocations"][0]["port"]

    # No duplicate allocation -- exactly one reservation exists.
    listed = client.get(f"/api/reservations?host_id={enrolled_host.id}").json()
    assert listed["total"] == 1


def test_idempotency_conflict_on_reused_key_different_payload(client: TestClient, enrolled_host):
    first = _allocate(
        client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}], request_id="task-123"
    )
    assert first.status_code == 201

    second = _allocate(
        client, enrolled_host.id, [{"name": "db", "purpose": "postgres", "protocol": "tcp"}], request_id="task-123"
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # Still only the original allocation's reservation exists.
    listed = client.get(f"/api/reservations?host_id={enrolled_host.id}").json()
    assert listed["total"] == 1
