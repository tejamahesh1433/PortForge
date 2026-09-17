"""macOS port collector.

Uses ``lsof``, the tool called out in the project brief, since psutil's
system-wide connection enumeration is unsupported on modern macOS without
root. Process executable path / working directory are enriched afterwards
via psutil on a per-PID, best-effort basis.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .. import platform as pf
from ..models import DiscoveredPort, PortState, Protocol, Source
from .base import CollectorError, logger, run_command, safe_process_metadata

# Matches the NAME column of `lsof -nP`, e.g.:
#   *:3000
#   127.0.0.1:5432
#   [::1]:5433
#   [fe80::1]:5433->[fe80::2]:22 (ESTABLISHED)
_NAME_RE = re.compile(
    r"""^(?:\[(?P<v6host>[^\]]*)\]|(?P<v4host>[^:\s]+)):(?P<port>\d+)
        (?:\s*->.*)?
        (?:\s+\((?P<state>[A-Z_]+)\))?$""",
    re.VERBOSE,
)


def _parse_lsof_line(line: str, protocol: Protocol) -> Optional[dict]:
    parts = line.split(None, 8)
    if len(parts) < 9:
        return None

    command, pid_str, _user, _fd, _type, _device, _size, _node, name = parts

    match = _NAME_RE.match(name.strip())
    if not match:
        return None

    host = match.group("v6host")
    if host is None:
        host = match.group("v4host")
    if host in ("*", ""):
        host = "0.0.0.0" if protocol == Protocol.TCP else "0.0.0.0"

    try:
        pid = int(pid_str)
    except ValueError:
        pid = None

    return {
        "command": command,
        "pid": pid,
        "bind_address": host,
        "port": int(match.group("port")),
        "raw_state": match.group("state") or ("LISTEN" if protocol == Protocol.TCP else "NONE"),
    }


class MacOSCollector:
    """Collects listening TCP ports and bound UDP ports on macOS via lsof."""

    def collect(self) -> List[DiscoveredPort]:
        hostname = pf.get_hostname()
        host_id = pf.get_host_id()
        operating_system = pf.OperatingSystem.MACOS.value

        results: List[DiscoveredPort] = []
        seen: set[tuple] = set()

        for protocol, args in (
            (Protocol.TCP, ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]),
            (Protocol.UDP, ["lsof", "-nP", "-iUDP"]),
        ):
            try:
                output = run_command(args)
            except CollectorError as exc:
                logger.warning("macOS %s collection failed: %s", protocol.value, exc)
                continue

            lines = output.splitlines()
            if lines:
                lines = lines[1:]  # drop header row

            for line in lines:
                parsed = _parse_lsof_line(line, protocol)
                if parsed is None:
                    continue

                key = (protocol, parsed["bind_address"], parsed["port"], parsed["pid"])
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
                        protocol=protocol,
                        bind_address=parsed["bind_address"],
                        source=source,
                        pid=parsed["pid"],
                        process_name=metadata.name or parsed["command"],
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

        logger.debug("macOS collector found %d ports", len(results))
        return results
