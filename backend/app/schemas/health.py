from __future__ import annotations

from .common import ApiModel


class HealthOut(ApiModel):
    status: str
    service: str
    database: str
    version: str
