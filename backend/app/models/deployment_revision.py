"""DeploymentRevision entity — durable immutable package/revision identity."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class DeploymentRevision(Base, TimestampMixin):
    __tablename__ = "deployment_revisions"

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
    revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    package_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("host_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_known_good: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    host: Mapped["Host"] = relationship("Host", back_populates="deployment_revisions")  # type: ignore[name-defined]
    source_deployment: Mapped[Optional["HostDeployment"]] = relationship(  # type: ignore[name-defined]
        "HostDeployment",
        foreign_keys=[source_deployment_id],
        back_populates="source_revision",
    )

    __table_args__ = (
        UniqueConstraint("host_id", "revision_id", name="uq_deployment_revisions_host_revision"),
        Index("ix_deployment_revisions_project_env_host", "project", "environment", "host_id"),
        Index(
            "ix_deployment_revisions_known_good",
            "host_id",
            "project",
            "environment",
            postgresql_where=text("is_known_good"),
        ),
    )
