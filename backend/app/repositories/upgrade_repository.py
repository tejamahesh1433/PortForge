"""Queries for the HostUpgrade entity."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models.host_upgrade import HostUpgrade

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}


class UpgradeRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, upgrade_id: uuid.UUID) -> Optional[HostUpgrade]:
        return self.db.get(HostUpgrade, upgrade_id)

    def list_for_host(self, host_id: uuid.UUID) -> Sequence[HostUpgrade]:
        stmt = (
            select(HostUpgrade)
            .where(HostUpgrade.host_id == host_id)
            .order_by(HostUpgrade.created_at.desc())
        )
        return self.db.execute(stmt).scalars().all()

    def get_non_terminal_for_host(self, host_id: uuid.UUID) -> Optional[HostUpgrade]:
        """Return the active (non-terminal) upgrade for a host, if any."""
        stmt = (
            select(HostUpgrade)
            .where(
                HostUpgrade.host_id == host_id,
                HostUpgrade.state.not_in(TERMINAL_STATES),
            )
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_pending_for_host(self, host_id: uuid.UUID) -> Optional[HostUpgrade]:
        """Return the oldest unclaimed upgrade (APPROVED or WAITING_FOR_AGENT)."""
        stmt = (
            select(HostUpgrade)
            .where(
                HostUpgrade.host_id == host_id,
                HostUpgrade.state.in_(["APPROVED", "WAITING_FOR_AGENT"]),
            )
            .order_by(HostUpgrade.created_at.asc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_request_id_for_host(
        self, host_id: uuid.UUID, request_id: str
    ) -> Optional[HostUpgrade]:
        stmt = (
            select(HostUpgrade)
            .where(
                HostUpgrade.host_id == host_id,
                HostUpgrade.request_id == request_id,
            )
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def fail_active_for_host(self, host_id: uuid.UUID, reason: str, now: datetime) -> int:
        """Transition all non-terminal upgrades for a host to FAILED."""
        stmt = (
            update(HostUpgrade)
            .where(
                HostUpgrade.host_id == host_id,
                HostUpgrade.state.not_in(TERMINAL_STATES),
            )
            .values(state="FAILED", failure_reason=reason, completed_at=now)
        )
        result = self.db.execute(stmt)
        return result.rowcount

    def get_restart_eligible_for_host(
        self, host_id: uuid.UUID, *, for_update: bool = False
    ) -> Optional[HostUpgrade]:
        """Return the oldest upgrade in RESTARTING or VERIFYING_HEALTH for a host.

        Used by Central heartbeat reconciliation to advance the state machine
        across the process-boundary gap (old agent restarted; new agent reconnects).
        Pass for_update=True to acquire a row-level lock and prevent duplicate
        transitions from concurrent heartbeat requests.
        """
        stmt = (
            select(HostUpgrade)
            .where(
                HostUpgrade.host_id == host_id,
                HostUpgrade.state.in_(["RESTARTING", "VERIFYING_HEALTH"]),
            )
            .order_by(HostUpgrade.created_at.asc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_active_summaries_for_hosts(
        self, host_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, HostUpgrade]:
        """Batch-fetch the non-terminal upgrade for each host in host_ids.
        Returns a dict keyed by host_id.
        """
        if not host_ids:
            return {}
        stmt = (
            select(HostUpgrade)
            .where(
                HostUpgrade.host_id.in_(host_ids),
                HostUpgrade.state.not_in(TERMINAL_STATES),
            )
        )
        rows = self.db.execute(stmt).scalars().all()
        result: dict[uuid.UUID, HostUpgrade] = {}
        for row in rows:
            # If multiple non-terminal rows somehow exist, keep the latest
            if row.host_id not in result or row.created_at > result[row.host_id].created_at:
                result[row.host_id] = row
        return result
