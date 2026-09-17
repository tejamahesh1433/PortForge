from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..schemas.health import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def get_health(db: Session = Depends(get_db)) -> HealthOut:
    """Useful but deliberately non-sensitive: no credentials, no
    connection strings, no configuration secrets -- just enough to tell a
    human or a monitor whether the service and its database are up.
    """
    settings = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:
        database_status = "unavailable"

    return HealthOut(
        status="ok" if database_status == "connected" else "degraded",
        service="portforge",
        database=database_status,
        version=settings.version,
    )
