from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..schemas.health import HealthOut, GlobalDiagnosticsOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def get_health(db: Session = Depends(get_db)) -> HealthOut:
    """Useful but deliberately non-sensitive: no credentials, no
    connection strings, no configuration secrets -- just enough to tell a
    human or a monitor whether the service and its database are up.
    """
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:
        database_status = "unavailable"

    return HealthOut(
        status="ok" if database_status == "connected" else "degraded",
        service="portforge",
        database=database_status,
        version=settings.version,
    )


@router.get("/diagnostics", response_model=GlobalDiagnosticsOut)
def get_diagnostics(db: Session = Depends(get_db)) -> GlobalDiagnosticsOut:
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:
        database_status = "unavailable"

    from datetime import datetime, timezone
    from ..schemas.health_status import derive_health_state, HostHealthState
    from ..repositories.host_repository import HostRepository
    from ..repositories.activity_repository import ActivityRepository

    repo = HostRepository(db)
    hosts = repo.list(limit=1000)
    
    now = datetime.now(timezone.utc)
    healthy, stale, offline = 0, 0, 0
    for host in hosts:
        state, _, _ = derive_health_state(
            host.last_seen,
            settings.host_stale_after_seconds,
            settings.host_offline_after_seconds,
            now
        )
        if state == HostHealthState.HEALTHY:
            healthy += 1
        elif state == HostHealthState.STALE:
            stale += 1
        elif state == HostHealthState.OFFLINE:
            offline += 1

    latest_ingestion = None
    activity_repo = ActivityRepository(db)
    # Just a quick proxy for latest ingestion is the max observed_at of ports or activity,
    # but we can query the max last_scan_observed_at from hosts.
    import sqlalchemy as sa
    from app.models.host import Host
    max_scan = db.scalar(sa.select(sa.func.max(Host.last_scan_observed_at)))
    if max_scan:
        latest_ingestion = max_scan.isoformat()

    return GlobalDiagnosticsOut(
        status="ok" if database_status == "connected" else "degraded",
        service="portforge",
        database=database_status,
        version=settings.version,
        host_count_total=len(hosts),
        host_count_healthy=healthy,
        host_count_stale=stale,
        host_count_offline=offline,
        latest_ingestion_time=latest_ingestion,
    )
