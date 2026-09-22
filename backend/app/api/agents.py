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
from ..schemas.probe import PendingProbeOut, ProbeResultIn, ProbeResultOut
from ..security.auth import AuthenticatedAgent, require_admin, require_agent
from ..services import compatibility_service, enrollment_service, host_service, ingestion_service, probe_service

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

    # v1.1-A: advisory only -- computed from the request body alone, never
    # persisted, never blocks enrollment (see services/compatibility_service.py).
    compatibility = compatibility_service.evaluate_protocol_compatibility(payload.protocol_version)
    return EnrollmentResponse(host_id=result.host_id, agent_token=result.agent_token, protocol_compatibility=compatibility)


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
    compatibility = compatibility_service.evaluate_protocol_compatibility(payload.protocol_version)

    # v1.1-B: deliver any pending remote bind-probe requests on this same
    # heartbeat round-trip -- no second transport, no extra round trip. A
    # legacy agent that doesn't read this field simply never claims them;
    # they expire on their own TTL (see docs/v1.1/remote-probe-design.md).
    delivered = probe_service.claim_pending_probes(db, agent.host_id)
    pending_probes = [
        PendingProbeOut(probe_id=p.id, port=p.port, protocol=p.protocol, bind_address=p.bind_address)
        for p in delivered
    ]

    return HeartbeatResponse(
        host_id=host.id,
        last_seen=host.last_seen,
        status=host.status,
        protocol_compatibility=compatibility,
        pending_probes=pending_probes,
    )


@router.post("/probes/result", response_model=ProbeResultOut)
def submit_probe_result(
    payload: ProbeResultIn,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> ProbeResultOut:
    """The only new agent-facing endpoint this increment adds (task §26:
    "keep public API additions minimal"). `host_id` in the body is
    cross-checked against the authenticated credential exactly like every
    other agent endpoint -- the REAL authority for "which host is this"
    is the bearer token, never the body (task §8).
    """
    if payload.host_id != agent.host_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "host_id does not match the authenticated agent credential.")

    try:
        probe = probe_service.submit_result(
            db, agent.host_id, payload.probe_id, payload.available, payload.reason
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except probe_service.ProbeOwnershipError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    return ProbeResultOut(probe_id=probe.id, status=probe.status)


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
        duplicates_merged=result.duplicates_merged,
    )
