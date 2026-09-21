from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional


class HostHealthState(str, enum.Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class HostHealthReason(str, enum.Enum):
    AGENT_HEALTHY = "AGENT_HEALTHY"
    AGENT_SYNC_STALE = "AGENT_SYNC_STALE"
    AGENT_OFFLINE = "AGENT_OFFLINE"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    CENTRAL_DATABASE_UNAVAILABLE = "CENTRAL_DATABASE_UNAVAILABLE"


def derive_health_state(
    last_seen: datetime,
    stale_after_seconds: int,
    offline_after_seconds: int,
    now: Optional[datetime] = None,
) -> tuple[HostHealthState, HostHealthReason, int]:
    """
    Derive the authoritative health state of a host from its last_seen timestamp.
    Returns (state, reason, age_seconds).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    # Ensure timezones are correct for calculation
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    age_seconds = int((now - last_seen).total_seconds())

    if age_seconds > offline_after_seconds:
        return HostHealthState.OFFLINE, HostHealthReason.AGENT_OFFLINE, age_seconds
    elif age_seconds > stale_after_seconds:
        return HostHealthState.STALE, HostHealthReason.AGENT_SYNC_STALE, age_seconds
    else:
        return HostHealthState.HEALTHY, HostHealthReason.AGENT_HEALTHY, age_seconds
