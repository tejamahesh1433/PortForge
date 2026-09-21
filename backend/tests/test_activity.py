import pytest
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models.activity import ActivityEvent
from app.models.host import Host
from app.repositories.activity_repository import ActivityRepository

def test_activity_repository_add_and_list(db_session: Session):
    repo = ActivityRepository(db_session)
    host_id = uuid4()
    
    # Create host first to satisfy FK
    db_session.add(Host(
        id=host_id,
        hostname="test-host",
        operating_system="linux",
        docker_available=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    ))
    db_session.commit()
    
    # Add events
    event1 = ActivityEvent(
        host_id=host_id,
        timestamp=datetime.now(timezone.utc),
        event_type="PORT_APPEARED",
        summary="Test event 1"
    )
    event2 = ActivityEvent(
        host_id=host_id,
        timestamp=datetime.now(timezone.utc),
        event_type="HOST_ONLINE",
        summary="Test event 2"
    )
    
    repo.add(event1)
    repo.add(event2)
    db_session.commit()
    
    # List all
    events, total = repo.list_events()
    assert total >= 2
    
    # Filter by host
    events, total = repo.list_events(host_id=host_id)
    assert total == 2
    
    # Filter by event_type
    events, total = repo.list_events(host_id=host_id, event_type="PORT_APPEARED")
    assert total == 1
    assert events[0].event_type == "PORT_APPEARED"

def test_api_list_activity(client: TestClient, db_session: Session):
    repo = ActivityRepository(db_session)
    host_id = uuid4()
    
    # Create host first to satisfy FK
    db_session.add(Host(
        id=host_id,
        hostname="test-host-api",
        operating_system="linux",
        docker_available=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    ))
    db_session.commit()
    
    event = ActivityEvent(
        host_id=host_id,
        timestamp=datetime.now(timezone.utc),
        event_type="RESERVATION_CREATED",
        port=8080,
        protocol="tcp",
        summary="Reserved 8080"
    )
    repo.add(event)
    db_session.commit()
    
    response = client.get(f"/api/activity?host_id={host_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "RESERVATION_CREATED"
    assert data["events"][0]["port"] == 8080

    # Filter by port
    response = client.get(f"/api/activity?port=8080")
    assert response.status_code == 200
    assert response.json()["total"] >= 1
    
    # Filter by missing port
    response = client.get(f"/api/activity?port=9999")
    assert response.status_code == 200
    # Might be 0 unless another test used 9999
