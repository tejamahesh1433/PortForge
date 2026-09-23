"""Heartbeat handling. See api/agents.py: the host_id in the request body
is validated against the *authenticated* host_id before this is ever
called -- this service trusts its `host_id` argument completely.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host import Host
from ..models.activity import ActivityEvent
from ..repositories.host_repository import HostRepository
from ..repositories.activity_repository import ActivityRepository
from ..schemas.health_status import HostHealthState, derive_health_state


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
    protocol_version: Optional[int] = None,
    contract_version: Optional[int] = None,
    python_version: Optional[str] = None,
) -> Host:
    repo = HostRepository(db)
    existing_host = repo.get(host_id)
    
    was_offline = True
    if existing_host is not None:
        if (existing_host.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
            raise ValueError("Host identity is decommissioned.")
        settings = get_settings()
        prev_state, _, _ = derive_health_state(
            existing_host.last_seen,
            settings.host_stale_after_seconds,
            settings.host_offline_after_seconds,
            timestamp,
        )
        was_offline = (prev_state == HostHealthState.OFFLINE) or (existing_host.status != "online")

    host = repo.upsert(
        host_id=host_id,
        hostname=hostname,
        operating_system=operating_system,
        os_version=os_version,
        architecture=architecture,
        agent_version=agent_version,
        docker_available=docker_available,
        now=timestamp,
        protocol_version=protocol_version,
        contract_version=contract_version,
        python_version=python_version,
    )

    if was_offline:
        activity_repo = ActivityRepository(db)
        activity_repo.add(
            ActivityEvent(
                host_id=host_id,
                timestamp=timestamp,
                event_type="HOST_ONLINE",
                identity_context=hostname,
                summary=f"Host '{hostname}' came online",
            )
        )

    db.commit()
    db.refresh(host)
    return host
