"""Central reservations: read (public) + agent-authenticated write/sync +
dashboard-initiated write.

Agent POST/DELETE always act on the *authenticated* agent's own host_id --
there is no host_id in those write request bodies, so one agent's credential
can never create or delete a reservation on another host's behalf.

The /dashboard write routes are unauthenticated by design: PortForge runs as
a trusted private/LAN control plane (no login/session/token architecture is
in scope -- see docs/phase7c4_ux_audit.md and the Phase 7C.4 acceptance
task), so any client reaching Central can drive a dashboard-initiated
reservation. This is distinct from agent enrollment (api/agents.py's
`/enrollment-tokens`), which remains behind `require_admin` -- minting an
agent credential is a different trust boundary than an already-trusted
operator reserving a port through the dashboard.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.common import Page
from ..schemas.reservation import DashboardReservationIn, ReservationIn, ReservationOut
from ..security.auth import AuthenticatedAgent, require_agent
from ..services import reservation_service

router = APIRouter(prefix="/reservations", tags=["reservations"])


@router.get("", response_model=Page[ReservationOut])
def list_reservations(
    host_id: Optional[uuid.UUID] = Query(default=None),
    port: Optional[int] = Query(default=None, ge=0, le=65535),
    project: Optional[str] = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Page[ReservationOut]:
    rows, total = reservation_service.list_reservations(
        db, host_id=host_id, port=port, project=project, limit=limit, offset=offset
    )
    return Page(
        items=[ReservationOut.model_validate(_serializable(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ReservationOut, status_code=status.HTTP_201_CREATED)
def create_reservation(
    payload: ReservationIn,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> ReservationOut:
    try:
        reservation = reservation_service.create_reservation(
            db,
            host_id=agent.host_id,
            port=payload.port,
            protocol=payload.protocol,
            bind_address=payload.bind_address,
            project=payload.project,
            service=payload.service,
            purpose=payload.purpose,
            notes=payload.notes,
            local_reservation_id=payload.local_reservation_id,
        )
    except reservation_service.ReservationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return ReservationOut.model_validate(_serializable(reservation))


@router.post("/dashboard", response_model=ReservationOut, status_code=status.HTTP_201_CREATED)
def create_reservation_dashboard(
    payload: DashboardReservationIn,
    db: Session = Depends(get_db),
) -> ReservationOut:
    try:
        reservation = reservation_service.create_reservation(
            db,
            host_id=payload.host_id,
            port=payload.port,
            protocol=payload.protocol,
            bind_address=payload.bind_address,
            project=payload.project,
            service=payload.service,
            purpose=payload.purpose,
            notes=payload.notes,
            local_reservation_id=payload.local_reservation_id,
        )
    except reservation_service.ReservationConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return ReservationOut.model_validate(_serializable(reservation))


@router.delete("/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_reservation(
    reservation_id: uuid.UUID,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> None:
    deleted = reservation_service.delete_reservation(db, host_id=agent.host_id, reservation_id=reservation_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found.")


@router.delete("/dashboard/{host_id}/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_reservation_dashboard(
    host_id: uuid.UUID,
    reservation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    deleted = reservation_service.delete_reservation(db, host_id=host_id, reservation_id=reservation_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found.")


def _serializable(reservation) -> dict:
    return {
        "id": reservation.id,
        "host_id": reservation.host_id,
        "port": reservation.port,
        "protocol": reservation.protocol.value,
        "bind_address": reservation.bind_address,
        "project": reservation.project,
        "service": reservation.service,
        "purpose": reservation.purpose,
        "notes": reservation.notes,
        "local_reservation_id": reservation.local_reservation_id,
        "created_at": reservation.created_at,
        "updated_at": reservation.updated_at,
    }
