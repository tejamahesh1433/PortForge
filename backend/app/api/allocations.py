"""Phase 8A: POST/GET/DELETE /api/allocations.

Unauthenticated by design, same posture as the dashboard's
`/api/reservations/dashboard` routes (Phase 7C.5) -- PortForge is a
trusted private/LAN control plane, and authentication for coding-agent
allocation is explicitly out of scope for Phase 8A. See
docs/phase8a_agent_allocation.md for the full contract.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.allocation import AllocationIn, AllocationOut
from ..services import allocation_service

router = APIRouter(prefix="/allocations", tags=["allocations"])


@router.post("", response_model=AllocationOut, status_code=201)
def create_allocation(payload: AllocationIn, db: Session = Depends(get_db)) -> AllocationOut:
    return allocation_service.create_allocation(db, payload)


@router.get("/{allocation_id}", response_model=AllocationOut)
def get_allocation(allocation_id: uuid.UUID, db: Session = Depends(get_db)) -> AllocationOut:
    return allocation_service.get_allocation(db, allocation_id)


@router.delete("/{allocation_id}", response_model=AllocationOut)
def release_allocation(allocation_id: uuid.UUID, db: Session = Depends(get_db)) -> AllocationOut:
    return allocation_service.release_allocation(db, allocation_id)
