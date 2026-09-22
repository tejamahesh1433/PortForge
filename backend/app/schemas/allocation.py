"""Phase 8A: atomic multi-port allocation bundles.

See docs/phase8a_allocation_audit.md for the terminology (Recommendation
vs Reservation vs Allocation) and docs/phase8a_agent_allocation.md for the
full API/error/idempotency contract with examples. This module is schemas
only -- all behavior lives in services/allocation_service.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import Field, field_validator, model_validator

from .common import ApiModel

# A bundle this large is already an unusual request; bounding it keeps one
# allocation call from becoming an unbounded loop inside a single
# host-locked transaction (see services/allocation_service.py -- the whole
# bundle runs under one `pg_advisory_xact_lock`, so an enormous bundle
# would hold that lock, and therefore block every other allocation AND
# every snapshot ingestion for that host, for an unreasonably long time).
MAX_BUNDLE_SIZE = 20


class AllocationRequestItem(ApiModel):
    """One requested port within a bundle. `name` is the caller's own
    bundle-unique key (e.g. "frontend", "database") -- distinct from
    `purpose`, which selects the port range (see
    recommendation_service.DEFAULT_RANGES) and must be one of the known
    service types.
    """

    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=64)
    protocol: str = Field(default="tcp", pattern="^(tcp|udp)$")
    # "Try this port first if safe" -- never "force this port even if
    # occupied" (see docs/phase8a_agent_allocation.md "Preferred port").
    preferred_port: Optional[int] = Field(default=None, ge=1, le=65535)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class AllocationIn(ApiModel):
    project: str = Field(min_length=1, max_length=255)
    host_id: uuid.UUID
    requests: List[AllocationRequestItem] = Field(min_length=1, max_length=MAX_BUNDLE_SIZE)
    # Optional client-generated idempotency key (Phase 8A §13). Omitted ->
    # no replay/conflict protection for that specific call; every call
    # allocates fresh.
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def _unique_request_names(self) -> "AllocationIn":
        names = [item.name for item in self.requests]
        if len(names) != len(set(names)):
            raise ValueError("request names must be unique within a bundle")
        return self


class AllocationHostOut(ApiModel):
    id: uuid.UUID
    hostname: str


class AllocationEntryOut(ApiModel):
    name: str
    purpose: str
    protocol: str
    port: int
    reservation_id: uuid.UUID
    # v1.1-D, both additive/optional for backward compatibility with any
    # caller constructing this schema without them:
    bind_address: Optional[str] = None
    # Live-computed at read time from v1.1-B's HostProbe data, never a
    # stored creation-time snapshot -- see docs/v1.1/v1.1-d-data-audit.md
    # "Remote-probe evidence on allocations" for why. Same 5-value
    # contract as AllocationValidationOut.bind_probe.
    bind_probe: str = "not_remote_capable"


class AllocationValidationOut(ApiModel):
    """Honest disclosure of how strong this allocation's guarantee
    actually is -- see docs/phase8a_allocation_audit.md §7. Central never
    performs a real socket bind() for any host, including the one it
    happens to run on; `bind_probe` is always `"not_remote_capable"` in
    Phase 8A. `snapshot_age_seconds` is the target host's own last-sync
    age at the moment of allocation, so a caller can judge freshness for
    themselves rather than being told a bare "yes".
    """

    snapshot_age_seconds: int
    host_health_state: str
    bind_probe: str = "not_remote_capable"


class AllocationOut(ApiModel):
    idempotent_replay: bool = False
    allocation_id: uuid.UUID
    project: str
    host: AllocationHostOut
    status: str
    allocations: List[AllocationEntryOut]
    validation: AllocationValidationOut
    created_at: datetime
    released_at: Optional[datetime]
    # v1.1-D: additive, optional -- the caller-supplied idempotency key
    # (Sec13), already stored on the Allocation row, simply never exposed
    # before now.
    request_id: Optional[str] = None
