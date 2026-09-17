from datetime import datetime, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.port_observation import CurrentPortObservation, PortObservationEvent
from app.models.host import Host

def test_central_distinct_bind_address_handling(client: TestClient, db: Session, enrolled_host: Host):
    """
    Test that the Central Registry correctly accepts and stores multiple address-distinct
    observations on the same (host, port, protocol) simultaneously without unique constraint 
    collisions or overwrites.
    """
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    # Simulate a dual-stack Docker container or native service listening on both
    # 0.0.0.0 and :: on the same port.
    payload = {
        "scan_id": scan_id,
        "observed_at": now,
        "observations": [
            {
                "port": 3000,
                "protocol": "tcp",
                "bind_address": "0.0.0.0",
                "state": "active",
                "source": "docker",
                "project_name": "test_project",
                "first_seen": now,
                "last_seen": now
            },
            {
                "port": 3000,
                "protocol": "tcp",
                "bind_address": "::",
                "state": "active",
                "source": "docker",
                "project_name": "test_project",
                "first_seen": now,
                "last_seen": now
            }
        ]
    }
    
    # 1. Ingest the snapshot
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {enrolled_host.agent_token}"},
        json=payload
    )
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["observations_processed"] == 2
    assert data["appeared"] == 2
    
    # 2. Verify that CurrentPortObservation holds 2 separate records
    current_obs = db.query(CurrentPortObservation).filter(
        CurrentPortObservation.host_id == enrolled_host.id,
        CurrentPortObservation.port == 3000,
        CurrentPortObservation.protocol == "tcp"
    ).all()
    
    assert len(current_obs) == 2
    addresses = {obs.bind_address for obs in current_obs}
    assert addresses == {"0.0.0.0", "::"}
    
    # 3. Verify that the event log recorded 2 separate appearances
    events = db.query(PortObservationEvent).filter(
        PortObservationEvent.host_id == enrolled_host.id,
        PortObservationEvent.port == 3000,
        PortObservationEvent.protocol == "tcp"
    ).all()
    
    assert len(events) == 2
    event_addresses = {event.bind_address for event in events}
    assert event_addresses == {"0.0.0.0", "::"}
