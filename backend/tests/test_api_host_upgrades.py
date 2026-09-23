"""Tests for Phase 10 Safe Agent Upgrade Management — admin & agent APIs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}
ENROLL_TOKEN = None  # populated per-test below

_ARTIFACT_URL = "https://example.com/portforge_agent-1.1.0-py3-none-any.whl"
_ARTIFACT_SHA = "a" * 64
_TARGET_VERSION = "1.1.0"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mint_token(client) -> str:
    r = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert r.status_code == 200
    return r.json()["enrollment_token"]


def _enroll(
    client,
    hostname: str = "upgrade-host",
    agent_version: str = "1.0.0",
    host_id: uuid.UUID | None = None,
) -> tuple[str, uuid.UUID]:
    hid = host_id or uuid.uuid4()
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
    assert r.status_code == 200
    return r.json()["agent_token"], hid


def _heartbeat(client, agent_token: str, host_id: uuid.UUID, agent_version: str = "1.0.0"):
    return client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": "upgrade-host",
            "operating_system": "linux",
            "agent_version": agent_version,
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )


def _create_upgrade(
    client,
    host_id: uuid.UUID,
    target_version: str = _TARGET_VERSION,
    artifact_url: str = _ARTIFACT_URL,
    artifact_sha256: str = _ARTIFACT_SHA,
    request_id: str | None = None,
    previous_artifact_url: str | None = None,
    previous_artifact_sha256: str | None = None,
) -> dict:
    body: dict = {
        "target_version": target_version,
        "artifact_url": artifact_url,
        "artifact_sha256": artifact_sha256,
    }
    if request_id:
        body["request_id"] = request_id
    if previous_artifact_url:
        body["previous_artifact_url"] = previous_artifact_url
    if previous_artifact_sha256:
        body["previous_artifact_sha256"] = previous_artifact_sha256
    r = client.post(f"/api/hosts/{host_id}/upgrades", headers=ADMIN, json=body)
    return r


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------


def test_create_upgrade_requires_admin(client):
    _, host_id = _enroll(client)
    r = client.post(
        f"/api/hosts/{host_id}/upgrades",
        json={"target_version": "1.1.0", "artifact_url": _ARTIFACT_URL, "artifact_sha256": _ARTIFACT_SHA},
    )
    assert r.status_code == 401


def test_agent_cannot_create_upgrade(client):
    agent_token, host_id = _enroll(client)
    r = client.post(
        f"/api/hosts/{host_id}/upgrades",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"target_version": "1.1.0", "artifact_url": _ARTIFACT_URL, "artifact_sha256": _ARTIFACT_SHA},
    )
    assert r.status_code == 401


def test_list_upgrades_requires_admin(client):
    _, host_id = _enroll(client)
    r = client.get(f"/api/hosts/{host_id}/upgrades")
    assert r.status_code == 401


def test_get_upgrade_requires_admin(client):
    r = client.get(f"/api/upgrades/{uuid.uuid4()}")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Create upgrade validation
# ---------------------------------------------------------------------------


def test_decommissioned_host_rejected(client):
    _, host_id = _enroll(client)
    client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "test"})
    r = _create_upgrade(client, host_id)
    assert r.status_code == 409


def test_downgrade_rejected(client):
    _, host_id = _enroll(client, agent_version="2.0.0")
    r = _create_upgrade(client, host_id, target_version="1.0.0")
    assert r.status_code == 422


def test_create_upgrade_success_and_stored(client):
    _, host_id = _enroll(client, agent_version="1.0.0")
    r = _create_upgrade(client, host_id)
    assert r.status_code == 201
    body = r.json()
    assert body["target_version"] == _TARGET_VERSION
    assert body["artifact_sha256"] == _ARTIFACT_SHA
    assert body["artifact_url"] == _ARTIFACT_URL
    assert body["state"] in ("APPROVED", "WAITING_FOR_AGENT")
    assert body["previous_version"] == "1.0.0"


def test_online_host_gets_approved_state(client):
    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    # Send a heartbeat so the host appears online (recent last_seen)
    _heartbeat(client, agent_token, host_id)
    r = _create_upgrade(client, host_id)
    assert r.status_code == 201
    assert r.json()["state"] == "APPROVED"


def test_offline_host_gets_waiting_state(client, db):
    """A host that hasn't heartbeated recently gets WAITING_FOR_AGENT."""
    from datetime import timedelta
    from app.models.host import Host

    _, host_id = _enroll(client, agent_version="1.0.0")
    # Force last_seen to be very old
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=9999)
    db.flush()

    r = _create_upgrade(client, host_id)
    assert r.status_code == 201
    assert r.json()["state"] == "WAITING_FOR_AGENT"


