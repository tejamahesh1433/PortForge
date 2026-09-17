from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..repositories.host_repository import HostRepository
from ..repositories.port_repository import PortRepository
from ..schemas.common import Page
from ..schemas.host import HostOut
from ..schemas.port import PortObservationOut

router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.get("", response_model=Page[HostOut])
def list_hosts(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[HostOut]:
    repo = HostRepository(db)
    hosts = repo.list(limit=limit, offset=offset)
    total = repo.count()
    return Page(items=[HostOut.model_validate(h) for h in hosts], total=total, limit=limit, offset=offset)


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: uuid.UUID, db: Session = Depends(get_db)) -> HostOut:
    repo = HostRepository(db)
    host = repo.get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")
    return HostOut.model_validate(host)


@router.get("/{host_id}/ports", response_model=list[PortObservationOut])
def get_host_ports(host_id: uuid.UUID, db: Session = Depends(get_db)) -> list[PortObservationOut]:
    host_repo = HostRepository(db)
    host = host_repo.get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")

    port_repo = PortRepository(db)
    rows = port_repo.list_current_for_host(host_id)
    return [PortObservationOut.from_observation(row, hostname=host.hostname) for row in rows]
