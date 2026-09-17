"""GET /api/projects -- aggregates current observations by project_name.

See schemas/project.py's module docstring for the deliberately
conservative identity strategy: a "project" is a distinct project_name
string, not a resolved cross-host entity.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.host import Host
from ..repositories.port_repository import PortRepository
from ..schemas.project import ProjectOut, ProjectServiceEntry

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    port_repo = PortRepository(db)
    project_names = port_repo.list_distinct_projects()

    results: list[ProjectOut] = []
    for name in project_names:
        rows, _ = port_repo.query(project=name, limit=500)
        # `query()` does a case-insensitive substring match; keep only
        # exact matches here so e.g. "ocrforge" and "ocrforge-backup"
        # don't get merged into one project entry.
        rows = [r for r in rows if r.project_name == name]
        if not rows:
            continue

        entries = []
        hostnames = set()
        for row in rows:
            host = db.get(Host, row.host_id)
            hostname = host.hostname if host else "unknown"
            hostnames.add(hostname)
            entries.append(
                ProjectServiceEntry(
                    host_id=row.host_id,
                    hostname=hostname,
                    port=row.port,
                    protocol=row.protocol.value,
                    service_name=row.service_name,
                    purpose=row.purpose,
                    category=row.category,
                    state=row.state.value,
                )
            )

        results.append(
            ProjectOut(
                project_name=name,
                host_count=len(hostnames),
                port_count=len(entries),
                hosts=sorted(hostnames),
                entries=entries,
            )
        )

    return results
