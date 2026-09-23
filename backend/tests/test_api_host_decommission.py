"""Phase 8 — Permanent Host Decommission lifecycle.

Distinct from Remove Record (DELETE). Decommission retains a tombstone and
rejects ordinary same-UUID enrollment until Reactivate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.activity import ActivityEvent
from app.models.agent_credential import AgentCredential
from app.models.host import Host

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}


def _mint_enrollment_token(client: TestClient) -> str:
    response = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()["enrollment_token"]


def _enroll(
    client: TestClient, host_id: uuid.UUID | None = None, hostname: str = "decom-host"
) -> tuple[str, uuid.UUID]:
    host_id = host_id or uuid.uuid4()
    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": _mint_enrollment_token(client),
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "agent_version": "1.3.0",
            "protocol_version": 1,
        },
    )
    assert enroll.status_code == 200, enroll.text
    return enroll.json()["agent_token"], host_id


def _heartbeat(client: TestClient, token: str, host_id: uuid.UUID, hostname: str = "decom-host"):
    return client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "agent_version": "1.3.0",
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )


def _sync(client: TestClient, token: str, host_id: uuid.UUID):
    return client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": str(host_id),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "observations": [],
        },
    )


def test_new_host_defaults_active(client: TestClient):
    _, host_id = _enroll(client)
    body = client.get(f"/api/hosts/{host_id}").json()
    assert body["lifecycle_state"] == "ACTIVE"
    assert body["decommissioned_at"] is None


def test_decommission_requires_admin(client: TestClient):
    _, host_id = _enroll(client)
    assert client.post(f"/api/hosts/{host_id}/decommission").status_code == 401
    assert (
        client.post(
            f"/api/hosts/{host_id}/decommission",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )


def test_agent_credential_cannot_decommission(client: TestClient):
    token, host_id = _enroll(client)
    r = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_enrollment_token_cannot_decommission(client: TestClient):
    _, host_id = _enroll(client)
    enroll_tok = _mint_enrollment_token(client)
    r = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers={"Authorization": f"Bearer {enroll_tok}"},
    )
    assert r.status_code == 401


def test_decommission_unknown_host_404(client: TestClient):
    r = client.post(f"/api/hosts/{uuid.uuid4()}/decommission", headers=ADMIN)
    assert r.status_code == 404


def test_active_to_decommissioned_with_reason_and_timestamp(client: TestClient, db_session):
    token, host_id = _enroll(client, hostname="lab-box")
    assert _heartbeat(client, token, host_id, "lab-box").status_code == 200

    before = datetime.now(timezone.utc)
    r = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers=ADMIN,
        json={"reason": "retired lab box"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_state"] == "DECOMMISSIONED"
    assert body["decommission_reason"] == "retired lab box"
    assert body["hostname"] == "lab-box"
    assert body["id"] == str(host_id)
    assert body["decommissioned_at"] is not None
    # Host row still present
    assert client.get(f"/api/hosts/{host_id}").status_code == 200
    host = db_session.get(Host, host_id)
    assert host is not None
    assert host.lifecycle_state == "DECOMMISSIONED"
    assert host.decommissioned_at is not None
    assert host.decommissioned_at >= before.replace(tzinfo=host.decommissioned_at.tzinfo)


def test_credential_invalidation_heartbeat_and_sync_rejected(client: TestClient):
    token, host_id = _enroll(client)
    assert _heartbeat(client, token, host_id).status_code == 200
    assert _sync(client, token, host_id).status_code == 200

    assert client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN).status_code == 200

    assert _heartbeat(client, token, host_id).status_code == 401
    assert _sync(client, token, host_id).status_code == 401


def test_same_uuid_enrollment_rejected_while_decommissioned(client: TestClient):
    token, host_id = _enroll(client, hostname="tombstone-host")
    assert client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN).status_code == 200

    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": _mint_enrollment_token(client),
            "host_id": str(host_id),
            "hostname": "tombstone-host",
            "operating_system": "linux",
            "agent_version": "1.3.0",
            "protocol_version": 1,
        },
    )
    assert enroll.status_code == 409, enroll.text
    assert "decommissioned" in enroll.json()["detail"].lower()
    # No duplicate host rows
    page = client.get("/api/hosts?limit=500").json()
    matches = [h for h in page["items"] if h["id"] == str(host_id)]
    assert len(matches) == 1
    assert matches[0]["lifecycle_state"] == "DECOMMISSIONED"
    # Old credential still dead
    assert _heartbeat(client, token, host_id).status_code == 401


def test_decommission_idempotent(client: TestClient, db_session):
    _, host_id = _enroll(client)
    r1 = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers=ADMIN,
        json={"reason": "first"},
    )
    assert r1.status_code == 200
    ts1 = r1.json()["decommissioned_at"]

    r2 = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers=ADMIN,
        json={"reason": "second"},
    )
    assert r2.status_code == 200
    assert r2.json()["decommissioned_at"] == ts1
    assert r2.json()["decommission_reason"] == "first"

    events = db_session.execute(
        select(ActivityEvent).where(
            ActivityEvent.host_id == host_id,
            ActivityEvent.event_type == "HOST_DECOMMISSIONED",
        )
    ).scalars().all()
    assert len(events) == 1


def test_reactivate_requires_admin(client: TestClient):
    _, host_id = _enroll(client)
    client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN)
    assert client.post(f"/api/hosts/{host_id}/reactivate").status_code == 401


def test_reactivate_flow_and_new_enrollment(client: TestClient, db_session):
    old_token, host_id = _enroll(client, hostname="revive-me")
    assert client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN).status_code == 200

    # Already ACTIVE -> 409
    _, other = _enroll(client, hostname="other-active")
    assert client.post(f"/api/hosts/{other}/reactivate", headers=ADMIN).status_code == 409

    r = client.post(f"/api/hosts/{host_id}/reactivate", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_state"] == "ACTIVE"
    assert body["decommissioned_at"] is None
    assert body["decommission_reason"] is None

    # Old credential still invalid
    assert _heartbeat(client, old_token, host_id).status_code == 401

    # New enrollment same UUID
    new_token, enrolled_id = _enroll(client, host_id=host_id, hostname="revive-me")
    assert enrolled_id == host_id
    assert _heartbeat(client, new_token, host_id, "revive-me").status_code == 200
    assert _sync(client, new_token, host_id).status_code == 200
    assert _heartbeat(client, old_token, host_id).status_code == 401

    page = client.get("/api/hosts?limit=500").json()
    matches = [h for h in page["items"] if h["id"] == str(host_id)]
    assert len(matches) == 1
    assert matches[0]["lifecycle_state"] == "ACTIVE"

    events = db_session.execute(
        select(ActivityEvent).where(
            ActivityEvent.host_id == host_id,
            ActivityEvent.event_type == "HOST_REACTIVATED",
        )
    ).scalars().all()
    assert len(events) == 1


def test_remove_record_active_unchanged(client: TestClient, db_session):
    token, host_id = _enroll(client, hostname="rr-active")
    assert client.delete(f"/api/hosts/{host_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/api/hosts/{host_id}").status_code == 404
    assert _heartbeat(client, token, host_id).status_code == 401
    # Re-enroll allowed after Remove Record
    new_token, _ = _enroll(client, host_id=host_id, hostname="rr-active")
    assert _heartbeat(client, new_token, host_id, "rr-active").status_code == 200


def test_remove_record_decommissioned_removes_tombstone(client: TestClient):
    token, host_id = _enroll(client, hostname="rr-decom")
    assert client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN).status_code == 200
    assert client.delete(f"/api/hosts/{host_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/api/hosts/{host_id}").status_code == 404
    # UUID may enroll again (tombstone gone)
    new_token, _ = _enroll(client, host_id=host_id, hostname="rr-decom")
    body = client.get(f"/api/hosts/{host_id}").json()
    assert body["lifecycle_state"] == "ACTIVE"
    assert _heartbeat(client, new_token, host_id, "rr-decom").status_code == 200
    assert _heartbeat(client, token, host_id).status_code == 401


def test_decommission_rolls_back_on_failure(client: TestClient, db_session, monkeypatch):
    _, host_id = _enroll(client)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(
        "app.services.host_lifecycle_service.AgentRepository.revoke_active_credential_for_host",
        boom,
    )
    r = client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN)
    assert r.status_code == 500
    host = db_session.get(Host, host_id)
    db_session.refresh(host)
    assert host.lifecycle_state == "ACTIVE"
    assert host.decommissioned_at is None
    active = db_session.execute(
        select(func.count())
        .select_from(AgentCredential)
        .where(AgentCredential.host_id == host_id, AgentCredential.revoked_at.is_(None))
    ).scalar_one()
    assert active == 1
