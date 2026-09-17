from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.conflict import ConflictOut
from ..services.conflict_service import list_conflicts

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


@router.get("", response_model=list[ConflictOut])
def get_conflicts(
    host_id: Optional[uuid.UUID] = Query(default=None), db: Session = Depends(get_db)
) -> list[ConflictOut]:
    return list_conflicts(db, host_id=host_id)
