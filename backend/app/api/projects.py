"""Operational project inventory and drill-down."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.project import ProjectDetailOut, ProjectOut
from ..services.project_service import get_project, list_projects

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def project_inventory(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    return list_projects(db, limit=limit, offset=offset)


@router.get("/{project_name}", response_model=ProjectDetailOut)
def project_detail(
    project_name: str,
    port_limit: int = Query(default=200, ge=1, le=500),
    port_offset: int = Query(default=0, ge=0),
    reservation_limit: int = Query(default=100, ge=1, le=500),
    reservation_offset: int = Query(default=0, ge=0),
    activity_limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ProjectDetailOut:
    project = get_project(
        db,
        project_name,
        port_limit=port_limit,
        port_offset=port_offset,
        reservation_limit=reservation_limit,
        reservation_offset=reservation_offset,
        activity_limit=activity_limit,
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project
