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


def test_heartbeat_rollback_sets_allow_downgrade(client, db):
    """Admin rollback deliveries must flag allow_downgrade for the agent."""
    from app.models.host import Host

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    prev_url = "https://example.com/portforge_agent-1.0.0-py3-none-any.whl"
    prev_sha = "b" * 64
    upgrade = _create_upgrade(
        client,
        host_id,
        previous_artifact_url=prev_url,
        previous_artifact_sha256=prev_sha,
    ).json()

    # Advance original upgrade to SUCCEEDED, then set host to the new version
    # so the rollback target is a true downgrade relative to the host.
    for state in (
        "DOWNLOADING",
        "VERIFYING",
        "INSTALLING",
        "RESTARTING",
        "VERIFYING_HEALTH",
        "SUCCEEDED",
    ):
        r = client.post(
            f"/api/agent/upgrades/{upgrade['id']}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state, "reported_version": _TARGET_VERSION},
        )
        assert r.status_code == 200, r.text

    h = db.get(Host, host_id)
    assert h is not None
    h.agent_version = _TARGET_VERSION
    db.flush()

    r = client.post(f"/api/upgrades/{upgrade['id']}/rollback", headers=ADMIN)
    assert r.status_code == 201

    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    pu = hb.json()["pending_upgrade"]
    assert pu is not None
    assert pu["target_version"] == "1.0.0"
    assert pu["allow_downgrade"] is True
    assert pu["artifact_filename"] == "portforge_agent-1.0.0-py3-none-any.whl"


def test_heartbeat_normal_upgrade_allow_downgrade_false(client):
    agent_token, host_id = _enroll(client)
    _create_upgrade(client, host_id)
    hb = _heartbeat(client, agent_token, host_id)
    pu = hb.json()["pending_upgrade"]
    assert pu["allow_downgrade"] is False


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


# ---------------------------------------------------------------------------
# Phase 20: heartbeat reconciliation tests
# ---------------------------------------------------------------------------


def _walk_to_restarting(client, agent_token: str, host_id: uuid.UUID, upgrade_id: str) -> None:
    """Advance an upgrade from APPROVED/WAITING_FOR_AGENT all the way to RESTARTING."""
    for state in ("DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING"):
        r = client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state},
        )
        assert r.status_code == 200, f"Failed to advance to {state}: {r.text}"


def test_heartbeat_reconciles_restarting_to_succeeded(client, db):
    """New process heartbeats with target version: RESTARTING → SUCCEEDED (via VERIFYING_HEALTH)."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    _walk_to_restarting(client, agent_token, host_id, upgrade_id)

    # Authenticated heartbeat from the new process carrying the target version
    # advances RESTARTING → VERIFYING_HEALTH → SUCCEEDED in one reconciliation.
    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "SUCCEEDED"
    assert row.completed_at is not None


def test_heartbeat_wrong_version_leaves_restarting(client, db):
    """Heartbeat with a version that does not match target leaves state unchanged."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    _walk_to_restarting(client, agent_token, host_id, upgrade_id)

    hb = _heartbeat(client, agent_token, host_id, agent_version="9.9.9")
    assert hb.status_code == 200
    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "RESTARTING"


def test_heartbeat_does_not_resurrect_failed(client, db):
    """A FAILED upgrade is never touched by reconciliation, even with a matching version."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    # Drive the upgrade to FAILED via the status API.
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "simulated failure"},
    )

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "FAILED"

    # Heartbeat with the target version must not change the terminal state.
    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    db.refresh(row)
    assert row.state == "FAILED"


def test_heartbeat_install_failure_not_completed_by_matching_version(client, db):
    """Install-stage FAILED stays FAILED even if a later heartbeat reports target version."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    for state in ("DOWNLOADING", "VERIFYING", "INSTALLING"):
        r = client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state},
        )
        assert r.status_code == 200
    r = client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "Installation failed"},
    )
    assert r.status_code == 200

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "FAILED"

    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    db.refresh(row)
    assert row.state == "FAILED"


