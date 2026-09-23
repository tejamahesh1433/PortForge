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
