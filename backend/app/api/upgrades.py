"""Admin-facing upgrade management API (Phase 10 + Phase 21 recovery/rollout)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..repositories.upgrade_repository import UpgradeRepository
from ..schemas.upgrade import (
    RolloutAdvanceRequest,
    RolloutCreateRequest,
    RolloutOut,
    StuckRecoveryOut,
    UpgradeCreateRequest,
    UpgradeOut,
    UpgradeStatusOut,
)
from ..security.auth import require_admin
from ..services import upgrade_service
from ..services import upgrade_recovery_service, upgrade_rollout_service
from ..services import upgrade_status_service

# Routes prefixed with /hosts (admin host-scoped upgrade operations)
host_upgrades_router = APIRouter(prefix="/hosts", tags=["upgrades"])
# Routes prefixed with /upgrades (admin upgrade-id operations)
upgrades_router = APIRouter(prefix="/upgrades", tags=["upgrades"])
# Routes prefixed with /upgrade-rollouts (fleet rollout operations)
rollouts_router = APIRouter(prefix="/upgrade-rollouts", tags=["upgrade-rollouts"])


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


@upgrades_router.get(
    "/{upgrade_id}/status",
    response_model=UpgradeStatusOut,
    dependencies=[Depends(require_admin)],
    summary="Get Typed Upgrade Status",
)
def get_upgrade_status(upgrade_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeStatusOut:
    """Admin: derived operator-facing status (runtime vs control-plane separated)."""
    try:
        return upgrade_status_service.get_upgrade_status(db, upgrade_id)
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@host_upgrades_router.get(
    "/{host_id}/upgrade-status",
    response_model=UpgradeStatusOut,
    dependencies=[Depends(require_admin)],
    summary="Get Host Upgrade Status",
)
def get_host_upgrade_status(host_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeStatusOut:
    """Admin: active or most recent upgrade status for a host."""
    try:
        status_out = upgrade_status_service.get_host_upgrade_status(db, host_id)
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if status_out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No upgrades for this host.")
    return status_out


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


# ---------------------------------------------------------------------------
# Phase 21: recover-stuck, cancel, retry
# ---------------------------------------------------------------------------

@upgrades_router.post(
    "/recover-stuck",
    response_model=StuckRecoveryOut,
    dependencies=[Depends(require_admin)],
    summary="Recover Stuck Upgrades",
)
def recover_stuck(db: Session = Depends(get_db)) -> StuckRecoveryOut:
    """Admin: find and mark timed-out non-terminal upgrades as FAILED."""
    recovered = upgrade_recovery_service.recover_stuck_upgrades(db)
    db.commit()
    return StuckRecoveryOut(
        recovered=len(recovered),
        upgrade_ids=[u.id for u in recovered],
    )


@upgrades_router.post(
    "/{upgrade_id}/cancel",
    response_model=UpgradeOut,
    dependencies=[Depends(require_admin)],
    summary="Cancel Upgrade",
)
def cancel_upgrade(upgrade_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeOut:
    """Admin: cancel an upgrade that is in a safe-to-cancel state."""
    try:
        upgrade = upgrade_recovery_service.cancel_upgrade(db, upgrade_id)
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(upgrade)
    return _upgrade_out(upgrade)


@upgrades_router.post(
    "/{upgrade_id}/retry",
    response_model=UpgradeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Retry Upgrade",
)
def retry_upgrade(upgrade_id: uuid.UUID, db: Session = Depends(get_db)) -> UpgradeOut:
    """Admin: create a new upgrade row retrying the same artifact from FAILED state."""
    try:
        new_upgrade = upgrade_recovery_service.retry_upgrade(db, upgrade_id)
    except upgrade_service.UpgradeNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(new_upgrade)
    return _upgrade_out(new_upgrade)


# ---------------------------------------------------------------------------
# Phase 21: fleet rollouts
# ---------------------------------------------------------------------------

@rollouts_router.post(
    "",
    response_model=RolloutOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Create Upgrade Rollout",
)
def create_rollout(payload: RolloutCreateRequest, db: Session = Depends(get_db)) -> RolloutOut:
    """Admin: create upgrade rows for the first canary slice of an eligible host set."""
    try:
        result = upgrade_rollout_service.create_rollout(
            db,
            host_ids=payload.host_ids,
            target_version=payload.target_version,
            artifact_url=payload.artifact_url,
            artifact_sha256=payload.artifact_sha256,
            artifact_filename=payload.artifact_filename,
            canary_size=payload.canary_size,
            concurrency=payload.concurrency,
            stop_on_failure=payload.stop_on_failure,
            offline_policy=payload.offline_policy,
            skip_if_current=payload.skip_if_current,
            request_id=payload.request_id,
            previous_artifact_url=payload.previous_artifact_url,
            previous_artifact_sha256=payload.previous_artifact_sha256,
        )
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    return result


@rollouts_router.post(
    "/{request_id}/advance",
    response_model=RolloutOut,
    dependencies=[Depends(require_admin)],
    summary="Advance Upgrade Rollout",
)
def advance_rollout(
    request_id: str,
    payload: RolloutAdvanceRequest,
    db: Session = Depends(get_db),
) -> RolloutOut:
    """Admin: advance the rollout by creating upgrade rows for the next batch."""
    try:
        result = upgrade_rollout_service.advance_rollout(
            db,
            request_id,
            host_ids=payload.host_ids,
            target_version=payload.target_version,
            artifact_url=payload.artifact_url,
            artifact_sha256=payload.artifact_sha256,
            artifact_filename=payload.artifact_filename,
            canary_size=payload.canary_size,
            concurrency=payload.concurrency,
            stop_on_failure=payload.stop_on_failure,
            offline_policy=payload.offline_policy,
            skip_if_current=payload.skip_if_current,
            previous_artifact_url=payload.previous_artifact_url,
            previous_artifact_sha256=payload.previous_artifact_sha256,
        )
    except upgrade_service.UpgradeError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    return result


@rollouts_router.get(
    "/{request_id}",
    response_model=RolloutOut,
    dependencies=[Depends(require_admin)],
    summary="Get Rollout Status",
)
def get_rollout(request_id: str, db: Session = Depends(get_db)) -> RolloutOut:
    """Admin: get aggregate rollout status (DB rows only; skipped hosts not shown)."""
    result = upgrade_rollout_service.get_rollout(db, request_id)
    db.commit()
    return result
