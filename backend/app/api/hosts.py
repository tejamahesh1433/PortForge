from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..repositories.host_repository import HostRepository
from ..repositories.port_repository import PortRepository
from ..repositories.probe_repository import ProbeRepository
from ..schemas.common import Page
from ..schemas.port import PortObservationOut
from datetime import datetime, timezone
from ..config import get_settings
from ..models.host import Host
from ..schemas.health_status import derive_health_state
from ..schemas.host import HostOut, HostDiagnosticsOut, HostDecommissionRequest
from ..security.auth import require_admin
from ..services import compatibility_service
from ..services import host_lifecycle_service

router = APIRouter(prefix="/hosts", tags=["hosts"])

def _build_host_out(host: Host) -> HostOut:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    health_state, reason, age_seconds = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now
    )
    
    snapshot_age = None
    if host.last_scan_observed_at:
        snapshot_age = int((now - host.last_scan_observed_at).total_seconds())

    return HostOut(
        id=host.id,
        hostname=host.hostname,
        display_name=host.display_name,
        operating_system=host.operating_system,
        os_version=host.os_version,
        architecture=host.architecture,
        agent_version=host.agent_version,
        docker_available=host.docker_available,
        first_seen=host.first_seen,
        last_seen=host.last_seen,
        status=host.status,
        health_state=health_state.value,
        health_reason=reason.value,
        age_seconds=age_seconds,
        snapshot_age_seconds=snapshot_age,
        protocol_version=host.protocol_version,
        protocol_compatibility=compatibility_service.evaluate_protocol_compatibility(host.protocol_version),
        lifecycle_state=getattr(host, "lifecycle_state", None) or "ACTIVE",
        decommissioned_at=getattr(host, "decommissioned_at", None),
        decommission_reason=getattr(host, "decommission_reason", None),
    )

@router.get("", response_model=Page[HostOut])
def list_hosts(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[HostOut]:
    repo = HostRepository(db)
    hosts = repo.list(limit=limit, offset=offset)
    total = repo.count()
    return Page(items=[_build_host_out(h) for h in hosts], total=total, limit=limit, offset=offset)


@router.get("/{host_id}", response_model=HostOut)
def get_host(host_id: uuid.UUID, db: Session = Depends(get_db)) -> HostOut:
    repo = HostRepository(db)
    host = repo.get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")
    return _build_host_out(host)


@router.get("/{host_id}/diagnostics", response_model=HostDiagnosticsOut)
def get_host_diagnostics(host_id: uuid.UUID, db: Session = Depends(get_db)) -> HostDiagnosticsOut:
    repo = HostRepository(db)
    host = repo.get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")
    settings = get_settings()
    host_out = _build_host_out(host)

    # v1.1-D task Sec8: only computed here (a single-host read), never in
    # list_hosts -- one bounded EXISTS query per detail view, not one per
    # row of a 500-host list (see ProbeRepository.host_has_probe_history's
    # own docstring).
    if host_out.health_state != "HEALTHY":
        probe_capability = "unavailable_offline"
    elif ProbeRepository(db).host_has_probe_history(host_id):
        probe_capability = "supported"
    else:
        probe_capability = "unknown"

    return HostDiagnosticsOut(
        host=host_out,
        last_scan_observed_at=host.last_scan_observed_at,
        stale_threshold_seconds=settings.host_stale_after_seconds,
        offline_threshold_seconds=settings.host_offline_after_seconds,
        probe_capability=probe_capability,
    )


@router.get("/{host_id}/ports", response_model=list[PortObservationOut])
def get_host_ports(host_id: uuid.UUID, db: Session = Depends(get_db)) -> list[PortObservationOut]:
    host_repo = HostRepository(db)
    host = host_repo.get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")

    port_repo = PortRepository(db)
    rows = port_repo.list_current_for_host(host_id)
    return [PortObservationOut.from_observation(row, hostname=host.hostname) for row in rows]

@router.delete(
    "/{host_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(require_admin)],
    summary="Remove Record",
)
def remove_host_record(host_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Administratively purge a Central host *record*.

    This is Remove Record — not remote stop, not permanent decommission, and
    not an uninstall. The still-running agent (if any) is not contacted; its
    next heartbeat/sync fails authentication because the credential row is
    deleted. Re-enrollment with a new enrollment token may recreate the same
    host UUID (agent-local identity) as a fresh enrollment.

    Requires the admin bootstrap bearer token (`require_admin`).
    """
    repo = HostRepository(db)
    try:
        if not repo.delete(host_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        # Map unexpected purge failures to 500 without leaking internals.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Failed to remove host record.",
        ) from None


@router.post(
    "/{host_id}/decommission",
    response_model=HostOut,
    dependencies=[Depends(require_admin)],
    summary="Decommission Host",
)
def decommission_host(
    host_id: uuid.UUID,
    payload: HostDecommissionRequest | None = None,
    db: Session = Depends(get_db),
) -> HostOut:
    """Retire a host identity as a Central tombstone.

    Distinct from Remove Record: the host row is retained, credentials are
    revoked, and ordinary same-UUID enrollment is rejected until Reactivate.
    The remote agent process is not contacted or stopped.
    """
    body = payload or HostDecommissionRequest()
    try:
        host = host_lifecycle_service.decommission_host(
            db, host_id, reason=body.reason
        )
        db.commit()
        db.refresh(host)
        return _build_host_out(host)
    except host_lifecycle_service.HostLifecycleError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Failed to decommission host.",
        ) from None


@router.post(
    "/{host_id}/reactivate",
    response_model=HostOut,
    dependencies=[Depends(require_admin)],
    summary="Reactivate Host",
)
def reactivate_host(host_id: uuid.UUID, db: Session = Depends(get_db)) -> HostOut:
    """Clear DECOMMISSIONED tombstone markers.

    Does not issue an agent credential. Operator must mint an enrollment
    token and enroll explicitly after reactivation.
    """
    try:
        host = host_lifecycle_service.reactivate_host(db, host_id)
        db.commit()
        db.refresh(host)
        return _build_host_out(host)
    except host_lifecycle_service.HostLifecycleError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, str(exc)) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Failed to reactivate host.",
        ) from None
