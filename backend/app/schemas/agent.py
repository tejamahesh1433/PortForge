"""Schemas for the authenticated agent-facing endpoints: enrollment,
heartbeat, and observation-snapshot ingestion.

Every field an agent submits is validated here -- authentication proves
*who* is talking, not that *what* they sent is well-formed or reasonable.
Ports are bounded 0-65535, protocol must be a real value, strings are
length-capped, and a whole snapshot batch is capped
(`PORTFORGE_MAX_OBSERVATIONS_PER_SNAPSHOT`) so a malformed or hostile
payload can't exhaust memory or blow up ingestion time.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import Field, field_validator

from .common import ApiModel
from .deployment import PendingDeploymentOut
from .probe import PendingProbeOut
from .upgrade import PendingUpgradeOut as PendingUpgradeOut  # noqa: F401 (re-exported)

_MAX_STR = 4096
_MAX_SHORT_STR = 255


class EnrollmentRequest(ApiModel):
    enrollment_token: str = Field(min_length=1, max_length=_MAX_STR)
    host_id: uuid.UUID
    hostname: str = Field(min_length=1, max_length=_MAX_SHORT_STR)
    operating_system: str = Field(min_length=1, max_length=32)
    os_version: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    architecture: Optional[str] = Field(default=None, max_length=64)
    agent_version: Optional[str] = Field(default=None, max_length=64)
    docker_available: bool = False
    # v1.1-A: additive, optional -- an omitted field (a v1.0 agent) is
    # handled as "unknown" compatibility, never rejected. NOT persisted
    # anywhere (no Host column) -- see services/compatibility_service.py.
    protocol_version: Optional[int] = Field(default=None, ge=1)
    # Phase 9: additive, optional fleet intelligence fields
    contract_version: Optional[int] = Field(default=None, ge=1)
    python_version: Optional[str] = Field(default=None, max_length=64)


class EnrollmentResponse(ApiModel):
    host_id: uuid.UUID
    agent_token: str  # returned exactly once, at enrollment time -- never again
    # v1.1-A: advisory only, "compatible" | "warning" | "unknown" -- see
    # services/compatibility_service.py. Never gates enrollment.
    protocol_compatibility: str = "unknown"


class HeartbeatRequest(ApiModel):
    host_id: uuid.UUID
    hostname: str = Field(min_length=1, max_length=_MAX_SHORT_STR)
    operating_system: str = Field(min_length=1, max_length=32)
    os_version: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    architecture: Optional[str] = Field(default=None, max_length=64)
    agent_version: Optional[str] = Field(default=None, max_length=64)
    docker_available: bool = False
    timestamp: datetime
    protocol_version: Optional[int] = Field(default=None, ge=1)
    # Phase 9: additive, optional fleet intelligence fields
    contract_version: Optional[int] = Field(default=None, ge=1)
    python_version: Optional[str] = Field(default=None, max_length=64)


class HeartbeatResponse(ApiModel):
    host_id: uuid.UUID
    last_seen: datetime
    status: str
    protocol_compatibility: str = "unknown"
    # v1.1-B: additive. A legacy agent that doesn't look for this field
    # simply never acts on it -- the probe(s) just expire unclaimed (see
    # docs/v1.1/remote-probe-design.md "Offline/stale host interaction").
    pending_probes: List[PendingProbeOut] = []
    # Phase 10: additive. A legacy agent that doesn't read this field simply
    # never acts on it -- the upgrade just stays APPROVED/WAITING_FOR_AGENT.
    pending_upgrade: Optional[PendingUpgradeOut] = None
    # Phase 18: additive. Legacy agents ignore unknown fields.
    pending_deployment: Optional[PendingDeploymentOut] = None


class ObservationIn(ApiModel):
    port: int = Field(ge=0, le=65535)
    protocol: str = Field(pattern="^(tcp|udp)$")
    bind_address: str = Field(min_length=1, max_length=64)
    state: str = Field(min_length=1, max_length=16)
    source: str = Field(min_length=1, max_length=32)

    pid: Optional[int] = Field(default=None, ge=0, le=4_294_967_295)
    process_name: Optional[str] = Field(default=None, max_length=512)
    process_path: Optional[str] = Field(default=None, max_length=1024)
    working_directory: Optional[str] = Field(default=None, max_length=1024)

    container_id: Optional[str] = Field(default=None, max_length=128)
    container_name: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    container_image: Optional[str] = Field(default=None, max_length=512)
    container_port: Optional[int] = Field(default=None, ge=0, le=65535)

    docker_compose_project: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    service_name: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)

    project_name: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    purpose: Optional[str] = Field(default=None, max_length=_MAX_SHORT_STR)
    category: Optional[str] = Field(default=None, max_length=64)
    detection_confidence: Optional[str] = Field(default=None, max_length=16)

    first_seen: datetime
    last_seen: datetime


class SnapshotSubmission(ApiModel):
    """A complete-snapshot submission -- see services/ingestion_service.py
    for the exact "this replaces everything currently known for this host"
    semantics this implies.
    """

    scan_id: uuid.UUID
    host_id: uuid.UUID
    observed_at: datetime
    observations: List[ObservationIn]

    @field_validator("observations")
    @classmethod
    def _bounded_batch(cls, value: List[ObservationIn]) -> List[ObservationIn]:
        # The actual numeric limit is enforced in the service layer (it's
        # configurable via settings, which a plain Pydantic validator can't
        # see without extra plumbing) -- this just guards against an
        # absurd, memory-hostile payload before it's even fully parsed.
        if len(value) > 100_000:
            raise ValueError("snapshot batch is unreasonably large")
        return value


class SnapshotResult(ApiModel):
    scan_id: uuid.UUID
    accepted: bool
    reason: Optional[str] = None
    observations_processed: int = 0
    appeared: int = 0
    changed: int = 0
    disappeared: int = 0
    # How many of `observations_processed` were collapsed into an
    # already-counted canonical binding identity (host+port+protocol+
    # bind_address) -- see ingestion_service.py's "Duplicate-binding
    # canonicalization". Lets a caller tell "raw observations != canonical
    # bindings" apart from actual data loss.
    duplicates_merged: int = 0
