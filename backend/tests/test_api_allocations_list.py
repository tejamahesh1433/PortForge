"""v1.1-D: HTTP-level tests for GET /api/allocations (list/filter/paginate).

See docs/v1.1/v1.1-d-data-audit.md for why this endpoint exists (Category
C -- no list endpoint existed before v1.1-D) and why bind_probe on each
entry is computed live rather than stored at creation time.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import EnrolledHost


def _allocate(client: TestClient, host_id, requests, project="jarvis", request_id=None):
    body = {"project": project, "host_id": str(host_id), "requests": requests}
    if request_id:
        body["request_id"] = request_id
    return client.post("/api/allocations", json=body)


def _enroll_second_host(client: TestClient) -> EnrolledHost:
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    assert mint_response.status_code == 200, mint_response.text
    enrollment_token = mint_response.json()["enrollment_token"]

    host_id = uuid.uuid4()
    enroll_response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": enrollment_token,
            "host_id": str(host_id),
            "hostname": "second-host",
            "operating_system": "linux",
            "docker_available": True,
        },
    )
    assert enroll_response.status_code == 200, enroll_response.text
    return EnrolledHost(id=host_id, agent_token=enroll_response.json()["agent_token"], hostname="second-host")


def test_list_shows_active_allocation(client: TestClient, enrolled_host):
    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()

    response = client.get("/api/allocations")
    assert response.status_code == 200
    page = response.json()
    ids = {item["allocation_id"] for item in page["items"]}
    assert created["allocation_id"] in ids
    assert page["total"] >= 1
    assert page["limit"] == 50
    assert page["offset"] == 0


def test_list_shows_released_allocation_with_empty_entries(client: TestClient, enrolled_host):
    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    alloc_id = created["allocation_id"]
    release = client.delete(f"/api/allocations/{alloc_id}")
    assert release.status_code == 200

    response = client.get("/api/allocations", params={"status": "released"})
    page = response.json()
    item = next(i for i in page["items"] if i["allocation_id"] == alloc_id)
    assert item["status"] == "released"
    # Known, honest limitation (Phase 8A, unchanged by v1.1-D): a released
    # allocation's entries are a live join against reservations, which no
    # longer exist -- entries is empty, not a historical snapshot.
    assert item["allocations"] == []


def test_list_host_filter(client: TestClient, enrolled_host):
    other = _enroll_second_host(client)
    a1 = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    a2 = _allocate(client, other.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()

    response = client.get("/api/allocations", params={"host_id": str(enrolled_host.id)})
    ids = {i["allocation_id"] for i in response.json()["items"]}
    assert a1["allocation_id"] in ids
    assert a2["allocation_id"] not in ids


def test_list_project_filter(client: TestClient, enrolled_host):
    a1 = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}], project="proj-a").json()
    a2 = _allocate(
        client, enrolled_host.id, [{"name": "db", "purpose": "postgres", "protocol": "tcp"}], project="proj-b"
    ).json()

    response = client.get("/api/allocations", params={"project": "proj-a"})
    ids = {i["allocation_id"] for i in response.json()["items"]}
    assert a1["allocation_id"] in ids
    assert a2["allocation_id"] not in ids


def test_list_state_filter_active_excludes_released(client: TestClient, enrolled_host):
    active = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    released = _allocate(client, enrolled_host.id, [{"name": "db", "purpose": "postgres", "protocol": "tcp"}]).json()
    client.delete(f"/api/allocations/{released['allocation_id']}")

    response = client.get("/api/allocations", params={"status": "active"})
    ids = {i["allocation_id"] for i in response.json()["items"]}
    assert active["allocation_id"] in ids
    assert released["allocation_id"] not in ids


def test_list_search_matches_project_and_request_id(client: TestClient, enrolled_host):
    created = _allocate(
        client,
        enrolled_host.id,
        [{"name": "api", "purpose": "api", "protocol": "tcp"}],
        project="v11d-search-target",
        request_id="v11d-search-req-1",
    ).json()

    by_project = client.get("/api/allocations", params={"search": "search-target"}).json()
    assert created["allocation_id"] in {i["allocation_id"] for i in by_project["items"]}

    by_request_id = client.get("/api/allocations", params={"search": "search-req-1"}).json()
    assert created["allocation_id"] in {i["allocation_id"] for i in by_request_id["items"]}


def test_list_bounded_result_behavior(client: TestClient, enrolled_host):
    for i in range(5):
        _allocate(client, enrolled_host.id, [{"name": f"svc{i}", "purpose": "generic", "protocol": "tcp"}])

    response = client.get("/api/allocations", params={"host_id": str(enrolled_host.id), "limit": 2})
    page = response.json()
    assert len(page["items"]) == 2
    assert page["total"] >= 5
    assert page["limit"] == 2


def test_list_limit_upper_bound_rejected(client: TestClient):
    response = client.get("/api/allocations", params={"limit": 101})
    assert response.status_code == 422


def test_list_exposes_request_id_and_bind_address(client: TestClient, enrolled_host):
    created = _allocate(
        client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}], request_id="v11d-req-x"
    ).json()

    response = client.get("/api/allocations", params={"host_id": str(enrolled_host.id)})
    item = next(i for i in response.json()["items"] if i["allocation_id"] == created["allocation_id"])
    assert item["request_id"] == "v11d-req-x"
    # Phase 8A allocations never set a specific bind address -- None is the
    # genuinely correct value here, just confirming the field is present.
    assert "bind_address" in item["allocations"][0]


def test_list_no_probe_evidence_is_not_remote_capable(client: TestClient, enrolled_host):
    """Legacy/never-probed case (task Sec23: 'legacy/no-probe evidence')."""
    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()

    response = client.get("/api/allocations", params={"host_id": str(enrolled_host.id)})
    item = next(i for i in response.json()["items"] if i["allocation_id"] == created["allocation_id"])
    assert item["allocations"][0]["bind_probe"] == "not_remote_capable"


def test_list_reflects_fresh_verified_free_probe_evidence(client: TestClient, enrolled_host, db_session):
    from app.services import probe_service

    created = _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}]).json()
    port = created["allocations"][0]["port"]

    probe = probe_service.queue_probe(db_session, enrolled_host.id, port, "tcp")
    probe_service.claim_pending_probes(db_session, enrolled_host.id)
    probe_service.submit_result(db_session, enrolled_host.id, probe.id, True, None)

    response = client.get("/api/allocations", params={"host_id": str(enrolled_host.id)})
    item = next(i for i in response.json()["items"] if i["allocation_id"] == created["allocation_id"])
    assert item["allocations"][0]["bind_probe"] == "verified_free"


def test_list_never_leaks_agent_token_or_admin_token(client: TestClient, enrolled_host):
    _allocate(client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    response = client.get("/api/allocations")
    text = response.text
    assert enrolled_host.agent_token not in text
    assert "test-admin-bootstrap-token" not in text
    assert "password" not in text.lower()


def test_get_single_allocation_also_exposes_request_id_and_entry_evidence(client: TestClient, enrolled_host):
    created = _allocate(
        client, enrolled_host.id, [{"name": "api", "purpose": "api", "protocol": "tcp"}], request_id="v11d-detail-req"
    ).json()
    response = client.get(f"/api/allocations/{created['allocation_id']}")
    body = response.json()
    assert body["request_id"] == "v11d-detail-req"
    assert body["allocations"][0]["bind_probe"] == "not_remote_capable"
    assert "bind_address" in body["allocations"][0]  # present even though it's null (Phase 8A never sets one)
