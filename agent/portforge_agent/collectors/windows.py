"""Windows port collector.

Uses ``psutil``, which on Windows talks directly to the native
``GetExtendedTcpTable`` / ``GetExtendedUdpTable`` APIs. This is the "reliable
Python API" option called out for Windows in the project brief: it avoids
parsing PowerShell text output, needs no elevated privileges for normal
discovery, and gives us the owning PID directly.
"""
from __future__ import annotations

import socket
from typing import List

from .. import platform as pf
from ..models import DiscoveredPort, PortState, Protocol, Source
from .base import CollectorError, logger, safe_process_metadata


class WindowsCollector:
    """Collects listening TCP ports and bound UDP ports on Windows."""

    def collect(self) -> List[DiscoveredPort]:
        try:
            import psutil
        except ImportError as exc:  # pragma: no cover - hard dependency
            raise CollectorError("psutil is required on Windows") from exc

        try:
            connections = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError) as exc:
            raise CollectorError(
                "Access denied while listing network connections"
            ) from exc
        except OSError as exc:
            raise CollectorError(f"Failed to list network connections: {exc}") from exc

        hostname = pf.get_hostname()
        host_id = pf.get_host_id()
        operating_system = pf.OperatingSystem.WINDOWS.value

        results: List[DiscoveredPort] = []
        seen: set[tuple] = set()

        for conn in connections:
            if conn.laddr is None or not conn.laddr:
                continue

            if conn.type == socket.SOCK_STREAM:
                protocol = Protocol.TCP
                if conn.status != psutil.CONN_LISTEN:
                    continue
                raw_state = conn.status
            elif conn.type == socket.SOCK_DGRAM:
                protocol = Protocol.UDP
                raw_state = conn.status if conn.status != psutil.CONN_NONE else "NONE"
            else:
                continue

            bind_address = conn.laddr.ip
            port = conn.laddr.port
            pid = conn.pid

            key = (protocol, bind_address, port, pid)
            if key in seen:
                continue
            seen.add(key)

            metadata = safe_process_metadata(pid)
            source = Source.PROCESS if pid else Source.SYSTEM

            results.append(
                DiscoveredPort(
                    hostname=hostname,
                    host_id=host_id,
                    operating_system=operating_system,
                    port=port,
                    protocol=protocol,
                    bind_address=bind_address,
                    source=source,
                    pid=pid,
                    process_name=metadata.name,
                    process_path=metadata.path,
                    working_directory=metadata.working_directory,
                    command_line=metadata.command_line,
                    parent_pid=metadata.parent_pid,
                    parent_process_name=metadata.parent_name,
                    parent_working_directory=metadata.parent_working_directory,
                    state=PortState.ACTIVE,
                    raw_state=str(raw_state),
                )
            )

        logger.debug("Windows collector found %d ports", len(results))
        return results
