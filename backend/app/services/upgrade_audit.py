"""Upgrade lifecycle audit via existing ActivityEvent table (Phase 22).

No new audit system — reuses activity_events. Idempotent for terminal
SUCCEEDED/FAILED emissions keyed by (upgrade_id, event_type).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.activity import ActivityEvent
from ..repositories.activity_repository import ActivityRepository

# event_type column is String(32)
EVT_CREATED = "UPGRADE_CREATED"
EVT_CLAIMED = "UPGRADE_CLAIMED"
EVT_STATE = "UPGRADE_STATE"
EVT_SUCCEEDED = "UPGRADE_SUCCEEDED"
EVT_FAILED = "UPGRADE_FAILED"
EVT_CANCELLED = "UPGRADE_CANCELLED"
EVT_RETRY = "UPGRADE_RETRY"
EVT_ROLLBACK = "UPGRADE_ROLLBACK"
EVT_STUCK = "UPGRADE_STUCK"
EVT_RECONCILED = "UPGRADE_RECONCILED"
EVT_ROLLOUT_STOP = "UPGRADE_ROLLOUT_STOP"


def _has_event(
    db: Session,
    *,
    host_id: uuid.UUID,
    event_type: str,
    upgrade_id: uuid.UUID,
) -> bool:
    """True if an activity event already exists for this upgrade+type."""
    rows = db.scalars(
        select(ActivityEvent)
        .where(
            ActivityEvent.host_id == host_id,
            ActivityEvent.event_type == event_type,
        )
        .order_by(ActivityEvent.timestamp.desc())
        .limit(50)
    ).all()
    uid = str(upgrade_id)
    for row in rows:
        meta = row.metadata_json or {}
        if meta.get("upgrade_id") == uid:
            return True
    return False


def emit_upgrade_event(
    db: Session,
    *,
    host_id: uuid.UUID,
    event_type: str,
    summary: str,
    upgrade_id: uuid.UUID,
    metadata: Optional[dict[str, Any]] = None,
    idempotent: bool = False,
    now: Optional[datetime] = None,
) -> Optional[ActivityEvent]:
    """Append an upgrade activity event.

    When ``idempotent`` is True, skip if the same (host, event_type, upgrade_id)
    was already recorded — used for SUCCEEDED/FAILED so heartbeat reconcile
    cannot flood the activity feed.
    """
    if len(event_type) > 32:
        event_type = event_type[:32]

    if idempotent and _has_event(db, host_id=host_id, event_type=event_type, upgrade_id=upgrade_id):
        return None

    if now is None:
        now = datetime.now(timezone.utc)

    meta = dict(metadata or {})
    meta["upgrade_id"] = str(upgrade_id)

    event = ActivityEvent(
        host_id=host_id,
        timestamp=now,
        event_type=event_type,
        summary=summary[:2048],
        metadata_json=meta,
        source="upgrade",
    )
    ActivityRepository(db).add(event)
    return event
