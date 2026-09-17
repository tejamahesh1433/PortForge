"""Queries for central reservations."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.reservation import CentralReservation


class ReservationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, reservation_id: uuid.UUID) -> Optional[CentralReservation]:
        return self.db.get(CentralReservation, reservation_id)

    def get_by_binding(
        self, host_id: uuid.UUID, port: int, protocol: str, bind_address: Optional[str]
    ) -> Optional[CentralReservation]:
        stmt = select(CentralReservation).where(
            CentralReservation.host_id == host_id,
            CentralReservation.port == port,
            CentralReservation.protocol == protocol,
            CentralReservation.bind_address == bind_address,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_local_id(self, host_id: uuid.UUID, local_reservation_id: str) -> Optional[CentralReservation]:
        stmt = select(CentralReservation).where(
            CentralReservation.host_id == host_id,
            CentralReservation.local_reservation_id == local_reservation_id,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list(
        self,
        host_id: Optional[uuid.UUID] = None,
        port: Optional[int] = None,
        project: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[CentralReservation], int]:
        conditions = []
        if host_id is not None:
            conditions.append(CentralReservation.host_id == host_id)
        if port is not None:
            conditions.append(CentralReservation.port == port)
        if project:
            conditions.append(func.lower(CentralReservation.project).contains(project.lower()))

        stmt = select(CentralReservation)
        count_stmt = select(func.count()).select_from(CentralReservation)
        for condition in conditions:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total = self.db.execute(count_stmt).scalar_one()
        stmt = stmt.order_by(CentralReservation.port).limit(limit).offset(offset)
        rows = self.db.execute(stmt).scalars().all()
        return rows, total

    def add(self, reservation: CentralReservation) -> CentralReservation:
        self.db.add(reservation)
        self.db.flush()
        return reservation

    def delete(self, reservation: CentralReservation) -> None:
        self.db.delete(reservation)
