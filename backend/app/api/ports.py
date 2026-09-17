"""GET /api/ports -- answers "where is port X being used" across every
host the central registry knows about. Multiple hosts reporting the same
port number is always valid and never itself flagged as a conflict (that
would only be a same-host reservation-vs-active mismatch -- see
conflicts.py); two different machines using port 8000 for two entirely
different projects is completely normal and expected.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.host import Host
from ..repositories.port_repository import PortRepository
from ..schemas.common import Page
from ..schemas.port import PortObservationOut

router = APIRouter(prefix="/ports", tags=["ports"])


@router.get("", response_model=Page[PortObservationOut])
def list_ports(
    port: Optional[int] = Query(default=None, ge=0, le=65535),
    project: Optional[str] = Query(default=None, max_length=255),
    purpose: Optional[str] = Query(default=None, max_length=255),
    source: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[PortObservationOut]:
    repo = PortRepository(db)
    rows, total = repo.query(
        port=port, project=project, purpose=purpose, source=source, limit=limit, offset=offset
    )

    # One extra query for hostnames rather than a join per row -- the
    # result set is bounded by `limit` (<=500), so this stays cheap.
    host_ids = {row.host_id for row in rows}
    hostnames = {}
    if host_ids:
        for host_id in host_ids:
            host = db.get(Host, host_id)
            if host is not None:
                hostnames[host_id] = host.hostname

    items = [PortObservationOut.from_observation(row, hostname=hostnames.get(row.host_id)) for row in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)
