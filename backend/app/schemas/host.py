from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel
from .upgrade import UpgradeSummary


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

    # Phase 8: operator lifecycle (independent of health_state).
    lifecycle_state: str = "ACTIVE"
    decommissioned_at: Optional[datetime] = None
    decommission_reason: Optional[str] = None


class HostDecommissionRequest(ApiModel):
    reason: Optional[str] = Field(default=None, max_length=1024)


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


class FleetHostOut(ApiModel):
    """Fleet-intelligence view of a host (Phase 9/10).

    Extends HostOut fields with derived update_availability, Phase 9
    columns, and a compact active_upgrade summary.
    """

    id: uuid.UUID
    hostname: str
    display_name: Optional[str]
    operating_system: str
    os_version: Optional[str]
    architecture: Optional[str]
    agent_version: Optional[str]
    docker_available: bool
    first_seen: datetime
    status: str

    # Health
    health_state: str = "HEALTHY"
    health_reason: str = "AGENT_HEALTHY"
    age_seconds: int = 0

    # Protocol
    protocol_version: Optional[int] = None
    protocol_compatibility: str = "unknown"

    # Lifecycle
    lifecycle_state: str = "ACTIVE"
    decommissioned_at: Optional[datetime] = None
    decommission_reason: Optional[str] = None

    # Fleet intelligence (Phase 9)
    last_heartbeat: datetime
    last_sync: Optional[datetime] = None
    update_availability: str = "UNKNOWN"
    target_version: Optional[str] = None
    contract_version: Optional[int] = None
    python_version: Optional[str] = None
    last_error: Optional[str] = None

    # Upgrade state (Phase 10) — absent when no active upgrade
    active_upgrade: Optional[UpgradeSummary] = None
