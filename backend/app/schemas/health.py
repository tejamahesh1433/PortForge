from __future__ import annotations

from .common import ApiModel


class HealthOut(ApiModel):
    status: str
    service: str
    database: str
    version: str
    # v1.1-A: Central's own agent<->Central wire-protocol version --
    # unauthenticated, always present, so `portforge doctor` (which may
    # run on a host with no agent credential yet) can compare it against
    # the agent's own canonical protocol_version without needing to
    # enroll first. See services/compatibility_service.py.
    protocol_version: int


class GlobalDiagnosticsOut(ApiModel):
    status: str
    service: str
    database: str
    version: str
    host_count_total: int
    host_count_healthy: int
    host_count_stale: int
    host_count_offline: int
    latest_ingestion_time: str | None
