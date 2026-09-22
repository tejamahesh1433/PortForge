"""v1.1-B: agent-side remote bind-probe delivery/execution.

Reuses `bindprobe.py::probe_bind()` exactly -- there is exactly ONE
definition of "is this port free" in the agent, used identically whether
the caller is `portforge check` running locally for a human, or Central
asking this host to probe on its own behalf for a REMOTE recommendation/
allocation elsewhere. The probe itself is exactly as safe as every other
use of `probe_bind`: a real `bind()` immediately followed by `close()`,
never `listen()`, no `SO_REUSEADDR`, bounded (no retry loop) -- see
bindprobe.py's own module docstring for the full safety reasoning, which
this module does not repeat or alter.

`process_pending_probes()` is the ONE shared function both
`central_sync.py::sync_now()` (the one-shot `portforge central sync`
command) and `runtime/agent.py::AgentRuntime.run()` (the always-on daemon)
call after a successful heartbeat -- see docs/v1.1/remote-probe-design.md.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .bindprobe import probe_bind
from .models import Protocol

logger = logging.getLogger("portforge_agent.remote_probe")

# Bounded -- mirrors Central's own max_probes_delivered_per_heartbeat
# default; even if Central ever sent more, this is a second, independent
# bound so one malformed/huge response can't make one heartbeat cycle do
# unbounded work (task §5: "bounded number of probe requests per heartbeat").
MAX_PROBES_PER_CYCLE = 10


def process_pending_probes(client, heartbeat_data: Any) -> int:
    """Reads `pending_probes` from a heartbeat response body (absent
    entirely for a Central that predates this field -- handled identically
    to an empty list, so an older/newer agent+Central pairing degrades
    silently rather than erroring). Runs each entry's real local bind
    probe and submits the result back to Central.

    Never raises -- a probe delivery/execution/submission failure is
    logged and skipped, never allowed to break the calling heartbeat cycle
    (task §5: "do not block normal heartbeat"). Returns the number of
    probes actually processed (attempted), for callers that want to log a
    count.
    """
    if not isinstance(heartbeat_data, dict):
        return 0
    pending = heartbeat_data.get("pending_probes")
    if not isinstance(pending, list) or not pending:
        return 0

    processed = 0
    for item in pending[:MAX_PROBES_PER_CYCLE]:
        if not isinstance(item, dict):
            logger.warning("Skipping malformed pending probe entry: %r", item)
            continue
        try:
            probe_id = str(item["probe_id"])
            port = int(item["port"])
            if not (1 <= port <= 65535):
                raise ValueError(f"port {port} out of range")
            protocol_raw = str(item.get("protocol", "tcp")).lower()
            bind_address = str(item.get("bind_address") or "0.0.0.0")
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed pending probe entry %r: %s", item, exc)
            continue

        protocol = Protocol.UDP if protocol_raw == "udp" else Protocol.TCP

        host_id = str(heartbeat_data.get("host_id", ""))
        try:
            result = probe_bind(port, protocol, bind_address)
        except Exception as exc:  # pragma: no cover - probe_bind already catches OSError internally
            logger.warning("Probe %s execution raised unexpectedly: %s", probe_id, exc)
            _safe_submit(client, host_id, probe_id, None, str(exc))
            processed += 1
            continue

        _safe_submit(client, host_id, probe_id, result.available, result.reason)
        processed += 1

    return processed


def _safe_submit(client, host_id: str, probe_id: str, available, reason) -> None:
    try:
        client.submit_probe_result(host_id, probe_id, available, reason)
    except Exception as exc:  # pragma: no cover - CentralClient itself already catches network errors
        logger.warning("Could not submit probe %s result: %s", probe_id, exc)
