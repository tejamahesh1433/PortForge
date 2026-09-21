import uuid
from datetime import datetime, timedelta, timezone

from app.models.activity import ActivityEvent
from app.models.host import Host
from app.models.port_observation import CurrentPortObservation
from app.services import reservation_service


def add_host(db, hostname: str, *, last_seen=None, docker_available=True):
    now = datetime.now(timezone.utc)
    host = Host(
        id=uuid.uuid4(),
        hostname=hostname,
        operating_system="windows" if "win" in hostname else "linux",
        docker_available=docker_available,
        first_seen=now,
        last_seen=last_seen or now,
        last_scan_observed_at=last_seen or now,
    )
    db.add(host)
    db.commit()
    return host


def add_binding(db, host, port: int, project: str, **overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        host_id=host.id,
        port=port,
        protocol="tcp",
        bind_address="0.0.0.0",
        state="ACTIVE",
        source="process",
        process_name="python",
        pid=1234,
        project_name=project,
        purpose="api",
        detection_confidence="high",
        first_seen=now,
        last_seen=now,
        observed_at=now,
        scan_id=uuid.uuid4(),
    )
    values.update(overrides)
    db.add(CurrentPortObservation(**values))
    db.commit()


def test_project_detail_preserves_multi_host_same_port_and_composition(client, db_session):
    host_a = add_host(db_session, "win-dev")
    host_b = add_host(db_session, "linux-dev")
    add_binding(db_session, host_a, 8000, "shared-app")
    add_binding(
        db_session,
        host_b,
        8000,
        "shared-app",
        source="docker",
        process_name=None,
        pid=None,
        container_id="container-1",
        container_name="shared-api",
        container_image="shared:latest",
        container_port=8000,
        docker_compose_project="shared-app",
        service_name="api",
    )

    response = client.get("/api/projects/shared-app")
    assert response.status_code == 200
    body = response.json()
    assert body["host_count"] == 2
    assert body["port_count"] == 2
    assert body["process_count"] == 1
    assert body["docker_binding_count"] == 1
    assert body["container_count"] == 1
    assert {item["host_hostname"] for item in body["ports"]["items"]} == {"win-dev", "linux-dev"}
    assert {item["port"] for item in body["ports"]["items"]} == {8000}
    assert body["conflict_count"] == 0


def test_project_detail_includes_reservation_activity_and_health(client, db_session):
    now = datetime.now(timezone.utc)
    healthy = add_host(db_session, "healthy-host", last_seen=now)
    offline = add_host(db_session, "offline-host", last_seen=now - timedelta(minutes=10))
    add_binding(db_session, healthy, 3000, "ops-app")
    add_binding(db_session, offline, 3001, "ops-app")
    reservation_service.create_reservation(
        db_session,
        host_id=healthy.id,
        port=3100,
        protocol="tcp",
        bind_address="0.0.0.0",
        project="ops-app",
        service="worker",
        purpose="worker",
        notes=None,
        local_reservation_id=None,
    )
    db_session.add(
        ActivityEvent(
            host_id=healthy.id,
            timestamp=now,
            event_type="PORT_APPEARED",
            port=3000,
            protocol="tcp",
            bind_address="0.0.0.0",
            identity_context="python",
            metadata_json={"project_name": "ops-app"},
            summary="Port appeared",
        )
    )
    db_session.commit()

    body = client.get("/api/projects/ops-app").json()
    assert body["reservation_count"] == 1
    assert body["reservations"]["items"][0]["project"] == "ops-app"
    assert body["healthy_host_count"] == 1
    assert body["offline_host_count"] == 1
    assert {event["event_type"] for event in body["activity"]} >= {"PORT_APPEARED", "RESERVATION_CREATED"}


def test_reservation_only_project_is_visible(client, db_session):
    host = add_host(db_session, "reservation-host")
    reservation_service.create_reservation(
        db_session,
        host_id=host.id,
        port=9200,
        protocol="tcp",
        bind_address=None,
        project="reserved-only",
        service=None,
        purpose="metrics",
        notes=None,
        local_reservation_id=None,
    )

    inventory = client.get("/api/projects").json()
    summary = next(item for item in inventory if item["project_name"] == "reserved-only")
    assert summary["port_count"] == 0
    assert summary["reservation_count"] == 1
    detail = client.get("/api/projects/reserved-only").json()
    assert detail["host_count"] == 1
    assert detail["ports"]["total"] == 0


def test_project_detail_paginates_ports_and_returns_404(client, db_session):
    host = add_host(db_session, "paging-host")
    for port in (5000, 5001, 5002):
        add_binding(db_session, host, port, "paged-app")

    response = client.get("/api/projects/paged-app?port_limit=2&port_offset=1")
    assert response.status_code == 200
    assert response.json()["ports"]["total"] == 3
    assert len(response.json()["ports"]["items"]) == 2
    assert client.get("/api/projects/does-not-exist").status_code == 404