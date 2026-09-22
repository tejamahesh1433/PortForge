"""v1.1-B: fresh remote probe evidence integrated into
GET /api/recommendations -- verification/bind_probe fields, occupied
candidates skipped, expired/missing evidence never treated as free.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models.host import Host
from app.services import probe_service


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="rec-probe-host", operating_system="linux", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def test_no_probe_evidence_stays_central_suggestion(client, db_session):
    host_id = _make_host(db_session)
    response = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    assert response.status_code == 200
    body = response.json()
    assert body["verification"] == "central_suggestion"
    assert body["bind_probe"] == "not_remote_capable"


def test_no_probe_evidence_queues_one_for_next_time(client, db_session):
    host_id = _make_host(db_session)
    response = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = response.json()["recommended_port"]

    evidence = probe_service.get_probe_evidence(db_session, host_id, candidate, "tcp")
    assert evidence.probe is not None
    assert evidence.probe.status == "PENDING"


def test_fresh_verified_free_upgrades_to_locally_verified(client, db_session):
    host_id = _make_host(db_session)
    first = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = first.json()["recommended_port"]

    probe = probe_service.queue_probe(db_session, host_id, candidate, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, True, None)

    second = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    body = second.json()
    assert body["recommended_port"] == candidate
    assert body["verification"] == "locally_verified"
    assert body["bind_probe"] == "verified_free"


def test_fresh_verified_occupied_candidate_is_skipped(client, db_session):
    host_id = _make_host(db_session)
    first = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = first.json()["recommended_port"]

    probe = probe_service.queue_probe(db_session, host_id, candidate, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, False, "occupied")

    second = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    body = second.json()
    assert body["recommended_port"] != candidate
    assert body["recommended_port"] is not None


def test_expired_probe_evidence_never_treated_as_fresh(client, db_session):
    host_id = _make_host(db_session)
    first = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = first.json()["recommended_port"]

    probe = probe_service.queue_probe(db_session, host_id, candidate, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, True, None)
    probe.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    second = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    body = second.json()
    # Still recommends the port (expired evidence isn't treated as
    # occupied either -- see task §9's symmetric requirement), but must
    # NOT claim it as locally_verified anymore.
    assert body["recommended_port"] == candidate
    assert body["verification"] == "central_suggestion"
    assert body["bind_probe"] == "expired"


def test_unknown_host_recommendation_still_works_no_probe_crash(client):
    """Regression guard for the real bug found during implementation:
    probe queuing must never break a recommendation for a not-yet-enrolled
    host_id (test_recommendation_unknown_host's existing v1.0 behavior).
    """
    response = client.get(f"/api/recommendations?host_id={uuid.uuid4()}&service_type=api")
    assert response.status_code == 200
    assert response.json()["recommended_port"] is not None
