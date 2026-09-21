from __future__ import annotations

from .common import ApiModel


class HealthOut(ApiModel):
    status: str
    service: str
    database: str
    version: str


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
