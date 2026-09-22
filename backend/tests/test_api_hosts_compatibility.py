"""v1.1-D: Host protocol-compatibility and probe-capability display tests.

See docs/v1.1/v1.1-d-data-audit.md "Host protocol compatibility" for why
this required a small, user-approved reversal of v1.1-A's "never
persisted" decision -- protocol_version is now stored (raw integer only),
compatibility is always recomputed live from it via the existing,
unchanged compatibility_service.evaluate_protocol_compatibility().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _enroll_via_api(client: TestClient, protocol_version=None) -> tuple[str, str]:
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    enrollment_token = mint_response.json()["enrollment_token"]
    host_id = str(uuid.uuid4())

    payload = {
        "enrollment_token": enrollment_token,
        "host_id": host_id,
        "hostname": "compat-test-host",
        "operating_system": "linux",
        "docker_available": True,
    }
    if protocol_version is not None:
        payload["protocol_version"] = protocol_version

    enroll_response = client.post("/api/agent/enroll", json=payload)
    assert enroll_response.status_code == 200, enroll_response.text
    return enroll_response.json()["agent_token"], host_id


def _heartbeat(client: TestClient, agent_token, host_id, protocol_version=None):
    payload = {
        "host_id": host_id,
        "hostname": "compat-test-host",
        "operating_system": "linux",
        "docker_available": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if protocol_version is not None:
        payload["protocol_version"] = protocol_version
    return client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {agent_token}"}, json=payload)


def test_compatible_agent_shows_compatible(client: TestClient):
    from app.services.compatibility_service import PROTOCOL_VERSION

    agent_token, host_id = _enroll_via_api(client, protocol_version=PROTOCOL_VERSION)

    response = client.get(f"/api/hosts/{host_id}")
    body = response.json()
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert body["protocol_compatibility"] == "compatible"


def test_mismatched_agent_shows_warning_not_broken(client: TestClient):
    from app.services.compatibility_service import PROTOCOL_VERSION

    mismatched = PROTOCOL_VERSION + 1
    agent_token, host_id = _enroll_via_api(client, protocol_version=mismatched)

    response = client.get(f"/api/hosts/{host_id}")
    body = response.json()
    assert body["protocol_version"] == mismatched
    assert body["protocol_compatibility"] == "warning"


def test_legacy_agent_with_no_protocol_version_shows_unknown_not_broken(client: TestClient):
    """A pre-v1.1-A agent never sends protocol_version at all -- must
    display honestly as 'unknown', never as an error/broken state.
    """
    agent_token, host_id = _enroll_via_api(client, protocol_version=None)

    response = client.get(f"/api/hosts/{host_id}")
    assert response.status_code == 200  # never blocks/errors on missing metadata
    body = response.json()
    assert body["protocol_version"] is None
    assert body["protocol_compatibility"] == "unknown"


def test_heartbeat_updates_stored_protocol_version(client: TestClient):
    from app.services.compatibility_service import PROTOCOL_VERSION

    agent_token, host_id = _enroll_via_api(client, protocol_version=None)
    assert client.get(f"/api/hosts/{host_id}").json()["protocol_compatibility"] == "unknown"

    _heartbeat(client, agent_token, host_id, protocol_version=PROTOCOL_VERSION)

    body = client.get(f"/api/hosts/{host_id}").json()
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert body["protocol_compatibility"] == "compatible"


def test_heartbeat_omitting_protocol_version_does_not_erase_previous_value(client: TestClient):
    """A later heartbeat that doesn't carry protocol_version (a flaky
    transport, not a downgrade) must not wipe out a previously-known
    value -- see HostRepository.upsert's own docstring.
    """
    from app.services.compatibility_service import PROTOCOL_VERSION

    agent_token, host_id = _enroll_via_api(client, protocol_version=PROTOCOL_VERSION)
    _heartbeat(client, agent_token, host_id, protocol_version=None)

    body = client.get(f"/api/hosts/{host_id}").json()
    assert body["protocol_version"] == PROTOCOL_VERSION  # preserved, not erased


def test_compatibility_never_blocks_enroll_or_heartbeat(client: TestClient):
    from app.services.compatibility_service import PROTOCOL_VERSION

    agent_token, host_id = _enroll_via_api(client, protocol_version=PROTOCOL_VERSION + 99)
    response = _heartbeat(client, agent_token, host_id, protocol_version=PROTOCOL_VERSION + 99)
    assert response.status_code == 200  # advisory only, never gates operation


def test_probe_capability_unknown_when_never_probed(client: TestClient):
    agent_token, host_id = _enroll_via_api(client)
    response = client.get(f"/api/hosts/{host_id}/diagnostics")
    assert response.json()["probe_capability"] == "unknown"


def test_probe_capability_supported_after_a_completed_probe(client: TestClient, db_session):
    from app.services import probe_service

    agent_token, host_id = _enroll_via_api(client)
    host_uuid = uuid.UUID(host_id)
    probe = probe_service.queue_probe(db_session, host_uuid, 19999, "tcp")
    probe_service.claim_pending_probes(db_session, host_uuid)
    probe_service.submit_result(db_session, host_uuid, probe.id, True, None)

    response = client.get(f"/api/hosts/{host_id}/diagnostics")
    assert response.json()["probe_capability"] == "supported"


def test_probe_capability_unavailable_offline_for_stale_host(client: TestClient, db_session):
    from app.models.host import Host

    agent_token, host_id = _enroll_via_api(client)
    host = db_session.get(Host, uuid.UUID(host_id))
    host.last_seen = datetime(2020, 1, 1, tzinfo=timezone.utc)  # force OFFLINE
    db_session.commit()

    response = client.get(f"/api/hosts/{host_id}/diagnostics")
    assert response.json()["probe_capability"] == "unavailable_offline"


def test_list_hosts_never_errors_on_missing_protocol_metadata(client: TestClient, db_session):
    """The bulk hosts list must render legacy hosts (no protocol_version
    at all, inserted directly like existing Phase 7 fixtures do) without
    special-casing -- this must never become an empty-success/error state.
    """
    from app.models.host import Host

    db_session.add(
        Host(
            id=uuid.uuid4(),
            hostname="legacy-host",
            operating_system="linux",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    response = client.get("/api/hosts")
    assert response.status_code == 200
    legacy = next(h for h in response.json()["items"] if h["hostname"] == "legacy-host")
    assert legacy["protocol_version"] is None
    assert legacy["protocol_compatibility"] == "unknown"
