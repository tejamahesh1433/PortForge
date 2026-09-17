"""Docker / Docker Compose port collector.

Docker discovery is entirely optional and isolated from the Windows/macOS/
Linux collectors: it never mixes Docker-specific logic into them, and its
absence (CLI missing, daemon down, permission denied, timeout, malformed
output) must never prevent native OS port discovery from running.

Design: rather than guessing container ownership from Windows/macOS/Linux
process names (``com.docker.backend.exe``, ``docker-proxy``, ...), this
collector asks Docker directly for authoritative metadata:

1. ``docker ps -q``       -- IDs of currently running containers.
2. ``docker inspect <id>...`` -- full structured JSON per container, giving
   ``NetworkSettings.Ports`` (actual published host bindings),
   ``Config.Labels`` (Compose project/service, when present), and
   ``Config.Image`` / ``State.Status`` / ``NetworkSettings.Networks``.

``NetworkSettings.Ports`` is keyed by ``"<container_port>/<protocol>"``. A
value of ``null`` means the port is EXPOSEd in the image but not published
to the host -- it must NOT be reported as host-port usage. A value of a
non-empty list means it is published; each list entry is one host binding
(``{"HostIp": ..., "HostPort": ...}``), and a single container port can have
multiple bindings (e.g. Docker Desktop typically dual-stack publishes to
both ``0.0.0.0`` and ``::``).

Every `docker` invocation goes through ``_resolve_docker_executable()``
rather than the bare string ``"docker"`` -- physically reproduced on
tejamaheshs-MacBook-Pro.local: a macOS LaunchAgent's PATH is launchd's
minimal ``/usr/bin:/bin:/usr/sbin:/sbin``, which doesn't include Homebrew
or Docker Desktop's bundled CLI location, so background (non-interactive)
discovery reported "Required command not found: docker" even though
Docker worked fine interactively. See that function's docstring for the
resolution order (PATH first, then a small fixed list of known macOS
locations -- see also service_gen.py, which independently gives the
LaunchAgent itself a fuller PATH via the same
``platform.MACOS_EXTRA_BIN_DIRS`` list, as defense in depth).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from .. import platform as pf
from ..models import DiscoveredPort, PortState, Protocol, Source
from .base import CollectorError, run_command

logger = logging.getLogger("portforge_agent.collectors.docker")

# Docker Desktop being closed can otherwise make `docker` CLI calls hang for
# a while before failing; keep discovery calls short so a missing daemon
# never noticeably delays `scan`.
_CHECK_TIMEOUT = 5.0
_INSPECT_TIMEOUT = 10.0

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class DockerUnavailableError(CollectorError):
    """Docker CLI/daemon is not usable right now.

    This is an expected, common state (Docker not installed, Desktop not
    running, no permission) -- not a bug. Collectors catch it and simply
    report zero Docker ports.
    """


def _resolve_docker_executable() -> Optional[str]:
    """Absolute path to the `docker` CLI, or None if it can't be found.

    Tries normal PATH resolution first (`shutil.which`) -- when the
    invoking process's PATH already includes it (the common interactive
    case, and Linux/Windows in general), this is a no-op versus the old
    bare `"docker"` behavior. Only falls back to a small, fixed list of
    well-known macOS install locations (platform.MACOS_EXTRA_BIN_DIRS)
    when PATH resolution fails -- exactly the launchd LaunchAgent case
    (minimal /usr/bin:/bin:/usr/sbin:/sbin PATH, no Homebrew/Docker
    Desktop locations). Never searches the filesystem recursively, never
    sources a shell startup file, and never runs anything outside this
    fixed list -- each candidate is checked to actually be a file and
    executable before being returned.
    """
    found = shutil.which("docker")
    if found:
        return found

    if pf.detect_os() != pf.OperatingSystem.MACOS:
        return None

    for directory in pf.MACOS_EXTRA_BIN_DIRS:
        # Plain string join, not pathlib -- MACOS_EXTRA_BIN_DIRS is always
        # forward-slash (it only ever matters on macOS), and pathlib.Path
        # renders with the *host* OS's separator on str(), which would be
        # wrong here if this ever ran under test on a non-POSIX machine.
        candidate = f"{directory}/docker"
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return None


def _run_docker(args: List[str], timeout: float) -> str:
    executable = _resolve_docker_executable()
    if executable is None:
        # Same message `run_command` itself would raise on a bare
        # FileNotFoundError for "docker" -- preserves the existing,
        # distinct "not installed" wording callers/logs already expect,
        # separate from a found-but-failing invocation below (e.g. "daemon
        # unavailable"), which still raises its own distinct message via
        # run_command's normal non-zero-exit handling.
        raise DockerUnavailableError("Required command not found: docker")
    try:
        return run_command([executable, *args], timeout=timeout)
    except CollectorError as exc:
        raise DockerUnavailableError(str(exc)) from exc


def is_docker_available() -> bool:
    """Best-effort, side-effect-free check for whether Docker can be used."""
    try:
        _run_docker(["version", "--format", "{{.Server.Version}}"], _CHECK_TIMEOUT)
        return True
    except DockerUnavailableError:
        return False


def _list_running_container_ids() -> List[str]:
    output = _run_docker(["ps", "-q"], _CHECK_TIMEOUT)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _inspect_containers(container_ids: List[str]) -> List[dict]:
    if not container_ids:
        return []

    output = _run_docker(["inspect", *container_ids], _INSPECT_TIMEOUT)
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise DockerUnavailableError(f"Malformed docker inspect output: {exc}") from exc

    if not isinstance(data, list):
        raise DockerUnavailableError("Unexpected docker inspect output shape (expected a JSON array)")

    return data


def _parse_port_key(key: str) -> Optional[Tuple[int, Protocol]]:
    """Parse a NetworkSettings.Ports key like ``"5432/tcp"``."""
    if not isinstance(key, str) or "/" not in key:
        return None

    port_str, _, proto_str = key.partition("/")
    try:
        port = int(port_str)
    except ValueError:
        return None

    proto_str = proto_str.strip().lower()
    if proto_str == "tcp":
        protocol = Protocol.TCP
    elif proto_str == "udp":
        protocol = Protocol.UDP
    else:
        return None

    return port, protocol


def _normalize_host_ip(host_ip: Optional[str]) -> str:
    """Normalize Docker's HostIp to the same wildcard notation the native
    collectors use, so merge-by-binding can match exactly (see discovery.py).
    """
    if not host_ip:
        return "0.0.0.0"
    return host_ip


def _container_name(record: dict) -> Optional[str]:
    name = record.get("Name")
    if not isinstance(name, str):
        return None
    return name[1:] if name.startswith("/") else name or None


def _container_labels(record: dict) -> Dict[str, str]:
    config = record.get("Config")
    if not isinstance(config, dict):
        return {}
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return {}
    return {str(k): str(v) for k, v in labels.items() if v is not None}


def _container_command(record: dict) -> Optional[List[str]]:
    """The container's actual resolved entrypoint+args (top-level .Path/.Args
    from `docker inspect`), not the Dockerfile's declared CMD -- when an
    ENTRYPOINT wraps CMD, .Path/.Args reflect what's really running. Falls
    back to Config.Cmd if .Path is unavailable.
    """
    path = record.get("Path")
    args = record.get("Args")
    if isinstance(path, str) and isinstance(args, list):
        return [path, *[str(a) for a in args]]

    config = record.get("Config")
    if isinstance(config, dict):
        cmd = config.get("Cmd")
        if isinstance(cmd, list) and cmd:
            return [str(c) for c in cmd]

    return None


def container_record_to_ports(
    record: Dict[str, Any], hostname: str, host_id: str, operating_system: str
) -> List[DiscoveredPort]:
    """Convert one ``docker inspect`` record into published host-port observations.

    Only entries in ``NetworkSettings.Ports`` with an actual (non-null,
    non-empty) host binding list are reported -- EXPOSE-only ports are
    silently skipped, per Docker semantics.
    """
    if not isinstance(record, dict):
        return []

    results: List[DiscoveredPort] = []

    raw_id = record.get("Id")
    container_id = raw_id[:12] if isinstance(raw_id, str) else None
    name = _container_name(record)

    config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
    image = config.get("Image") if isinstance(config, dict) else None
    labels = _container_labels(record)
    compose_project = labels.get(COMPOSE_PROJECT_LABEL)
    compose_service = labels.get(COMPOSE_SERVICE_LABEL)
    command = _container_command(record)

    state = record.get("State") if isinstance(record.get("State"), dict) else {}
    status = state.get("Status") if isinstance(state, dict) else None

    network_settings = record.get("NetworkSettings") if isinstance(record.get("NetworkSettings"), dict) else {}
    networks_dict = network_settings.get("Networks") if isinstance(network_settings, dict) else {}
    networks = sorted(networks_dict.keys()) if isinstance(networks_dict, dict) else []

    ports = network_settings.get("Ports") if isinstance(network_settings, dict) else {}
    if not isinstance(ports, dict):
        return results

    for port_key, bindings in ports.items():
        parsed = _parse_port_key(port_key)
        if parsed is None:
            continue
        container_port, protocol = parsed

        # null / missing / empty => EXPOSEd but not published to the host.
        if not bindings or not isinstance(bindings, list):
            continue

        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            host_port_raw = binding.get("HostPort")
            try:
                host_port = int(host_port_raw)
            except (TypeError, ValueError):
                continue

            bind_address = _normalize_host_ip(binding.get("HostIp"))

            results.append(
                DiscoveredPort(
                    hostname=hostname,
                    host_id=host_id,
                    operating_system=operating_system,
                    port=host_port,
                    protocol=protocol,
                    bind_address=bind_address,
                    source=Source.DOCKER,
                    container_id=container_id,
                    container_name=name,
                    docker_compose_project=compose_project,
                    container_image=image,
                    container_status=status,
                    docker_networks=networks or None,
                    docker_labels=labels or None,
                    container_command=command,
                    host_port=host_port,
                    container_port=container_port,
                    service_name=compose_service,
                    state=PortState.ACTIVE,
                    raw_state=status,
                )
            )

    return results


class DockerCollector:
    """Collects published host ports from currently running Docker containers."""

    def collect(self) -> List[DiscoveredPort]:
        hostname = pf.get_hostname()
        host_id = pf.get_host_id()
        operating_system = pf.detect_os().value

        container_ids = _list_running_container_ids()  # may raise DockerUnavailableError

        if not container_ids:
            return []

        records = _inspect_containers(container_ids)  # may raise DockerUnavailableError

        results: List[DiscoveredPort] = []
        seen: set = set()

        for record in records:
            try:
                ports = container_record_to_ports(record, hostname, host_id, operating_system)
            except Exception as exc:  # malformed per-container metadata must never abort the scan
                container_id = record.get("Id") if isinstance(record, dict) else None
                logger.warning("Skipping malformed container record %s: %s", container_id, exc)
                continue

            for port in ports:
                key = (port.protocol, port.bind_address, port.host_port, port.container_id)
                if key in seen:
                    continue
                seen.add(key)
                results.append(port)

        logger.debug("Docker collector found %d published host ports", len(results))
        return results
