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
