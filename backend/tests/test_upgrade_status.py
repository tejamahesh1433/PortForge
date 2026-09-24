"""Phase 22: typed upgrade status, safe actions, audit idempotency."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.activity import ActivityEvent
from app.services.upgrade_status_service import CHECKSUM_MISMATCH

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}
_ARTIFACT_URL = "https://example.com/portforge_agent-1.1.0-py3-none-any.whl"
_ARTIFACT_SHA = "a" * 64
_TARGET = "1.1.0"


def _mint_token(client) -> str:
    r = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert r.status_code == 200
    return r.json()["enrollment_token"]


def _enroll(client, hostname: str = "obs-host", agent_version: str = "1.0.0"):
    hid = uuid.uuid4()
    r = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": _mint_token(client),
            "host_id": str(hid),
            "hostname": hostname,
            "operating_system": "linux",
            "agent_version": agent_version,
            "docker_available": False,
            "protocol_version": 1,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["agent_token"], hid


def _heartbeat(client, agent_token, host_id, version="1.0.0"):
    r = client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": "obs-host",
            "operating_system": "linux",
            "agent_version": version,
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create(client, host_id):
    r = client.post(
        f"/api/hosts/{host_id}/upgrades",
        headers=ADMIN,
        json={
            "target_version": _TARGET,
            "artifact_url": _ARTIFACT_URL,
            "artifact_sha256": _ARTIFACT_SHA,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _advance(client, agent_token, upgrade_id, *states):
    for state in states:
        body = {"state": state}
        if state == "FAILED":
            body["failure_reason"] = "test failure"
        r = client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json=body,
        )
        assert r.status_code == 200, f"{state}: {r.text}"


def test_typed_status_waiting_for_agent(client, db):
    from app.models.host import Host

    token, host_id = _enroll(client)
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(hours=2)
    db.flush()
    up = _create(client, host_id)
    assert up["state"] == "WAITING_FOR_AGENT"

    r = client.get(f"/api/upgrades/{up['id']}/status", headers=ADMIN)
    assert r.status_code == 200
    st = r.json()
    assert st["upgrade_state"] == "WAITING_FOR_AGENT"
    assert st["waiting_reason"] == "WAITING_FOR_AGENT"
    assert st["progress_status"] == "WAITING"
    assert "CANCEL" in st["operator_actions"]
    assert "RETRY" not in st["operator_actions"]
    assert "Waiting for agent" in st["explanation"]


def test_runtime_control_plane_divergence_failed_but_healthy(client):
    token, host_id = _enroll(client, hostname="div-host")
    _heartbeat(client, token, host_id, "1.0.0")
    up = _create(client, host_id)
    uid = up["id"]
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "FAILED", "failure_reason": "Download failed: timeout"},
    )
    _heartbeat(client, token, host_id, _TARGET)

    st = client.get(f"/api/upgrades/{uid}/status", headers=ADMIN).json()
    assert st["upgrade_state"] == "FAILED"
    assert st["current_version"] == _TARGET
    assert st["runtime_matches_target"] is True
    assert st["reconciliation_status"] == "RUNTIME_OK_HISTORY_FAILED"
    assert "RETRY" in st["operator_actions"]


def test_checksum_failure_not_auto_retryable(client):
    token, host_id = _enroll(client, hostname="sha-host")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    uid = up["id"]
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "FAILED", "failure_reason": "SHA256 checksum mismatch"},
    )
    st = client.get(f"/api/upgrades/{uid}/status", headers=ADMIN).json()
    assert st["failure_code"] == CHECKSUM_MISMATCH
    assert st["retryable"] is False
    assert "RETRY" in st["operator_actions"]
    assert "checksum" in st["failure_summary"].lower()


def test_restarting_offline_not_auto_failed(client, db):
    from app.models.host import Host

    token, host_id = _enroll(client, hostname="mac-sleep")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    uid = up["id"]
    _advance(client, token, uid, "DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING")

    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(hours=3)
    db.commit()

    st = client.get(f"/api/upgrades/{uid}/status", headers=ADMIN).json()
    assert st["upgrade_state"] == "RESTARTING"
    assert st["host_health"] == "OFFLINE"
    assert st["waiting_reason"] == "WAITING_FOR_RESTART"
    assert st["progress_status"] == "WAITING"
    assert "CANCEL" not in st["operator_actions"]


def test_safe_actions_cancel_rejected_installing(client):
    token, host_id = _enroll(client, hostname="inst-host")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    uid = up["id"]
    _advance(client, token, uid, "DOWNLOADING", "VERIFYING", "INSTALLING")
    st = client.get(f"/api/upgrades/{uid}/status", headers=ADMIN).json()
    assert "CANCEL" not in st["operator_actions"]
    r = client.post(f"/api/upgrades/{uid}/cancel", headers=ADMIN)
    assert r.status_code == 409


def test_audit_succeeded_idempotent_on_reconcile(client, db):
    token, host_id = _enroll(client, hostname="aud-host")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    uid = up["id"]
    _advance(client, token, uid, "DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING")
    _heartbeat(client, token, host_id, _TARGET)
    _heartbeat(client, token, host_id, _TARGET)

    events = (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.host_id == host_id,
            ActivityEvent.event_type == "UPGRADE_SUCCEEDED",
        )
        .all()
    )
    assert len(events) == 1


def test_audit_create_and_cancel(client, db):
    token, host_id = _enroll(client, hostname="aud-c")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    client.post(f"/api/upgrades/{up['id']}/cancel", headers=ADMIN)

    types = [
        e.event_type
        for e in db.query(ActivityEvent).filter(ActivityEvent.host_id == host_id).all()
        if e.event_type.startswith("UPGRADE_")
    ]
    assert "UPGRADE_CREATED" in types
    assert "UPGRADE_CANCELLED" in types


def test_retry_preserves_failed_history(client):
    token, host_id = _enroll(client, hostname="retry-h")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    uid = up["id"]
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"state": "FAILED", "failure_reason": "Download failed: reset"},
    )
    r = client.post(f"/api/upgrades/{uid}/retry", headers=ADMIN)
    assert r.status_code == 201
    new_id = r.json()["id"]
    assert new_id != uid
    old = client.get(f"/api/upgrades/{uid}", headers=ADMIN).json()
    assert old["state"] == "FAILED"
    st = client.get(f"/api/upgrades/{new_id}/status", headers=ADMIN).json()
    # Attempt lineage: original FAILED + new APPROVED share artifact/target.
    assert st["related_attempt_count"] >= 2 or st["attempt_index"] >= 2
    hist = client.get(f"/api/hosts/{host_id}/upgrades", headers=ADMIN).json()
    assert len(hist) >= 2
    assert any(h["id"] == uid and h["state"] == "FAILED" for h in hist)


def test_rollout_stop_reason_and_summary(client):
    hosts = []
    for i in range(2):
        t, h = _enroll(client, hostname=f"roll-{i}-{uuid.uuid4().hex[:4]}")
        _heartbeat(client, t, h)
        hosts.append((t, h))
    rid = f"p22-roll-{uuid.uuid4().hex[:8]}"
    body = {
        "host_ids": [str(h) for _, h in hosts],
        "target_version": _TARGET,
        "artifact_url": _ARTIFACT_URL,
        "artifact_sha256": _ARTIFACT_SHA,
        "canary_size": 1,
        "concurrency": 1,
        "stop_on_failure": True,
        "request_id": rid,
    }
    r = client.post("/api/upgrade-rollouts", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["operator_summary"]
    mid = out["members"][0]
    uid = mid["upgrade_id"]
    tok = next(t for t, h in hosts if str(h) == mid["host_id"] or h == uuid.UUID(mid["host_id"]))
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {tok}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{uid}/status",
        headers={"Authorization": f"Bearer {tok}"},
        json={"state": "FAILED", "failure_reason": "boom"},
    )
    adv_body = {k: body[k] for k in body if k != "request_id"}
    adv = client.post(f"/api/upgrade-rollouts/{rid}/advance", json=adv_body, headers=ADMIN)
    assert adv.status_code == 200, adv.text
    paused = adv.json()
    assert paused["status"] == "paused"
    assert paused["stop_reason"] == "ROLLOUT_STOPPED_ON_FAILURE"
    assert "stopped" in paused["operator_summary"].lower()
    assert paused["failed"] >= 1


def test_host_upgrade_status_endpoint(client):
    token, host_id = _enroll(client, hostname="host-st")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    r = client.get(f"/api/hosts/{host_id}/upgrade-status", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["upgrade_id"] == up["id"]


def test_decommissioned_actions_view_only(client):
    token, host_id = _enroll(client, hostname="decom-h")
    _heartbeat(client, token, host_id)
    up = _create(client, host_id)
    r = client.post(
        f"/api/hosts/{host_id}/decommission",
        headers=ADMIN,
        json={"reason": "test"},
    )
    assert r.status_code in (200, 204), r.text
    st = client.get(f"/api/upgrades/{up['id']}/status", headers=ADMIN).json()
    assert st["host_lifecycle"] == "DECOMMISSIONED"
    assert st["operator_actions"] == ["VIEW"]
