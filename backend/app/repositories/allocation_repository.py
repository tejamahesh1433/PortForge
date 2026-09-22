"""Queries for allocation bundles."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models.allocation import Allocation


class AllocationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, allocation_id: uuid.UUID) -> Optional[Allocation]:
        return self.db.get(Allocation, allocation_id)

    def get_by_request_id(self, request_id: str) -> Optional[Allocation]:
        stmt = select(Allocation).where(Allocation.request_id == request_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def add(self, allocation: Allocation) -> Allocation:
        self.db.add(allocation)
        self.db.flush()
        return allocation

    def list(
        self,
        host_id: Optional[uuid.UUID] = None,
        project: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Allocation], int]:
        """v1.1-D: the Allocations page's list source (task Sec2). `search`
        is a broader free-text match (project name OR request_id
        substring) than `project` (an exact-filter-style match on project
        name alone), mirroring `ReservationRepository.list`'s existing
        case-insensitive `contains` convention.
        """
        conditions = []
        if host_id is not None:
            conditions.append(Allocation.host_id == host_id)
        if project:
            conditions.append(func.lower(Allocation.project) == project.lower())
        if status:
            conditions.append(Allocation.status == status)
        if search:
            needle = f"%{search.lower()}%"
            conditions.append(
                or_(func.lower(Allocation.project).like(needle), func.lower(Allocation.request_id).like(needle))
            )

        stmt = select(Allocation)
        count_stmt = select(func.count()).select_from(Allocation)
        for condition in conditions:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total = self.db.execute(count_stmt).scalar_one()
        # Deterministic ordering (task Sec21): newest first, `id` as a
        # tie-break for rows with an identical created_at.
        stmt = stmt.order_by(Allocation.created_at.desc(), Allocation.id.desc()).limit(limit).offset(offset)
        rows = self.db.execute(stmt).scalars().all()
        return rows, total
