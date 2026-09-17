"""Regression coverage for the Phase 6 physical-observation preservation
repair: Central must store multiple *physical* observations for the same
(host, protocol, port) as distinct rows when their bind addresses differ
-- e.g. a dual-stack service listening on both `0.0.0.0` and `::`, or a
service additionally bound to `127.0.0.1` -- rather than colliding on a
unique constraint or silently overwriting one address with another. See
`app/models/port_observation.py`'s `uq_current_port_binding` constraint
(host_id, port, protocol, bind_address) and
`services/ingestion_service.py`'s `_binding_key()`, both of which already
key identity on the full 4-tuple including bind_address -- this file
proves that design holds end-to-end through the real HTTP ingestion path.

Uses the shared `db`/`enrolled_host`/`client` fixtures from conftest.py
(the same disposable-per-test-database, real-HTTP-enrollment
infrastructure every other backend test in this suite already uses) --
no mocking of the database or the ingestion path, no hardcoded database
name, and cleanup is automatic via `db_session`'s transaction-rollback
fixture regardless of whether an assertion here fails.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.port_observation import CurrentPortObservation, PortObservationEvent

from .conftest import EnrolledHost

PORT = 8000
PROTOCOL = "tcp"
THREE_ADDRESSES = {"0.0.0.0", "::", "127.0.0.1"}


def _submit(client: TestClient, host: EnrolledHost, observations: list[dict]) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "scan_id": str(uuid.uuid4()),
        "host_id": str(host.id),
        "observed_at": now,
        "observations": observations,
    }
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {host.agent_token}"},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _observation(bind_address: str, port: int = PORT, protocol: str = PROTOCOL) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "port": port,
        "protocol": protocol,
        "bind_address": bind_address,
        "state": "ACTIVE",  # matches the real agent's PortState enum value -- see module docstring
        "source": "docker",
        "project_name": "test_project",
        "first_seen": now,
        "last_seen": now,
    }


def test_central_distinct_bind_address_handling(client: TestClient, db: Session, enrolled_host: EnrolledHost):
    """Three distinct bind addresses on the SAME (host, protocol, port)
    must all survive ingestion as distinct physical observations -- not
    weakened to different ports, per the regression requirement.
    """
    observations = [_observation(addr) for addr in sorted(THREE_ADDRESSES)]

    result = _submit(client, enrolled_host, observations)
    assert result["accepted"] is True
    assert result["observations_processed"] == 3
    assert result["appeared"] == 3

    current_obs = (
        db.query(CurrentPortObservation)
        .filter(
            CurrentPortObservation.host_id == enrolled_host.id,
            CurrentPortObservation.port == PORT,
            CurrentPortObservation.protocol == PROTOCOL,
        )
        .all()
    )
    assert len(current_obs) == 3
    assert {obs.bind_address for obs in current_obs} == THREE_ADDRESSES

    events = (
        db.query(PortObservationEvent)
        .filter(
            PortObservationEvent.host_id == enrolled_host.id,
            PortObservationEvent.port == PORT,
            PortObservationEvent.protocol == PROTOCOL,
        )
        .all()
    )
    assert len(events) == 3
    assert {event.bind_address for event in events} == THREE_ADDRESSES


def test_distinct_bind_addresses_survive_a_second_unchanged_snapshot(
    client: TestClient, db: Session, enrolled_host: EnrolledHost
):
    """A routine re-scan reporting the exact same three bindings again
    must not merge/drop any of them, and must not record new history
    events for the unchanged re-confirmation (see ingestion_service.py's
    "current state vs. history" strategy).
    """
    observations = [_observation(addr) for addr in sorted(THREE_ADDRESSES)]
    _submit(client, enrolled_host, observations)

    second = _submit(client, enrolled_host, [_observation(addr) for addr in sorted(THREE_ADDRESSES)])
    assert second["appeared"] == 0
    assert second["changed"] == 0
    assert second["disappeared"] == 0

    current_obs = (
        db.query(CurrentPortObservation)
        .filter(
            CurrentPortObservation.host_id == enrolled_host.id,
            CurrentPortObservation.port == PORT,
            CurrentPortObservation.protocol == PROTOCOL,
        )
        .all()
    )
    assert len(current_obs) == 3  # still all three, not collapsed


def test_one_bind_address_disappearing_leaves_the_others_intact(
    client: TestClient, db: Session, enrolled_host: EnrolledHost
):
    """Full-snapshot semantics apply per-binding: dropping one address
    from a later submission removes only that physical observation, not
    its siblings on the same (host, protocol, port).
    """
    _submit(client, enrolled_host, [_observation(addr) for addr in sorted(THREE_ADDRESSES)])

    remaining = {"0.0.0.0", "::"}
    result = _submit(client, enrolled_host, [_observation(addr) for addr in sorted(remaining)])
    assert result["disappeared"] == 1

    current_obs = (
        db.query(CurrentPortObservation)
        .filter(
            CurrentPortObservation.host_id == enrolled_host.id,
            CurrentPortObservation.port == PORT,
            CurrentPortObservation.protocol == PROTOCOL,
        )
        .all()
    )
    assert {obs.bind_address for obs in current_obs} == remaining


def test_cross_host_isolation_same_port_and_protocol(
    client: TestClient, db: Session, enrolled_host: EnrolledHost
):
    """host A / TCP / 8000 and host B / TCP / 8000 must remain
    independent -- submitting for one must never affect, merge with, or
    be visible under the other's current observations.
    """
    host_a = enrolled_host

    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    assert mint_response.status_code == 200
    host_b_id = uuid.uuid4()
    enroll_b = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": mint_response.json()["enrollment_token"],
            "host_id": str(host_b_id),
            "hostname": "test-enrolled-host-b",
            "operating_system": "windows",
            "docker_available": False,
        },
    )
    assert enroll_b.status_code == 200
    host_b = EnrolledHost(id=host_b_id, agent_token=enroll_b.json()["agent_token"], hostname="test-enrolled-host-b")

    _submit(client, host_a, [_observation("0.0.0.0")])
    _submit(client, host_b, [_observation("0.0.0.0")])

    obs_a = (
        db.query(CurrentPortObservation)
        .filter(CurrentPortObservation.host_id == host_a.id, CurrentPortObservation.port == PORT)
        .all()
    )
    obs_b = (
        db.query(CurrentPortObservation)
        .filter(CurrentPortObservation.host_id == host_b.id, CurrentPortObservation.port == PORT)
        .all()
    )

    assert len(obs_a) == 1
    assert len(obs_b) == 1
    assert obs_a[0].host_id != obs_b[0].host_id
    assert obs_a[0].id != obs_b[0].id  # distinct rows, not shared/aliased

    # Releasing/dropping host A's binding must not touch host B's.
    result = _submit(client, host_a, [])
    assert result["disappeared"] == 1

    obs_a_after = (
        db.query(CurrentPortObservation)
        .filter(CurrentPortObservation.host_id == host_a.id, CurrentPortObservation.port == PORT)
        .all()
    )
    obs_b_after = (
        db.query(CurrentPortObservation)
        .filter(CurrentPortObservation.host_id == host_b.id, CurrentPortObservation.port == PORT)
        .all()
    )
    assert obs_a_after == []
    assert len(obs_b_after) == 1  # untouched
