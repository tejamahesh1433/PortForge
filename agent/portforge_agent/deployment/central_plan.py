"""Map target plans to Central deployment API payloads (Phase 18)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..manifest import discover_manifest_path, load_and_validate_manifest
from ..targets.models import TargetsError
from ..workspace.discover import discover_workspace

_INGRESS_PLAN_KEYS = frozenset(
    {"name", "scheme", "hostname", "public_port", "service", "status", "bound_host_port"}
)


def resolve_deployment_project(project_root: Path) -> str:
    manifest_path = discover_manifest_path(str(project_root), walk_up=False)
    if manifest_path is not None:
        return load_and_validate_manifest(manifest_path).project
    model = discover_workspace(project_root, include_local_runtime=False)
    project = (model.existing_manifest or {}).get("project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    raise TargetsError(
        "MANIFEST_INVALID",
        "Could not determine project name; add portforge.yml with a project field.",
    )


def services_for_deployment_plan(services: List[dict]) -> List[dict]:
    planned: List[dict] = []
    for row in services:
        host_port = row.get("host_port")
        if host_port is None:
            continue
        planned.append(
            {
                "name": row["service"],
                "internal_port": row["internal_port"],
                "host_port": host_port,
                "protocol": row.get("protocol", "tcp"),
            }
        )
    return planned


def ingress_for_deployment_plan(bindings: List[dict]) -> List[dict]:
    planned: List[dict] = []
    for binding in bindings:
        entry = {key: binding[key] for key in _INGRESS_PLAN_KEYS if key in binding}
        planned.append(entry)
    return planned


def build_deployment_plan_request(
    target_payload: dict,
    *,
    project: str,
    workspace_fingerprint: Optional[str] = None,
) -> dict:
    return {
        "host_id": target_payload["host_id"],
        "project": project,
        "environment": target_payload["environment"],
        "target_alias": target_payload.get("target"),
        "workspace_fingerprint": workspace_fingerprint,
        "services": services_for_deployment_plan(target_payload.get("services") or []),
        "ingress_bindings": ingress_for_deployment_plan(target_payload.get("ingress") or []),
    }


def ports_json_from_target_services(services: List[dict]) -> Optional[dict]:
    entries: List[dict] = []
    for row in services:
        host_port = row.get("host_port")
        if host_port is None:
            continue
        entry: Dict[str, Any] = {
            "name": row["service"],
            "internal_port": row["internal_port"],
            "host_port": host_port,
            "protocol": row.get("protocol", "tcp"),
        }
        if row.get("reason_code"):
            entry["reason_code"] = row["reason_code"]
        entries.append(entry)
    if not entries:
        return None
    return {"services": entries}


def ingress_json_from_target_bindings(bindings: List[dict]) -> Optional[dict]:
    if not bindings:
        return None
    cleaned = ingress_for_deployment_plan(bindings)
    return {"bindings": cleaned} if cleaned else None
