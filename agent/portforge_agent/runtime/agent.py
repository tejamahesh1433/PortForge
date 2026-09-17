"""Main runtime loop for PortForge Agent."""

import logging
import signal
import sys
import time

from ..central_client import CentralClient
from ..central_config import load_central_config
from ..credentials import load_credential
from .backoff import ExponentialBackoff
from .state import load_state
from .sync import SyncManager

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
        
        # Scan and heartbeat intervals (hardcoded for now, could be in config)
        scan_interval = 15.0
        
        while self._running:
            start_time = time.time()
            
            # 1. Capture local snapshot
            try:
                snapshot = self.sync_manager.capture_snapshot()
            except Exception as e:
                logger.error("Failed to capture local snapshot: %s", e)
                snapshot = None
                
            # 2. Flush snapshot to central
            if snapshot:
                success = self.sync_manager.flush_snapshot(snapshot)
                if success:
                    self.backoff.reset()
                else:
                    self.backoff.next_delay() # just advance state

            # 3. Sleep until next scan interval, minus elapsed time, or apply backoff
            elapsed = time.time() - start_time
            sleep_time = max(0.1, scan_interval - elapsed)
            
            # If we are failing, we could use the backoff delay if it's larger
            if self.backoff.current_delay is not None:
                sleep_time = max(sleep_time, self.backoff.current_delay)
                
            # Sleep in small increments to allow responsive shutdown
            slept = 0.0
            while slept < sleep_time and self._running:
                time.sleep(0.5)
                slept += 0.5
                
        logger.info("PortForge Agent Runtime stopped cleanly.")
