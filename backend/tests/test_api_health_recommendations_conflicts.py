"""HTTP-level tests for /api/health, /api/recommendations, /api/conflicts."""
import uuid
from datetime import datetime, timezone

from app.models.host import Host


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["service"] == "portforge"


def test_health_exposes_protocol_version(client):
    """v1.1-A: unauthenticated and always present, so `portforge doctor`
    can compare it against the agent's own canonical protocol_version
    without needing an enrolled credential first.
    """
    from app.services.compatibility_service import PROTOCOL_VERSION

    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol_version"] == PROTOCOL_VERSION
    assert isinstance(body["protocol_version"], int)


def test_health_never_exposes_secrets(client):
    response = client.get("/api/health")
    text = response.text
    assert "test-admin-bootstrap-token" not in text
    assert "password" not in text.lower()
    assert "token" not in text.lower()


def test_recommendation_is_labeled_central_suggestion(client, db_session):
    host_id = _make_host(db_session)
    response = client.get(f"/api/recommendations?host_id={host_id}&service_type=api")
    assert response.status_code == 200
    body = response.json()
    assert body["verification"] == "central_suggestion"
    assert body["recommended_port"] is not None


def test_recommendation_unknown_host(client):
    response = client.get(f"/api/recommendations?host_id={uuid.uuid4()}&service_type=api")
    assert response.status_code == 200  # no observations for that host -> full range free
    assert response.json()["recommended_port"] is not None


def test_conflicts_empty_by_default(client):
    response = client.get("/api/conflicts")
    assert response.status_code == 200
    assert response.json() == []


def test_health_check_never_creates_a_probe(client, db_session):
    """v1.1-B task §25: `portforge doctor`'s central_connectivity check
    hits /api/health -- this must never be a path that creates a
    HostProbe row (only GET /api/recommendations and POST /api/allocations
    can, per services/probe_service.py::queue_probe's two real callers).
    """
    from app.models.host_probe import HostProbe

    for _ in range(3):
        assert client.get("/api/health").status_code == 200

    assert db_session.query(HostProbe).count() == 0
