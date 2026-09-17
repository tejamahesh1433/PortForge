"""Security-focused tests: unauthenticated/invalid/wrong-host/revoked
credentials, secret redaction, malformed and oversized payloads, and
SQL-injection-shaped strings treated as inert data (SQLAlchemy's
parameterized queries throughout -- no route ever builds SQL by string
concatenation, see repositories/__init__.py).
"""
import uuid
from datetime import datetime, timezone

from app.repositories.agent_repository import AgentRepository
from app.security.tokens import hash_token


def _enroll(client) -> tuple[str, str]:
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
            "hostname": "h",
            "operating_system": "windows",
            "docker_available": False,
        },
    )
    return enroll_response.json()["agent_token"], host_id


def _snapshot_body(host_id: str, observations=None) -> dict:
    return {
        "scan_id": str(uuid.uuid4()),
        "host_id": host_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "observations": observations or [],
    }


def test_unauthenticated_ingestion_rejected(client):
    response = client.post("/api/agent/observations", json=_snapshot_body(str(uuid.uuid4())))
    assert response.status_code == 401


def test_invalid_token_ingestion_rejected(client):
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": "Bearer not-a-real-token"},
        json=_snapshot_body(str(uuid.uuid4())),
    )
    assert response.status_code == 401


def test_wrong_host_token_cannot_write_another_hosts_data(client):
    agent_token_a, host_a = _enroll(client)
    _, host_b = _enroll(client)

    # host A's credential trying to submit observations claiming to be host B.
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token_a}"},
        json=_snapshot_body(host_b),
    )
    assert response.status_code == 403


def test_revoked_credential_rejected(client, db_session):
    agent_token, host_id = _enroll(client)

    # Re-enrolling the same host revokes the old credential (see
    # enrollment_service.enroll_host / replace_credential_for_host).
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    new_enrollment_token = mint_response.json()["enrollment_token"]
    client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": new_enrollment_token,
            "host_id": host_id,
            "hostname": "h",
            "operating_system": "windows",
            "docker_available": False,
        },
    )

    # The OLD token must no longer work.
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json=_snapshot_body(host_id),
    )
    assert response.status_code == 401


def test_secret_never_stored_in_raw_form(client, db_session):
    agent_token, host_id = _enroll(client)
    repo = AgentRepository(db_session)
    credential = repo.get_active_credential_for_host(uuid.UUID(host_id))
    assert credential.token_hash != agent_token
    assert credential.token_hash == hash_token(agent_token)


def test_malformed_payload_rejected_not_crashed(client):
    agent_token, host_id = _enroll(client)
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"scan_id": "not-a-uuid", "host_id": host_id, "observed_at": "not-a-date", "observations": "not-a-list"},
    )
    assert response.status_code == 422  # rejected cleanly, not a 500


def test_oversized_batch_rejected(client):
    agent_token, host_id = _enroll(client)
    huge_observations = [
        {
            "port": 8000,
            "protocol": "tcp",
            "bind_address": "0.0.0.0",
            "state": "ACTIVE",
            "source": "process",
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
    ] * 6000  # exceeds the default max_observations_per_snapshot (5000)

    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json=_snapshot_body(host_id, huge_observations),
    )
    assert response.status_code == 413


def test_oversized_string_field_rejected(client):
    agent_token, host_id = _enroll(client)
    observations = [
        {
            "port": 8000,
            "protocol": "tcp",
            "bind_address": "0.0.0.0",
            "state": "ACTIVE",
            "source": "process",
            "process_name": "x" * 10_000,  # exceeds max_length=512
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
    ]
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json=_snapshot_body(host_id, observations),
    )
    assert response.status_code == 422


def test_sql_injection_shaped_strings_are_treated_as_inert_data(client):
    """SQLAlchemy's parameterized queries mean a hostile-looking string in
    a text field is just data -- never executed as SQL. This proves it by
    round-tripping such a string through a real reservation and reading it
    back unchanged, and confirming no server error occurs.
    """
    agent_token, host_id = _enroll(client)
    injection_payload = "'; DROP TABLE hosts; --"

    response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 8003, "project": injection_payload, "notes": injection_payload},
    )
    assert response.status_code == 201
    assert response.json()["project"] == injection_payload

    # The hosts table must still exist and be queryable.
    hosts_response = client.get("/api/hosts")
    assert hosts_response.status_code == 200


def test_sql_injection_shaped_query_filter_is_inert(client, db_session):
    from app.models.port_observation import CurrentPortObservation

    _, host_id = _enroll(client)
    now = datetime.now(timezone.utc)
    db_session.add(
        CurrentPortObservation(
            host_id=uuid.UUID(host_id),
            port=8000,
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            project_name="ocrforge",
            first_seen=now,
            last_seen=now,
            observed_at=now,
            scan_id=uuid.uuid4(),
        )
    )
    db_session.commit()

    response = client.get("/api/ports?project=' OR '1'='1")
    assert response.status_code == 200
    assert response.json()["total"] == 0  # treated as a literal (non-matching) substring, not SQL


def test_stale_snapshot_rejected_over_http(client):
    agent_token, host_id = _enroll(client)
    t0 = datetime.now(timezone.utc)

    first = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"scan_id": str(uuid.uuid4()), "host_id": host_id, "observed_at": t0.isoformat(), "observations": []},
    )
    assert first.status_code == 200

    from datetime import timedelta

    stale_time = t0 - timedelta(minutes=5)
    second = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": host_id,
            "observed_at": stale_time.isoformat(),
            "observations": [],
        },
    )
    assert second.status_code == 409


def test_replayed_enrollment_token_rejected_over_http(client):
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

    body["host_id"] = str(uuid.uuid4())
    second = client.post("/api/agent/enroll", json=body)
    assert second.status_code == 401
