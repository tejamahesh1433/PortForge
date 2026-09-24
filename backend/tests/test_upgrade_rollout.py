"""Tests for Phase 21 fleet upgrade rollout."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}

_ARTIFACT_URL = "https://example.com/portforge_agent-1.1.0-py3-none-any.whl"
_ARTIFACT_SHA = "a" * 64
_TARGET_VERSION = "1.1.0"

_ROLLOUT_BASE = {
    "target_version": _TARGET_VERSION,
    "artifact_url": _ARTIFACT_URL,
    "artifact_sha256": _ARTIFACT_SHA,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mint_token(client) -> str:
    r = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert r.status_code == 200
    return r.json()["enrollment_token"]


def _enroll(
    client,
    hostname: str = "rollout-host",
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
            "hostname": "rollout-host",
            "operating_system": "linux",
            "agent_version": agent_version,
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )


def _create_rollout(client, host_ids: list[uuid.UUID], request_id: str | None = None, **kwargs) -> dict:
    body = {**_ROLLOUT_BASE, "host_ids": [str(h) for h in host_ids]}
    if request_id:
        body["request_id"] = request_id
    body.update(kwargs)
    r = client.post("/api/upgrade-rollouts", headers=ADMIN, json=body)
    return r


def _advance_rollout(client, request_id: str, host_ids: list[uuid.UUID], **kwargs) -> dict:
    body = {**_ROLLOUT_BASE, "host_ids": [str(h) for h in host_ids]}
    body.update(kwargs)
    r = client.post(f"/api/upgrade-rollouts/{request_id}/advance", headers=ADMIN, json=body)
    return r


def _advance_upgrade_to_succeeded(client, agent_token: str, upgrade_id: str) -> None:
    for state in ("DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING", "VERIFYING_HEALTH"):
        client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state},
        )
    client.post(
        f"/api/agent/upgrades/{upgrade_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"state": "SUCCEEDED", "reported_version": _TARGET_VERSION},
    )


# ---------------------------------------------------------------------------
# Basic rollout creation
# ---------------------------------------------------------------------------

def test_create_rollout_creates_rows_for_all_hosts(client, db):
    """With canary_size=0, concurrency=3 and 3 hosts, all 3 get upgrade rows."""

    tokens_and_ids = [_enroll(client, hostname=f"h{i}") for i in range(3)]
    host_ids = [hid for _, hid in tokens_and_ids]

    r = _create_rollout(client, host_ids, canary_size=0, concurrency=3)
    assert r.status_code == 201
    body = r.json()
    assert body["request_id"]
    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 3


def test_create_rollout_returns_request_id(client):
    """Response must include the generated request_id."""
    _, host_id = _enroll(client)
    r = _create_rollout(client, [host_id])
    assert r.status_code == 201
    assert r.json()["request_id"]


def test_create_rollout_with_explicit_request_id(client):
    _, host_id = _enroll(client)
    r = _create_rollout(client, [host_id], request_id="fleet-batch-001")
    assert r.status_code == 201
    assert r.json()["request_id"] == "fleet-batch-001"


def test_create_rollout_idempotent_same_request_id(client, db):
    """Resubmitting the same request_id returns the existing row, not a new one."""

    _, host_id = _enroll(client)
    rid = "idem-rollout-" + uuid.uuid4().hex[:8]

    r1 = _create_rollout(client, [host_id], request_id=rid)
    assert r1.status_code == 201
    uid1 = r1.json()["members"][0]["upgrade_id"]

    r2 = _create_rollout(client, [host_id], request_id=rid)
    assert r2.status_code == 201
    uid2 = r2.json()["members"][0]["upgrade_id"]

    assert uid1 == uid2


# ---------------------------------------------------------------------------
# Canary
# ---------------------------------------------------------------------------

def test_canary_only_first_n_hosts_get_rows(client, db):
    """With canary_size=1 and 3 eligible hosts, only 1 gets an upgrade row."""

    tokens_and_ids = [_enroll(client, hostname=f"canary-h{i}") for i in range(3)]
    host_ids = [hid for _, hid in tokens_and_ids]

    r = _create_rollout(client, host_ids, canary_size=1)
    assert r.status_code == 201
    body = r.json()

    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 1


def test_advance_after_canary_success_creates_more(client, db):
    """After canary host succeeds, advance must create rows for remaining hosts."""

    tokens_and_ids = [_enroll(client, hostname=f"adv-h{i}") for i in range(3)]
    host_ids = sorted([hid for _, hid in tokens_and_ids])
    agent_tokens = {hid: tok for tok, hid in tokens_and_ids}

    rid = "advance-canary-" + uuid.uuid4().hex[:8]
    r = _create_rollout(client, host_ids, request_id=rid, canary_size=1, concurrency=3)
    assert r.status_code == 201
    body = r.json()

    upgrading_members = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading_members) == 1

    canary_member = upgrading_members[0]
    canary_hid = uuid.UUID(canary_member["host_id"])
    canary_tok = agent_tokens[canary_hid]
    _advance_upgrade_to_succeeded(client, canary_tok, canary_member["upgrade_id"])

    # Advance the rollout
    r2 = _advance_rollout(client, rid, host_ids, canary_size=1, concurrency=3)
    assert r2.status_code == 200
    body2 = r2.json()
    upgrading2 = [m for m in body2["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading2) >= 2


def test_advance_with_canary_not_complete_waits(client, db):
    """If canary is still in-flight, advance must not create more rows."""
    from app.models.host_upgrade import HostUpgrade
    from sqlalchemy import select

    tokens_and_ids = [_enroll(client, hostname=f"wait-h{i}") for i in range(3)]
    host_ids = [hid for _, hid in tokens_and_ids]

    rid = "canary-wait-" + uuid.uuid4().hex[:8]
    r = _create_rollout(client, host_ids, request_id=rid, canary_size=1, concurrency=3)
    assert r.status_code == 201

    # Don't advance canary; call advance immediately
    r2 = _advance_rollout(client, rid, host_ids, canary_size=1, concurrency=3)
    assert r2.status_code == 200

    # Still only 1 row should exist (canary in-flight)
    stmt = select(HostUpgrade).where(HostUpgrade.request_id == rid)
    rows = db.execute(stmt).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# stop_on_failure
# ---------------------------------------------------------------------------

def test_stop_on_failure_blocks_advance(client, db):
    """If a host in the rollout fails and stop_on_failure=True, advance creates nothing new."""
    from app.models.host_upgrade import HostUpgrade
    from sqlalchemy import select

    tokens_and_ids = [_enroll(client, hostname=f"stop-h{i}") for i in range(3)]
    host_ids = sorted([hid for _, hid in tokens_and_ids])
    agent_tokens = {hid: tok for tok, hid in tokens_and_ids}

    rid = "stop-fail-" + uuid.uuid4().hex[:8]
    r = _create_rollout(client, host_ids, request_id=rid, canary_size=1, concurrency=3)
    assert r.status_code == 201

    upgrading = [m for m in r.json()["members"] if m["disposition"] == "upgrading"]
    canary_uid = upgrading[0]["upgrade_id"]
    canary_hid = uuid.UUID(upgrading[0]["host_id"])
    canary_tok = agent_tokens[canary_hid]

    # Fail the canary upgrade
    client.post(
        f"/api/agent/upgrades/{canary_uid}/status",
        headers={"Authorization": f"Bearer {canary_tok}"},
        json={"state": "FAILED", "failure_reason": "test failure"},
    )

    r2 = _advance_rollout(client, rid, host_ids, canary_size=1, concurrency=3, stop_on_failure=True)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "paused"

    stmt = select(HostUpgrade).where(HostUpgrade.request_id == rid)
    rows = db.execute(stmt).scalars().all()
    assert len(rows) == 1  # No new rows created


def test_stop_on_failure_false_continues(client, db):
    """With stop_on_failure=False, advance continues even if some hosts failed."""
    from app.models.host_upgrade import HostUpgrade
    from sqlalchemy import select

    tokens_and_ids = [_enroll(client, hostname=f"nostop-h{i}") for i in range(3)]
    host_ids = sorted([hid for _, hid in tokens_and_ids])
    agent_tokens = {hid: tok for tok, hid in tokens_and_ids}

    rid = "nostop-fail-" + uuid.uuid4().hex[:8]
    r = _create_rollout(client, host_ids, request_id=rid, canary_size=1, concurrency=3, stop_on_failure=False)
    assert r.status_code == 201

    upgrading = [m for m in r.json()["members"] if m["disposition"] == "upgrading"]
    canary_uid = upgrading[0]["upgrade_id"]
    canary_hid = uuid.UUID(upgrading[0]["host_id"])
    canary_tok = agent_tokens[canary_hid]

    client.post(
        f"/api/agent/upgrades/{canary_uid}/status",
        headers={"Authorization": f"Bearer {canary_tok}"},
        json={"state": "FAILED", "failure_reason": "test"},
    )

    r2 = _advance_rollout(client, rid, host_ids, canary_size=0, concurrency=3, stop_on_failure=False)
    assert r2.status_code == 200

    stmt = select(HostUpgrade).where(HostUpgrade.request_id == rid)
    rows = db.execute(stmt).scalars().all()
    assert len(rows) >= 2


# ---------------------------------------------------------------------------
# offline_policy
# ---------------------------------------------------------------------------

def test_offline_skip_policy(client, db):
    """Offline host with SKIP policy gets skipped disposition, no row."""
    from app.models.host import Host

    _, host_id = _enroll(client)
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=9999)
    db.flush()

    r = _create_rollout(client, [host_id], offline_policy="SKIP")
    assert r.status_code == 201
    body = r.json()
    skipped = [m for m in body["members"] if m["disposition"] == "skipped_offline"]
    assert len(skipped) == 1
    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 0


def test_offline_wait_policy(client, db):
    """Offline host with WAIT policy gets an upgrade row in WAITING_FOR_AGENT state."""
    from app.models.host import Host

    _, host_id = _enroll(client)
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=9999)
    db.flush()

    r = _create_rollout(client, [host_id], offline_policy="WAIT")
    assert r.status_code == 201
    body = r.json()
    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 1
    assert upgrading[0]["state"] == "WAITING_FOR_AGENT"


def test_offline_fail_policy(client, db):
    """Offline host with FAIL policy gets offline_failed disposition, no row."""
    from app.models.host import Host

    _, host_id = _enroll(client)
    host = db.get(Host, host_id)
    host.last_seen = datetime.now(timezone.utc) - timedelta(seconds=9999)
    db.flush()

    r = _create_rollout(client, [host_id], offline_policy="FAIL")
    assert r.status_code == 201
    body = r.json()
    failed_offline = [m for m in body["members"] if m["disposition"] == "offline_failed"]
    assert len(failed_offline) == 1


# ---------------------------------------------------------------------------
# Decommissioned hosts
# ---------------------------------------------------------------------------

def test_decommissioned_host_skipped(client, db):
    """Decommissioned hosts must get skipped_decommissioned disposition with no row."""
    _, host_id = _enroll(client)
    client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "test"})

    r = _create_rollout(client, [host_id])
    assert r.status_code == 201
    body = r.json()
    skipped = [m for m in body["members"] if m["disposition"] == "skipped_decommissioned"]
    assert len(skipped) == 1
    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 0


# ---------------------------------------------------------------------------
# skip_if_current
# ---------------------------------------------------------------------------

def test_skip_if_current_skips_already_current(client, db):
    """Host already running the target version gets skipped_current disposition."""
    _, host_id = _enroll(client, agent_version=_TARGET_VERSION)

    r = _create_rollout(client, [host_id], skip_if_current=True)
    assert r.status_code == 201
    body = r.json()
    skipped = [m for m in body["members"] if m["disposition"] == "skipped_current"]
    assert len(skipped) == 1


def test_skip_if_current_false_creates_row(client, db):
    """skip_if_current=False must create an upgrade row even if already on target."""
    _, host_id = _enroll(client, agent_version=_TARGET_VERSION)

    r = _create_rollout(client, [host_id], skip_if_current=False)
    assert r.status_code == 201
    body = r.json()
    upgrading = [m for m in body["members"] if m["disposition"] == "upgrading"]
    assert len(upgrading) == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_concurrency_bounds_initial_rows(client, db):
    """With concurrency=1 and 3 hosts, only 1 row created initially."""
    from sqlalchemy import select
    from app.models.host_upgrade import HostUpgrade

    host_ids = [_enroll(client, hostname=f"conc-h{i}")[1] for i in range(3)]
    rid = "conc-" + uuid.uuid4().hex[:8]

    r = _create_rollout(client, host_ids, request_id=rid, canary_size=0, concurrency=1)
    assert r.status_code == 201

    stmt = select(HostUpgrade).where(HostUpgrade.request_id == rid)
    rows = db.execute(stmt).scalars().all()
    assert len(rows) == 1


def test_advance_respects_concurrency_slots(client, db):
    """advance_rollout must not create more rows than concurrency allows."""
    from sqlalchemy import select
    from app.models.host_upgrade import HostUpgrade

    tokens_and_ids = [_enroll(client, hostname=f"slot-h{i}") for i in range(4)]
    host_ids = sorted([hid for _, hid in tokens_and_ids])
    agent_tokens = {hid: tok for tok, hid in tokens_and_ids}

    rid = "slots-" + uuid.uuid4().hex[:8]
    # Start with concurrency=1, canary=1
    r = _create_rollout(client, host_ids, request_id=rid, canary_size=1, concurrency=2)
    assert r.status_code == 201

    upgrading = [m for m in r.json()["members"] if m["disposition"] == "upgrading"]
    uid = upgrading[0]["upgrade_id"]
    hid = uuid.UUID(upgrading[0]["host_id"])
    tok = agent_tokens[hid]
    _advance_upgrade_to_succeeded(client, tok, uid)

    # advance with concurrency=2 — should create 2 more rows (1 already done)
    r2 = _advance_rollout(client, rid, host_ids, canary_size=1, concurrency=2)
    assert r2.status_code == 200

    stmt = select(HostUpgrade).where(HostUpgrade.request_id == rid)
    rows = db.execute(stmt).scalars().all()
    # We had 1 (succeeded) + up to 2 new = 3 total
    non_terminal = [row for row in rows if row.state not in ("SUCCEEDED", "FAILED", "ROLLED_BACK")]
    assert len(non_terminal) <= 2


# ---------------------------------------------------------------------------
# get_rollout
# ---------------------------------------------------------------------------

def test_get_rollout_returns_aggregate(client, db):
    """GET /api/upgrade-rollouts/{request_id} returns aggregate with correct counts."""
    _, host_id = _enroll(client)
    rid = "get-rollout-" + uuid.uuid4().hex[:8]
    _create_rollout(client, [host_id], request_id=rid)

    r = client.get(f"/api/upgrade-rollouts/{rid}", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == rid
    assert body["total"] >= 1


def test_get_rollout_unknown_request_id_returns_empty(client):
    """Unknown request_id returns an empty rollout (no rows found)."""
    r = client.get(f"/api/upgrade-rollouts/nonexistent-rollout-{uuid.uuid4().hex}", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["members"] == []


def test_get_rollout_requires_admin(client):
    r = client.get(f"/api/upgrade-rollouts/{uuid.uuid4()}")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Phase 20 reconcile still works
# ---------------------------------------------------------------------------

def test_rollout_does_not_break_phase20_reconciliation(client, db):
    """Upgrade rows created via rollout still go through Phase 20 heartbeat reconciliation."""
    from app.models.host_upgrade import HostUpgrade

    agent_token, host_id = _enroll(client)
    rid = "phase20-compat-" + uuid.uuid4().hex[:8]
    r = _create_rollout(client, [host_id], request_id=rid)
    assert r.status_code == 201

    m = r.json()["members"][0]
    upgrade_id = m["upgrade_id"]

    for state in ("DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING"):
        client.post(
            f"/api/agent/upgrades/{upgrade_id}/status",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"state": state},
        )

    # Heartbeat with target version triggers reconciliation
    hb = client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": "rollout-host",
            "operating_system": "linux",
            "agent_version": _TARGET_VERSION,
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )
    assert hb.status_code == 200

    row = db.get(HostUpgrade, uuid.UUID(upgrade_id))
    db.refresh(row)
    assert row.state == "SUCCEEDED"
