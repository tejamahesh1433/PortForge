from __future__ import annotations

import uuid
from typing import List, Optional, Tuple

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..models.activity import ActivityEvent


class ActivityRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, event: ActivityEvent) -> ActivityEvent:
        self.db.add(event)
        return event

    def list_events(
        self,
        host_id: Optional[uuid.UUID] = None,
        event_type: Optional[str] = None,
        port: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ActivityEvent], int]:
        stmt = select(ActivityEvent)
        count_stmt = select(ActivityEvent)

        if host_id:
            stmt = stmt.where(ActivityEvent.host_id == host_id)
            count_stmt = count_stmt.where(ActivityEvent.host_id == host_id)
        if event_type:
            stmt = stmt.where(ActivityEvent.event_type == event_type)
            count_stmt = count_stmt.where(ActivityEvent.event_type == event_type)
        if port is not None:
            stmt = stmt.where(ActivityEvent.port == port)
            count_stmt = count_stmt.where(ActivityEvent.port == port)

        stmt = stmt.order_by(desc(ActivityEvent.timestamp)).limit(limit).offset(offset)

        # Simple count for pagination (can be expensive on large tables, but fine for our bounded history)
        # Using a subquery for exact count of the filtered set
        from sqlalchemy import func

        total = self.db.scalar(select(func.count()).select_from(count_stmt.subquery())) or 0

        results = self.db.scalars(stmt).all()
        return list(results), total
