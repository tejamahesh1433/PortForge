from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.recommendation import CentralRecommendationOut
from ..services.recommendation_service import suggest_port

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=CentralRecommendationOut)
def get_recommendation(
    host_id: uuid.UUID = Query(...),
    service_type: str = Query(...),
    protocol: str = Query(default="tcp", pattern="^(tcp|udp)$"),
    db: Session = Depends(get_db),
) -> CentralRecommendationOut:
    """See schemas/recommendation.py: this is always a `central_suggestion`,
    never a verified-available answer. The requesting client is expected
    to run `portforge check <port>` on the target host before acting on it.
    """
    return suggest_port(db, host_id=host_id, service_type=service_type, protocol=protocol)
