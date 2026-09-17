from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .common import ApiModel

if TYPE_CHECKING:
    from ..models.port_observation import CurrentPortObservation


class PortObservationOut(ApiModel):
    id: uuid.UUID
    host_id: uuid.UUID
    host_hostname: Optional[str] = None  # denormalized for convenience -- see PortOut queries

    port: int
    protocol: str
    bind_address: str
    state: str
    source: str

    pid: Optional[int]
    process_name: Optional[str]
    process_path: Optional[str]
    working_directory: Optional[str]

    container_id: Optional[str]
    container_name: Optional[str]
    container_image: Optional[str]
    container_port: Optional[int]

    docker_compose_project: Optional[str]
    service_name: Optional[str]

    project_name: Optional[str]
    purpose: Optional[str]
    category: Optional[str]
    detection_confidence: Optional[str]

    first_seen: datetime
    last_seen: datetime
    observed_at: datetime

    @classmethod
    def from_observation(cls, row: "CurrentPortObservation", hostname: Optional[str] = None) -> "PortObservationOut":
        """Explicit conversion (rather than relying on `from_attributes` +
        implicit str-enum coercion) so `protocol`/`state` are always
        plain, unambiguous strings in the API response.
        """
        return cls(
            id=row.id,
            host_id=row.host_id,
            host_hostname=hostname,
            port=row.port,
            protocol=row.protocol.value,
            bind_address=row.bind_address,
            state=row.state.value,
            source=row.source,
            pid=row.pid,
            process_name=row.process_name,
            process_path=row.process_path,
            working_directory=row.working_directory,
            container_id=row.container_id,
            container_name=row.container_name,
            container_image=row.container_image,
            container_port=row.container_port,
            docker_compose_project=row.docker_compose_project,
            service_name=row.service_name,
            project_name=row.project_name,
            purpose=row.purpose,
            category=row.category,
            detection_confidence=row.detection_confidence,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            observed_at=row.observed_at,
        )
