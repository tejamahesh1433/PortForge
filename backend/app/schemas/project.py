"""Operational project summaries over existing host-scoped observations."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from .activity import ActivityEventOut
from .common import ApiModel, Page
from .conflict import ConflictOut
from .port import PortObservationOut
from .reservation import ReservationOut


class ProjectServiceEntry(ApiModel):
    host_id: uuid.UUID
    hostname: str
    port: int
    protocol: str
    service_name: Optional[str]
    purpose: Optional[str]
    category: Optional[str]
    state: str


class ProjectHostOut(ApiModel):
    host_id: uuid.UUID
    hostname: str
    operating_system: str
    docker_available: bool
    health_state: str
    health_reason: str
    age_seconds: int
    snapshot_age_seconds: Optional[int]
    binding_count: int


class ProjectOut(ApiModel):
    project_name: str
    host_count: int
    port_count: int
    process_count: int = 0
    docker_binding_count: int = 0
    container_count: int = 0
    reservation_count: int = 0
    conflict_count: int = 0
    healthy_host_count: int = 0
    stale_host_count: int = 0
    offline_host_count: int = 0
    last_activity: Optional[datetime] = None
    hosts: List[str]
    entries: List[ProjectServiceEntry] = []


class ProjectDetailOut(ProjectOut):
    host_details: List[ProjectHostOut]
    ports: Page[PortObservationOut]
    reservations: Page[ReservationOut]
    conflicts: List[ConflictOut]
    activity: List[ActivityEventOut]
