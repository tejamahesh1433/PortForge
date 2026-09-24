"""Tests for Phase 21 upgrade recovery: stuck detection, cancel, retry."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}

_ARTIFACT_URL = "https://example.com/portforge_agent-1.1.0-py3-none-any.whl"
_ARTIFACT_SHA = "a" * 64
_TARGET_VERSION = "1.1.0"


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_api_host_upgrades.py)
# ---------------------------------------------------------------------------

def _mint_token(client) -> str:
    r = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert r.status_code == 200
    return r.json()["enrollment_token"]


def _enroll(
    client,
    hostname: str = "recovery-host",
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


def _create_upgrade(client, host_id: uuid.UUID) -> dict:
    r = client.post(
        f"/api/hosts/{host_id}/upgrades",
        headers=ADMIN,
        json={
            "target_version": _TARGET_VERSION,
            "artifact_url": _ARTIFACT_URL,
            "artifact_sha256": _ARTIFACT_SHA,
        },
    )
    assert r.status_code == 201
    return r.json()


def _advance_to(client, agent_token: str, upgrade_id: str, *states: str) -> None:
    for state in states:
        r = client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state},
        )
        assert r.status_code == 200, f"Failed to advance to {state}: {r.text}"


# ---------------------------------------------------------------------------
# Stuck detection
# ---------------------------------------------------------------------------

def test_recover_stuck_marks_restarting_as_failed(client, db):
    """A RESTARTING upgrade past the restart threshold must become FAILED stuck_timeout."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    _advance_to(client, agent_token, upgrade_id, "DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING")

    # Force updated_at well past the threshold (default 900s)
    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    db.flush()

    r = client.post("/api/upgrades/recover-stuck", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["recovered"] == 1
    assert str(upgrade_id) in [str(uid) for uid in body["upgrade_ids"]]

    db.expire(row)
    db.refresh(row)
    assert row.state == "FAILED"
    assert "stuck_timeout" in row.failure_reason
    assert "RESTARTING" in row.failure_reason


def test_recover_stuck_marks_pending_timed_out(client, db):
    """APPROVED upgrade past pending threshold (3600s) becomes FAILED."""
    from app.models.host_upgrade import HostUpgrade

    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    # APPROVED or WAITING_FOR_AGENT — either is pending bucket
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=4000)
    db.flush()

    r = client.post("/api/upgrades/recover-stuck", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["recovered"] >= 1

    db.expire(row)
    db.refresh(row)
    assert row.state == "FAILED"
    assert "stuck_timeout" in (row.failure_reason or "")


def test_recover_stuck_does_not_touch_succeeded(client, db):
    """SUCCEEDED row must not be touched by recovery."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    for s in ("DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING"):
        _advance_to(client, agent_token, upgrade_id, s)

    # Heartbeat with target version to set SUCCEEDED
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "VERIFYING_HEALTH"},
    )
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "SUCCEEDED", "reported_version": _TARGET_VERSION},
    )

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "SUCCEEDED"
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=99999)
    db.flush()

    r = client.post("/api/upgrades/recover-stuck", headers=ADMIN)
    assert r.status_code == 200

    db.expire(row)
    db.refresh(row)
    assert row.state == "SUCCEEDED"


def test_recover_stuck_does_not_touch_already_failed(client, db):
    """FAILED row must remain FAILED after recovery sweep."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "DOWNLOADING"},
    )
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "original reason"},
    )

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "FAILED"
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=99999)
    db.flush()

    r = client.post("/api/upgrades/recover-stuck", headers=ADMIN)
    assert r.status_code == 200

    db.expire(row)
    db.refresh(row)
    assert row.state == "FAILED"
    assert row.failure_reason == "original reason"


