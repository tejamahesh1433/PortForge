"""Queries for enrollment tokens and per-host agent credentials."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.agent_credential import AgentCredential, EnrollmentToken


class AgentRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- Enrollment tokens --------------------------------------------------

    def create_enrollment_token(
        self, token_hash: str, created_at: datetime, expires_at: Optional[datetime], label: Optional[str]
    ) -> EnrollmentToken:
        token = EnrollmentToken(
            token_hash=token_hash, created_at=created_at, expires_at=expires_at, label=label
        )
        self.db.add(token)
        self.db.flush()
        return token

    def get_enrollment_token_by_hash(self, token_hash: str) -> Optional[EnrollmentToken]:
        stmt = select(EnrollmentToken).where(EnrollmentToken.token_hash == token_hash)
        return self.db.execute(stmt).scalar_one_or_none()

    def consume_enrollment_token(self, token: EnrollmentToken, host_id: uuid.UUID, consumed_at: datetime) -> None:
        token.consumed_at = consumed_at
        token.consumed_by_host_id = host_id
        self.db.flush()

    # --- Agent credentials ---------------------------------------------------

    def get_active_credential_by_token_hash(self, token_hash: str) -> Optional[AgentCredential]:
        stmt = select(AgentCredential).where(
            AgentCredential.token_hash == token_hash, AgentCredential.revoked_at.is_(None)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_active_credential_for_host(self, host_id: uuid.UUID) -> Optional[AgentCredential]:
        stmt = select(AgentCredential).where(
            AgentCredential.host_id == host_id, AgentCredential.revoked_at.is_(None)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def replace_credential_for_host(
        self, host_id: uuid.UUID, token_hash: str, created_at: datetime
    ) -> AgentCredential:
        """Issue a new credential for a host, revoking any prior one --
        used both on first enrollment and if a host is ever re-enrolled.
        """
        existing = self.get_active_credential_for_host(host_id)
        if existing is not None:
            existing.revoked_at = created_at
            self.db.flush()

        credential = AgentCredential(host_id=host_id, token_hash=token_hash, created_at=created_at)
        self.db.add(credential)
        self.db.flush()
        return credential
