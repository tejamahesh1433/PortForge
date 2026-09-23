"""Agent registration/provisioning workflow.

Flow (see docs/architecture.md "Authentication / enrollment" for the full
write-up and why this is secure enough for a private-network deployment):

1. An admin mints an enrollment token out of band (`mint_enrollment_token`,
   behind `require_admin` -- see api/agents.py) -- only its SHA-256 hash is
   stored; the raw token is returned exactly once, to the admin's terminal.
2. A new agent calls `/api/agent/enroll` with that raw token plus its own
   already-generated persistent host UUID (see
   agent/portforge_agent/identity.py) and basic host facts.
3. `enroll_host` verifies the token is unexpired and unconsumed, registers
   (or re-registers) the Host row keyed by that UUID, issues a brand-new
   per-host agent credential (revoking any prior one), and marks the
   enrollment token consumed -- it can never be replayed to enroll a
   second host.
4. The agent stores the raw per-host token locally (never in a project
   `.portforge.yml`) and uses it as a Bearer credential on every future
   call.

This is intentionally not OAuth: a private-deployment bearer-token scheme
with hashed-at-rest storage and one-time enrollment tokens is
proportionate to the threat model (a small number of machines on a
network the admin controls), and avoids the complexity (client
registration, redirect flows, refresh-token rotation) that OAuth exists to
solve for a very different problem (third-party delegated access).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..repositories.agent_repository import AgentRepository
from ..repositories.host_repository import HostRepository
from ..security.tokens import generate_token, hash_token


class EnrollmentError(Exception):
    """Base class for enrollment failures -- see subclasses for the exact reason."""


class InvalidEnrollmentTokenError(EnrollmentError):
    pass


class EnrollmentTokenExpiredError(EnrollmentError):
    pass


class EnrollmentTokenConsumedError(EnrollmentError):
    pass


class HostDecommissionedError(EnrollmentError):
    """Same-UUID enroll rejected because Central holds a DECOMMISSIONED tombstone."""

    def __init__(self, message: str = "Host identity is decommissioned."):
        super().__init__(message)


@dataclass(frozen=True)
class MintedEnrollmentToken:
    raw_token: str  # returned exactly once -- never persisted or logged in raw form
    expires_at: Optional[datetime]


def mint_enrollment_token(
    db: Session, label: Optional[str] = None, ttl: Optional[timedelta] = timedelta(hours=24)
) -> MintedEnrollmentToken:
    repo = AgentRepository(db)
    raw_token = generate_token()
    now = datetime.now(timezone.utc)
    expires_at = (now + ttl) if ttl is not None else None
    repo.create_enrollment_token(hash_token(raw_token), created_at=now, expires_at=expires_at, label=label)
    return MintedEnrollmentToken(raw_token=raw_token, expires_at=expires_at)


@dataclass(frozen=True)
class EnrollmentResult:
    host_id: uuid.UUID
    agent_token: str  # returned exactly once


def enroll_host(
    db: Session,
    raw_enrollment_token: str,
    host_id: uuid.UUID,
    hostname: str,
    operating_system: str,
    os_version: Optional[str],
    architecture: Optional[str],
    agent_version: Optional[str],
    docker_available: bool,
    protocol_version: Optional[int] = None,
    contract_version: Optional[int] = None,
    python_version: Optional[str] = None,
) -> EnrollmentResult:
    agent_repo = AgentRepository(db)
    host_repo = HostRepository(db)

    token = agent_repo.get_enrollment_token_by_hash(hash_token(raw_enrollment_token))
    if token is None:
        raise InvalidEnrollmentTokenError("Enrollment token is invalid.")

    now = datetime.now(timezone.utc)
    if token.consumed_at is not None:
        raise EnrollmentTokenConsumedError("Enrollment token has already been used.")
    if token.expires_at is not None and token.expires_at < now:
        raise EnrollmentTokenExpiredError("Enrollment token has expired.")

    existing = host_repo.get(host_id)
    if existing is not None and (existing.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
        # Do not consume the enrollment token; operator must Reactivate first.
        raise HostDecommissionedError("Host identity is decommissioned.")

    host_repo.upsert(
        host_id=host_id,
        hostname=hostname,
        operating_system=operating_system,
        os_version=os_version,
        architecture=architecture,
        agent_version=agent_version,
        docker_available=docker_available,
        now=now,
        protocol_version=protocol_version,
        contract_version=contract_version,
        python_version=python_version,
    )

    raw_agent_token = generate_token()
    agent_repo.replace_credential_for_host(host_id, hash_token(raw_agent_token), created_at=now)
    agent_repo.consume_enrollment_token(token, host_id=host_id, consumed_at=now)

    db.commit()
    return EnrollmentResult(host_id=host_id, agent_token=raw_agent_token)
