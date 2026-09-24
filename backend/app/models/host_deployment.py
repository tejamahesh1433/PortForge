"""HostDeployment entity — one durable execution attempt per apply or rollback."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class HostDeployment(Base, TimestampMixin):
    __tablename__ = "host_deployments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    host_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=False,
    )
    project: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    target_alias: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    package_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    package_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("deployment_revisions.id"),
        nullable=True,
    )
    rollback_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("deployment_revisions.id"),
        nullable=True,
    )
    ports_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ingress_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    health_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_agent_update_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    host: Mapped["Host"] = relationship("Host", back_populates="deployments")  # type: ignore[name-defined]
    result_revision: Mapped[Optional["DeploymentRevision"]] = relationship(  # type: ignore[name-defined]
        "DeploymentRevision",
        foreign_keys=[result_revision_id],
    )
    rollback_revision: Mapped[Optional["DeploymentRevision"]] = relationship(  # type: ignore[name-defined]
        "DeploymentRevision",
        foreign_keys=[rollback_revision_id],
    )
    source_revision: Mapped[Optional["DeploymentRevision"]] = relationship(  # type: ignore[name-defined]
        "DeploymentRevision",
        foreign_keys="DeploymentRevision.source_deployment_id",
        back_populates="source_deployment",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("host_id", "request_id", name="uq_host_deployments_host_request"),
        Index("ix_host_deployments_host_state", "host_id", "state"),
        Index("ix_host_deployments_project_env_host", "project", "environment", "host_id"),
        Index(
            "uq_host_deployments_active",
            "project",
            "environment",
            "host_id",
            unique=True,
            postgresql_where=text("state NOT IN ('SUCCEEDED', 'FAILED', 'ROLLED_BACK')"),
        ),
    )
