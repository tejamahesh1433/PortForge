"""Tests for Phase 9 Fleet Intelligence API."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}


def _mint_and_enroll(client, hostname: str = "fleet-host", agent_version: str = "1.0.0") -> tuple[str, uuid.UUID]:
    mint = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert mint.status_code == 200
    token = mint.json()["enrollment_token"]

    host_id = uuid.uuid4()
    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "agent_version": agent_version,
            "docker_available": False,
            "protocol_version": 1,
        },
    )
    assert enroll.status_code == 200
    return enroll.json()["agent_token"], host_id


def test_fleet_list_returns_enrolled_hosts(client):
    _mint_and_enroll(client, "fleet-host-1")
    resp = client.get("/api/fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["total"] >= 1
    host_data = body["items"][0]
    assert "update_availability" in host_data
    assert "last_heartbeat" in host_data


def test_fleet_list_has_fleet_intelligence_fields(client):
    _mint_and_enroll(client, "fleet-host-2", agent_version="1.1.0")
    resp = client.get("/api/fleet")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    item = items[0]
    # All fleet intelligence fields present
    assert "contract_version" in item
    assert "python_version" in item
    assert "last_error" in item
    assert "last_sync" in item
    assert "target_version" in item
    assert "active_upgrade" in item


def test_fleet_get_single_host(client):
    _, host_id = _mint_and_enroll(client, "fleet-detail-host")
    resp = client.get(f"/api/fleet/{host_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(host_id)
    assert "update_availability" in body
    assert body["lifecycle_state"] == "ACTIVE"


def test_fleet_404_for_unknown_host(client):
    resp = client.get(f"/api/fleet/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_fleet_update_availability_unknown_when_no_target_configured(client):
    """Without PORTFORGE_UPDATE_TARGET_VERSION set, availability is UNKNOWN."""
    _mint_and_enroll(client, "fleet-ua-host")
    resp = client.get("/api/fleet")
    assert resp.status_code == 200
    # target_version from settings is None → UNKNOWN
    for item in resp.json()["items"]:
        assert item["update_availability"] == "UNKNOWN"


def test_fleet_lifecycle_filter(client):
    agent_token, host_id = _mint_and_enroll(client, "fleet-decom-host")
    # Decommission the host
    resp = client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "test"})
    assert resp.status_code == 200

    # Filter by DECOMMISSIONED
    resp = client.get("/api/fleet?lifecycle_state=DECOMMISSIONED")
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(host_id) in ids

    # Filter by ACTIVE should not include it
    resp = client.get("/api/fleet?lifecycle_state=ACTIVE")
    assert resp.status_code == 200
    active_ids = [item["id"] for item in resp.json()["items"]]
    assert str(host_id) not in active_ids


def test_fleet_q_filter(client):
    unique = f"unique-host-{uuid.uuid4().hex[:8]}"
    _mint_and_enroll(client, unique)
    resp = client.get(f"/api/fleet?q={unique}")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["hostname"] == unique


def test_fleet_contract_version_stored_from_enrollment(client):
    mint = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    token = mint.json()["enrollment_token"]
    host_id = uuid.uuid4()
    client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "host_id": str(host_id),
            "hostname": "contract-host",
            "operating_system": "linux",
            "docker_available": False,
            "contract_version": 1,
            "python_version": "3.11.4",
        },
    )
    resp = client.get(f"/api/fleet/{host_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == 1
    assert body["python_version"] == "3.11.4"