def test_recover_stuck_respects_inflight_threshold(client, db):
    """DOWNLOADING upgrade within inflight threshold must NOT be marked stuck."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    _advance_to(client, agent_token, upgrade_id, "DOWNLOADING")

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    # Within inflight threshold (default 1800s)
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=100)
    db.flush()

    r = client.post("/api/upgrades/recover-stuck", headers=ADMIN)
    assert r.status_code == 200

    db.expire(row)
    db.refresh(row)
    assert row.state == "DOWNLOADING"


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def test_cancel_approved_upgrade(client, db):
    """APPROVED upgrade must be cancellable."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    _heartbeat_online(client, agent_token, host_id)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "APPROVED"

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "FAILED"
    assert body["failure_reason"] == "operator_cancelled"


def _heartbeat_online(client, agent_token, host_id):
    client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": "recovery-host",
            "operating_system": "linux",
            "agent_version": "1.0.0",
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )


def test_cancel_waiting_for_agent_upgrade(client, db):
    """WAITING_FOR_AGENT upgrade must be cancellable."""
    from app.models.host_upgrade import HostUpgrade
    from app.models.host import Host

    _, host_id = _enroll(client)
    # Force host to be offline so upgrade starts in WAITING_FOR_AGENT
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=9999)
    db.flush()

    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]
    assert upgrade["state"] == "WAITING_FOR_AGENT"

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["state"] == "FAILED"
    assert r.json()["failure_reason"] == "operator_cancelled"


def test_cancel_downloading_upgrade(client, db):
    """DOWNLOADING upgrade is in the safe cancel set."""
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]
    _advance_to(client, agent_token, upgrade_id, "DOWNLOADING")

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["state"] == "FAILED"


def test_cancel_installing_rejected(client, db):
    """INSTALLING state must be rejected (409) for cancel."""
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]
    _advance_to(client, agent_token, upgrade_id, "DOWNLOADING", "VERIFYING", "INSTALLING")

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 409


def test_cancel_restarting_rejected(client, db):
    """RESTARTING state must be rejected (409) for cancel."""
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]
    _advance_to(client, agent_token, upgrade_id, "DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING")

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 409


def test_cancel_terminal_rejected(client, db):
    """Already FAILED upgrade must return 409."""
    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "already done"},
    )

    r = client.post(f"/api/upgrades/{upgrade_id}/cancel", headers=ADMIN)
    assert r.status_code == 409


def test_cancel_not_found(client):
    r = client.post(f"/api/upgrades/{uuid.uuid4()}/cancel", headers=ADMIN)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def test_retry_creates_new_row_from_failed(client, db):
    """Retry from FAILED must create a new row; old row stays FAILED."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "network error"},
    )

    r = client.post(f"/api/upgrades/{upgrade_id}/retry", headers=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] != upgrade_id
    assert body["target_version"] == _TARGET_VERSION
    assert body["state"] in ("APPROVED", "WAITING_FOR_AGENT")

    # Original row stays FAILED
    original = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(original)
    assert original.state == "FAILED"
    assert original.failure_reason == "network error"


def test_retry_new_row_has_derived_request_id(client, db):
    """Retry must assign a derived request_id to the new row."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "FAILED", "failure_reason": "test"},
    )

    r = client.post(f"/api/upgrades/{upgrade_id}/retry", headers=ADMIN)
    assert r.status_code == 201

    new_id = r.json()["id"]
    new_row = db.get(HostUpgrade, uuid.UUID(new_id))
    db.refresh(new_row)
    assert new_row.request_id is not None
    assert "retry" in new_row.request_id


def test_retry_from_non_failed_rejected(client, db):
    """Retry from non-FAILED state (e.g. APPROVED) must be rejected."""
    _, host_id = _enroll(client)
    upgrade = _create_upgrade(client, host_id)
    upgrade_id = upgrade["id"]

    r = client.post(f"/api/upgrades/{upgrade_id}/retry", headers=ADMIN)
    assert r.status_code == 409


def test_retry_not_found(client):
    r = client.post(f"/api/upgrades/{uuid.uuid4()}/retry", headers=ADMIN)
    assert r.status_code == 404


def test_retry_requires_admin(client):
    r = client.post(f"/api/upgrades/{uuid.uuid4()}/retry")
    assert r.status_code == 401
