"""Queries for remote bind-probe requests/results (v1.1-B)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.host_probe import HostProbe


class ProbeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, probe_id: uuid.UUID) -> Optional[HostProbe]:
        return self.db.get(HostProbe, probe_id)

    def add(self, probe: HostProbe) -> HostProbe:
        self.db.add(probe)
        self.db.flush()
        return probe

    def count_active_for_host(self, host_id: uuid.UUID, now: datetime) -> int:
        """"Active" = PENDING/DELIVERED and not yet past its own TTL --
        used to bound queue growth per host (task §23).
        """
        stmt = select(func.count()).select_from(HostProbe).where(
            HostProbe.host_id == host_id,
            HostProbe.status.in_(("PENDING", "DELIVERED")),
            HostProbe.expires_at >= now,
        )
        return int(self.db.execute(stmt).scalar_one())

    def list_pending_for_host(self, host_id: uuid.UUID, now: datetime, limit: int) -> List[HostProbe]:
        stmt = (
            select(HostProbe)
            .where(HostProbe.host_id == host_id, HostProbe.status == "PENDING", HostProbe.expires_at >= now)
            .order_by(HostProbe.created_at)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def host_has_probe_history(self, host_id: uuid.UUID) -> bool:
        """v1.1-D task Sec8: a single bounded EXISTS check -- true only if
        this host's agent has actually answered at least one probe
        (COMPLETED or FAILED), never inferred from PENDING/DELIVERED alone
        (those just mean Central asked, not that the agent proved it can
        answer). Only ever called for a single host (host detail), never in
        a list-page loop -- see services/allocation_service.py's own
        batched equivalent for why a per-row version would be an N+1.
        """
        stmt = (
            select(HostProbe.id)
            .where(HostProbe.host_id == host_id, HostProbe.status.in_(("COMPLETED", "FAILED")))
            .limit(1)
        )
        return self.db.execute(stmt).first() is not None

    def list_recent_for_hosts(self, host_ids: List[uuid.UUID], limit: int = 500) -> List[HostProbe]:
        """v1.1-D: ONE batched query for a whole page of allocations,
        instead of one `find_latest_for_binding` call per allocation entry
        (an N+1 the task explicitly warns against -- Sec22). Ordered
        newest-first so a caller building a `(host_id, port, protocol,
        bind_address) -> HostProbe` map by "first occurrence wins" ends up
        with the latest probe per binding, matching
        `find_latest_for_binding`'s own semantics. Bounded by `limit`
        regardless of how many hosts are passed -- a deliberate cap, not a
        per-host limit, since this only ever backs one bounded list page.
        """
        if not host_ids:
            return []
        stmt = (
            select(HostProbe)
            .where(HostProbe.host_id.in_(host_ids))
            .order_by(HostProbe.created_at.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def find_latest_for_binding(
        self, host_id: uuid.UUID, port: int, protocol: str, bind_address: str
    ) -> Optional[HostProbe]:
        """Most recent probe (by creation time) for this exact binding on
        this host, regardless of status -- callers classify freshness
        themselves (see services/probe_service.py::classify_bind_probe).
        """
        stmt = (
            select(HostProbe)
            .where(
                HostProbe.host_id == host_id,
                HostProbe.port == port,
                HostProbe.protocol == protocol,
                HostProbe.bind_address == bind_address,
            )
            .order_by(HostProbe.created_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()
