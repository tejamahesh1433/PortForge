"""v1.1-B: remote bind-probe request/result schemas.

See docs/v1.1/remote-probe-design.md. `PendingProbeOut` is delivered
additively inside the existing HeartbeatResponse (schemas/agent.py) --
there is no separate "poll for probes" endpoint. `ProbeResultIn` is the
body of the one new agent-facing endpoint this increment adds.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .common import ApiModel


class PendingProbeOut(ApiModel):
    probe_id: uuid.UUID
    port: int
    protocol: str
    bind_address: str


class ProbeResultIn(ApiModel):
    host_id: uuid.UUID
    probe_id: uuid.UUID
    # None means the agent's own probe ATTEMPT failed (e.g. could not even
    # create a socket) -- distinct from a successful attempt that found the
    # port occupied (available=False). See services/probe_service.py.
    available: Optional[bool] = None
    reason: Optional[str] = Field(default=None, max_length=512)


class ProbeResultOut(ApiModel):
    probe_id: uuid.UUID
    status: str
