"""Tests for Phase 18 deployment orchestration service and APIs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models.deployment_revision import DeploymentRevision
from app.models.host_deployment import HostDeployment
from app.repositories.deployment_repository import DeploymentRepository
from app.services import deployment_service
from app.services.deployment_json import validate_ports_json

ADMIN = {"Authorization": "Bearer test-admin-bootstrap-token"}

_PACKAGE_URI = "https://artifacts.example/pkg.tar.gz"
_PACKAGE_SHA = "a" * 64
_MANIFEST_SHA = "b" * 64
_PLAN_HASH = "c" * 64
_PROJECT = "my-app"
_ENV = "staging"


def _mint_token(client) -> str:
    r = client.post("/api/agent/enrollment-tokens", headers=ADMIN)
    assert r.status_code == 200
    return r.json()["enrollment_token"]


def _enroll(client, hostname: str = "deploy-host") -> tuple[str, uuid.UUID]:
    host_id = uuid.uuid4()
    r = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": _mint_token(client),
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "docker_available": True,
            "protocol_version": 1,
        },
    )
    assert r.status_code == 200
    return r.json()["agent_token"], host_id


def _heartbeat(client, agent_token: str, host_id: uuid.UUID):
    return client.post(
        "/api/agent/heartbeat",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "host_id": str(host_id),
            "hostname": "deploy-host",
            "operating_system": "linux",
            "docker_available": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
        },
    )


def _create_deployment(
    client,
    host_id: uuid.UUID,
    *,
    request_id: str = "req-1",
    package_uri: str = _PACKAGE_URI,
    plan_hash: str = _PLAN_HASH,
    ports_json: dict | None = None,
    rollback_revision_id: str | None = None,
):
    body = {
        "host_id": str(host_id),
        "project": _PROJECT,
        "environment": _ENV,
        "request_id": request_id,
        "plan_hash": plan_hash,
        "package_uri": package_uri,
        "package_sha256": _PACKAGE_SHA,
        "package_manifest_sha256": _MANIFEST_SHA,
    }
    if ports_json is not None:
        body["ports_json"] = ports_json
    if rollback_revision_id is not None:
        body["rollback_revision_id"] = rollback_revision_id
    return client.post("/api/deployments", json=body)


def _claim(client, agent_token: str, deployment_id: uuid.UUID):
    return client.post(
        f"/api/agent/deployments/{deployment_id}/claim",
        headers={"Authorization": f"Bearer {agent_token}"},
    )


def _status(client, agent_token: str, deployment_id: uuid.UUID, claim_token: str, **fields):
    return client.post(
        f"/api/agent/deployments/{deployment_id}/status",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"claim_token": claim_token, **fields},
    )


def _succeed_deployment(client, db, agent_token: str, host_id: uuid.UUID, deployment_id: uuid.UUID, revision_id: str):
    claim = _claim(client, agent_token, deployment_id)
    assert claim.status_code == 200
    token = claim.json()["claim_token"]
    for state in ("PREPARING", "TRANSFERRING", "STARTING", "VERIFYING"):
        r = _status(client, agent_token, deployment_id, token, state=state)
        assert r.status_code == 200
    r = _status(
        client,
        agent_token,
        deployment_id,
        token,
        state="SUCCEEDED",
        revision_id=revision_id,
    )
    assert r.status_code == 200
    db.expire_all()
    return r.json()


def test_build_plan_does_not_insert(db, client, enrolled_host):
    before = db.scalar(select(func.count()).select_from(HostDeployment))
    r = client.post(
        "/api/deployments/plan",
        json={
            "host_id": str(enrolled_host.id),
            "project": _PROJECT,
            "environment": _ENV,
            "services": [{"name": "api", "internal_port": 8000, "host_port": 18000, "protocol": "tcp"}],
        },
    )
    assert r.status_code == 200
    assert len(r.json()["plan_hash"]) == 64
    after = db.scalar(select(func.count()).select_from(HostDeployment))
    assert before == after


def test_create_deployment_idempotent(client, enrolled_host):
    r1 = _create_deployment(client, enrolled_host.id, request_id="same-req")
    assert r1.status_code == 201
    r2 = _create_deployment(client, enrolled_host.id, request_id="same-req")
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


def test_concurrent_active_conflict(client, enrolled_host):
    r1 = _create_deployment(client, enrolled_host.id, request_id="req-a")
    assert r1.status_code == 201
    r2 = _create_deployment(client, enrolled_host.id, request_id="req-b")
    assert r2.status_code == 409


def test_claim_token_mismatch_rejected(client, enrolled_host):
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    r = _status(client, agent_token, deployment_id, "wrong-token", state="PREPARING")
    assert r.status_code == 409


def test_succeed_creates_known_good_revision(client, db, enrolled_host):
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    body = _succeed_deployment(client, db, agent_token, host_id, deployment_id, "rev-001")
    assert body["state"] == "SUCCEEDED"
    assert body["result_revision_id"] is not None

    revision = db.get(DeploymentRevision, uuid.UUID(body["result_revision_id"]))
    assert revision is not None
    assert revision.is_known_good is True
    assert revision.revision_id == "rev-001"


def test_rollback_points_at_revision(client, db, enrolled_host):
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    first_id = uuid.UUID(_create_deployment(client, host_id, request_id="deploy-1").json()["id"])
    _succeed_deployment(client, db, agent_token, host_id, first_id, "rev-001")

    second = _create_deployment(client, host_id, request_id="deploy-2")
    assert second.status_code == 201
    second_id = uuid.UUID(second.json()["id"])
    _succeed_deployment(client, db, agent_token, host_id, second_id, "rev-002")

    rollback = client.post(
        f"/api/deployments/{second_id}/rollback",
        json={"request_id": "rollback-1"},
    )
    assert rollback.status_code == 201
    body = rollback.json()
    assert body["rollback_revision_id"] is not None

    revision = db.get(DeploymentRevision, uuid.UUID(body["rollback_revision_id"]))
    assert revision is not None
    assert revision.revision_id == "rev-001"


def test_decommissioned_host_rejected(client, enrolled_host):
    host_id = enrolled_host.id
    client.post(f"/api/hosts/{host_id}/decommission", headers=ADMIN, json={"reason": "test"})
    r = _create_deployment(client, host_id)
    assert r.status_code == 409


def test_package_uri_http_rejected(client, enrolled_host):
    r = _create_deployment(
        client,
        enrolled_host.id,
        package_uri="http://artifacts.example/pkg.tar.gz",
    )
    assert r.status_code == 422


def test_json_allowlist_rejects_secret_keys():
    with pytest.raises(Exception, match="Disallowed key"):
        validate_ports_json(
            {
                "services": [
                    {
                        "name": "api",
                        "internal_port": 8000,
                        "host_port": 18000,
                        "protocol": "tcp",
                        "api_key": "secret-value",
                    }
                ]
            }
        )


def test_heartbeat_includes_pending_deployment(client, enrolled_host):
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    _create_deployment(client, host_id)

    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    pending = hb.json()["pending_deployment"]
    assert pending is not None
    assert pending["project"] == _PROJECT
    assert pending["environment"] == _ENV
    assert pending["state"] == "APPROVED"
    assert pending["package_uri"] == _PACKAGE_URI


def test_heartbeat_with_pending_deployment_remains_additive_for_legacy_agents(
    client, enrolled_host
):
    """v1.4 agents ignore unknown fields; heartbeat must still succeed.

    Qualification: old agent → new development Central continues
    heartbeat/sync and simply never claims deployment work.
    """
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    created = _create_deployment(client, host_id)
    assert created.status_code == 201

    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    body = hb.json()
    assert "pending_probes" in body
    assert body.get("pending_deployment") is not None
    # Job stays unclaimed until a Phase-18-aware agent claims it.
    detail = client.get(f"/api/deployments/{created.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["state"] == "APPROVED"
    assert detail.json().get("claim_token") in (None, "")
    assert detail.json().get("claimed_at") is None


def test_package_uri_allowlist_enforced(client, enrolled_host, monkeypatch):
    monkeypatch.setenv("PORTFORGE_DEPLOYMENT_ARTIFACT_HOSTS", "allowed.example")
    get_settings.cache_clear()
    r = _create_deployment(
        client,
        enrolled_host.id,
        package_uri="https://blocked.example/pkg.tar.gz",
    )
    assert r.status_code == 422
    get_settings.cache_clear()


def test_service_build_plan_unit(db, enrolled_host):
    plan = deployment_service.build_plan(
        host_id=enrolled_host.id,
        project=_PROJECT,
        environment=_ENV,
        services=[{"name": "api", "internal_port": 8000}],
    )
    assert plan["plan_hash"]
    assert db.scalar(select(func.count()).select_from(HostDeployment)) == 0


def test_heartbeat_redelivers_expired_in_progress_for_reclaim(client, enrolled_host, db):
    """After lease expiry, STARTING deployments must reappear on heartbeat for reclaim."""
    from datetime import datetime, timedelta, timezone

    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    claim = _claim(client, agent_token, deployment_id)
    token = claim.json()["claim_token"]
    for state in ("PREPARING", "TRANSFERRING", "STARTING"):
        assert _status(client, agent_token, deployment_id, token, state=state).status_code == 200

    # While lease is live, pending should not re-offer the in-progress job as APPROVED-only —
    # but STARTING with live lease is not redelivered (agent still holds it).
    hb_live = _heartbeat(client, agent_token, host_id)
    assert hb_live.status_code == 200
    # No second APPROVED; in-progress with live lease is held — pending may be null
    # (only APPROVED or expired non-terminal are eligible).
    pending_live = hb_live.json().get("pending_deployment")
    assert pending_live is None or pending_live.get("state") != "APPROVED"

    # Expire lease
    row = db.get(HostDeployment, deployment_id)
    row.claim_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()

    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    pending = hb.json()["pending_deployment"]
    assert pending is not None
    assert pending["deployment_id"] == str(deployment_id)
    assert pending["state"] == "STARTING"


# ---------------------------------------------------------------------------
# B. SUCCEEDED without/with revision_id + idempotency
# ---------------------------------------------------------------------------

def test_succeeded_without_revision_id_is_422(client, enrolled_host):
    """SUCCEEDED state without a revision_id must be rejected with 422."""
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    claim = _claim(client, agent_token, deployment_id)
    assert claim.status_code == 200
    token = claim.json()["claim_token"]
    for state in ("PREPARING", "TRANSFERRING", "STARTING", "VERIFYING"):
        r = _status(client, agent_token, deployment_id, token, state=state)
        assert r.status_code == 200, f"Unexpected failure for {state}: {r.text}"
    # SUCCEEDED without revision_id → 422
    r = _status(client, agent_token, deployment_id, token, state="SUCCEEDED")
    assert r.status_code == 422


def test_succeeded_with_revision_id_is_200(client, db, enrolled_host):
    """SUCCEEDED with a valid revision_id must be accepted with 200."""
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    body = _succeed_deployment(client, db, agent_token, host_id, deployment_id, "rev-test")
    assert body["state"] == "SUCCEEDED"
    assert body["result_revision_id"] is not None


def test_second_succeeded_on_terminal_is_409(client, db, enrolled_host):
    """After a deployment is SUCCEEDED (terminal), any further status update → 409."""
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    _succeed_deployment(client, db, agent_token, host_id, deployment_id, "rev-done")
    # Terminal check happens before claim-token verification; any token value triggers 409.
    r = _status(
        client, agent_token, deployment_id, "any-token",
        state="SUCCEEDED", revision_id="rev-done-2",
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# C. ports_json delivered on pending_deployment
# ---------------------------------------------------------------------------

def test_heartbeat_delivers_ports_json_on_pending_deployment(client, enrolled_host):
    """ports_json stored on creation must be forwarded in the heartbeat response."""
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    ports = {
        "services": [
            {"name": "api", "internal_port": 8000, "host_port": 18000, "protocol": "tcp"}
        ]
    }
    _create_deployment(client, host_id, ports_json=ports)
    hb = _heartbeat(client, agent_token, host_id)
    assert hb.status_code == 200
    pending = hb.json()["pending_deployment"]
    assert pending is not None
    assert pending["ports_json"] == ports


# ---------------------------------------------------------------------------
# F. Claim lease edge-cases
# ---------------------------------------------------------------------------

def test_stale_claim_token_rejected_after_expiry(client, db, enrolled_host):
    """A claim token that has expired must be rejected for status updates (→ 409)."""
    from datetime import timedelta

    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    claim = _claim(client, agent_token, deployment_id)
    assert claim.status_code == 200
    token = claim.json()["claim_token"]

    # Manually expire the lease in the DB.
    dep = db.get(HostDeployment, deployment_id)
    dep.claim_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db.flush()
    db.commit()

    r = _status(client, agent_token, deployment_id, token, state="PREPARING")
    assert r.status_code == 409


def test_new_claim_after_expiry_rejects_old_token(client, db, enrolled_host):
    """After lease expires and a new claim is made, the old token is rejected."""
    from datetime import timedelta

    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])

    # First claim.
    claim1 = _claim(client, agent_token, deployment_id)
    assert claim1.status_code == 200
    old_token = claim1.json()["claim_token"]

    # Expire the lease.
    dep = db.get(HostDeployment, deployment_id)
    dep.claim_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db.flush()
    db.commit()

    # Second claim issues a new token.
    claim2 = _claim(client, agent_token, deployment_id)
    assert claim2.status_code == 200
    new_token = claim2.json()["claim_token"]
    assert new_token != old_token

    # Old token must now be rejected.
    r = _status(client, agent_token, deployment_id, old_token, state="PREPARING")
    assert r.status_code == 409


def test_claim_after_terminal_deployment_is_409(client, db, enrolled_host):
    """Claiming a deployment that is already in a terminal state must return 409."""
    agent_token, host_id = enrolled_host.agent_token, enrolled_host.id
    deployment_id = uuid.UUID(_create_deployment(client, host_id).json()["id"])
    _succeed_deployment(client, db, agent_token, host_id, deployment_id, "rev-1")
    r = _claim(client, agent_token, deployment_id)
    assert r.status_code == 409
