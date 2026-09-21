"""Orchestrates a single explicit sync to the central server: enroll,
push a fresh discovery snapshot, push local reservations.

Nothing here ever runs implicitly -- only the `portforge central ...` CLI
commands call into this module (see cli.py). `scan`/`check`/`next`/
`reserve`/`conflicts` remain completely unaware this module exists, which
is what makes "local operation never depends on the central server" true
by construction rather than by a runtime enabled/disabled check sprinkled
through already-working Phase 1-4 code.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from . import platform as pf
from .central_client import CentralClient, CentralResult
from .central_config import CentralConfig
from .discovery import discover_all_ports
from .models import DiscoveredPort
from .paths import reservations_path as default_reservations_path
from .reservations.storage import ReservationStorageError, ReservationStore

AGENT_VERSION = "1.0.0"


def _observation_dict(port: DiscoveredPort) -> dict:
    return {
        "port": port.host_port if port.host_port is not None else port.port,
        "protocol": port.protocol.value,
        "bind_address": port.bind_address,
        "state": port.state.value,
        "source": port.source.value,
        "pid": port.pid,
        "process_name": port.process_name,
        "process_path": port.process_path,
        "working_directory": port.working_directory,
        "container_id": port.container_id,
        "container_name": port.container_name,
        "container_image": port.container_image,
        "container_port": port.container_port,
        "docker_compose_project": port.docker_compose_project,
        "service_name": port.service_name,
        "project_name": port.project_name,
        "purpose": port.purpose,
        "category": port.category,
        "detection_confidence": port.detection.confidence.value if port.detection else None,
        "first_seen": port.first_seen.isoformat(),
        "last_seen": port.last_seen.isoformat(),
    }


@dataclass
class SyncOutcome:
    health: CentralResult
    heartbeat: Optional[CentralResult] = None
    observations: Optional[CentralResult] = None
    reservations: List[CentralResult] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        if self.error:
            return False
        if self.heartbeat is not None and not self.heartbeat.success:
            return False
        if self.observations is not None and not self.observations.success:
            return False
        return True


def check_status(config: CentralConfig) -> CentralResult:
    if not config.url:
        return CentralResult(success=False, error="Central sync is not configured (no url).")
    client = CentralClient(config.url, config.token)
    return client.health()


def sync_now(config: CentralConfig) -> SyncOutcome:
    if not config.is_usable():
        return SyncOutcome(health=CentralResult(success=False, error="Central sync is not enabled/configured."))

    client = CentralClient(config.url, config.token)

    health = client.health()
    if not health.success:
        return SyncOutcome(health=health, error="Central server is not reachable.")

    host_id = pf.get_host_id()
    now = datetime.now(timezone.utc)

    heartbeat = client.heartbeat(
        host_id=host_id,
        hostname=pf.get_hostname(),
        operating_system=pf.detect_os().value,
        os_version=pf.get_os_version(),
        architecture=None,
        agent_version=AGENT_VERSION,
        docker_available=True,
        timestamp=now.isoformat(),
    )
    if not heartbeat.success:
        return SyncOutcome(health=health, heartbeat=heartbeat, error="Heartbeat failed.")

    ports = discover_all_ports()
    scan_id = str(uuid.uuid4())
    observations_result = client.submit_observations(
        scan_id=scan_id,
        host_id=host_id,
        observed_at=now.isoformat(),
        observations=[_observation_dict(p) for p in ports],
    )

    reservation_results: List[CentralResult] = []
    try:
        reservations = ReservationStore(default_reservations_path()).load()
    except ReservationStorageError:
        reservations = []

    for reservation in reservations:
        if reservation.host_id != host_id:
            continue
        result = client.sync_reservation(
            {
                "port": reservation.port,
                "protocol": reservation.protocol.value,
                "bind_address": reservation.bind_address,
                "project": reservation.project,
                "service": reservation.service,
                "purpose": reservation.purpose,
                "notes": reservation.notes,
                "local_reservation_id": reservation.reservation_id,
            }
        )
        reservation_results.append(result)

    return SyncOutcome(
        health=health,
        heartbeat=heartbeat,
        observations=observations_result,
        reservations=reservation_results,
    )


def enroll(config_path, url: str, enrollment_token: str) -> CentralResult:
    from .central_config import save_central_config

    client = CentralClient(url, token=None)
    host_id = pf.get_host_id()
    result = client.enroll(
        enrollment_token=enrollment_token,
        host_id=host_id,
        hostname=pf.get_hostname(),
        operating_system=pf.detect_os().value,
        os_version=pf.get_os_version(),
        architecture=None,
        agent_version=AGENT_VERSION,
        docker_available=True,
    )
    if not result.success:
        return result

    agent_token = result.data.get("agent_token") if result.data else None
    if not agent_token:
        return CentralResult(success=False, error="Enrollment response did not include an agent token.")

    save_central_config(CentralConfig(enabled=True, url=url, token=agent_token), path=config_path)
    return CentralResult(success=True, status_code=result.status_code, data={"host_id": host_id})
