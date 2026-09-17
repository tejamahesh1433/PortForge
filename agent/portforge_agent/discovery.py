"""Discovery engine: picks the right collector for this OS and runs it."""
from __future__ import annotations

import dataclasses
import logging
from typing import List, Tuple

from . import platform as pf
from .collectors.base import CollectorError
from .collectors.docker import DockerCollector
from .collectors.linux import LinuxCollector
from .collectors.macos import MacOSCollector
from .collectors.windows import WindowsCollector
from .detection import enrich_ports
from .models import DiscoveredPort, Source

logger = logging.getLogger("portforge_agent.discovery")

_COLLECTORS = {
    pf.OperatingSystem.WINDOWS: WindowsCollector,
    pf.OperatingSystem.MACOS: MacOSCollector,
    pf.OperatingSystem.LINUX: LinuxCollector,
}


class UnsupportedOperatingSystemError(Exception):
    """Raised when PortForge has no collector for the current OS."""


def get_collector_for(operating_system: pf.OperatingSystem):
    """Return an instantiated collector for the given operating system."""
    collector_cls = _COLLECTORS.get(operating_system)
    if collector_cls is None:
        raise UnsupportedOperatingSystemError(
            f"No collector is available for operating system: {operating_system}"
        )
    return collector_cls()


def discover_ports(operating_system: pf.OperatingSystem | None = None) -> List[DiscoveredPort]:
    """Run local port discovery and return normalized results.

    A collector failing to run at all (missing tool, access denied outright)
    is logged and results in an empty result set rather than crashing the
    whole scan, per the read-only, never-crash discovery requirement.
    """
    target_os = operating_system or pf.detect_os()

    try:
        collector = get_collector_for(target_os)
    except UnsupportedOperatingSystemError:
        logger.error("Unsupported operating system: %s", target_os)
        return []

    try:
        ports = collector.collect()
    except CollectorError as exc:
        logger.error("Discovery failed for %s: %s", target_os, exc)
        return []

    deduped: dict[tuple, DiscoveredPort] = {}
    for port in ports:
        deduped[port.dedupe_key()] = port

    return sorted(deduped.values(), key=lambda p: (p.port, p.protocol.value, p.bind_address))


def discover_docker_ports() -> List[DiscoveredPort]:
    """Discover ports published to the host by running Docker containers.

    Docker is entirely optional (Phase 2 requirement): if the CLI is
    missing, the daemon is unreachable, the user lacks permission, or the
    call times out, this is logged and an empty list is returned -- never
    an exception, and this never affects :func:`discover_ports`.
    """
    try:
        ports = DockerCollector().collect()
    except CollectorError as exc:
        logger.info("Docker discovery unavailable: %s", exc)
        return []

    deduped: dict[tuple, DiscoveredPort] = {}
    for port in ports:
        deduped[(port.protocol, port.bind_address, port.host_port, port.container_id)] = port

    return sorted(
        deduped.values(), key=lambda p: (p.host_port or p.port, p.protocol.value, p.bind_address)
    )


def _binding_key(port: DiscoveredPort) -> Tuple:
    """Identity used to match a native observation with a Docker observation
    of the *same host socket*.

    Deliberately (protocol, bind_address, host_port) -- NOT pid. A
    container's host-visible PID (or the PID of a proxying process like
    Windows Docker Desktop's backend or Linux's docker-proxy) has no
    reliable relationship to the process running inside the container, so
    matching on it would be both unnecessary and misleading. bind_address is
    compared as an exact string: every collector (Windows/macOS/Linux and
    Docker) already normalizes wildcard notations (``*``, empty HostIp) down
    to canonical ``0.0.0.0`` / ``::`` before a DiscoveredPort is created, so
    two observations of the same real socket always arrive here already
    using the same wildcard spelling -- while genuinely different bindings
    (``127.0.0.1``, a specific LAN IP, ``::1``) are correctly kept distinct
    because they simply aren't string-equal.
    """
    host_port = port.host_port if port.host_port is not None else port.port
    return (port.protocol, port.bind_address, host_port)


def merge_native_and_docker(
    native_ports: List[DiscoveredPort], docker_ports: List[DiscoveredPort]
) -> List[DiscoveredPort]:
    """Combine native OS discovery with Docker discovery into one view.

    When a native observation (e.g. Windows attributing a socket to
    ``com.docker.backend.exe``) and a Docker observation describe the same
    host socket (see :func:`_binding_key`), they are merged into a single
    record instead of being shown as two unrelated occupied ports:

    - Docker's ownership metadata (container id/name, Compose
      project/service, image, status, networks, labels, container_port)
      takes precedence, since it is authoritative.
    - The native process's pid/process_name/process_path/working_directory
      are kept as secondary information rather than discarded, so a user
      still sees *what the OS thinks is holding the socket* alongside
      *what Docker says owns it*.
    - The native raw connection state (e.g. ``LISTEN``) is preferred over
      the container status string, since it's the more precise fact about
      the socket itself.

    A Docker port with no matching native observation (the OS-level scan
    missed it, e.g. a permission gap) is still included, source=docker, with
    no pid/process_name -- Docker visibility is never dropped just because
    native discovery didn't also see it. A native port with no matching
    Docker observation is passed through unchanged.
    """
    native_by_key: dict[Tuple, DiscoveredPort] = {}
    for port in native_ports:
        native_by_key.setdefault(_binding_key(port), port)

    merged: List[DiscoveredPort] = []
    consumed_native_keys: set = set()

    for docker_port in docker_ports:
        key = _binding_key(docker_port)
        native_match = native_by_key.get(key)

        if native_match is not None:
            consumed_native_keys.add(key)
            merged.append(
                dataclasses.replace(
                    docker_port,
                    pid=native_match.pid,
                    process_name=native_match.process_name,
                    process_path=native_match.process_path,
                    working_directory=native_match.working_directory,
                    raw_state=native_match.raw_state or docker_port.raw_state,
                )
            )
        else:
            merged.append(docker_port)

    for port in native_ports:
        if _binding_key(port) in consumed_native_keys:
            continue
        merged.append(port)

    return sorted(
        merged, key=lambda p: (p.host_port or p.port, p.protocol.value, p.bind_address)
    )


def discover_all_ports(operating_system: pf.OperatingSystem | None = None) -> List[DiscoveredPort]:
    """Native + Docker discovery, merged and enriched into a single view.

    This is what the `scan` CLI command uses. :func:`discover_ports` (native
    only, unenriched) and :func:`merge_native_and_docker` (unenriched) are
    left untouched for backward compatibility with Phase 1/2 callers and
    tests -- enrichment (project/purpose/category detection) is layered on
    only here, as the last step, so it can never affect the raw discovery
    or merge logic it builds on.
    """
    native_ports = discover_ports(operating_system)
    docker_ports = discover_docker_ports()
    merged = merge_native_and_docker(native_ports, docker_ports)
    return enrich_ports(merged)


def discover_docker_view() -> List[DiscoveredPort]:
    """Docker-sourced ports only, enriched with native metadata where matched.

    Used by the `docker` CLI command: same merge as :func:`discover_all_ports`
    so the two commands never disagree, filtered down to source=docker.
    """
    return [p for p in discover_all_ports() if p.source == Source.DOCKER]