def test_duplicate_request_id_idempotent(client):
    _, host_id = _enroll(client)
    r1 = _create_upgrade(client, host_id, request_id="req-abc-123")
    assert r1.status_code == 201
    r2 = _create_upgrade(client, host_id, request_id="req-abc-123")
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_second_upgrade_rejected_while_first_active(client):
    _, host_id = _enroll(client)
    r1 = _create_upgrade(client, host_id)
    assert r1.status_code == 201
    r2 = _create_upgrade(client, host_id, request_id="other-request")
    assert r2.status_code == 409


def test_host_not_found_returns_404(client):
    r = _create_upgrade(client, uuid.uuid4())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Heartbeat delivers pending upgrade
# ---------------------------------------------------------------------------


def test_heartbeat_delivers_pending_upgrade(client):
    agent_token, host_id = _enroll(client)
    _create_upgrade(client, host_id)

    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    body = hb.json()
    assert body["pending_upgrade"] is not None
    pu = body["pending_upgrade"]
    assert pu["target_version"] == _TARGET_VERSION
    assert pu["artifact_sha256"] == _ARTIFACT_SHA
    assert "state" in pu


def test_heartbeat_no_pending_upgrade_when_none(client):
    agent_token, host_id = _enroll(client)
    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    assert hb.json()["pending_upgrade"] is None


# ---------------------------------------------------------------------------
# Agent status update
# ---------------------------------------------------------------------------


def test_agent_can_update_own_upgrade_status(client):
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    r = client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "DOWNLOADING"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "DOWNLOADING"


def test_agent_a_cannot_update_agent_b_upgrade(client):
    agent_a_token, host_a_id = _enroll(client, hostname="host-a")
    agent_b_token, host_b_id = _enroll(client, hostname="host-b")

    upgrade = _create_upgrade(client, host_a_id).json()
    upgrade_id = upgrade["id"]

    # Agent B tries to update Agent A's upgrade
    r = client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_b_token}"},
        json={"state": "DOWNLOADING"},
    )
    assert r.status_code == 403


def test_success_path_full_state_machine(client):
    """Walk through the full happy-path state machine to SUCCEEDED."""
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    def _status(state: str, **kwargs):
        return client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state, **kwargs},
        )

    assert _status("DOWNLOADING").status_code == 200
    assert _status("VERIFYING").status_code == 200
    assert _status("INSTALLING").status_code == 200
    assert _status("RESTARTING").status_code == 200
    assert _status("VERIFYING_HEALTH").status_code == 200
    r = _status("SUCCEEDED", reported_version=_TARGET_VERSION)
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCEEDED"
    assert r.json()["completed_at"] is not None


def test_succeeded_requires_correct_reported_version(client):
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    def _status(state, **kw):
        return client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state, **kw},
        )

    for s in ["DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING", "VERIFYING_HEALTH"]:
        assert _status(s).status_code == 200

    # Wrong version
    r = _status("SUCCEEDED", reported_version="9.9.9")
    assert r.status_code == 422

    # Correct version
    r = _status("SUCCEEDED", reported_version=_TARGET_VERSION)
    assert r.status_code == 200