def test_heartbeat_other_host_cannot_complete(client, db):
    """Host B's heartbeat does not advance Host A's upgrade."""
    from app.models.host_upgrade import HostUpgrade

    agent_a, host_a = _enroll(client, hostname="host-a")
    agent_b, host_b = _enroll(client, hostname="host-b")

    upgrade = _create_upgrade(client, host_a).json()
    upgrade_id = upgrade["id"]
    _walk_to_restarting(client, agent_a, host_a, upgrade_id)

    # Host B heartbeats with A's target version — must not touch A's upgrade.
    hb = _heartbeat(client, agent_b, host_b, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "RESTARTING"


def test_heartbeat_wrong_credential_rejected(client, db):
    """Invalid / foreign credential cannot reconcile an upgrade."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]
    _walk_to_restarting(client, agent_token, host_id, upgrade_id)

    bad = _heartbeat(client, "not-a-real-token", host_id, agent_version=_TARGET_VERSION)
    assert bad.status_code in (401, 403)

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "RESTARTING"


def test_heartbeat_idempotent_after_succeeded(client, db):
    """Additional heartbeats after SUCCEEDED leave the row unchanged."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    _walk_to_restarting(client, agent_token, host_id, upgrade_id)
    _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "SUCCEEDED"
    completed_at_first = row.completed_at

    # Subsequent heartbeat must not mutate the terminal row.
    _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    db.refresh(row)
    assert row.state == "SUCCEEDED"
    assert row.completed_at == completed_at_first


def test_heartbeat_skips_approved_without_restarting(client, db):
    """A matching-version heartbeat against an APPROVED upgrade must not skip to SUCCEEDED."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]

    # The upgrade is APPROVED (or WAITING_FOR_AGENT) — not RESTARTING.
    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state in ("APPROVED", "WAITING_FOR_AGENT")

    # Heartbeat with the target version must leave the upgrade untouched.
    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    db.refresh(row)
    assert row.state not in ("SUCCEEDED", "VERIFYING_HEALTH")
    assert row.state in ("APPROVED", "WAITING_FOR_AGENT")


def test_heartbeat_decommissioned_host_not_reconciled(client, db):
    """DECOMMISSIONED hosts must not have upgrades advanced by heartbeat."""
    from app.models.host import Host
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade = _create_upgrade(client, host_id).json()
    upgrade_id = upgrade["id"]
    _walk_to_restarting(client, agent_token, host_id, upgrade_id)

    host = db.get(Host, host_id)
    host.lifecycle_state = "DECOMMISSIONED"
    db.commit()

    # Heartbeat may be rejected by auth/lifecycle policy; either way no SUCCEEDED.
    _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "RESTARTING"


def test_rollback_terminal_not_reconciled(client, db):
    """A ROLLED_BACK upgrade is not touched by a version-matching heartbeat."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    prev_url = "https://example.com/portforge_agent-1.0.0-py3-none-any.whl"
    prev_sha = "b" * 64
    upgrade = _create_upgrade(
        client,
        host_id,
        previous_artifact_url=prev_url,
        previous_artifact_sha256=prev_sha,
    ).json()
    upgrade_id = upgrade["id"]

    # Rollback transitions the original upgrade to ROLLED_BACK.
    r = client.post(f"/api/upgrades/{upgrade_id}/rollback", headers=ADMIN)
    assert r.status_code == 201

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "ROLLED_BACK"

    # Heartbeat with the original target version must not change the terminal state.
    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200
    db.refresh(row)
    assert row.state == "ROLLED_BACK"


def test_stale_attempt_cannot_complete_current(client, db):
    """A terminal FAILED attempt A cannot complete; only current RESTARTING B can."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client, agent_version="1.0.0")
    upgrade_a = _create_upgrade(client, host_id).json()
    id_a = upgrade_a["id"]

    client.post(
        f"/api/agent/upgrades/{id_a}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{id_a}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "stale attempt"},
    )
    row_a = db.get(HostUpgrade, uuid.UUID(id_a))
    db.refresh(row_a)
    assert row_a.state == "FAILED"

    upgrade_b = _create_upgrade(client, host_id).json()
    id_b = upgrade_b["id"]
    _walk_to_restarting(client, agent_token, host_id, id_b)

    hb = _heartbeat(client, agent_token, host_id, agent_version=_TARGET_VERSION)
    assert hb.status_code == 200

    db.refresh(row_a)
    assert row_a.state == "FAILED"
    row_b = db.get(HostUpgrade, uuid.UUID(id_b))
    db.refresh(row_b)
    assert row_b.state == "SUCCEEDED"


def test_reconcile_upgrade_after_heartbeat_unit(db):
    """Unit-test reconcile_upgrade_after_heartbeat directly (no HTTP layer)."""
    from app.services.upgrade_service import reconcile_upgrade_after_heartbeat

    # Should return None for decommissioned host.
    result = reconcile_upgrade_after_heartbeat(
        db, uuid.uuid4(), "1.1.0", lifecycle_state="DECOMMISSIONED"
    )
    assert result is None

    # Should return None when reported_version is empty.
    result = reconcile_upgrade_after_heartbeat(db, uuid.uuid4(), None)
    assert result is None

    result = reconcile_upgrade_after_heartbeat(db, uuid.uuid4(), "")
    assert result is None

    # Should return None when no RESTARTING/VERIFYING_HEALTH upgrade exists.
    result = reconcile_upgrade_after_heartbeat(db, uuid.uuid4(), "1.1.0")
    assert result is None
