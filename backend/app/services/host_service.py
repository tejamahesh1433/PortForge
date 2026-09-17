"""Heartbeat handling. See api/agents.py: the host_id in the request body
is validated against the *authenticated* host_id before this is ever
called -- this service trusts its `host_id` argument completely.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models.host import Host
from ..repositories.host_repository import HostRepository


def record_heartbeat(
    db: Session,
    host_id: uuid.UUID,
    hostname: str,
    operating_system: str,
    os_version: Optional[str],
    architecture: Optional[str],
    agent_version: Optional[str],
    docker_available: bool,
    timestamp: datetime,
) -> Host:
    repo = HostRepository(db)
    host = repo.upsert(
        host_id=host_id,
        hostname=hostname,
        operating_system=operating_system,
        os_version=os_version,
        architecture=architecture,
        agent_version=agent_version,
        docker_available=docker_available,
        now=timestamp,
    )
    db.commit()
    db.refresh(host)
    return host