def test_failure_report_sets_state_and_reason(client):
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    r = client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "download timed out"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "FAILED"
    assert body["failure_reason"] == "download timed out"


def test_backward_transition_rejected(client):
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    def _status(state, **kw):
        return client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state, **kw},
        )

    assert _status("DOWNLOADING").status_code == 200
    assert _status("VERIFYING").status_code == 200
    # Try to go back to DOWNLOADING
    r = _status("DOWNLOADING")
    assert r.status_code == 422


def test_cannot_update_terminal_upgrade(client):
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    def _status(state, **kw):
        return client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state, **kw},
        )

    for s in ["DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING", "VERIFYING_HEALTH"]:
        assert _status(s).status_code == 200
    _status("SUCCEEDED", reported_version=_TARGET_VERSION)

    r = _status("DOWNLOADING")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_rollback_creates_new_upgrade(client):
    _, host_id = _enroll(client, agent_version="1.0.0")
    # Create original upgrade with previous artifact metadata
    prev_url = "https://example.com/portforge_agent-1.0.0-py3-none-any.whl"
    prev_sha = "b" * 64
    upgrade = _create_upgrade(
        client,
        host_id,
        previous_artifact_url=prev_url,
        previous_artifact_sha256=prev_sha,
    ).json()
    upgrade_id = upgrade["id"]

    r = client.post(f"/api/upgrades/{upgrade_id}/rollback", headers=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] != upgrade_id
    assert body["target_version"] == "1.0.0"
    assert body["artifact_url"] == prev_url
    assert body["artifact_sha256"] == prev_sha


def test_rollback_fails_when_no_previous_artifact(client):
    _, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    r = client.post(f"/api/upgrades/{upgrade_id}/rollback", headers=ADMIN)
    assert r.status_code == 422


def test_rollback_requires_admin(client):
    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    r = client.post(f"/api/upgrades/{upgrade['id']}/rollback")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Decommission cancels active upgrade
# ---------------------------------------------------------------------------


