"""Agent enrollment tokens and per-host credentials.

Only *hashes* of secrets are ever persisted (SHA-256 of the raw token --
see security/tokens.py) -- the raw enrollment token and the raw per-host
agent token exist only transiently: in the admin's terminal when minted,
and in the agent's local, protected config file (never in a project's
`.portforge.yml`, never logged). See security/tokens.py and
api/agents.py for the full enrollment flow.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid


class EnrollmentToken(Base):
    """A one-time (or admin-defined-expiry) token an admin mints out of
    band and gives to a new machine so it can register itself. Consumed
    (marked used) on successful registration -- a consumed or expired
    token can never be replayed to register a second host.
    """

    __tablename__ = "enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 hex
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_host_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AgentCredential(Base):
    """The per-host token an agent uses to authenticate every request after
    enrollment. One active credential per host; "revocation" replaces the
    stored hash (or sets revoked_at) rather than trying to track multiple
    simultaneously-valid tokens per host, which Phase 5 doesn't need.
    """

    __tablename__ = "agent_credentials"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_uuid)
    # Not unique: revoking + reissuing a credential (see replace_credential_for_host)
    # inserts a new row and keeps the old (revoked) one for history, so a
    # host can have more than one row here over its lifetime -- exactly one
    # of which is ever active (revoked_at IS NULL) at a time.
    host_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("hosts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 hex

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
