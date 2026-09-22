import uuid
import pytest
from app.schemas.allocation import AllocationIn, AllocationRequestItem

from unittest.mock import patch

def test_verify_allocation_not_found(client, db_session):
    resp = client.post(f"/api/allocations/{uuid.uuid4()}/verify")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ALLOCATION_NOT_FOUND"

@patch("app.services.allocation_service.probe_service.get_probe_evidence_map")
def test_verify_allocation_mocked_success(mock_evidence_map, client, db_session, enrolled_host):
    # 1. Create allocation
    req = AllocationIn(
        project="test_verify",
        host_id=enrolled_host.id,
        requests=[AllocationRequestItem(name="web", purpose="frontend")]
    )
    alloc_resp = client.post("/api/allocations", json=req.model_dump(mode="json"))
    assert alloc_resp.status_code == 201
    alloc_data = alloc_resp.json()
    alloc_id = alloc_data["allocation_id"]
    port = alloc_data["allocations"][0]["port"]

    # 2. Setup mock for fast verification success
    from unittest.mock import MagicMock
    mock_evidence = MagicMock()
    mock_evidence.probe.status = "COMPLETED"
    mock_evidence_map.return_value = {
        (enrolled_host.id, port, "tcp", "0.0.0.0"): mock_evidence
    }

    # 3. Verify
    verify_resp = client.post(f"/api/allocations/{alloc_id}/verify")
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["allocation_id"] == alloc_id
    assert data["status"] == "active"
    # Evidence should remain not_remote_capable because no agent picked it up
    assert data["allocations"][0]["bind_probe"] == "not_remote_capable"

def test_verify_allocation_offline_host(client, db_session, enrolled_host):
    # 1. Create allocation while host is online
    req = AllocationIn(
        project="test_offline",
        host_id=enrolled_host.id,
        requests=[AllocationRequestItem(name="web", purpose="frontend")]
    )
    alloc_resp = client.post("/api/allocations", json=req.model_dump(mode="json"))
    assert alloc_resp.status_code == 201
    alloc_id = alloc_resp.json()["allocation_id"]

    # 2. Make host offline
    from datetime import datetime, timezone, timedelta
    from app.models.host import Host
    host_model = db_session.query(Host).filter(Host.id == enrolled_host.id).first()
    host_model.last_seen = datetime.now(timezone.utc) - timedelta(minutes=6)
    db_session.commit()

    # 3. Verify
    verify_resp = client.post(f"/api/allocations/{alloc_id}/verify")
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["allocations"][0]["bind_probe"] == "not_remote_capable"

@patch("app.services.allocation_service.probe_service.get_probe_evidence_map")
def test_verify_allocation_multi_port(mock_evidence_map, client, db_session, enrolled_host):
    # 1. Create multi-port allocation
    req = AllocationIn(
        project="test_multi",
        host_id=enrolled_host.id,
        requests=[
            AllocationRequestItem(name="web", purpose="frontend"),
            AllocationRequestItem(name="admin", purpose="api")
        ]
    )
    alloc_resp = client.post("/api/allocations", json=req.model_dump(mode="json"))
    assert alloc_resp.status_code == 201
    alloc_data = alloc_resp.json()
    alloc_id = alloc_data["allocation_id"]
    port1 = alloc_data["allocations"][0]["port"]
    port2 = alloc_data["allocations"][1]["port"]

    # 2. Mock evidence map to only have ONE completed, the other is None (so it loops)
    # But wait, to avoid infinite loop we need to raise StopIteration or change state in side_effect.
    from unittest.mock import MagicMock
    mock_evidence1 = MagicMock()
    mock_evidence1.probe.status = "COMPLETED"
    mock_evidence2 = MagicMock()
    mock_evidence2.probe.status = "COMPLETED"
    
    # We will simulate that on the first poll, only port1 is done. On second poll, both are done.
    mock_evidence_map.side_effect = [
        { (enrolled_host.id, port1, "tcp", "0.0.0.0"): mock_evidence1 },
        { 
            (enrolled_host.id, port1, "tcp", "0.0.0.0"): mock_evidence1,
            (enrolled_host.id, port2, "tcp", "0.0.0.0"): mock_evidence2 
        }
    ]

    # 3. Verify
    verify_resp = client.post(f"/api/allocations/{alloc_id}/verify")
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert mock_evidence_map.call_count == 2
