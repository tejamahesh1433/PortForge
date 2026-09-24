"""Stuck-upgrade recovery, cancel, and retry operations (Phase 21).

All mutations go through create_upgrade or direct state transitions on
existing rows — never FAILED→SUCCEEDED, never terminal mutation (except
retry which creates a NEW row).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host_upgrade import HostUpgrade
from ..repositories.upgrade_repository import UpgradeRepository
from ..services.upgrade_service import (
    UpgradeError,
    UpgradeNotFoundError,
    create_upgrade,
)

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}

_STUCK_PENDING = {"APPROVED", "WAITING_FOR_AGENT"}
_STUCK_INFLIGHT = {"DOWNLOADING", "VERIFYING", "INSTALLING"}
_STUCK_RESTART = {"RESTARTING", "VERIFYING_HEALTH"}

_CANCEL_SAFE = {"APPROVED", "WAITING_FOR_AGENT", "DOWNLOADING", "VERIFYING"}
_CANCEL_REJECT = {"INSTALLING", "RESTARTING", "VERIFYING_HEALTH"}


def recover_stuck_upgrades(
    db: Session,
    *,
    now: Optional[datetime] = None,
    limit: int = 100,
) -> list[HostUpgrade]:
    """Mark timed-out non-terminal upgrades as FAILED.

    Applies per-bucket thresholds from settings (PORTFORGE_UPGRADE_STUCK_*).
    Never touches SUCCEEDED/FAILED/ROLLED_BACK rows.  Never converts FAILED
    to SUCCEEDED.  Returns the list of rows that were transitioned.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    settings = get_settings()
    repo = UpgradeRepository(db)
    candidates = repo.list_stuck_candidates(limit=limit)

    recovered: list[HostUpgrade] = []
    for upgrade in candidates:
        state = upgrade.state
        if state in TERMINAL_STATES:
            continue

        if state in _STUCK_PENDING:
            threshold = settings.upgrade_stuck_pending_seconds
        elif state in _STUCK_INFLIGHT:
            threshold = settings.upgrade_stuck_inflight_seconds
        elif state in _STUCK_RESTART:
            threshold = settings.upgrade_stuck_restart_seconds
        else:
            continue

        updated = upgrade.updated_at
        if updated is None:
            continue
        # updated_at may be timezone-naive (stored as UTC); normalise.
        if updated.tzinfo is None:
            from datetime import timezone as _tz
            updated = updated.replace(tzinfo=_tz.utc)

        age = (now - updated).total_seconds()
        if age >= threshold:
            stuck_from = state
            upgrade.state = "FAILED"
            upgrade.failure_reason = f"stuck_timeout:{state}"
            upgrade.completed_at = now
            db.flush()
            recovered.append(upgrade)

            from ..services.upgrade_audit import EVT_STUCK, EVT_FAILED, emit_upgrade_event

            emit_upgrade_event(
                db,
                host_id=upgrade.host_id,
                event_type=EVT_STUCK,
                summary=f"Stuck upgrade timed out in {stuck_from}",
                upgrade_id=upgrade.id,
                metadata={"stuck_from": stuck_from},
                idempotent=True,
            )
            emit_upgrade_event(
                db,
                host_id=upgrade.host_id,
                event_type=EVT_FAILED,
                summary=f"Upgrade failed: stuck_timeout:{stuck_from}",
                upgrade_id=upgrade.id,
                metadata={"failure_reason": f"stuck_timeout:{stuck_from}"},
                idempotent=True,
            )

    return recovered


def cancel_upgrade(db: Session, upgrade_id: uuid.UUID) -> HostUpgrade:
    """Cancel an upgrade that has not reached an unrecoverable in-progress state.

    SAFE (→ FAILED operator_cancelled): APPROVED, WAITING_FOR_AGENT,
                                        DOWNLOADING, VERIFYING
    REJECT (409): INSTALLING, RESTARTING, VERIFYING_HEALTH, any terminal
    """
    repo = UpgradeRepository(db)
    upgrade = repo.get(upgrade_id)
    if upgrade is None:
        raise UpgradeNotFoundError()

    if upgrade.state in TERMINAL_STATES:
        raise UpgradeError(
            f"Upgrade is already in terminal state {upgrade.state!r} and cannot be cancelled.",
            status_code=409,
        )
    if upgrade.state in _CANCEL_REJECT:
        raise UpgradeError(
            f"Cannot cancel upgrade in state {upgrade.state!r}: "
            "installation or restart is already in progress.",
            status_code=409,
        )
    if upgrade.state not in _CANCEL_SAFE:
        raise UpgradeError(
            f"Unrecognised state {upgrade.state!r}.",
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    upgrade.state = "FAILED"
    upgrade.failure_reason = "operator_cancelled"
    upgrade.completed_at = now
    db.flush()

    from ..services.upgrade_audit import EVT_CANCELLED, emit_upgrade_event

    emit_upgrade_event(
        db,
        host_id=upgrade.host_id,
        event_type=EVT_CANCELLED,
        summary="Upgrade cancelled by operator",
        upgrade_id=upgrade.id,
        metadata={"failure_reason": "operator_cancelled"},
        idempotent=True,
    )
    return upgrade


def retry_upgrade(
    db: Session,
    upgrade_id: uuid.UUID,
    *,
    request_id: Optional[str] = None,
) -> HostUpgrade:
    """Create a NEW upgrade row reusing the same artifact and host.

    Only allowed from FAILED state.  Never mutates the original FAILED row.
    The new request_id defaults to ``{original_request_id}:retry:{8-hex}``.
    """
    repo = UpgradeRepository(db)
    original = repo.get(upgrade_id)
    if original is None:
        raise UpgradeNotFoundError()

    if original.state != "FAILED":
        raise UpgradeError(
            f"Retry is only allowed from FAILED state, not {original.state!r}.",
            status_code=409,
        )

    if request_id is None:
        base = original.request_id or str(original.id)
        request_id = f"{base}:retry:{uuid.uuid4().hex[:8]}"

    new_upgrade = create_upgrade(
        db,
        host_id=original.host_id,
        target_version=original.target_version,
        artifact_url=original.artifact_url,
        artifact_sha256=original.artifact_sha256,
        artifact_filename=original.artifact_filename,
        request_id=request_id,
        created_by="admin",
        previous_artifact_url=original.previous_artifact_url,
        previous_artifact_sha256=original.previous_artifact_sha256,
    )

    from ..services.upgrade_audit import EVT_RETRY, emit_upgrade_event

    # Only emit RETRY when a new row was created (idempotent create returns existing)
    if new_upgrade.id != original.id:
        emit_upgrade_event(
            db,
            host_id=original.host_id,
            event_type=EVT_RETRY,
            summary=f"Operator retry of failed upgrade {original.id}",
            upgrade_id=new_upgrade.id,
            metadata={
                "parent_upgrade_id": str(original.id),
                "request_id": request_id,
            },
            idempotent=True,
        )
    return new_upgrade
