"""Queries for the Host entity."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.host import Host


class HostRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, host_id: uuid.UUID) -> Optional[Host]:
        return self.db.get(Host, host_id)

    def list(self, limit: int = 50, offset: int = 0) -> Sequence[Host]:
        stmt = select(Host).order_by(Host.last_seen.desc()).limit(limit).offset(offset)
        return self.db.execute(stmt).scalars().all()

    def count(self) -> int:
        from sqlalchemy import func

        return self.db.execute(select(func.count()).select_from(Host)).scalar_one()

    def upsert(
        self,
        host_id: uuid.UUID,
        hostname: str,
        operating_system: str,
        os_version: Optional[str],
        architecture: Optional[str],
        agent_version: Optional[str],
        docker_available: bool,
        now: datetime,
    ) -> Host:
        host = self.get(host_id)
        if host is None:
            host = Host(
                id=host_id,
                hostname=hostname,
                operating_system=operating_system,
                os_version=os_version,
                architecture=architecture,
                agent_version=agent_version,
                docker_available=docker_available,
                first_seen=now,
                last_seen=now,
                status="online",
            )
            self.db.add(host)
        else:
            host.hostname = hostname
            host.operating_system = operating_system
            host.os_version = os_version
            host.architecture = architecture
            host.agent_version = agent_version
            host.docker_available = docker_available
            host.last_seen = now
            host.status = "online"

        self.db.flush()
        return host
