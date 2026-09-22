"""v1.1-B: HTTP-level tests for probe delivery (via heartbeat) and result
submission (POST /api/agent/probes/result), including cross-host
authorization (task §8).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services import probe_service


def _heartbeat(client, agent_token, host_id, protocol_version=None):
    body = {
        "host_id": str(host_id),
        "hostname": "test-enrolled-host",
        "operating_system": "linux",
        "docker_available": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if protocol_version is not None:
        body["protocol_version"] = protocol_version
    return client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {agent_token}"}, json=body)


def test_heartbeat_with_no_pending_probes_has_empty_list(client, enrolled_host):
    response = _heartbeat(client, enrolled_host.agent_token, enrolled_host.id)
    assert response.status_code == 200
    assert response.json()["pending_probes"] == []


def test_heartbeat_delivers_queued_probe(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9100, "tcp", "0.0.0.0")
    db_session.commit()

    response = _heartbeat(client, enrolled_host.agent_token, enrolled_host.id)
    assert response.status_code == 200
    pending = response.json()["pending_probes"]
    assert len(pending) == 1
    assert pending[0]["probe_id"] == str(probe.id)
    assert pending[0]["port"] == 9100
    assert pending[0]["protocol"] == "tcp"
    assert pending[0]["bind_address"] == "0.0.0.0"


def test_heartbeat_does_not_redeliver_already_delivered_probe(client, enrolled_host, db_session):
    probe_service.queue_probe(db_session, enrolled_host.id, 9101, "tcp", "0.0.0.0")
    db_session.commit()

    first = _heartbeat(client, enrolled_host.agent_token, enrolled_host.id)
    assert len(first.json()["pending_probes"]) == 1

    second = _heartbeat(client, enrolled_host.agent_token, enrolled_host.id)
    assert len(second.json()["pending_probes"]) == 0


def test_submit_probe_result_free(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9102, "tcp", "0.0.0.0")
    db_session.commit()
    _heartbeat(client, enrolled_host.agent_token, enrolled_host.id)

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json={"host_id": str(enrolled_host.id), "probe_id": str(probe.id), "available": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    evidence = probe_service.get_probe_evidence(db_session, enrolled_host.id, 9102, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.VERIFIED_FREE


def test_submit_probe_result_occupied(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9103, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json={"host_id": str(enrolled_host.id), "probe_id": str(probe.id), "available": False, "reason": "in use"},
    )
    assert response.status_code == 200
    evidence = probe_service.get_probe_evidence(db_session, enrolled_host.id, 9103, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.VERIFIED_OCCUPIED


def test_submit_probe_result_requires_authentication(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9104, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        json={"host_id": str(enrolled_host.id), "probe_id": str(probe.id), "available": True},
    )
    assert response.status_code == 401


def test_submit_probe_result_host_id_mismatch_rejected(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9105, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json={"host_id": str(uuid.uuid4()), "probe_id": str(probe.id), "available": True},
    )
    assert response.status_code == 403


def _enroll_second_host(client) -> "tuple[str, str]":
    mint = client.post("/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"})
    token = mint.json()["enrollment_token"]
    host_id = str(uuid.uuid4())
    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "host_id": host_id,
            "hostname": "second-host",
            "operating_system": "linux",
            "docker_available": True,
        },
    )
    assert enroll.status_code == 200, enroll.text
    return enroll.json()["agent_token"], host_id


def test_host_a_cannot_receive_host_b_pending_probe(client, enrolled_host, db_session):
    """Task §8: Host A cannot receive Host B's pending probe. Since
    delivery is scoped by the AUTHENTICATED host_id (not a query param),
    host B's own heartbeat simply never sees host A's probe at all.
    """
    other_token, other_host_id = _enroll_second_host(client)
    probe_service.queue_probe(db_session, enrolled_host.id, 9106, "tcp", "0.0.0.0")
    db_session.commit()

    response = _heartbeat(client, other_token, uuid.UUID(other_host_id))
    assert response.json()["pending_probes"] == []


def test_host_a_cannot_submit_result_for_host_b_probe(client, enrolled_host, db_session):
    """Task §8: Host A cannot submit a result for Host B's probe."""
    other_token, other_host_id = _enroll_second_host(client)
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9107, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"host_id": other_host_id, "probe_id": str(probe.id), "available": True},
    )
    # host_id in body (other_host_id) matches the authenticated credential,
    # but the PROBE itself belongs to a different host -- service-layer
    # ownership check must still reject this.
    assert response.status_code == 403

    evidence = probe_service.get_probe_evidence(db_session, enrolled_host.id, 9107, "tcp", "0.0.0.0")
    assert evidence.bind_probe == probe_service.NOT_REMOTE_CAPABLE  # untouched


def test_invalid_revoked_credential_cannot_submit_result(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9108, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": "Bearer totally-invalid-token"},
        json={"host_id": str(enrolled_host.id), "probe_id": str(probe.id), "available": True},
    )
    assert response.status_code == 401


def test_unknown_probe_id_returns_404(client, enrolled_host):
    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json={"host_id": str(enrolled_host.id), "probe_id": str(uuid.uuid4()), "available": True},
    )
    assert response.status_code == 404


def test_malformed_result_payload_rejected(client, enrolled_host, db_session):
    probe = probe_service.queue_probe(db_session, enrolled_host.id, 9109, "tcp", "0.0.0.0")
    db_session.commit()

    response = client.post(
        "/api/agent/probes/result",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json={"host_id": str(enrolled_host.id), "probe_id": "not-a-uuid", "available": True},
    )
    assert response.status_code == 422
