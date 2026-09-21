from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel


class ReservationIn(ApiModel):
    """Request body for POST /api/reservations (agent-authenticated: the
    reservation is always created for the *authenticated* host -- see
    api/reservations.py; there is no host_id field here on purpose).
    """

    port: int = Field(ge=0, le=65535)
    protocol: str = Field(default="tcp", pattern="^(tcp|udp)$")
    bind_address: Optional[str] = Field(default=None, max_length=64)
    project: str = Field(min_length=1, max_length=255)
    service: Optional[str] = Field(default=None, max_length=255)
    purpose: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=1024)
    local_reservation_id: Optional[str] = Field(default=None, max_length=64)


class DashboardReservationIn(ReservationIn):
    """Request body for POST /api/reservations/dashboard (unauthenticated UI endpoint).
    Unlike agent creation, the dashboard manages multiple hosts and must
    explicitly provide the host_id.
    """

    host_id: uuid.UUID


class ReservationOut(ApiModel):
    id: uuid.UUID
    host_id: uuid.UUID
    port: int
    protocol: str
    bind_address: Optional[str]
    project: str
    service: Optional[str]
    purpose: Optional[str]
    notes: Optional[str]
    local_reservation_id: Optional[str]
    allocation_id: Optional[uuid.UUID] = None
    request_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
