"""v1.3 Phase 1 — Remove Record (DELETE /api/hosts/{id}).

Administrative hard purge of a Central host record. Does not stop the remote
agent, permanently block identity, or change protocol/contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.activity import ActivityEvent
from app.models.agent_credential import AgentCredential
from app.models.allocation import Allocation
from app.models.host import Host
from app.models.host_probe import HostProbe
from app.models.port_observation import CurrentPortObservation, PortObservationEvent
from app.models.reservation import CentralReservation
from app.models.scan import Scan
from app.repositories.host_repository import HostRepository

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}


def _mint_enrollment_token(client: TestClient) -> str:
    response = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()["enrollment_token"]


def _enroll(client: TestClient, host_id: uuid.UUID | None = None, hostname: str = "remove-record-host") -> tuple[str, uuid.UUID]:
    host_id = host_id or uuid.uuid4()
    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": _mint_enrollment_token(client),
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "docker_available": True,
            "agent_version": "1.2.0",
        },
    )
    assert enroll.status_code == 200, enroll.text
    body = enroll.json()
    return body["agent_token"], uuid.UUID(body["host_id"])


def _heartbeat(client: TestClient, agent_token: str, host_id: uuid.UUID, hostname: str = "remove-record-host"):
    return client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "docker_available": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_version": "1.2.0",
            "protocol_version": 1,
        },
    )


def _observations(client: TestClient, agent_token: str, host_id: uuid.UUID):
    now = datetime.now(timezone.utc).isoformat()
    return client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": str(host_id),
            "observed_at": now,
            "observations": [
                {
                    "port": 18080,
                    "protocol": "tcp",
                    "bind_address": "0.0.0.0",
                    "state": "ACTIVE",
                    "source": "native",
                    "process_name": "smoke",
                    "pid": 1,
                    "first_seen": now,
                    "last_seen": now,
                }
            ],
        },
    )


def _remove(client: TestClient, host_id: uuid.UUID, headers: dict | None = None):
    return client.delete(f"/api/hosts/{host_id}", headers=headers if headers is not None else ADMIN)


def _count(db_session, model, host_id: uuid.UUID) -> int:
    return db_session.execute(
        select(func.count()).select_from(model).where(model.host_id == host_id)
    ).scalar_one()


# --- authorization ---


def test_remove_record_requires_admin(client: TestClient, enrolled_host):
    response = client.delete(f"/api/hosts/{enrolled_host.id}")
    assert response.status_code == 401
    assert client.get(f"/api/hosts/{enrolled_host.id}").status_code == 200


def test_remove_record_rejects_invalid_admin_token(client: TestClient, enrolled_host):
    response = _remove(client, enrolled_host.id, headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert client.get(f"/api/hosts/{enrolled_host.id}").status_code == 200


def test_remove_record_rejects_agent_credential(client: TestClient, enrolled_host):
    response = _remove(
        client,
        enrolled_host.id,
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
    )
    assert response.status_code == 401
    assert client.get(f"/api/hosts/{enrolled_host.id}").status_code == 200


def test_remove_record_rejects_enrollment_token_as_admin(client: TestClient, enrolled_host):
    enrollment_token = _mint_enrollment_token(client)
    response = _remove(
        client,
        enrolled_host.id,
        headers={"Authorization": f"Bearer {enrollment_token}"},
    )
    assert response.status_code == 401


# --- endpoint contract ---


def test_remove_record_unknown_host_404(client: TestClient):
    response = _remove(client, uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["detail"] == "Host not found."


def test_remove_record_invalid_host_id_422(client: TestClient):
    response = client.delete("/api/hosts/not-a-uuid", headers=ADMIN)
    assert response.status_code == 422


def test_remove_record_success_204_empty_body(client: TestClient):
    _, host_id = _enroll(client)
    response = _remove(client, host_id)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/hosts/{host_id}").status_code == 404


# --- credential lifecycle ---


def test_heartbeat_and_sync_work_before_remove(client: TestClient):
    agent_token, host_id = _enroll(client)
    assert _heartbeat(client, agent_token, host_id).status_code == 200
    assert _observations(client, agent_token, host_id).status_code == 200


def test_old_credential_rejected_after_remove_and_cannot_recreate_host(
    client: TestClient, db_session
):
    agent_token, host_id = _enroll(client)
    assert _heartbeat(client, agent_token, host_id).status_code == 200

    assert _remove(client, host_id).status_code == 204

    assert db_session.get(Host, host_id) is None
    assert (
        db_session.execute(
            select(func.count()).select_from(AgentCredential).where(AgentCredential.host_id == host_id)
        ).scalar_one()
        == 0
    )

    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 401
    obs = _observations(client, agent_token, host_id)
    assert obs.status_code == 401

    # Auth failure must not upsert the host back into existence.
    assert db_session.get(Host, host_id) is None
    assert client.get(f"/api/hosts/{host_id}").status_code == 404


# --- same-UUID re-enrollment ---


def test_same_uuid_reenroll_issues_new_credential_single_host_row(client: TestClient, db_session):
    host_uuid = uuid.uuid4()
    token_a, host_id = _enroll(client, host_id=host_uuid, hostname="reenroll-host")
    assert host_id == host_uuid
    assert _heartbeat(client, token_a, host_id, hostname="reenroll-host").status_code == 200

    assert _remove(client, host_id).status_code == 204
    assert _heartbeat(client, token_a, host_id, hostname="reenroll-host").status_code == 401

    token_b, host_id_b = _enroll(client, host_id=host_uuid, hostname="reenroll-host")
    assert host_id_b == host_uuid
    assert token_b != token_a

    assert _heartbeat(client, token_a, host_uuid, hostname="reenroll-host").status_code == 401
    assert _heartbeat(client, token_b, host_uuid, hostname="reenroll-host").status_code == 200
    assert _observations(client, token_b, host_uuid).status_code == 200

    host_count = db_session.execute(
        select(func.count()).select_from(Host).where(Host.id == host_uuid)
    ).scalar_one()
    assert host_count == 1


def test_same_hostname_different_uuid_remain_independent(client: TestClient, db_session):
    token_x, host_x = _enroll(client, hostname="shared-name")
    token_y, host_y = _enroll(client, hostname="shared-name")
    assert host_x != host_y

    assert _remove(client, host_x).status_code == 204

    assert client.get(f"/api/hosts/{host_x}").status_code == 404
    assert client.get(f"/api/hosts/{host_y}").status_code == 200
    assert _heartbeat(client, token_y, host_y, hostname="shared-name").status_code == 200
    assert _heartbeat(client, token_x, host_x, hostname="shared-name").status_code == 401

    remaining = db_session.execute(
        select(func.count()).select_from(Host).where(Host.hostname == "shared-name")
    ).scalar_one()
    assert remaining == 1


# --- dependent purge ---


def test_remove_record_purges_all_host_dependents(client: TestClient, db_session, enrolled_host):
    host_id = enrolled_host.id
    now = datetime.now(timezone.utc)

    # Active allocation (+ reservations).
    active = client.post(
        "/api/allocations",
        json={
            "project": "purge-active",
            "host_id": str(host_id),
            "request_id": f"purge-active-{uuid.uuid4()}",
            "requests": [{"name": "api", "purpose": "api", "protocol": "tcp", "preferred_port": 48111}],
        },
    )
    assert active.status_code == 201, active.text

    # Released allocation (history row still host-owned).
    released_create = client.post(
        "/api/allocations",
        json={
            "project": "purge-released",
            "host_id": str(host_id),
            "request_id": f"purge-released-{uuid.uuid4()}",
            "requests": [{"name": "web", "purpose": "frontend", "protocol": "tcp", "preferred_port": 48112}],
        },
    )
    assert released_create.status_code == 201, released_create.text
    released_id = released_create.json()["allocation_id"]
    assert client.delete(f"/api/allocations/{released_id}").status_code == 200

    # Standalone reservation (not via allocation).
    db_session.add(
        CentralReservation(host_id=host_id, port=48113, protocol="tcp", project="standalone")
    )

    # Observed ports + event history.
    scan_id = uuid.uuid4()
    db_session.add(
        CurrentPortObservation(
            host_id=host_id,
            port=48114,
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="native",
            first_seen=now,
            last_seen=now,
            observed_at=now,
            scan_id=scan_id,
        )
    )
    db_session.add(
        PortObservationEvent(
            host_id=host_id,
            port=48114,
            protocol="tcp",
            bind_address="0.0.0.0",
            event_type="appeared",
            scan_id=scan_id,
            occurred_at=now,
        )
    )

    # Scan / probe / activity / credential already exists from enrollment.
    db_session.add(
        Scan(
            id=uuid.uuid4(),
            host_id=host_id,
            observed_at=now,
            observation_count=1,
            received_at=now,
        )
    )
    db_session.add(
        HostProbe(
            host_id=host_id,
            port=48115,
            protocol="tcp",
            bind_address="0.0.0.0",
            status="PENDING",
            expires_at=now + timedelta(minutes=5),
        )
    )
    db_session.add(
        ActivityEvent(
            host_id=host_id,
            timestamp=now,
            event_type="HOST_ONLINE",
            summary="fixture activity for purge",
        )
    )
    db_session.commit()

    assert _count(db_session, AgentCredential, host_id) >= 1
    assert _count(db_session, Allocation, host_id) >= 1
    assert _count(db_session, CentralReservation, host_id) >= 1
    assert _count(db_session, CurrentPortObservation, host_id) >= 1
    assert _count(db_session, PortObservationEvent, host_id) >= 1
    assert _count(db_session, Scan, host_id) >= 1
    assert _count(db_session, HostProbe, host_id) >= 1
    assert _count(db_session, ActivityEvent, host_id) >= 1

    assert _remove(client, host_id).status_code == 204

    assert db_session.get(Host, host_id) is None
    assert _count(db_session, AgentCredential, host_id) == 0
    assert _count(db_session, Allocation, host_id) == 0
    assert _count(db_session, CentralReservation, host_id) == 0
    assert _count(db_session, CurrentPortObservation, host_id) == 0
    assert _count(db_session, PortObservationEvent, host_id) == 0
    assert _count(db_session, Scan, host_id) == 0
    assert _count(db_session, HostProbe, host_id) == 0
    assert _count(db_session, ActivityEvent, host_id) == 0


def test_remove_record_rolls_back_on_mid_purge_failure(client: TestClient, db_session, monkeypatch):
    agent_token, host_id = _enroll(client)
    assert _heartbeat(client, agent_token, host_id).status_code == 200

    original = HostRepository.delete

    def _fail_after_partial(self, hid):  # noqa: ANN001
        ok = original(self, hid)
        if ok:
            raise RuntimeError("forced purge failure after dependent deletes")
        return ok

    monkeypatch.setattr(HostRepository, "delete", _fail_after_partial)

    response = _remove(client, host_id)
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to remove host record."

    db_session.expire_all()
    assert db_session.get(Host, host_id) is not None
    assert _count(db_session, AgentCredential, host_id) >= 1
    assert _heartbeat(client, agent_token, host_id).status_code == 200


def test_list_hosts_unchanged_authorization_posture(client: TestClient, enrolled_host):
    # Phase 1 must not alter read posture (still unauthenticated list/get).
    assert client.get("/api/hosts").status_code == 200
    assert client.get(f"/api/hosts/{enrolled_host.id}").status_code == 200
