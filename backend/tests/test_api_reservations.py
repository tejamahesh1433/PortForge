"""HTTP-level tests for /api/reservations: authenticated write, public read."""
import uuid
from datetime import datetime, timezone

from app.models.host import Host


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


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


def test_list_reservations_is_public(client):
    response = client.get("/api/reservations")
    assert response.status_code == 200


def test_create_reservation_requires_authentication(client):
    response = client.post("/api/reservations", json={"port": 8003, "project": "deeptrace"})
    assert response.status_code == 401


def test_create_and_list_reservation(client):
    agent_token, host_id = _enroll(client)
    response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 8003, "protocol": "tcp", "project": "deeptrace", "service": "api"},
    )
    assert response.status_code == 201
    reservation = response.json()
    assert reservation["host_id"] == host_id

    listed = client.get(f"/api/reservations?host_id={host_id}")
    assert listed.json()["total"] == 1


def test_create_reservation_always_uses_authenticated_host(client):
    """A reservation's host_id can never be spoofed -- there is no host_id
    field in the request body at all.
    """
    agent_token, host_id = _enroll(client)
    response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 8003, "project": "deeptrace"},
    )
    assert response.json()["host_id"] == host_id


def test_delete_reservation_requires_authentication(client, db_session):
    host_id = _make_host(db_session)
    response = client.delete(f"/api/reservations/{uuid.uuid4()}")
    assert response.status_code == 401


def test_delete_own_reservation(client):
    agent_token, host_id = _enroll(client)
    create_response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 8003, "project": "deeptrace"},
    )
    reservation_id = create_response.json()["id"]

    delete_response = client.delete(
        f"/api/reservations/{reservation_id}", headers={"Authorization": f"Bearer {agent_token}"}
    )
    assert delete_response.status_code == 204


def test_cannot_delete_another_hosts_reservation(client):
    agent_token_a, _ = _enroll(client)
    create_response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token_a}"},
        json={"port": 8003, "project": "deeptrace"},
    )
    reservation_id = create_response.json()["id"]

    agent_token_b, _ = _enroll(client)
    delete_response = client.delete(
        f"/api/reservations/{reservation_id}", headers={"Authorization": f"Bearer {agent_token_b}"}
    )
    assert delete_response.status_code == 404  # not found from host B's perspective -- never leaked


def test_reservation_validation_rejects_bad_port(client):
    agent_token, _ = _enroll(client)
    response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 99999, "project": "deeptrace"},
    )
    assert response.status_code == 422


def test_reservation_validation_rejects_bad_protocol(client):
    agent_token, _ = _enroll(client)
    response = client.post(
        "/api/reservations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"port": 8000, "protocol": "sctp", "project": "deeptrace"},
    )
    assert response.status_code == 422


def test_create_and_delete_dashboard_reservation(client, db_session):
    """The /dashboard write routes are deliberately unauthenticated (Phase
    7C.4 acceptance): PortForge runs as a trusted private/LAN control plane
    with no login/session/token architecture in scope, so a dashboard client
    can create and release a reservation without any credential -- this is
    a different trust boundary than agent enrollment (still require_admin;
    see test_agents.py), which mints a durable per-host credential.
    """
    host_id = str(_make_host(db_session))

    create_response = client.post(
        "/api/reservations/dashboard",
        json={"host_id": host_id, "port": 9000, "project": "ui-project"},
    )
    assert create_response.status_code == 201
    reservation = create_response.json()
    assert reservation["host_id"] == host_id
    assert reservation["port"] == 9000

    delete_response = client.delete(f"/api/reservations/dashboard/{host_id}/{reservation['id']}")
    assert delete_response.status_code == 204

    listed = client.get(f"/api/reservations?host_id={host_id}")
    assert listed.json()["total"] == 0


def test_dashboard_reservation_create_still_enforces_conflict_protection(client, db_session):
    """Removing the admin-credential requirement must not weaken the
    existing conflict/validation guarantees -- a second reservation for the
    same host/port/protocol still 409s.
    """
    host_id = str(_make_host(db_session))
    first = client.post(
        "/api/reservations/dashboard",
        json={"host_id": host_id, "port": 9100, "project": "ui-project-a"},
    )
    assert first.status_code == 201

    second = client.post(
        "/api/reservations/dashboard",
        json={"host_id": host_id, "port": 9100, "project": "ui-project-b"},
    )
    assert second.status_code == 409


def test_dashboard_reservation_create_still_validates_port(client, db_session):
    host_id = str(_make_host(db_session))
    response = client.post(
        "/api/reservations/dashboard",
        json={"host_id": host_id, "port": 99999, "project": "ui-project"},
    )
    assert response.status_code == 422
