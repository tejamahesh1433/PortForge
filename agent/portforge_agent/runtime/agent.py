"""Main runtime loop for PortForge Agent."""

import logging
import signal
import time
from datetime import datetime, timezone

from ..central_client import CentralClient
from ..central_config import load_central_config
from ..credentials import load_credential
from ..version import PROTOCOL_VERSION, get_portforge_version
from .backoff import ExponentialBackoff
from .state import load_state, save_state
from .sync import SyncManager
from .. import platform as pf

logger = logging.getLogger(__name__)

class AgentRuntime:
    def __init__(self):
        self._running = False
        self.config = load_central_config()
        self.state = load_state()
        self.token = load_credential()
        
        self.client = CentralClient(
            base_url=self.config.url or "",
            token=self.token,
            timeout=10.0
        )
        self.sync_manager = SyncManager(self.client, self.state)
        self.backoff = ExponentialBackoff(initial=1.0, max_delay=60.0)

    def _handle_shutdown(self, signum, frame):
        logger.info("Received shutdown signal. Stopping runtime...")
        self._running = False

    def run(self):
        """Starts the infinite agent loop."""
        if not self.config.enabled:
            logger.warning("Central sync is not enabled in config. Agent will exit.")
            return

        if not self.token:
            logger.error("No agent credential found. Please run `portforge agent enroll` first.")
            return

        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._handle_shutdown)

        logger.info("PortForge Agent Runtime started.")
        
        scan_interval = 15.0
        heartbeat_interval = 30.0
        
        last_scan_time = 0.0
        last_heartbeat_time = 0.0
        
        while self._running:
            now = time.time()
            
            # Send Heartbeat
            if now - last_heartbeat_time >= heartbeat_interval:
                import platform
                import sys
                from ..collectors.docker import is_docker_available
                from ..agent_contract import CONTRACT_VERSION
                docker_available = is_docker_available()
                # Phase 9/10: include fleet inventory fields on every heartbeat.
                python_version = sys.version.split()[0]
                res = self.client.heartbeat(
                    host_id=pf.get_host_id(),
                    hostname=pf.get_hostname(),
                    operating_system=pf.detect_os().value,
                    os_version=pf.get_os_version(),
                    architecture=platform.machine(),
                    agent_version=get_portforge_version(),
                    docker_available=docker_available,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    protocol_version=PROTOCOL_VERSION,
                    contract_version=CONTRACT_VERSION,
                    python_version=python_version,
                )
                if res.success:
                    last_heartbeat_time = time.time()
                    self.state.last_heartbeat = last_heartbeat_time
                    save_state(self.state)
                    self.backoff.reset()

                    # v1.1-B: answer any remote bind-probe requests
                    # delivered on this same heartbeat. Never raises (see
                    # remote_probe.py) -- a probe failure must never break
                    # the always-on daemon's main loop.
                    from ..remote_probe import process_pending_probes

                    try:
                        process_pending_probes(self.client, res.data)
                    except Exception:
                        logger.warning("Unexpected error processing pending probes", exc_info=True)

                    # Phase 10: execute a pending upgrade if Central delivered one.
                    # Runs after probes so probe results are submitted first.
                    # Never raises -- a failed upgrade must not break the daemon loop.
                    if isinstance(res.data, dict) and res.data.get("pending_upgrade"):
                        try:
                            self._handle_pending_upgrade(res.data["pending_upgrade"])
                        except Exception:
                            logger.warning(
                                "Unexpected error handling pending upgrade", exc_info=True
                            )
                else:
                    logger.warning(f"Heartbeat failed: {res.error}")
                    self.backoff.next_delay()

            # Capture & Flush Snapshot
            if now - last_scan_time >= scan_interval:
                try:
                    snapshot = self.sync_manager.capture_snapshot()
                except Exception as e:
                    logger.error("Failed to capture local snapshot: %s", e)
                    snapshot = None
                    
                if snapshot:
                    success = self.sync_manager.flush_snapshot(snapshot)
                    if success:
                        self.backoff.reset()
                        last_scan_time = time.time()
                    else:
                        self.backoff.next_delay()
                        # Do not update last_scan_time on failure so we retry early based on backoff
            
            # Sleep until next action or backoff
            now = time.time()
            time_to_next_scan = max(0.0, scan_interval - (now - last_scan_time))
            time_to_next_hb = max(0.0, heartbeat_interval - (now - last_heartbeat_time))
            
            sleep_time = min(time_to_next_scan, time_to_next_hb)
            if sleep_time <= 0:
                sleep_time = 1.0 # Minimum throttle if falling behind
                
            if self.backoff.current_delay is not None:
                sleep_time = max(sleep_time, self.backoff.current_delay)
                
            # Sleep in small increments for responsive shutdown
            slept = 0.0
            while slept < sleep_time and self._running:
                time.sleep(0.5)
                slept += 0.5
                
        logger.info("PortForge Agent Runtime stopped cleanly.")

    def _handle_pending_upgrade(self, pending_upgrade: dict) -> None:
        """Process a pending_upgrade delivered by Central on a heartbeat response.

        Validates and executes the upgrade sequentially (not async) so the
        daemon doesn't race against itself. If the upgrade succeeds the agent
        process will restart via the platform adapter, cleanly ending the loop.
        If validation or installation fails, the failure is reported to Central
        and the daemon continues its normal loop.

        Never raises -- all exceptions are caught and logged.
        """
        from ..upgrade import run_upgrade
        from ..version import get_portforge_version

        host_id = pf.get_host_id()
        current_version = get_portforge_version()
        upgrade_id = pending_upgrade.get("id", "<unknown>")
        logger.info(
            "Upgrade request %s received: %s → %s",
            upgrade_id,
            current_version,
            pending_upgrade.get("target_version", "?"),
        )

        succeeded = run_upgrade(
            client=self.client,
            host_id=host_id,
            pending=pending_upgrade,
            current_version=current_version,
            allow_downgrade=bool(pending_upgrade.get("allow_downgrade", False)),
        )

        if succeeded:
            # restart_service() has been called; request clean loop exit so
            # the scheduled task / systemd / launchd can start the new process.
            logger.info("Upgrade initiated -- stopping runtime loop for clean restart.")
            self._running = False
