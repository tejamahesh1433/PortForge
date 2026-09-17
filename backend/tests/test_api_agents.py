"""HTTP-level tests for the authenticated agent endpoints."""
import uuid
from datetime import datetime, timezone

def test_enroll_requires_admin_token_to_mint(client):
    response = client.post("/api/agent/enrollment-tokens")
    assert response.status_code == 401


def test_mint_enrollment_token_with_admin_token(client):
    response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    assert response.status_code == 200
    assert "enrollment_token" in response.json()


def test_mint_enrollment_token_wrong_admin_token_rejected(client):
    response = client.post("/api/agent/enrollment-tokens", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def _enroll_via_api(client) -> tuple[str, str]:
    """Shared setup helper (not a test itself): mints an enrollment token
    and enrolls a fresh host, returning (agent_token, host_id).
    """
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    enrollment_token = mint_response.json()["enrollment_token"]
    host_id = str(uuid.uuid4())

    enroll_response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": enrollment_token,
            "host_id": host_id,
            "hostname": "test-host",
            "operating_system": "windows",
            "os_version": "10",
            "architecture": "x86_64",
            "agent_version": "0.1.0",
            "docker_available": True,
        },
    )
    assert enroll_response.status_code == 200
    body = enroll_response.json()
    assert body["host_id"] == host_id
    assert body["agent_token"]
    return body["agent_token"], host_id


def test_full_enrollment_flow(client):
    agent_token, host_id = _enroll_via_api(client)
    assert agent_token
    assert host_id


def test_invalid_enrollment_token_rejected_over_http(client):
    response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": "never-minted",
            "host_id": str(uuid.uuid4()),
            "hostname": "h",
            "operating_system": "windows",
            "docker_available": False,
        },
    )
    assert response.status_code == 401


def test_replayed_enrollment_token_rejected(client):
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    enrollment_token = mint_response.json()["enrollment_token"]

    body = {
        "enrollment_token": enrollment_token,
        "host_id": str(uuid.uuid4()),
        "hostname": "h",
        "operating_system": "windows",
        "docker_available": False,
    }
    first = client.post("/api/agent/enroll", json=body)
    assert first.status_code == 200

    body["host_id"] = str(uuid.uuid4())  # a *different* host trying to replay the same token
    second = client.post("/api/agent/enroll", json=body)
    assert second.status_code == 401


def test_heartbeat_requires_authentication(client):
    response = client.post(
        "/api/agent/heartbeat",
        json={
            "host_id": str(uuid.uuid4()),
            "hostname": "h",
            "operating_system": "windows",
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 401


def test_heartbeat_with_valid_token(client):
    agent_token, host_id = _enroll_via_api(client)
    response = client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": host_id,
            "hostname": "test-host",
            "operating_system": "windows",
            "docker_available": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["host_id"] == host_id


def test_heartbeat_host_id_mismatch_rejected(client):
    agent_token, host_id = _enroll_via_api(client)
    other_host_id = str(uuid.uuid4())
    response = client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": other_host_id,  # doesn't match the authenticated host
            "hostname": "test-host",
            "operating_system": "windows",
            "docker_available": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 403


def test_observations_submission_normal(client):
    agent_token, host_id = _enroll_via_api(client)
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": host_id,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "observations": [
                {
                    "port": 8000,
                    "protocol": "tcp",
                    "bind_address": "0.0.0.0",
                    "state": "ACTIVE",
                    "source": "process",
                    "process_name": "app.exe",
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["appeared"] == 1


def test_observations_host_id_mismatch_rejected(client):
    agent_token, host_id = _enroll_via_api(client)
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": str(uuid.uuid4()),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "observations": [],
        },
    )
    assert response.status_code == 403
