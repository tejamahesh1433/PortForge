"""Queries for the Host entity."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.host import Host


class HostRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, host_id: uuid.UUID) -> Optional[Host]:
        return self.db.get(Host, host_id)

    def get_many(self, host_ids: List[uuid.UUID]) -> Sequence[Host]:
        """v1.1-D: ONE query to resolve every host referenced by a page of
        allocations, instead of one `get()` per row (task Sec22).
        """
        if not host_ids:
            return []
        stmt = select(Host).where(Host.id.in_(host_ids))
        return self.db.execute(stmt).scalars().all()

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
        protocol_version: Optional[int] = None,
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
                protocol_version=protocol_version,
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
            # A request that omits protocol_version (a legacy agent, or a
            # transport that doesn't carry it) must not erase a
            # previously-known value -- only overwrite when a real value is
            # actually reported this time.
            if protocol_version is not None:
                host.protocol_version = protocol_version

        self.db.flush()
        return host

    def delete(self, host_id: uuid.UUID) -> bool:
        """Remove Record: hard-purge Central rows for ``host_id``.

        Dependency map (every model with a host FK / host reference in
        ``app.models``):

        - ``EnrollmentToken.consumed_by_host_id`` — nullable, no FK; nulled
        - ``PortObservationEvent.host_id`` → hosts.id
        - ``CurrentPortObservation.host_id`` → hosts.id
        - ``CentralReservation.host_id`` → hosts.id
          (also optional ``allocation_id`` → allocations.id ON DELETE SET NULL;
          reservations are deleted before allocations)
        - ``AgentCredential.host_id`` → hosts.id
        - ``Scan.host_id`` → hosts.id
        - ``HostProbe.host_id`` → hosts.id
        - ``Allocation.host_id`` → hosts.id
        - ``ActivityEvent.host_id`` → hosts.id

        Caller must ``commit()`` (or roll back) the surrounding Session.
        This method only ``flush()``es so a failed request can roll back the
        whole purge atomically.
        """
        from sqlalchemy import delete, update
        from ..models.port_observation import CurrentPortObservation, PortObservationEvent
        from ..models.reservation import CentralReservation
        from ..models.agent_credential import AgentCredential, EnrollmentToken
        from ..models.scan import Scan
        from ..models.host_probe import HostProbe
        from ..models.allocation import Allocation
        from ..models.activity import ActivityEvent

        host = self.get(host_id)
        if host is None:
            return False

        # Nullable back-reference (no FK) — clear before host row goes away.
        self.db.execute(
            update(EnrollmentToken)
            .where(EnrollmentToken.consumed_by_host_id == host_id)
            .values(consumed_by_host_id=None)
        )

        # Explicit dependent deletes (Host ORM cascades are incomplete for
        # credentials/allocations/probes/activity/scans).
        self.db.execute(delete(PortObservationEvent).where(PortObservationEvent.host_id == host_id))
        self.db.execute(delete(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id))
        self.db.execute(delete(CentralReservation).where(CentralReservation.host_id == host_id))
        self.db.execute(delete(AgentCredential).where(AgentCredential.host_id == host_id))
        self.db.execute(delete(Scan).where(Scan.host_id == host_id))
        self.db.execute(delete(HostProbe).where(HostProbe.host_id == host_id))
        self.db.execute(delete(Allocation).where(Allocation.host_id == host_id))
        self.db.execute(delete(ActivityEvent).where(ActivityEvent.host_id == host_id))

        self.db.delete(host)
        self.db.flush()
        return True
