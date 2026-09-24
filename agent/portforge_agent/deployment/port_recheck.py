"""Pre-apply port re-check: verify required host ports are free before compose up."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from ..models import Source

logger = logging.getLogger("portforge_agent.deployment.port_recheck")


# ---------------------------------------------------------------------------
# Verdict types
# ---------------------------------------------------------------------------


class PortRecheckVerdict(str, Enum):
    FREE = "FREE"
    EXPECTED_EXISTING_DEPLOYMENT = "EXPECTED_EXISTING_DEPLOYMENT"
    UNEXPECTED_OCCUPANT = "UNEXPECTED_OCCUPANT"
    UNKNOWN = "UNKNOWN"


@dataclass
class PortRecheckResult:
    service: str | None
    protocol: str  # normalised "tcp" or "udp"
    host_port: int
    verdict: PortRecheckVerdict
    reason: str


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PortConflictError(Exception):
    """A required port is occupied by an unexpected process/project."""

    failure_code = "DEPLOYMENT_PORT_CONFLICT"

    def __init__(self, conflicts: list[dict]) -> None:
        self.conflicts = conflicts
        super().__init__(f"Port conflict(s): {conflicts}")


class PortStateUnknownError(Exception):
    """Port occupancy cannot be determined — fail-closed."""

    failure_code = "DEPLOYMENT_PORT_STATE_UNKNOWN"

    def __init__(self, unknowns: list[dict]) -> None:
        self.unknowns = unknowns
        super().__init__(f"Port state unknown: {unknowns}")


# ---------------------------------------------------------------------------
# Helper: extract required (service, protocol, host_port) from ports_json
# ---------------------------------------------------------------------------


def required_host_ports_from_ports_json(
    ports_json: dict | None,
) -> list[tuple[str | None, str, int]]:
    """Return a list of (service_name, protocol, host_port) from ports_json.

    Only entries that declare an explicit host_port are returned.
    internal_port is never treated as a host binding.
    """
    if not ports_json or not isinstance(ports_json, dict):
        return []

    result: list[tuple[str | None, str, int]] = []
    for svc in ports_json.get("services") or []:
        if not isinstance(svc, dict):
            continue
        host_port = svc.get("host_port")
        if host_port is None:
            continue
        try:
            host_port_int = int(host_port)
        except (TypeError, ValueError):
            continue

        protocol = str(svc.get("protocol") or "tcp").lower()
        service_name: str | None = svc.get("name") or None
        if isinstance(service_name, str):
            service_name = service_name.strip() or None

        result.append((service_name, protocol, host_port_int))
    return result


# ---------------------------------------------------------------------------
# Core checker (accepts injected lists for testability)
# ---------------------------------------------------------------------------


def _proto_str(raw: Any) -> str:
    """Normalise a Protocol enum or plain string to lowercase 'tcp'/'udp'.

    In Python 3.11+, ``str(Protocol.TCP)`` returns ``'Protocol.TCP'`` (the
    enum name), NOT ``'tcp'`` (the value).  Using ``.value`` is the safe,
    version-independent way to get the underlying string for comparison.
    """
    if raw is None:
        return ""
    # Prefer .value so we always get "tcp"/"udp" regardless of Python version.
    if hasattr(raw, "value"):
        return str(raw.value).lower()
    return str(raw).lower()


def recheck_host_ports(
    required: Sequence[tuple[str | None, str, int]],
    *,
    expected_compose_project: str,
    docker_ports: Sequence[Any] | None = None,
    native_ports: Sequence[Any] | None = None,
    discovery_ok: bool = True,
) -> list[PortRecheckResult]:
    """Check each required (service, protocol, host_port) against discovered lists.

    Args:
        required: iterable of (service_name, protocol, host_port) tuples.
        expected_compose_project: the Compose project name this deployment owns.
        docker_ports: DiscoveredPort-like objects (duck-typed); if None, docker
            check is skipped for this port.
        native_ports: DiscoveredPort-like objects; if None, native check skipped.
        discovery_ok: if False, every required port is returned as UNKNOWN
            (caller signals that discovery infrastructure failed).
    """
    results: list[PortRecheckResult] = []

    for service, protocol, host_port in required:
        proto_lower = protocol.lower()

        if not discovery_ok:
            results.append(
                PortRecheckResult(
                    service=service,
                    protocol=proto_lower,
                    host_port=host_port,
                    verdict=PortRecheckVerdict.UNKNOWN,
                    reason="Port discovery unavailable; cannot confirm port is free",
                )
            )
            continue

        # --- Docker check ------------------------------------------------
        docker_match: Any = None
        if docker_ports is not None:
            for dp in docker_ports:
                dp_port = getattr(dp, "host_port", None)
                if dp_port is None:
                    dp_port = getattr(dp, "port", None)
                dp_proto = _proto_str(getattr(dp, "protocol", None))
                if dp_port == host_port and dp_proto == proto_lower:
                    docker_match = dp
                    break

        if docker_match is not None:
            compose_project = getattr(docker_match, "docker_compose_project", None)
            if compose_project and compose_project == expected_compose_project:
                results.append(
                    PortRecheckResult(
                        service=service,
                        protocol=proto_lower,
                        host_port=host_port,
                        verdict=PortRecheckVerdict.EXPECTED_EXISTING_DEPLOYMENT,
                        reason=(
                            f"Port held by expected compose project "
                            f"{compose_project!r}"
                        ),
                    )
                )
            else:
                results.append(
                    PortRecheckResult(
                        service=service,
                        protocol=proto_lower,
                        host_port=host_port,
                        verdict=PortRecheckVerdict.UNEXPECTED_OCCUPANT,
                        reason=(
                            f"Port held by different/unknown compose project "
                            f"{compose_project!r}"
                        ),
                    )
                )
            continue

        # --- Native OS check (non-docker listeners) ----------------------
        native_match: Any = None
        if native_ports is not None:
            for np in native_ports:
                np_source = getattr(np, "source", None)
                # Skip docker-sourced entries; already handled above.
                if np_source == Source.DOCKER or _proto_str(np_source) == "docker":
                    continue
                np_port = getattr(np, "host_port", None)
                if np_port is None:
                    np_port = getattr(np, "port", None)
                np_proto = _proto_str(getattr(np, "protocol", None))
                if np_port == host_port and np_proto == proto_lower:
                    native_match = np
                    break

        if native_match is not None:
            proc_name = getattr(native_match, "process_name", None) or "unknown"
            results.append(
                PortRecheckResult(
                    service=service,
                    protocol=proto_lower,
                    host_port=host_port,
                    verdict=PortRecheckVerdict.UNEXPECTED_OCCUPANT,
                    reason=f"Port held by native OS process: {proc_name}",
                )
            )
            continue

        # --- Port is free ------------------------------------------------
        results.append(
            PortRecheckResult(
                service=service,
                protocol=proto_lower,
                host_port=host_port,
                verdict=PortRecheckVerdict.FREE,
                reason="No listener found on this port",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Live wrapper (calls real discovery; used by handler.py)
# ---------------------------------------------------------------------------


def recheck_host_ports_live(
    required: Sequence[tuple[str | None, str, int]],
    expected_compose_project: str,
) -> list[PortRecheckResult]:
    """Live version that calls the real Docker and OS port discovery.

    If DockerCollector raises CollectorError, all required ports are returned
    as UNKNOWN (fail-closed: we cannot confirm they are free).
    """
    from ..collectors.base import CollectorError
    from ..collectors.docker import DockerCollector
    from ..discovery import discover_ports

    docker_ports: list[Any] | None = None
    discovery_ok = True

    try:
        docker_ports = DockerCollector().collect()
    except CollectorError as exc:
        logger.warning("Docker discovery failed during port recheck: %s", exc)
        discovery_ok = False

    native_ports: list[Any] | None = None
    if discovery_ok:
        try:
            native_ports = discover_ports()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Native port discovery failed during port recheck: %s", exc
            )
            # Native-only failure doesn't make everything UNKNOWN;
            # docker check can still classify ports.

    return recheck_host_ports(
        required,
        expected_compose_project=expected_compose_project,
        docker_ports=docker_ports,
        native_ports=native_ports,
        discovery_ok=discovery_ok,
    )


# ---------------------------------------------------------------------------
# Assert helper for handler.py
# ---------------------------------------------------------------------------


def assert_ports_clear_for_apply(results: list[PortRecheckResult]) -> None:
    """Raise on any port that isn't FREE or EXPECTED_EXISTING_DEPLOYMENT.

    Raises:
        PortConflictError: if any port has UNEXPECTED_OCCUPANT verdict.
        PortStateUnknownError: if any port has UNKNOWN verdict (and no conflicts).
    """
    conflicts = [
        {
            "service": r.service,
            "protocol": r.protocol,
            "host_port": r.host_port,
            "reason": r.reason,
        }
        for r in results
        if r.verdict == PortRecheckVerdict.UNEXPECTED_OCCUPANT
    ]
    if conflicts:
        raise PortConflictError(conflicts)

    unknowns = [
        {
            "service": r.service,
            "protocol": r.protocol,
            "host_port": r.host_port,
            "reason": r.reason,
        }
        for r in results
        if r.verdict == PortRecheckVerdict.UNKNOWN
    ]
    if unknowns:
        raise PortStateUnknownError(unknowns)
