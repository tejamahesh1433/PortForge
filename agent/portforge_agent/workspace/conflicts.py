from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..evaluate import evaluate_port, find_discovered, find_reservation
from ..models import PortState, Protocol
from .models import Conflict, PortRequirement, ServiceInfo, WorkspaceModel


def _host_requirements(services: Iterable[ServiceInfo]) -> List[PortRequirement]:
    requirements: List[PortRequirement] = []
    for service in services:
        for requirement in service.port_requirements:
            if requirement.role != "host":
                continue
            if requirement.classification not in {"EXPLICIT", "INFERRED"}:
                continue
            if requirement.port is None:
                continue
            requirements.append(requirement)
    return requirements


def _protocol(value: str) -> Protocol:
    try:
        return Protocol(value.lower())
    except ValueError:
        return Protocol.TCP


def build_conflicts(
    model: WorkspaceModel,
    *,
    local_ports=None,
    reservations=None,
    allocations=None,
    project_name: Optional[str] = None,
) -> List[Conflict]:
    conflicts: List[Conflict] = []
    host_requirements = _host_requirements(model.services)

    by_port: Dict[tuple[int, str], List[PortRequirement]] = {}
    for requirement in host_requirements:
        key = (requirement.port, requirement.protocol)
        by_port.setdefault(key, []).append(requirement)

    for (port, protocol), requirements in sorted(by_port.items()):
        parties = sorted({req.service for req in requirements})
        if len(parties) < 2:
            continue
        conflicts.append(
            Conflict(
                kind="INTERNAL_PROJECT_CONFLICT",
                severity="error",
                parties=parties,
                port=port,
                message=f"Host port {port}/{protocol} is claimed by multiple services: {', '.join(parties)}.",
                details={"protocol": protocol},
            )
        )

    service_ports: Dict[str, set[int]] = {}
    for service in model.services:
        ports = {
            req.port
            for req in service.port_requirements
            if req.role == "host" and req.port is not None and req.classification in {"EXPLICIT", "INFERRED", "AMBIGUOUS"}
        }
        if len(ports) > 1:
            conflicts.append(
                Conflict(
                    kind="CONFIGURATION_CONFLICT",
                    severity="warning",
                    parties=[service.name],
                    port=None,
                    message=f"Service '{service.name}' declares inconsistent host ports across sources.",
                    details={"ports": sorted(ports), "source_paths": sorted(service.source_paths)},
                )
            )
        service_ports[service.name] = ports

    if model.existing_manifest:
        manifest_ports = model.existing_manifest.get("ports") or {}
        for service_name, manifest_entry in manifest_ports.items():
            preferred = manifest_entry.get("preferred") if isinstance(manifest_entry, dict) else None
            if preferred is None:
                continue
            discovered_ports = service_ports.get(service_name, set())
            if discovered_ports and preferred not in discovered_ports:
                conflicts.append(
                    Conflict(
                        kind="CONFIGURATION_CONFLICT",
                        severity="warning",
                        parties=[service_name],
                        port=preferred,
                        message=(
                            f"Service '{service_name}' preferred port {preferred} in portforge.yml "
                            f"does not match discovered ports {sorted(discovered_ports)}."
                        ),
                        details={"manifest_preferred": preferred, "discovered_ports": sorted(discovered_ports)},
                    )
                )

    local_ports = local_ports or []
    reservations = reservations or []
    allocations = allocations or []

    for requirement in host_requirements:
        protocol = _protocol(requirement.protocol)
        discovered = find_discovered(local_ports, requirement.port, protocol)
        reservation = find_reservation(reservations, "local", requirement.port, protocol)
        evaluated = evaluate_port("local", requirement.port, protocol, discovered, reservation)
        if evaluated.state in {PortState.ACTIVE, PortState.CONFLICT, PortState.SYSTEM}:
            conflicts.append(
                Conflict(
                    kind="LOCAL_RUNTIME_CONFLICT",
                    severity="error",
                    parties=[requirement.service],
                    port=requirement.port,
                    message=f"Host port {requirement.port}/{protocol} is not free locally ({evaluated.state.value}).",
                    details={"state": evaluated.state.value},
                )
            )
        elif evaluated.state == PortState.RESERVED and reservation is not None:
            if project_name and reservation.project != project_name:
                conflicts.append(
                    Conflict(
                        kind="RESERVATION_CONFLICT",
                        severity="error",
                        parties=[requirement.service, reservation.project],
                        port=requirement.port,
                        message=(
                            f"Host port {requirement.port}/{protocol} is reserved for project "
                            f"'{reservation.project}'."
                        ),
                        details={"reservation_project": reservation.project},
                    )
                )

    if allocations:
        for requirement in host_requirements:
            for allocation in allocations:
                if allocation.get("status") not in {None, "active", "ACTIVE"}:
                    continue
                if project_name and allocation.get("project") == project_name:
                    continue
                for entry in allocation.get("allocations") or []:
                    if entry.get("port") == requirement.port and entry.get("protocol", "tcp") == requirement.protocol:
                        conflicts.append(
                            Conflict(
                                kind="CENTRAL_ALLOCATION_CONFLICT",
                                severity="error",
                                parties=[requirement.service, str(allocation.get("project"))],
                                port=requirement.port,
                                message=(
                                    f"Host port {requirement.port}/{requirement.protocol} is allocated to project "
                                    f"'{allocation.get('project')}'."
                                ),
                                details={"allocation_id": allocation.get("allocation_id")},
                            )
                        )

    return _dedupe_conflicts(conflicts)


def _dedupe_conflicts(conflicts: List[Conflict]) -> List[Conflict]:
    """Collapse duplicate conflict rows (e.g. same LOCAL_RUNTIME from dotenv+package evidence)."""
    seen: set[tuple] = set()
    unique: List[Conflict] = []
    for conflict in conflicts:
        key = (
            conflict.kind,
            conflict.port,
            conflict.severity,
            tuple(conflict.parties),
            conflict.message,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(conflict)
    return unique
