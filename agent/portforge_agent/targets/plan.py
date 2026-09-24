from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..central_client import CentralClient
from ..manifest import ManifestError, discover_manifest_path, load_and_validate_manifest
from ..project_adapter import resolve_host_ref
from ..workspace.discover import discover_workspace
from ..workspace.plan import _host_plan_requirements
from .config_overrides import existing_override_files, select_config_files_for_environment
from .ingress import optional_static_proxy_scan, parse_ingress_from_manifest, validate_ingress
from .models import IngressBinding, IngressStatus, ServicePortMapping, TargetPlan, TargetsError
from .request_id import namespace_request_id
from .resolve import resolve_target


def _allocation_host_id(allocation: dict) -> Optional[str]:
    host = allocation.get("host")
    if isinstance(host, dict):
        return host.get("id")
    return allocation.get("host_id")


def _port_conflict_on_host(
    allocations: List[dict],
    *,
    host_id: str,
    port: int,
    protocol: str,
    project_name: Optional[str],
) -> bool:
    for allocation in allocations:
        if allocation.get("status") not in {None, "active", "ACTIVE"}:
            continue
        if _allocation_host_id(allocation) != host_id:
            continue
        if project_name and allocation.get("project") == project_name:
            continue
        for entry in allocation.get("allocations") or []:
            if entry.get("port") == port and entry.get("protocol", "tcp") == protocol:
                return True
    return False


def _ensure_host_active(client: CentralClient, host_id: str) -> None:
    result = client.list_hosts()
    if not result.success:
        return
    items = (result.data or {}).get("items", [])
    match = next((item for item in items if item.get("id") == host_id), None)
    if match is not None and match.get("decommissioned_at"):
        raise TargetsError(
            "HOST_DECOMMISSIONED",
            f"Host {host_id} is decommissioned and cannot be used as a deployment target.",
            details=[{"host_id": host_id}],
        )


def _service_specs_from_manifest(manifest) -> List[dict]:
    specs: List[dict] = []
    for item in manifest.requests:
        internal = item.internal_port if item.internal_port is not None else item.preferred_port
        if internal is None:
            continue
        specs.append(
            {
                "service": item.name,
                "purpose": item.purpose,
                "protocol": item.protocol,
                "internal_port": internal,
                "preferred_host_port": item.preferred_port if item.preferred_port is not None else internal,
            }
        )
    return specs


def _service_specs_from_discovery(model) -> List[dict]:
    specs: List[dict] = []
    requirements = _host_plan_requirements(model.services)
    for requirement in requirements:
        if requirement.port is None:
            continue
        specs.append(
            {
                "service": requirement.service,
                "purpose": requirement.service,
                "protocol": requirement.protocol,
                "internal_port": requirement.port,
                "preferred_host_port": requirement.port,
            }
        )
    return specs


def _merge_service_specs(*groups: List[dict]) -> List[dict]:
    merged: Dict[str, dict] = {}
    for group in groups:
        for spec in group:
            merged[spec["service"]] = spec
    return list(merged.values())


def _bind_ingress_for_target(
    bindings: List[IngressBinding],
    *,
    environment: str,
    target_alias: str,
    service_ports: Dict[str, int],
) -> List[IngressBinding]:
    bound: List[IngressBinding] = []
    for binding in bindings:
        if binding.environment != environment or binding.target != target_alias:
            continue
        host_port = binding.bound_host_port or service_ports.get(binding.service)
        status = IngressStatus.TARGET_BOUND if host_port is not None else IngressStatus.INGRESS_PLANNED
        bound.append(
            IngressBinding(
                name=binding.name,
                scheme=binding.scheme,
                hostname=binding.hostname,
                public_port=binding.public_port,
                service=binding.service,
                environment=binding.environment,
                target=binding.target,
                bound_host_port=host_port,
                status=status,
            )
        )
    return bound


