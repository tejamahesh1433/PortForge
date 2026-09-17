"""Linux port collector.

Uses ``ss`` (from iproute2), the tool called out in the project brief.
``ss -H -tulnp`` lists listening TCP sockets and bound UDP sockets without
requiring root for sockets owned by the current user; sockets owned by
other users simply come back without process info, which we handle
gracefully rather than treating as an error.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .. import platform as pf
from ..models import DiscoveredPort, PortState, Protocol, Source
from .base import CollectorError, logger, run_command, safe_process_metadata

_ADDR_RE = re.compile(
    r"^(?:\[(?P<v6>[^\]]*)\]|(?P<v4>\*|[^:\s]+)):(?P<port>\*|\d+)$"
)
_PROCESS_RE = re.compile(r'users:\(\("(?P<name>[^"]+)",pid=(?P<pid>\d+)')


def _parse_ss_line(line: str) -> Optional[dict]:
    fields = line.split()
    if len(fields) < 5:
        return None

    netid, state = fields[0], fields[1]
    local_addr = fields[4]

    if netid not in ("tcp", "tcp6", "udp", "udp6"):
        return None

    protocol = Protocol.TCP if netid.startswith("tcp") else Protocol.UDP
    if protocol == Protocol.TCP and state != "LISTEN":
        return None

    match = _ADDR_RE.match(local_addr)
    if not match or match.group("port") == "*":
        return None

    bind_address = match.group("v6") if match.group("v6") is not None else match.group("v4")
    if bind_address == "*":
        bind_address = "0.0.0.0"

    pid = None
    process_name = None
    remainder = " ".join(fields[6:])
    proc_match = _PROCESS_RE.search(remainder)
    if proc_match:
        process_name = proc_match.group("name")
        pid = int(proc_match.group("pid"))

    return {
        "protocol": protocol,
        "bind_address": bind_address,
        "port": int(match.group("port")),
        "pid": pid,
        "process_name": process_name,
        "raw_state": state,
    }


class LinuxCollector:
    """Collects listening TCP ports and bound UDP ports on Linux via ss."""

    def collect(self) -> List[DiscoveredPort]:
        hostname = pf.get_hostname()
        host_id = pf.get_host_id()
        operating_system = pf.OperatingSystem.LINUX.value

        try:
            output = run_command(["ss", "-H", "-tulnp"])
        except CollectorError as exc:
            logger.warning("Linux collector failed: %s", exc)
            return []

        results: List[DiscoveredPort] = []
        seen: set[tuple] = set()

        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith(("State", "Netid")):
                continue

            parsed = _parse_ss_line(line)
            if parsed is None:
                continue

            key = (parsed["protocol"], parsed["bind_address"], parsed["port"], parsed["pid"])
            if key in seen:
                continue
            seen.add(key)

            metadata = safe_process_metadata(parsed["pid"])
            source = Source.PROCESS if parsed["pid"] else Source.SYSTEM

            results.append(
                DiscoveredPort(
                    hostname=hostname,
                    host_id=host_id,
                    operating_system=operating_system,
                    port=parsed["port"],
                    protocol=parsed["protocol"],
                    bind_address=parsed["bind_address"],
                    source=source,
                    pid=parsed["pid"],
                    process_name=metadata.name or parsed["process_name"],
                    process_path=metadata.path,
                    working_directory=metadata.working_directory,
                    command_line=metadata.command_line,
                    parent_pid=metadata.parent_pid,
                    parent_process_name=metadata.parent_name,
                    parent_working_directory=metadata.parent_working_directory,
                    state=PortState.ACTIVE,
                    raw_state=parsed["raw_state"],
                )
            )

        logger.debug("Linux collector found %d ports", len(results))
        return results
