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

    # v1.1-D: raw last-reported protocol_version (see models/host.py's
    # column docstring for why this reverses a deliberate v1.1-A
    # non-persistence choice). `protocol_compatibility` is always
    # recomputed live from this value via the existing, unchanged
    # compatibility_service.evaluate_protocol_compatibility() -- never a
    # stored verdict.
    protocol_version: Optional[int] = None
    protocol_compatibility: str = "unknown"


class HostDiagnosticsOut(ApiModel):
    host: HostOut
    last_scan_observed_at: Optional[datetime]
    stale_threshold_seconds: int
    offline_threshold_seconds: int
    # v1.1-D task Sec8: whether Central has ANY evidence this host's agent
    # has ever answered a remote bind probe. "unknown" covers both "never
    # asked" and "legacy agent" -- Central cannot honestly distinguish the
    # two from data alone (see docs/v1.1/v1.1-d-data-audit.md).
    probe_capability: str = "unknown"
