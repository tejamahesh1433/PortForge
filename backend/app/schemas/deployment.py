"""Schemas for deployment orchestration (Phase 18)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from .common import ApiModel


class DeploymentPlanRequest(ApiModel):
    host_id: uuid.UUID
    project: str = Field(min_length=1, max_length=256)
    environment: str = Field(min_length=1, max_length=64)
    target_alias: Optional[str] = Field(default=None, max_length=128)
    workspace_fingerprint: Optional[str] = Field(default=None, max_length=64)
    services: list[dict[str, Any]] = Field(default_factory=list)
    ingress_bindings: list[dict[str, Any]] = Field(default_factory=list)
    rollback_revision_id: Optional[str] = Field(default=None, max_length=64)


class DeploymentPlanOut(ApiModel):
    host_id: uuid.UUID
    project: str
    environment: str
    target_alias: Optional[str] = None
    workspace_fingerprint: Optional[str] = None
    services: list[dict[str, Any]]
    ingress_bindings: list[dict[str, Any]]
    rollback_revision_id: Optional[str] = None
    plan_hash: str


class DeploymentCreateRequest(ApiModel):
    host_id: uuid.UUID
    project: str = Field(min_length=1, max_length=256)
    environment: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    plan_hash: str = Field(min_length=64, max_length=64)
    package_uri: str = Field(min_length=1, max_length=2048)
    package_sha256: str = Field(min_length=64, max_length=64)
    package_manifest_sha256: str = Field(min_length=64, max_length=64)
    target_alias: Optional[str] = Field(default=None, max_length=128)
    workspace_fingerprint: Optional[str] = Field(default=None, max_length=64)
    ports_json: Optional[dict[str, Any]] = None
    ingress_json: Optional[dict[str, Any]] = None
    health_json: Optional[dict[str, Any]] = None
    rollback_revision_id: Optional[uuid.UUID] = None


class DeploymentRollbackRequest(ApiModel):
    request_id: str = Field(min_length=1, max_length=128)


class PendingDeploymentOut(ApiModel):
    deployment_id: uuid.UUID
    request_id: str
    project: str
    environment: str
    state: str
    package_uri: str
    package_sha256: str
    package_manifest_sha256: str
    plan_hash: str
    claim_token: Optional[str] = None
    claim_expires_at: Optional[datetime] = None


class DeploymentOut(ApiModel):
    id: uuid.UUID
    host_id: uuid.UUID
    project: str
    environment: str
    target_alias: Optional[str] = None
    request_id: str
    state: str
    plan_hash: str
    workspace_fingerprint: Optional[str] = None
    package_uri: str
    package_sha256: str
    package_manifest_sha256: str
    result_revision_id: Optional[uuid.UUID] = None
    rollback_revision_id: Optional[uuid.UUID] = None
    ports_json: Optional[dict[str, Any]] = None
    ingress_json: Optional[dict[str, Any]] = None
    health_json: Optional[dict[str, Any]] = None
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None
    approved_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    claim_token: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    last_agent_update_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeploymentStatusUpdate(ApiModel):
    state: str
    failure_code: Optional[str] = Field(default=None, max_length=64)
    failure_reason: Optional[str] = Field(default=None, max_length=1024)
    revision_id: Optional[str] = Field(default=None, max_length=64)


class DeploymentHealthUpdate(ApiModel):
    health_json: dict[str, Any]
