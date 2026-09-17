"""HTTP-level tests for GET /api/hosts, /api/ports, /api/projects."""
import uuid
from datetime import datetime, timezone

from app.models.host import Host
from app.models.port_observation import CurrentPortObservation


def _make_host(db, hostname="test-host") -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname=hostname, operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def _add_observation(db, host_id, port, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        host_id=host_id,
        port=port,
        protocol="tcp",
        bind_address="0.0.0.0",
        state="ACTIVE",
        source="process",
        process_name="app.exe",
        first_seen=now,
        last_seen=now,
        observed_at=now,
        scan_id=uuid.uuid4(),
    )
    defaults.update(overrides)
    db.add(CurrentPortObservation(**defaults))
    db.commit()


def test_list_hosts(client, db_session):
    _make_host(db_session, "host-a")
    _make_host(db_session, "host-b")
    response = client.get("/api/hosts")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_get_host_by_id(client, db_session):
    host_id = _make_host(db_session, "host-a")
    response = client.get(f"/api/hosts/{host_id}")
    assert response.status_code == 200
    assert response.json()["hostname"] == "host-a"


def test_get_host_not_found(client):
    response = client.get(f"/api/hosts/{uuid.uuid4()}")
    assert response.status_code == 404


def test_duplicate_hostname_different_uuids_both_stored(client, db_session):
    """Two hosts may legitimately share a hostname -- hostname is not a
    unique key.
    """
    id_a = _make_host(db_session, "shared-name")
    id_b = _make_host(db_session, "shared-name")
    assert id_a != id_b

    response = client.get("/api/hosts")
    hostnames = [h["hostname"] for h in response.json()["items"]]
    assert hostnames.count("shared-name") == 2


def test_get_host_ports(client, db_session):
    host_id = _make_host(db_session)
    _add_observation(db_session, host_id, 8000)
    _add_observation(db_session, host_id, 3000)

    response = client.get(f"/api/hosts/{host_id}/ports")
    assert response.status_code == 200
    ports = {p["port"] for p in response.json()}
    assert ports == {8000, 3000}


def test_hosts_pagination(client, db_session):
    for i in range(5):
        _make_host(db_session, f"host-{i}")
    response = client.get("/api/hosts?limit=2&offset=0")
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


# ---------------------------------------------------------------------------
# Multi-host port query: "where is port X being used"
# ---------------------------------------------------------------------------


def test_same_port_multiple_hosts_all_returned(client, db_session):
    host_a = _make_host(db_session, "host-a")
    host_b = _make_host(db_session, "host-b")
    _add_observation(db_session, host_a, 8000, project_name="project-a")
    _add_observation(db_session, host_b, 8000, project_name="project-b")

    response = client.get("/api/ports?port=8000")
    body = response.json()
    assert body["total"] == 2
    projects = {item["project_name"] for item in body["items"]}
    assert projects == {"project-a", "project-b"}


def test_ports_filter_by_project(client, db_session):
    host_id = _make_host(db_session)
    _add_observation(db_session, host_id, 8000, project_name="ocrforge")
    _add_observation(db_session, host_id, 3000, project_name="job-trailers-resume")

    response = client.get("/api/ports?project=ocrforge")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["project_name"] == "ocrforge"


def test_ports_filter_by_source(client, db_session):
    host_id = _make_host(db_session)
    _add_observation(db_session, host_id, 8000, source="docker")
    _add_observation(db_session, host_id, 3000, source="process")

    response = client.get("/api/ports?source=docker")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "docker"


def test_ports_filter_by_purpose(client, db_session):
    host_id = _make_host(db_session)
    _add_observation(db_session, host_id, 3306, purpose="mysql", category="database")

    response = client.get("/api/ports?purpose=database")
    body = response.json()
    assert body["total"] == 1


def test_ports_include_hostname(client, db_session):
    host_id = _make_host(db_session, "my-machine")
    _add_observation(db_session, host_id, 8000)
    response = client.get("/api/ports?port=8000")
    assert response.json()["items"][0]["host_hostname"] == "my-machine"


def test_ports_pagination(client, db_session):
    host_id = _make_host(db_session)
    for i in range(10):
        _add_observation(db_session, host_id, 9000 + i)
    response = client.get("/api/ports?limit=3&offset=0")
    body = response.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def test_projects_aggregates_across_hosts(client, db_session):
    host_a = _make_host(db_session, "host-a")
    host_b = _make_host(db_session, "host-b")
    _add_observation(db_session, host_a, 3000, project_name="shared-project", service_name="frontend")
    _add_observation(db_session, host_b, 8000, project_name="shared-project", service_name="api")

    response = client.get("/api/projects")
    body = response.json()
    project = next(p for p in body if p["project_name"] == "shared-project")
    assert project["host_count"] == 2
    assert project["port_count"] == 2


def test_same_project_name_different_hosts_shown_together(client, db_session):
    host_a = _make_host(db_session, "host-a")
    host_b = _make_host(db_session, "host-b")
    _add_observation(db_session, host_a, 8000, project_name="ocrforge")
    _add_observation(db_session, host_b, 8000, project_name="ocrforge")

    response = client.get("/api/projects")
    project = next(p for p in response.json() if p["project_name"] == "ocrforge")
    assert sorted(project["hosts"]) == ["host-a", "host-b"]
