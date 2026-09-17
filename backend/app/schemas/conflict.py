"""Central conflict schema: the same evaluation rule as the agent's local
`evaluate.py` (unknown/different project ownership vs. a reservation =
conflict, never assumed safe), computed here across whatever the central
registry currently has on file for one host -- not a live re-check.
"""
from __future__ import annotations

import uuid
from typing import Optional

from .common import ApiModel


class ConflictOut(ApiModel):
    host_id: uuid.UUID
    hostname: str
    port: int
    protocol: str
    reserved_for_project: str
    reserved_for_service: Optional[str]
    actual_project: Optional[str]
    actual_process_name: Optional[str]
    actual_container_name: Optional[str]
    reason: str
