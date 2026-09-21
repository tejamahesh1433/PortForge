from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..repositories.activity_repository import ActivityRepository
from ..schemas.activity import ActivityEventOut

router = APIRouter(prefix="/activity", tags=["activity"])


class ActivityResponse(BaseModel):
    events: List[ActivityEventOut]
    total: int


@router.get("", response_model=ActivityResponse)
def get_activity(
    host_id: Optional[uuid.UUID] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    port: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ActivityResponse:
    repo = ActivityRepository(db)
    events, total = repo.list_events(
        host_id=host_id,
        event_type=event_type,
        port=port,
        limit=limit,
        offset=offset,
    )
    return ActivityResponse(events=events, total=total)
