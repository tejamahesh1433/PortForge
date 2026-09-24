"""Queries for HostDeployment and DeploymentRevision entities."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models.deployment_revision import DeploymentRevision
from ..models.host_deployment import HostDeployment
from ..services.deployment_states import TERMINAL_STATES


class DeploymentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, deployment_id: uuid.UUID) -> Optional[HostDeployment]:
        return self.db.get(HostDeployment, deployment_id)

    def get_revision(self, revision_id: uuid.UUID) -> Optional[DeploymentRevision]:
        return self.db.get(DeploymentRevision, revision_id)

    def get_by_request_id_for_host(
        self, host_id: uuid.UUID, request_id: str
    ) -> Optional[HostDeployment]:
        stmt = (
            select(HostDeployment)
            .where(
                HostDeployment.host_id == host_id,
                HostDeployment.request_id == request_id,
            )
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_non_terminal_for_scope(
        self,
        host_id: uuid.UUID,
        project: str,
        environment: str,
    ) -> Optional[HostDeployment]:
        stmt = (
            select(HostDeployment)
            .where(
                HostDeployment.host_id == host_id,
                HostDeployment.project == project,
                HostDeployment.environment == environment,
                HostDeployment.state.not_in(TERMINAL_STATES),
            )
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_pending_for_host(self, host_id: uuid.UUID) -> Optional[HostDeployment]:
        """Return a non-terminal deployment ready for agent pickup or reclaim.

        - APPROVED jobs are always eligible (first claim / same-host retry).
        - In-progress jobs (TRANSFERRING/STARTING/...) are eligible only when the
          claim lease is missing or expired, so a restarted agent can reclaim
          after interruption without allowing a live claimant to be stolen.
        """
        from datetime import datetime, timezone

        from ..services.deployment_states import TERMINAL_STATES

        now = datetime.now(timezone.utc)
        stmt = (
            select(HostDeployment)
            .where(
                HostDeployment.host_id == host_id,
                HostDeployment.state.not_in(TERMINAL_STATES),
                or_(
                    HostDeployment.state == "APPROVED",
                    HostDeployment.claim_expires_at.is_(None),
                    HostDeployment.claim_expires_at <= now,
                ),
            )
            .order_by(HostDeployment.created_at.asc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_known_good_revision(
        self,
        host_id: uuid.UUID,
        project: str,
        environment: str,
    ) -> Optional[DeploymentRevision]:
        stmt = (
            select(DeploymentRevision)
            .where(
                DeploymentRevision.host_id == host_id,
                DeploymentRevision.project == project,
                DeploymentRevision.environment == environment,
                DeploymentRevision.is_known_good.is_(True),
            )
            .order_by(DeploymentRevision.created_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_for_host(self, host_id: uuid.UUID) -> Sequence[HostDeployment]:
        stmt = (
            select(HostDeployment)
            .where(HostDeployment.host_id == host_id)
            .order_by(HostDeployment.created_at.desc())
        )
        return self.db.execute(stmt).scalars().all()