def test_decommission_cancels_active_upgrade(client, db):
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = uuid.UUID(upgrade["id"])

    # Verify upgrade is non-terminal
    db_upgrade = db.get(HostUpgrade, upgrade_id)
    assert db_upgrade.state not in ("SUCCEEDED", "FAILED", "ROLLED_BACK")

    # Decommission the host
    r = client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "cleanup"})
    assert r.status_code == 200

    db.expire(db_upgrade)
    db.refresh(db_upgrade)
    assert db_upgrade.state == "FAILED"
    assert "decommissioned" in (db_upgrade.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# List + Get
# ---------------------------------------------------------------------------


def test_list_upgrades_for_host(client):
    _, host_id = _enroll(client)
    _create_upgrade(client, host_id, request_id="list-test-1")

    r = client.get(f"/api/hosts/{host_id}/upgrades", headers=ADMIN)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_get_upgrade_by_id(client):
    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    r = client.get(f"/api/upgrades/{upgrade_id}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["id"] == upgrade_id


def test_get_upgrade_not_found(client):
    r = client.get(f"/api/upgrades/{uuid.uuid4()}", headers=ADMIN)
    assert r.status_code == 404


def test_list_upgrades_unknown_host_returns_404(client):
    r = client.get(f"/api/hosts/{uuid.uuid4()}/upgrades", headers=ADMIN)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Fleet shows active upgrade
# ---------------------------------------------------------------------------


def test_fleet_shows_active_upgrade(client):
    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()

    r = client.get(f"/api/fleet/{host_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["active_upgrade"] is not None
    assert body["active_upgrade"]["id"] == upgrade["id"]
    assert body["active_upgrade"]["target_version"] == _TARGET_VERSION


# ---------------------------------------------------------------------------
# Security / Phase 8 interaction gaps
# ---------------------------------------------------------------------------


def test_enrollment_token_cannot_create_upgrade(client):
    """Enrollment tokens must not authorize upgrade creation."""
    _, host_id = _enroll(client)
    enroll_tok = _mint_token(client)
    r = client.post(
        f"/api/hosts/{host_id}/upgrades",
        headers={"Authorization": f"Bearer {enroll_tok}"},
        json={
            "target_version": _TARGET_VERSION,
            "artifact_url": _ARTIFACT_URL,
            "artifact_sha256": _ARTIFACT_SHA,
        },
    )
    assert r.status_code in (401, 403)


def test_untrusted_artifact_rejected_when_target_configured(client, monkeypatch):
    """When Central has a configured target, mismatched URL/SHA must fail closed."""
    monkeypatch.setenv("PORTFORGE_UPDATE_TARGET_VERSION", _TARGET_VERSION)
    monkeypatch.setenv("PORTFORGE_UPDATE_ARTIFACT_URL", _ARTIFACT_URL)
    monkeypatch.setenv("PORTFORGE_UPDATE_ARTIFACT_SHA256", _ARTIFACT_SHA)
    # Force settings reload if cached
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        _, host_id = _enroll(client)
        r = _create_upgrade(
            client,
            host_id,
            artifact_url="https://evil.example/malware.whl",
            artifact_sha256="b" * 64,
        )
        assert r.status_code == 422
        assert "configured artifact" in r.json()["detail"].lower() or "artifact" in r.json()["detail"].lower()
    finally:
        get_settings.cache_clear()


def test_remove_record_purges_upgrade_rows(client, db):
    """Remove Record must clear host_upgrades for the host."""
    from app.models.host_upgrade import HostUpgrade

    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id).json()
    assert client.delete(f"/api/hosts/{host_id}", headers=ADMIN).status_code == 204
    assert db.get(HostUpgrade, uuid.UUID(upgrade["id"])) is None
    assert client.get(f"/api/upgrades/{upgrade['id']}", headers=ADMIN).status_code == 404


def test_revoked_credential_cannot_retrieve_pending_upgrade(client):
    """Old credential after decommission must not receive upgrade work."""
    agent_token, host_id = _enroll(client)
    _create_upgrade(client, host_id)
    assert client.post(
        f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "gone"}
    ).status_code == 200

    r = _heartbeat(client, agent_token, host_id)
    assert r.status_code in (401, 403)
    if r.status_code == 200:
        assert r.json().get("pending_upgrade") in (None, {})


def test_reactivate_does_not_revive_cancelled_upgrade(client, db):
    """Reactivated host must not inherit a stale cancelled upgrade as pending."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    _heartbeat(client, agent_token, host_id)
    upgrade = _create_upgrade(client, host_id).json()
    client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "temp"})
    client.post(f"/api/hosts/{host_id}/reactivate", headers=ADMIN)

    # Re-enroll to get a fresh credential after decommission revoked the old one
    new_token, _ = _enroll(client, host_id=host_id, hostname="upgrade-host-reactivated")
    r = _heartbeat(client, new_token, host_id)
    assert r.status_code == 200
    assert r.json().get("pending_upgrade") is None

    row = db.get(HostUpgrade, uuid.UUID(upgrade["id"]))
    assert row is not None
    assert row.state == "FAILED"


def test_upgrade_persists_across_db_session(client, db):
    """Upgrade row survives a fresh repository read (Central restart proxy)."""
    from app.repositories.upgrade_repository import UpgradeRepository

    _, host_id = _enroll(client)
    upgrade_id = uuid.UUID(_create_upgrade(client, host_id).json()["id"])
    db.expire_all()
    loaded = UpgradeRepository(db).get(upgrade_id)
    assert loaded is not None
    assert loaded.state in ("APPROVED", "WAITING_FOR_AGENT")
    assert loaded.target_version == _TARGET_VERSION
