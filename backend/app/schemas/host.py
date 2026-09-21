from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from .common import ApiModel


class HostOut(ApiModel):
    id: uuid.UUID
    hostname: str
    display_name: Optional[str]
    operating_system: str
    os_version: Optional[str]
    architecture: Optional[str]
    agent_version: Optional[str]
    docker_available: bool
    first_seen: datetime
    last_seen: datetime
    status: str

    # Derived health fields
    health_state: str = "HEALTHY"
    health_reason: str = "AGENT_HEALTHY"
    age_seconds: int = 0
    snapshot_age_seconds: Optional[int] = None


class HostDiagnosticsOut(ApiModel):
    host: HostOut
    last_scan_observed_at: Optional[datetime]
    stale_threshold_seconds: int
    offline_threshold_seconds: int
