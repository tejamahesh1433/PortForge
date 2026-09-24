from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..project_adapter import resolve_host_ref
from .fingerprint import compute_workspace_fingerprint
from .models import CoordinatedPlanProposal, PortRequirement, ServiceInfo, WorkspaceModel


def _conflict_codes_for_port(model: WorkspaceModel, port: int, service: str) -> List[str]:
    codes: List[str] = []
    for conflict in model.conflicts:
        if conflict.port != port:
            continue
        if service not in conflict.parties and conflict.kind != "INTERNAL_PROJECT_CONFLICT":
            continue
        codes.append(conflict.kind)
    return sorted(set(codes))


def _host_plan_requirements(services: List[ServiceInfo]) -> List[PortRequirement]:
    requirements: List[PortRequirement] = []
    seen: set[tuple] = set()
    for service in services:
        for requirement in service.port_requirements:
            if requirement.role != "host":
                continue
            if requirement.classification not in {"EXPLICIT", "INFERRED"}:
                continue
            if not requirement.mutable:
                continue
            key = (requirement.service, requirement.port, requirement.protocol)
            if key in seen:
                continue
            seen.add(key)
            requirements.append(requirement)
    return requirements


def plan_workspace(
    model: WorkspaceModel,
    *,
    client=None,
    host=None,
) -> dict:
    from pathlib import Path

    requirements = _host_plan_requirements(model.services)
    service_rows: List[Dict[str, Any]] = []
    reasons: List[Dict[str, Any]] = []
    ready = True

    host_ref = host
    if client is not None and host is None and model.existing_manifest:
        host_ref, host_error, _code = resolve_host_ref(client, model.existing_manifest.get("host") or "")
        if host_error:
            reasons.append({"code": "CENTRAL_UNAVAILABLE", "message": host_error})
            ready = False

    for requirement in requirements:
        current_port = requirement.port
        conflict_codes = _conflict_codes_for_port(model, current_port, requirement.service) if current_port else []
        proposed_port = current_port
        reason_code = "PRESERVE"
        reason_message = "Preferred port is free."

        if conflict_codes:
            ready = False
            reason_code = conflict_codes[0]
            reason_message = f"Conflict detected: {reason_code}."
            proposed_port = None
            if client is not None and host_ref is not None:
                purpose = requirement.service
                if model.existing_manifest:
                    manifest_ports = model.existing_manifest.get("ports") or {}
                    manifest_entry = manifest_ports.get(requirement.service)
                    if isinstance(manifest_entry, dict) and manifest_entry.get("purpose"):
                        purpose = manifest_entry["purpose"]
                result = client.get_recommendation(host_ref.id, purpose, requirement.protocol)
                if result.success and isinstance(result.data, dict):
                    recommended = result.data.get("recommended_port") or result.data.get("port")
                    if isinstance(recommended, int):
                        proposed_port = recommended
                        reason_code = reason_code
                        reason_message = f"Recommended replacement due to {reason_code}."
                else:
                    reasons.append(
                        {
                            "code": "CENTRAL_UNAVAILABLE",
                            "service": requirement.service,
                            "port": current_port,
                            "message": result.error or "Central recommendation unavailable.",
                        }
                    )
            else:
                reasons.append(
                    {
                        "code": "NO_ALTERNATIVE",
                        "service": requirement.service,
                        "port": current_port,
                        "message": "No Central client available for replacement recommendation.",
                    }
                )

        service_rows.append(
            {
                "service": requirement.service,
                "protocol": requirement.protocol,
                "current_port": current_port,
                "proposed_port": proposed_port,
                "reason": {"code": reason_code, "message": reason_message, "conflicts": conflict_codes},
                "mutable": requirement.mutable,
                "classification": requirement.classification,
            }
        )

    fingerprint = model.workspace_fingerprint or compute_workspace_fingerprint(
        Path(model.project_root),
        model.fingerprint_inputs,
        {"services": [service.to_dict() for service in model.services]},
    )

    proposal = CoordinatedPlanProposal(
        workspace_fingerprint=fingerprint,
        services=service_rows,
        reasons=reasons,
        ready=ready and not any(row["reason"]["conflicts"] for row in service_rows if row["proposed_port"] is None),
    )
    return proposal.to_dict()


def build_manifest_draft(model: WorkspaceModel, plan: dict) -> str:
    project = (model.existing_manifest or {}).get("project") or "discovered-project"
    host = (model.existing_manifest or {}).get("host") or "workstation"
    lines = [
        "version: 1",
        f"project: {project}",
        "target:",
        f"  host: {host}",
        "ports:",
    ]
    for row in plan.get("services") or []:
        service = row.get("service")
        port = row.get("proposed_port") or row.get("current_port")
        if not service or port is None:
            continue
        lines.extend(
            [
                f"  {service}:",
                "    purpose: generic",
                "    protocol: tcp",
                f"    preferred: {port}",
            ]
        )
    return "\n".join(lines) + "\n"
