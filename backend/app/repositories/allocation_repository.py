"""Queries for allocation bundles."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
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
