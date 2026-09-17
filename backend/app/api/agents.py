"""Authenticated agent endpoints + admin enrollment-token minting.

Every route here that isn't `/agent/enroll` requires `require_agent` (a
valid, unrevoked per-host bearer credential -- see security/auth.py). The
`host_id` in a request body is **never trusted on its own**: every handler
below cross-checks it against `AuthenticatedAgent.host_id` (the identity
the bearer token actually proved) and rejects a mismatch outright, so one
host's credential can never be used to write another host's data.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..schemas.agent import (
    EnrollmentRequest,
    EnrollmentResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    SnapshotResult,
    SnapshotSubmission,
)
from ..security.auth import AuthenticatedAgent, require_admin, require_agent
from ..services import enrollment_service, host_service, ingestion_service

router = APIRouter(prefix="/agent", tags=["agent"])


class MintTokenRequest:
    """Query-param-free minimal body isn't needed -- label/ttl are optional
    query params on the mint endpoint itself, kept simple on purpose.
    """


@router.post("/enrollment-tokens", dependencies=[Depends(require_admin)])
def mint_enrollment_token(label: str | None = None, ttl_hours: int = 24, db: Session = Depends(get_db)) -> dict:
    """Admin-only. Returns the raw token exactly once -- it is never
    retrievable again and is not stored anywhere in raw form.
    """
    ttl = timedelta(hours=ttl_hours) if ttl_hours > 0 else None
    minted = enrollment_service.mint_enrollment_token(db, label=label, ttl=ttl)
    db.commit()
    return {
        "enrollment_token": minted.raw_token,
        "expires_at": minted.expires_at.isoformat() if minted.expires_at else None,
    }


@router.post("/enroll", response_model=EnrollmentResponse)
def enroll(payload: EnrollmentRequest, db: Session = Depends(get_db)) -> EnrollmentResponse:
    try:
        result = enrollment_service.enroll_host(
            db,
            raw_enrollment_token=payload.enrollment_token,
            host_id=payload.host_id,
            hostname=payload.hostname,
            operating_system=payload.operating_system,
            os_version=payload.os_version,
            architecture=payload.architecture,
            agent_version=payload.agent_version,
            docker_available=payload.docker_available,
        )
    except enrollment_service.EnrollmentError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    return EnrollmentResponse(host_id=result.host_id, agent_token=result.agent_token)


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    payload: HeartbeatRequest,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> HeartbeatResponse:
    if payload.host_id != agent.host_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "host_id does not match the authenticated agent credential.")

    host = host_service.record_heartbeat(
        db,
        host_id=agent.host_id,
        hostname=payload.hostname,
        operating_system=payload.operating_system,
        os_version=payload.os_version,
        architecture=payload.architecture,
        agent_version=payload.agent_version,
        docker_available=payload.docker_available,
        timestamp=payload.timestamp,
    )
    return HeartbeatResponse(host_id=host.id, last_seen=host.last_seen, status=host.status)


@router.post("/observations", response_model=SnapshotResult)
def submit_observations(
    payload: SnapshotSubmission,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> SnapshotResult:
    if payload.host_id != agent.host_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "host_id does not match the authenticated agent credential.")

    settings = get_settings()
    try:
        result = ingestion_service.ingest_snapshot(
            db,
            host_id=agent.host_id,
            scan_id=payload.scan_id,
            observed_at=payload.observed_at,
            observations=payload.observations,
            max_batch_size=settings.max_observations_per_snapshot,
        )
    except ingestion_service.StaleSnapshotError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ingestion_service.BatchTooLargeError as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except ingestion_service.SnapshotRejectedError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return SnapshotResult(
        scan_id=result.scan_id,
        accepted=result.accepted,
        reason=result.reason,
        observations_processed=result.observations_processed,
        appeared=result.appeared,
        changed=result.changed,
        disappeared=result.disappeared,
    )
