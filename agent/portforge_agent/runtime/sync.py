"""Synchronization logic for PortForge Agent Runtime.

Manages offline snapshot buffering and coordination with the central client.
Keeps only the most recent complete snapshot to prevent unbounded queue
growth when disconnected.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..central_client import CentralClient
from .state import RuntimeState, save_state
from ..identity import get_host_id
from ..discovery import discover_all_ports, discover_docker_view
from ..evaluate import evaluate_all

logger = logging.getLogger(__name__)


class SyncManager:
    def __init__(self, client: CentralClient, state: RuntimeState):
        self.client = client
        self.state = state
        self._buffered_snapshot: Optional[dict] = None

    def capture_snapshot(self) -> dict:
        """Runs the local discovery phase and builds a snapshot payload."""
        import time
        import uuid
        from .. import platform as pf
        
        start = time.time()
        native = discover_all_ports()
        docker = discover_docker_view() if pf.docker_available() else []
        evaluated = evaluate_all(native, docker)
        
        self.state.sequence_id += 1
        
        snapshot = {
            "scan_id": str(uuid.uuid4()),
            "observed_at": time.time(),
            "sequence": self.state.sequence_id,
            "observations": []
        }
        
        for port in evaluated:
            snapshot["observations"].append({
                "port": port.port,
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
                "detection_confidence": port.detection_confidence.value if port.detection_confidence else None
            })
            
        self.state.last_scan = time.time()
        self.state.last_observation_count = len(evaluated)
        save_state(self.state)
        
        logger.debug("Local scan complete in %.2fs. Found %d active bindings.", time.time() - start, len(evaluated))
        return snapshot

    def flush_snapshot(self, snapshot: Optional[dict] = None) -> bool:
        """Sends the provided snapshot (or the buffered one) to the central server."""
        import time
        
        target = snapshot or self._buffered_snapshot
        if not target:
            return True
            
        res = self.client.submit_observations(
            host_id=get_host_id(),
            scan_id=target["scan_id"],
            observed_at=target["observed_at"],
            observations=target["observations"]
        )
        
        if res.success:
            self._buffered_snapshot = None
            self.state.last_sync = time.time()
            self.state.last_sync_error = None
            save_state(self.state)
            logger.info("Successfully synchronized %d observations to central server.", len(target["observations"]))
            return True
        else:
            self._buffered_snapshot = target
            self.state.last_sync_error = res.error
            save_state(self.state)
            logger.warning("Failed to synchronize with central server: %s", res.error)
            return False

    def sync_reservations(self) -> bool:
        """Uploads local reservations that differ from central, respecting local authority."""
        from ..reserve_ops import sync_project_reservations
        
        try:
            res = sync_project_reservations(client=self.client)
            return True
        except Exception as e:
            logger.warning("Failed to synchronize reservations: %s", e)
            return False
