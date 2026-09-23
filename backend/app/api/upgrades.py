"""Admin-facing upgrade management API (Phase 10)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..repositories.upgrade_repository import UpgradeRepository
from ..schemas.upgrade import UpgradeCreateRequest, UpgradeOut
from ..security.auth import require_admin
from ..services import upgrade_service

# Routes prefixed with /hosts (admin host-scoped upgrade operations)
host_upgrades_router = APIRouter(prefix="/hosts", tags=["upgrades"])
# Routes prefixed with /upgrades (admin upgrade-id operations)
upgrades_router = APIRouter(prefix="/upgrades", tags=["upgrades"])


def _upgrade_out(upgrade) -> UpgradeOut:
    return UpgradeOut.model_validate(upgrade)


@host_upgrades_router.post(
    "/{host_id}/upgrades",
    response_model=UpgradeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Create Upgrade",
)
def create_upgrade(
    host_id: uuid.UUID,
    payload: UpgradeCreateRequest,
    db: Session = Depends(get_db),
) -> UpgradeOut:
    """Admin: create an approved upgrade request for a host."""
    try:
        upgrade = upgrade_service.create_upgrade(
            db,
            host_id=host_id,
            target_version=payload.target_version,
            artifact_url=payload.artifact_url,
            artifact_sha256=payload.artifact_sha256,
            artifact_filename=payload.artifact_filename,
            request_id=payload.request_id,
            created_by="admin",
            previous_artifact_url=payload.previous_artifact_url,
            previous_artifact_sha256=payload.previous_artifact_sha256,
        )
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(upgrade)
    return _upgrade_out(upgrade)


@host_upgrades_router.get(
    "/{host_id}/upgrades",
    response_model=list[UpgradeOut],
    dependencies=[Depends(require_admin)],
    summary="List Upgrades for Host",
)
def list_upgrades_for_host(
    host_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[UpgradeOut]:
    """Admin: list all upgrades for a host (most recent first)."""
    from ..repositories.host_repository import HostRepository

    host = HostRepository(db).get(host_id)
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host not found.")

    upgrades = UpgradeRepository(db).list_for_host(host_id)
    return [_upgrade_out(u) for u in upgrades]


@upgrades_router.get(
    "/{upgrade_id}",
    response_model=UpgradeOut,
    dependencies=[Depends(require_admin)],
    summary="Get Upgrade",
)
def get_upgrade(upgrade_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeOut:
    """Admin: fetch a single upgrade record."""
    upgrade = UpgradeRepository(db).get(upgrade_id)
    if upgrade is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upgrade not found.")
    return _upgrade_out(upgrade)


@upgrades_router.post(
    "/{upgrade_id}/rollback",
    response_model=UpgradeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Rollback Upgrade",
)
def rollback_upgrade(upgrade_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeOut:
    """Admin: create a rollback upgrade targeting the previous artifact.

    Requires the original upgrade to have previous_artifact_url and
    previous_artifact_sha256 populated.
    """
    try:
        rollback = upgrade_service.rollback_upgrade(db, upgrade_id, created_by="admin")
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(rollback)
    return _upgrade_out(rollback)
