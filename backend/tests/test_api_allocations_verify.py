import uuid
import pytest
from app.schemas.allocation import AllocationIn, AllocationRequestItem

def test_verify_allocation_not_found(client, db_session):
    resp = client.post(f"/api/allocations/{uuid.uuid4()}/verify")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ALLOCATION_NOT_FOUND"

def test_verify_allocation_success(client, db_session, enrolled_host):
    # 1. Create allocation
    req = AllocationIn(
        project="test_verify",
        host_id=enrolled_host.id,
        requests=[AllocationRequestItem(name="web", purpose="frontend")]
    )
    alloc_resp = client.post("/api/allocations", json=req.model_dump(mode="json"))
    assert alloc_resp.status_code == 201
    alloc_id = alloc_resp.json()["allocation_id"]

    # 2. Verify
    verify_resp = client.post(f"/api/allocations/{alloc_id}/verify")
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["allocation_id"] == alloc_id
    assert data["status"] == "active"
