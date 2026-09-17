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