def plan_for_target(
    *,
    project_root: Path,
    environment: str,
    target: Optional[str] = None,
    target_host_id: Optional[str] = None,
    central_url: Optional[str] = None,
    logical_request_id: Optional[str] = None,
    include_local_runtime: bool = False,
) -> dict:
    project_root = project_root.resolve()
    manifest = None
    manifest_path = discover_manifest_path(str(project_root), walk_up=False)
    manifest_data: dict = {}
    if manifest_path is not None:
        try:
            manifest = load_and_validate_manifest(manifest_path)
            manifest_data = {
                "environments": manifest.environments,
                "ingress": manifest.ingress,
                "ports": {
                    item.name: {
                        "purpose": item.purpose,
                        "protocol": item.protocol,
                        "preferred": item.preferred_port,
                        "internal": item.internal_port,
                    }
                    for item in manifest.requests
                },
            }
        except ManifestError:
            manifest = None

    env_name = (environment or "").strip()
    if not env_name:
        raise TargetsError("TARGET_REQUIRED", "environment is required for target-aware planning.")

    target_ref = resolve_target(manifest, env_name, target=target, target_host_id=target_host_id)

    model = discover_workspace(
        project_root,
        central_url=central_url,
        include_local_runtime=include_local_runtime,
    )

    client: Optional[CentralClient] = None
    host_ref = None
    allocations: List[dict] = []
    central_available = False

    if central_url:
        client = CentralClient(central_url)
        host_ref, host_error, host_code = resolve_host_ref(client, target_ref.host_id)
        if host_error:
            raise TargetsError(host_code or "HOST_NOT_FOUND", host_error)
        _ensure_host_active(client, host_ref.id)
        result = client.list_allocations()
        central_available = result.success
        if result.success:
            payload = result.data if isinstance(result.data, dict) else {}
            allocations = payload.get("items", []) if isinstance(payload.get("items"), list) else (result.data or [])

    manifest_specs = _service_specs_from_manifest(manifest) if manifest is not None else []
    discovery_specs = _service_specs_from_discovery(model)
    service_specs = _merge_service_specs(manifest_specs, discovery_specs)
    if not service_specs:
        raise TargetsError(
            "MANIFEST_INVALID",
            "No host-mutable services found in manifest or workspace discovery.",
        )

    project_name = manifest.project if manifest is not None else None
    mappings: List[ServicePortMapping] = []

    for spec in service_specs:
        internal_port = spec["internal_port"]
        requested_host_port = spec["preferred_host_port"]
        protocol = spec["protocol"]
        purpose = spec["purpose"]
        reason_code = "PRESERVE"
        reason_message = "Preferred host port is free on the target."
        host_port = requested_host_port

        if client is None or host_ref is None:
            reason_code = "CENTRAL_UNAVAILABLE"
            reason_message = "Central unavailable; host port conflict check skipped."
        elif _port_conflict_on_host(
            allocations,
            host_id=host_ref.id,
            port=requested_host_port,
            protocol=protocol,
            project_name=project_name,
        ):
            reason_code = "TARGET_PORT_CONFLICT"
            reason_message = f"Port {requested_host_port}/{protocol} is already allocated on target host."
            recommendation = client.get_recommendation(host_ref.id, purpose, protocol)
            if recommendation.success and isinstance(recommendation.data, dict):
                recommended = recommendation.data.get("recommended_port") or recommendation.data.get("port")
                if isinstance(recommended, int):
                    host_port = recommended
                    reason_message = (
                        f"Port {requested_host_port}/{protocol} conflicts on target; "
                        f"Central recommends {recommended}."
                    )
                else:
                    host_port = None
                    reason_code = "NO_ALTERNATIVE"
                    reason_message = "Target port conflict and no Central recommendation available."
            else:
                host_port = None
                reason_code = "CENTRAL_UNAVAILABLE"
                reason_message = recommendation.error or "Central recommendation unavailable after conflict."

        mappings.append(
            ServicePortMapping(
                service=spec["service"],
                internal_port=internal_port,
                host_port=host_port,
                protocol=protocol,
                reason_code=reason_code,
                reason_message=reason_message,
            )
        )

    ingress_bindings = parse_ingress_from_manifest(manifest_data) if manifest_data else []
    service_names = [spec["service"] for spec in service_specs]
    ingress_errors: List[dict] = []
    for binding in ingress_bindings:
        ingress_errors.extend(validate_ingress(binding, service_names))

    service_port_map = {row.service: row.host_port for row in mappings if row.host_port is not None}
    ingress_for_target = _bind_ingress_for_target(
        ingress_bindings,
        environment=env_name,
        target_alias=target_ref.alias,
        service_ports=service_port_map,
    )

    config_overrides = None
    if manifest is not None:
        config_overrides = select_config_files_for_environment(manifest, env_name)
        existing = existing_override_files(project_root, env_name)
        if existing:
            config_overrides["existing_override_files"] = existing

    proxy_evidence = optional_static_proxy_scan(project_root)

    namespaced_hint = None
    if logical_request_id:
        namespaced_hint = namespace_request_id(env_name, target_ref.alias, logical_request_id)

    intent_summary = {
        "environment": env_name,
        "target": target_ref.alias,
        "host_id": target_ref.host_id,
        "services": [row.to_dict() for row in mappings],
        "ingress": [row.to_dict() for row in ingress_for_target],
    }

    from ..mcp.plans import compute_target_plan_fingerprint, save_plan

    fingerprint_inputs = list(model.fingerprint_inputs)
    if manifest_path is not None:
        try:
            fingerprint_inputs.append(str(manifest_path.relative_to(project_root)))
        except ValueError:
            fingerprint_inputs.append(manifest_path.name)
    plan_hash, file_hashes = compute_target_plan_fingerprint(
        manifest,
        project_root,
        environment=env_name,
        target=target_ref.alias,
        host_id=target_ref.host_id,
        fingerprint_inputs=fingerprint_inputs,
        intent_summary=intent_summary,
    )

    plan_id = save_plan(
        project_root,
        {
            "mode": "target",
            "plan_hash": plan_hash,
            "file_hashes": file_hashes,
            "fingerprint_inputs": fingerprint_inputs,
            "intent_summary": intent_summary,
            "environment": env_name,
            "target": target_ref.alias,
            "host_id": target_ref.host_id,
            "project_root": str(project_root),
        },
    )

    plan = TargetPlan(
        plan_id=plan_id,
        environment=env_name,
        target=target_ref.alias,
        host_id=target_ref.host_id,
        services=mappings,
        ingress=ingress_for_target,
        namespaced_request_id_hint=namespaced_hint,
        committed=False,
        config_overrides=config_overrides,
        proxy_evidence=proxy_evidence,
    )

    payload = plan.to_dict()
    payload["schema_version"] = 1
    payload["central_available"] = central_available
    payload["host"] = {"id": target_ref.host_id, "hostname": host_ref.hostname if host_ref else target_ref.hostname}
    payload["plan_hash"] = plan_hash
    if ingress_errors:
        payload["ingress_errors"] = ingress_errors
    return payload
