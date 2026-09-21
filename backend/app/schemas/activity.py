from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel

from ..models.base import Protocol


class ActivityEventOut(BaseModel):
    id: uuid.UUID
    host_id: uuid.UUID
    timestamp: datetime
    event_type: str

    port: Optional[int] = None
    protocol: Optional[Protocol] = None
    bind_address: Optional[str] = None

    source: Optional[str] = None
    identity_context: Optional[str] = None
    reservation_id: Optional[uuid.UUID] = None

    summary: str
    metadata_json: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}
