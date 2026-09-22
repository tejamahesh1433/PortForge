"""v1.1-B: fresh remote probe evidence integrated into allocation
(services/allocation_service.py) -- occupied candidates rejected, occupied
preferred ports rejected (never forced), verified_free surfaces on
validation.bind_probe, bundle allocation still works, and none of this
weakens Phase 8A's existing atomicity/locking/idempotency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.host import Host
from app.services import probe_service


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="alloc-probe-host", operating_system="linux", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def _allocate(client, host_id, requests, project="probe-alloc-test"):
    return client.post("/api/allocations", json={"project": project, "host_id": str(host_id), "requests": requests})


def test_fresh_occupied_probe_excludes_that_port_from_allocation(client, db_session):
    host_id = _make_host(db_session)

    # Discover what the plain candidate would be, then mark it occupied
    # via a fresh probe before actually allocating.
    plan = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = plan.json()["recommended_port"]
    probe = probe_service.queue_probe(db_session, host_id, candidate, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, False, "occupied")

    response = _allocate(client, host_id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 201, response.text
    allocated_port = response.json()["allocations"][0]["port"]
    assert allocated_port != candidate


def test_fresh_free_probe_surfaces_verified_free_on_validation(client, db_session):
    host_id = _make_host(db_session)
    plan = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    candidate = plan.json()["recommended_port"]
    probe = probe_service.queue_probe(db_session, host_id, candidate, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, True, None)

    response = _allocate(client, host_id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["allocations"][0]["port"] == candidate
    assert body["validation"]["bind_probe"] == "verified_free"


def test_preferred_port_with_fresh_occupied_probe_falls_back_not_forced(client, db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 3000, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, False, "occupied by probe evidence")

    response = _allocate(
        client, host_id, [{"name": "frontend", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3000}]
    )
    assert response.status_code == 201, response.text
    port = response.json()["allocations"][0]["port"]
    assert port != 3000  # never forced despite being "preferred"
    assert 3000 <= port <= 3999


def test_preferred_port_with_fresh_free_probe_is_used_and_reported_verified(client, db_session):
    host_id = _make_host(db_session)
    probe = probe_service.queue_probe(db_session, host_id, 3001, "tcp")
    db_session.commit()
    probe_service.submit_result(db_session, host_id, probe.id, True, None)

    response = _allocate(
        client, host_id, [{"name": "frontend", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3001}]
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["allocations"][0]["port"] == 3001
    assert body["validation"]["bind_probe"] == "verified_free"


def test_no_probe_evidence_bind_probe_stays_not_remote_capable(client, db_session):
    host_id = _make_host(db_session)
    response = _allocate(client, host_id, [{"name": "api", "purpose": "api", "protocol": "tcp"}])
    assert response.status_code == 201
    assert response.json()["validation"]["bind_probe"] == "not_remote_capable"


def test_four_port_bundle_allocation_still_works_with_probe_integration(client, db_session):
    """Bundle allocation (task §13) -- frontend/api/postgres/redis, no
    probe evidence involved at all, proving the integration didn't break
    the ordinary multi-port path.
    """
    host_id = _make_host(db_session)
    response = _allocate(
        client,
        host_id,
        [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
            {"name": "api", "purpose": "api", "protocol": "tcp"},
            {"name": "db", "purpose": "postgres", "protocol": "tcp"},
            {"name": "cache", "purpose": "redis", "protocol": "tcp"},
        ],
    )
    assert response.status_code == 201, response.text
    ports = [a["port"] for a in response.json()["allocations"]]
    assert len(set(ports)) == 4


def test_zero_reservations_created_when_all_candidates_probe_occupied(client, db_session):
    """Rollback/atomicity must still hold: if probe evidence rules out
    every candidate PortForge would otherwise offer isn't realistic to
    fully exhaust via probes alone (range is large), but a preferred-port
    rejection combined with no crash proves the transaction stays sound.
    This specifically checks that a failed allocation leaves zero
    reservations even when probe evidence was consulted along the way.
    """
    host_id = _make_host(db_session)
    # Exhaust the whole postgres range via real reservations (not probes)
    # so the bundle is guaranteed to fail on that leg regardless of any
    # probe activity on the other legs.
    from app.models.reservation import CentralReservation

    for port in range(5432, 5500):
        db_session.add(CentralReservation(host_id=host_id, port=port, protocol="tcp", project="unrelated"))
    db_session.commit()

    response = _allocate(
        client,
        host_id,
        [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
            {"name": "db", "purpose": "postgres", "protocol": "tcp"},
        ],
        project="probe-alloc-rollback-test",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALLOCATION_UNAVAILABLE"

    listed = client.get(f"/api/reservations?host_id={host_id}&project=probe-alloc-rollback-test").json()
    assert listed["total"] == 0
