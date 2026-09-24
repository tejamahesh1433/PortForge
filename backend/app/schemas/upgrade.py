"""Schemas for agent upgrade management (Phase 10)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import ApiModel


class PendingUpgradeOut(ApiModel):
    """Delivered to agent on heartbeat response — minimal set of fields
    the agent needs to begin downloading and verifying the artifact.
    """

    id: uuid.UUID
    target_version: str
    artifact_url: str
    artifact_sha256: str
    artifact_filename: Optional[str] = None
    state: str
    # True only for admin rollback deliveries (target < host agent version).
    # Normal upgrades never set this; Central rejects create-time downgrades.
    allow_downgrade: bool = False


class UpgradeCreateRequest(ApiModel):
    """Admin-only upgrade request body."""

    target_version: str = Field(min_length=1, max_length=64)
    artifact_url: str = Field(min_length=1, max_length=2048)
    artifact_sha256: str = Field(min_length=64, max_length=64)
    artifact_filename: Optional[str] = Field(default=None, max_length=255)
    request_id: Optional[str] = Field(default=None, max_length=128)
    # Optional: prior artifact metadata to support rollback later.
    # Admin supplies this when the host's agent_version was installed
    # via an out-of-band mechanism (not tracked in host_upgrades).
    previous_artifact_url: Optional[str] = Field(default=None, max_length=2048)
    previous_artifact_sha256: Optional[str] = Field(default=None, max_length=64)


class UpgradeOut(ApiModel):
    """Full upgrade row, returned to admin."""

    id: uuid.UUID
    host_id: uuid.UUID
    request_id: Optional[str] = None
    state: str
    target_version: str
    artifact_url: str
    artifact_sha256: str
    artifact_filename: Optional[str] = None
    previous_version: Optional[str] = None
    previous_artifact_url: Optional[str] = None
    previous_artifact_sha256: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None


class UpgradeStatusUpdate(ApiModel):
    """Agent-reported status update for a specific upgrade."""

    state: str
    failure_reason: Optional[str] = Field(default=None, max_length=1024)
    # Agent reports its running version after installation — required for
    # Central to accept SUCCEEDED (must equal target_version exactly).
    reported_version: Optional[str] = Field(default=None, max_length=64)


class UpgradeSummary(ApiModel):
    """Compact summary included in FleetHostOut.active_upgrade."""

    id: uuid.UUID
    state: str
    target_version: str


# ---------------------------------------------------------------------------
# Phase 21: recovery, cancel, retry
# ---------------------------------------------------------------------------

class StuckRecoveryOut(ApiModel):
    """Result of a POST /api/upgrades/recover-stuck call."""

    recovered: int
    upgrade_ids: list[uuid.UUID]


# ---------------------------------------------------------------------------
# Phase 21: fleet rollout
# ---------------------------------------------------------------------------

class RolloutCreateRequest(ApiModel):
    """Body for POST /api/upgrade-rollouts."""

    host_ids: list[uuid.UUID]
    target_version: str = Field(min_length=1, max_length=64)
    artifact_url: str = Field(min_length=1, max_length=2048)
    artifact_sha256: str = Field(min_length=64, max_length=64)
    artifact_filename: Optional[str] = Field(default=None, max_length=255)
    canary_size: int = Field(default=0, ge=0)
    concurrency: int = Field(default=1, ge=1)
    stop_on_failure: bool = True
    offline_policy: str = Field(default="WAIT")   # WAIT | SKIP | FAIL
    skip_if_current: bool = True
    request_id: Optional[str] = Field(default=None, max_length=128)
    # Optional previous artifact metadata (forwarded to create_upgrade for rollback support)
    previous_artifact_url: Optional[str] = Field(default=None, max_length=2048)
    previous_artifact_sha256: Optional[str] = Field(default=None, max_length=64)


class RolloutAdvanceRequest(ApiModel):
    """Body for POST /api/upgrade-rollouts/{request_id}/advance.

    Accepts the full host set and policy again so Central can compute
    which hosts still need upgrade rows without storing policy in the DB.
    """

    host_ids: list[uuid.UUID]
    target_version: str = Field(min_length=1, max_length=64)
    artifact_url: str = Field(min_length=1, max_length=2048)
    artifact_sha256: str = Field(min_length=64, max_length=64)
    artifact_filename: Optional[str] = Field(default=None, max_length=255)
    canary_size: int = Field(default=0, ge=0)
    concurrency: int = Field(default=1, ge=1)
    stop_on_failure: bool = True
    offline_policy: str = Field(default="WAIT")
    skip_if_current: bool = True
    previous_artifact_url: Optional[str] = Field(default=None, max_length=2048)
    previous_artifact_sha256: Optional[str] = Field(default=None, max_length=64)


class RolloutMemberOut(ApiModel):
    """One host's participation in a rollout."""

    host_id: uuid.UUID
    disposition: str   # upgrading | skipped_current | skipped_offline | skipped_decommissioned | offline_failed
    upgrade_id: Optional[uuid.UUID] = None
    state: Optional[str] = None


class RolloutOut(ApiModel):
    """Aggregate rollout status."""

    request_id: str
    status: str           # active | paused | completed
    succeeded: int
    failed: int
    in_flight: int
    waiting: int
    skipped: int
    total: int
    members: list[RolloutMemberOut]
