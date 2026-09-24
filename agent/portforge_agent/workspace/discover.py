from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..central_client import CentralClient
from ..config_files import read_file_bytes, resolve_within_root
from ..discovery import discover_all_ports
from ..paths import reservations_path
from ..reservations.storage import ReservationStore
from .conflicts import build_conflicts
from .extract.compose import safe_extract_compose_services
from .extract.dockerfile import extract_dockerfile_services
from .extract.dotenv import parse_dotenv_ports
from .extract.helm import safe_extract_helm_services
from .extract.kubernetes import safe_extract_kubernetes_services
from .extract.manifest import extract_manifest_summary
from .extract.package_json import safe_extract_package_json_services
from .fingerprint import compute_workspace_fingerprint
from .models import Evidence, PortRequirement, ServiceInfo, WorkspaceModel
from .walk import walk_workspace


def _service_name_from_dotenv_key(key: str) -> str:
    if key.endswith("_PORT"):
        stem = key[: -len("_PORT")]
        return stem.lower() if stem else "app"
    return "app"


def _merge_services(existing: List[ServiceInfo], incoming: List[ServiceInfo]) -> List[ServiceInfo]:
    by_name: Dict[str, ServiceInfo] = {service.name: service for service in existing}
    for service in incoming:
        if service.name not in by_name:
            by_name[service.name] = service
            continue
        current = by_name[service.name]
        current.source_paths = sorted(set(current.source_paths + service.source_paths))
        current.port_requirements.extend(service.port_requirements)
        current.dependencies = sorted(set(current.dependencies + service.dependencies))
        current.evidence.extend(service.evidence)
        if service.type and not current.type:
            current.type = service.type
    return sorted(by_name.values(), key=lambda item: item.name)


def _extract_dotenv_services(source_path: str, text: str) -> List[ServiceInfo]:
    services: List[ServiceInfo] = []
    for entry in parse_dotenv_ports(text):
        service_name = _service_name_from_dotenv_key(entry["key"])
        services.append(
            ServiceInfo(
                name=service_name,
                source_paths=[source_path],
                port_requirements=[
                    PortRequirement(
                        service=service_name,
                        port=entry["port"],
                        protocol="tcp",
                        role="host",
                        classification="EXPLICIT",
                        mutable=True,
                        confidence="high",
                        evidence=[
                            Evidence(
                                source_path=source_path,
                                kind="dotenv",
                                detail=f"dotenv key {entry['key']}",
                                snippet_safe=f"{entry['key']}={entry['port']}",
                            )
                        ],
                    )
                ],
                confidence="high",
            )
        )
    return services


def _read_text(root: Path, relative: str) -> Optional[str]:
    try:
        path = resolve_within_root(root, relative)
    except Exception:
        return None
    data = read_file_bytes(path)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def discover_workspace(
    project_root: Path | str,
    *,
    central_url: Optional[str] = None,
    include_local_runtime: bool = True,
) -> WorkspaceModel:
    started = time.perf_counter()
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project root does not exist: {root}")

    walk_stats = walk_workspace(root)
    model = WorkspaceModel(
        project_root=str(root),
        files_considered=walk_stats.files_considered,
        ignored_directories=walk_stats.ignored_dir_count,
    )

    services: List[ServiceInfo] = []
    parsed_paths: List[str] = []
    manifest_summary: Optional[Dict[str, Any]] = None
    project_name: Optional[str] = None

    for relative in walk_stats.candidates.get("manifest", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        summary, manifest_services, warning = extract_manifest_summary(relative, text)
        if warning:
            model.warnings.append(warning)
            continue
        manifest_summary = summary
        project_name = summary.get("project") if summary else None
        services = _merge_services(services, manifest_services)
        parsed_paths.append(relative)
        model.files_parsed += 1

    for relative in walk_stats.candidates.get("dotenv", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        services = _merge_services(services, _extract_dotenv_services(relative, text))
        parsed_paths.append(relative)
        model.files_parsed += 1

    for relative in walk_stats.candidates.get("compose", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        extracted, warning = safe_extract_compose_services(text, relative)
        if warning:
            model.warnings.append(warning)
        else:
            services = _merge_services(services, extracted)
            parsed_paths.append(relative)
            model.files_parsed += 1

    for relative in walk_stats.candidates.get("dockerfile", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        services = _merge_services(services, extract_dockerfile_services(text, relative))
        parsed_paths.append(relative)
        model.files_parsed += 1

    for relative in walk_stats.candidates.get("package_json", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        extracted, warning = safe_extract_package_json_services(text, relative)
        if warning:
            model.warnings.append(warning)
        else:
            services = _merge_services(services, extracted)
            parsed_paths.append(relative)
            model.files_parsed += 1

    for relative in walk_stats.candidates.get("kubernetes", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        extracted, warning = safe_extract_kubernetes_services(text, relative)
        if warning:
            model.warnings.append(warning)
        elif extracted:
            services = _merge_services(services, extracted)
            parsed_paths.append(relative)
            model.files_parsed += 1

    for relative in walk_stats.candidates.get("helm", []):
        text = _read_text(root, relative)
        if text is None:
            continue
        extracted, warning = safe_extract_helm_services(text, relative)
        if warning:
            model.warnings.append(warning)
        elif extracted:
            services = _merge_services(services, extracted)
            parsed_paths.append(relative)
            model.files_parsed += 1

    model.services = services
    model.existing_manifest = manifest_summary
    model.fingerprint_inputs = sorted(set(parsed_paths))

    allocations: List[dict] = []
    if central_url:
        client = CentralClient(central_url)
        result = client.list_allocations()
        model.central_available = result.success
        model.central_error = result.error if not result.success else None
        if result.success:
            payload = result.data if isinstance(result.data, dict) else {}
            allocations = payload.get("items", []) if isinstance(payload.get("items"), list) else (result.data or [])

    local_ports = discover_all_ports() if include_local_runtime else []
    reservations = []
    if include_local_runtime:
        try:
            reservations = ReservationStore(reservations_path()).load()
        except Exception:
            reservations = []

    model.conflicts = build_conflicts(
        model,
        local_ports=local_ports,
        reservations=reservations,
        allocations=allocations,
        project_name=project_name,
    )

    intent_summary = {"services": [service.to_dict() for service in model.services]}
    model.workspace_fingerprint = compute_workspace_fingerprint(root, model.fingerprint_inputs, intent_summary)
    model.duration_ms = int((time.perf_counter() - started) * 1000)
    return model


def workspace_to_json(model: WorkspaceModel) -> dict:
    return model.to_dict()
