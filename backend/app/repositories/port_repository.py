"""Queries for current port observations and their history events."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import and_, func, select, true
from sqlalchemy.orm import Session

from ..models.port_observation import CurrentPortObservation, PortObservationEvent


class PortRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- current state --------------------------------------------------------

    def list_current_for_host(self, host_id: uuid.UUID) -> Sequence[CurrentPortObservation]:
        stmt = select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
        return self.db.execute(stmt).scalars().all()

    def get_current(
        self, host_id: uuid.UUID, port: int, protocol: str, bind_address: str
    ) -> Optional[CurrentPortObservation]:
        stmt = select(CurrentPortObservation).where(
            CurrentPortObservation.host_id == host_id,
            CurrentPortObservation.port == port,
            CurrentPortObservation.protocol == protocol,
            CurrentPortObservation.bind_address == bind_address,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def upsert_current(self, observation: CurrentPortObservation) -> None:
        self.db.add(observation)

    def delete_current(self, observation: CurrentPortObservation) -> None:
        self.db.delete(observation)

    def add_event(self, event: PortObservationEvent) -> None:
        self.db.add(event)

    def query(
        self,
        host_id: Optional[uuid.UUID] = None,
        port: Optional[int] = None,
        project: Optional[str] = None,
        purpose: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[CurrentPortObservation], int]:
        conditions = []
        if host_id is not None:
            conditions.append(CurrentPortObservation.host_id == host_id)
        if port is not None:
            conditions.append(CurrentPortObservation.port == port)
        if project:
            conditions.append(func.lower(CurrentPortObservation.project_name).contains(project.lower()))
        if purpose:
            conditions.append(
                func.lower(CurrentPortObservation.purpose).contains(purpose.lower())
                | func.lower(CurrentPortObservation.category).contains(purpose.lower())
            )
        if source:
            conditions.append(func.lower(CurrentPortObservation.source) == source.lower())

        where_clause = and_(*conditions) if conditions else true()

        count_stmt = select(func.count()).select_from(CurrentPortObservation).where(where_clause)
        total = self.db.execute(count_stmt).scalar_one()

        stmt = (
            select(CurrentPortObservation)
            .where(where_clause)
            .order_by(CurrentPortObservation.port, CurrentPortObservation.protocol)
            .limit(limit)
            .offset(offset)
        )
        rows = self.db.execute(stmt).scalars().all()
        return rows, total

    def list_distinct_projects(self, limit: int = 200) -> Sequence[str]:
        stmt = (
            select(CurrentPortObservation.project_name)
            .where(CurrentPortObservation.project_name.is_not(None))
            .distinct()
            .order_by(CurrentPortObservation.project_name)
            .limit(limit)
        )
        return [row[0] for row in self.db.execute(stmt).all()]

    def last_event_for_binding(
        self, host_id: uuid.UUID, port: int, protocol: str, bind_address: str
    ) -> Optional[PortObservationEvent]:
        stmt = (
            select(PortObservationEvent)
            .where(
                PortObservationEvent.host_id == host_id,
                PortObservationEvent.port == port,
                PortObservationEvent.protocol == protocol,
                PortObservationEvent.bind_address == bind_address,
            )
            .order_by(PortObservationEvent.occurred_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()
