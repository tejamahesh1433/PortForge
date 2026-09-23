"""Fleet intelligence API — Phase 9 operator-facing read surface.

Separate from /hosts to avoid overloading the existing list_hosts endpoint
with filters and fields the dashboard doesn't need.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.common import Page
from ..schemas.host import FleetHostOut
from ..services import fleet_service

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("", response_model=Page[FleetHostOut])
def list_fleet(
    lifecycle_state: str | None = Query(default=None),
    health_state: str | None = Query(default=None),
    update_availability: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Hostname substring search"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[FleetHostOut]:
    items, total = fleet_service.get_fleet(
        db,
        lifecycle_state=lifecycle_state,
        health_state_filter=health_state,
        update_availability_filter=update_availability,
        q=q,
        limit=limit,
        offset=offset,
    )
    return Page(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{host_id}", response_model=FleetHostOut)
def get_fleet_host(host_id: uuid.UUID, db: Session = Depends(get_db)) -> FleetHostOut:
    result = fleet_service.get_fleet_host(db, host_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")
    return result
