from __future__ import annotations

from typing import Any, Optional

from .. import config_manager as cm
from ..agent_contract import build_contract
from ..central_client import CentralResult
from ..manifest import ManifestError, load_and_validate_manifest
from ..project_adapter import build_candidate_preview, build_normalized_request, to_allocation_body
from ..workflow import WorkflowError, apply_workflow, get_workflow_status, prepare_workflow
from ..workspace import discover_workspace, plan_workspace, workspace_to_json
from . import MCP_SCHEMA_VERSION
from .approval import require_mutate_approval
from .context import load_manifest_and_host, make_client, resolve_manifest_path, resolve_project_root
from .errors import McpToolError, map_exception
from .plans import compute_plan_fingerprint, save_plan, verify_plan_fresh
from .scrub import scrub_secrets

Classification = str

READ = "READ"
PLAN = "PLAN"
MUTATE = "MUTATE"


def _url(arguments: dict, server_defaults: dict) -> Optional[str]:
    return arguments.get("central_url") or server_defaults.get("central_url")


def _raise_central_failure(result: CentralResult) -> None:
    if result.success:
        return
    payload = result.data if isinstance(result.data, dict) else {}
    underlying = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
    raise McpToolError(
        underlying.get("code", "CENTRAL_UNAVAILABLE"),
        underlying.get("message", result.error or "Central request failed."),
        details=underlying.get("details", []),
    )


def _config_targets_from_manifest(manifest) -> list:
    targets: list = []
    if manifest.config is None:
        return targets
    for m in manifest.config.dotenv:
        targets.append({"file": m.file, "type": "dotenv", "mappings": sorted(m.values.items())})
    for m in manifest.config.compose:
        services = {}
        for svc_name, entries in m.services.items():
            services[svc_name] = [
                {"allocation": e.allocation, "container": e.container, "protocol": e.protocol} for e in entries
            ]
        targets.append({"file": m.file, "type": "compose", "services": services})
    for m in manifest.config.kubernetes:
        targets.append(
            {
                "file": m.file,
                "type": "kubernetes",
                "host_ports": [
                    {
                        "kind": hp.kind,
                        "name": hp.name,
                        "namespace": hp.namespace,
                        "container": hp.container,
                        "container_port": hp.container_port,
                        "allocation": hp.allocation,
                    }
                    for hp in m.host_ports
                ],
                "node_ports": [
                    {
                        "name": np.name,
                        "namespace": np.namespace,
                        "service_port": np.service_port,
                        "allocation": np.allocation,
                    }
                    for np in m.node_ports
                ],
            }
        )
    return targets


def _fields_affected(config_targets: list) -> list:
    fields: list = []
    for target in config_targets:
        entry: dict[str, Any] = {"file": target["file"], "type": target["type"]}
        if target["type"] == "dotenv":
            entry["keys"] = [key for key, _ in target.get("mappings", [])]
        elif target["type"] == "compose":
            entry["services"] = list(target.get("services", {}).keys())
        elif target["type"] == "kubernetes":
            entry["host_ports"] = len(target.get("host_ports", []))
            entry["node_ports"] = len(target.get("node_ports", []))
        fields.append(entry)
    return fields


def _rollback_info() -> dict:
    return {
        "config_rollback": "portforge config rollback <mutation_id> --project-root <dir>",
        "allocation_release": "portforge allocation release <allocation_id>",
        "note": "If config was applied, roll back config before releasing the allocation.",
    }


def _verification_plan() -> dict:
    return {
        "workflow_status": "portforge workflow status --request-id <request_id> --project-root <dir>",
        "allocation_verify": "portforge allocation verify <allocation_id>",
    }


def _handle_capabilities(arguments: dict, server_defaults: dict) -> dict:
    contract = build_contract()
    central_available = False
    central_error = None
    try:
        client = make_client(_url(arguments, server_defaults))
        health = client.health()
        central_available = health.success
        if not health.success:
            central_error = health.error
    except McpToolError as exc:
        central_error = scrub_secrets(exc.message)

    return {
        **contract,
        "mcp_schema_version": MCP_SCHEMA_VERSION,
        "tools": [spec["name"] for spec in TOOL_SPECS],
        "central_available": central_available,
        "central_error": scrub_secrets(central_error) if isinstance(central_error, str) else central_error,
        "supported_mutation_types": ["dotenv", "compose", "kubernetes-hostPort", "kubernetes-nodePort"],
        "unsupported_automatic_mutations": ["containerPort", "Service port", "targetPort"],
    }


def _handle_project_inspect(arguments: dict, server_defaults: dict) -> dict:
    url = _url(arguments, server_defaults)
    project_root = arguments.get("project_root")
    manifest_path = arguments.get("manifest_path")

    try:
        path = resolve_manifest_path(project_root, manifest_path)
        manifest = load_and_validate_manifest(path)
    except ManifestError as exc:
        raise map_exception(exc)

    rows = []
    for item in manifest.requests:
        rows.append(
            {
                "service": item.name,
                "purpose": item.purpose,
                "protocol": item.protocol,
                "preferred_port": item.preferred_port,
                "requested_range": item.requested_range,
                "allocation_status": "UNALLOCATED",
            }
        )

    central_available = False
    central_error = None
    try:
        client = make_client(url)
        result = client.list_allocations()
        central_available = result.success
        central_error = result.error if not result.success else None
        if result.success:
            allocations = result.data.get("items", []) if isinstance(result.data, dict) else (result.data or [])
            for allocation in allocations:
                if allocation.get("project") != manifest.project:
                    continue
                for entry in allocation.get("allocations", []):
                    match = next((row for row in rows if row["service"] == entry.get("name")), None)
                    if match is not None:
                        match.update(
                            {
                                "allocation_id": allocation.get("allocation_id"),
                                "host": allocation.get("host"),
                                "allocated_port": entry.get("port"),
                                "allocation_status": allocation.get("status"),
                            }
                        )
    except McpToolError as exc:
        central_error = exc.message

    return {
        "project": manifest.project,
        "manifest_path": str(path.resolve()),
        "manifest_valid": True,
        "host": manifest.host,
        "central_available": central_available,
        "central_error": central_error,
        "services": rows,
        "config_targets": _config_targets_from_manifest(manifest),
    }


def _handle_workspace_discover(arguments: dict, server_defaults: dict) -> dict:
    project_root = resolve_project_root(arguments.get("project_root"))
    include_local_runtime = arguments.get("include_local_runtime", True)
    model = discover_workspace(
        project_root,
        central_url=_url(arguments, server_defaults),
        include_local_runtime=include_local_runtime,
    )
    return workspace_to_json(model)


def _handle_project_plan(arguments: dict, server_defaults: dict) -> dict:
    if arguments.get("workspace"):
        project_root = resolve_project_root(arguments.get("project_root"), arguments.get("manifest_path"))
        url = _url(arguments, server_defaults)
        model = discover_workspace(project_root, central_url=url, include_local_runtime=True)
        client = None
        host = None
        if url:
            try:
                client = make_client(url)
                if model.existing_manifest:
                    from ..project_adapter import resolve_host_ref

                    host, host_error, _code = resolve_host_ref(client, model.existing_manifest.get("host") or "")
                    if host_error:
                        host = None
            except McpToolError:
                client = None
        plan = plan_workspace(model, client=client, host=host)
        from .plans import compute_workspace_plan_fingerprint

        intent_summary = {
            "services": [
                {"name": row.get("service"), "proposed_port": row.get("proposed_port"), "current_port": row.get("current_port")}
                for row in plan.get("services", [])
            ]
        }
        plan_hash, file_hashes = compute_workspace_plan_fingerprint(
            project_root, model.fingerprint_inputs, intent_summary
        )
        plan_id = save_plan(
            project_root,
            {
                "mode": "workspace",
                "plan_hash": plan_hash,
                "file_hashes": file_hashes,
                "fingerprint_inputs": list(model.fingerprint_inputs),
                "intent_summary": intent_summary,
                "project_root": str(project_root),
            },
        )
        return {
            "mode": "workspace",
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "project_root": str(project_root),
            "discovery": workspace_to_json(model),
            "plan": plan,
            "committed": False,
        }

    client, manifest, host, project_root = load_manifest_and_host(
        _url(arguments, server_defaults),
        arguments.get("project_root"),
        arguments.get("manifest_path"),
    )
    prepared = prepare_workflow(client, manifest, host, project_root)
    config_targets = _config_targets_from_manifest(manifest)
    plan_hash = compute_plan_fingerprint(manifest, project_root)

    plan_body = {
        "plan_hash": plan_hash,
        "project": manifest.project,
        "project_root": str(project_root),
        "host": prepared["host"],
        "requests": prepared["requests"],
        "config_files": prepared["config_files"],
        "warnings": prepared["warnings"],
    }
    plan_id = save_plan(project_root, plan_body)

    return {
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "ready": prepared["ready"],
        "project": manifest.project,
        "host": prepared["host"],
        "requests": prepared["requests"],
        "candidates": prepared["requests"],
        "config_files": prepared["config_files"],
        "fields_affected": _fields_affected(config_targets),
        "verification_plan": _verification_plan(),
        "rollback_info": _rollback_info(),
        "warnings": prepared["warnings"],
        "committed": False,
    }


def _handle_project_provision(arguments: dict, server_defaults: dict) -> dict:
    request_id = arguments.get("request_id")
    if not request_id:
        raise McpToolError("INVALID_PARAMS", "request_id is required.")

    client, manifest, host, project_root = load_manifest_and_host(
        _url(arguments, server_defaults),
        arguments.get("project_root"),
        arguments.get("manifest_path"),
    )

    plan_id = arguments.get("plan_id")
    if plan_id:
        from .plans import load_plan

        record = load_plan(project_root, plan_id)
        if record.get("mode") == "workspace":
            verify_plan_fresh(project_root, plan_id, manifest=None)
        else:
            verify_plan_fresh(project_root, plan_id, manifest)

    try:
        return apply_workflow(client, manifest, host, project_root, request_id)
    except WorkflowError as exc:
        raise map_exception(exc)


def _handle_project_verify(arguments: dict, server_defaults: dict) -> dict:
    request_id = arguments.get("request_id")
    allocation_id = arguments.get("allocation_id")
    if not request_id and not allocation_id:
        raise McpToolError("INVALID_PARAMS", "Provide request_id and/or allocation_id.")

    payload: dict[str, Any] = {}
    project_root = resolve_project_root(arguments.get("project_root"), arguments.get("manifest_path"))

    if request_id:
        try:
            payload["workflow"] = get_workflow_status(project_root, request_id)
        except WorkflowError as exc:
            raise map_exception(exc)

    if allocation_id:
        client = make_client(_url(arguments, server_defaults))
        result = client.verify_allocation(allocation_id)
        _raise_central_failure(result)
        payload["allocation"] = result.data

    return payload


def _handle_project_rollback(arguments: dict, server_defaults: dict) -> dict:
    mutation_id = arguments.get("mutation_id")
    if not mutation_id:
        raise McpToolError("INVALID_PARAMS", "mutation_id is required.")
    project_root = resolve_project_root(arguments.get("project_root"))
    try:
        return cm.rollback_mutation(project_root, mutation_id)
    except cm.ConfigError as exc:
        raise map_exception(exc)


def _handle_allocation_recommend(arguments: dict, server_defaults: dict) -> dict:
    host_id = arguments.get("host_id")
    purpose = arguments.get("purpose")
    if host_id and purpose:
        client = make_client(_url(arguments, server_defaults))
        protocol = arguments.get("protocol") or "tcp"
        result = client.get_recommendation(host_id, purpose, protocol)
        _raise_central_failure(result)
        return {"recommendation": result.data}

    client, manifest, host, _project_root = load_manifest_and_host(
        _url(arguments, server_defaults),
        arguments.get("project_root"),
        arguments.get("manifest_path"),
    )
    return {
        "project": manifest.project,
        "host": {"id": host.id, "hostname": host.hostname},
        "requests": build_candidate_preview(client, host, manifest.requests),
    }


def _handle_allocation_create(arguments: dict, server_defaults: dict) -> dict:
    request_id = arguments.get("request_id")
    if not request_id:
        raise McpToolError("INVALID_PARAMS", "request_id is required.")

    client, manifest, host, _project_root = load_manifest_and_host(
        _url(arguments, server_defaults),
        arguments.get("project_root"),
        arguments.get("manifest_path"),
    )
    normalized = build_normalized_request(manifest, host)
    body = to_allocation_body(normalized, request_id)
    result = client.create_allocation(
        project=body["project"],
        host_id=body["host_id"],
        requests=body["requests"],
        request_id=body["request_id"],
    )
    _raise_central_failure(result)
    data = result.data
    ports = {entry["name"]: entry["port"] for entry in data.get("allocations", [])}
    return {
        "schema_version": 1,
        "committed": True,
        "allocation_id": data["allocation_id"],
        "project": data["project"],
        "host": data["host"],
        "ports": ports,
        "allocations": data["allocations"],
    }


def _handle_allocation_release(arguments: dict, server_defaults: dict) -> dict:
    allocation_id = arguments.get("allocation_id")
    if not allocation_id:
        raise McpToolError("INVALID_PARAMS", "allocation_id is required.")

    client = make_client(_url(arguments, server_defaults))
    result = client.release_allocation(allocation_id)
    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {}
        underlying = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
        if underlying.get("code") == "ALLOCATION_NOT_FOUND":
            return {"status": "released", "idempotent_replay": True}
        _raise_central_failure(result)
    return result.data


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "portforge_capabilities",
        "description": "Return PortForge agent contract, MCP metadata, and Central availability.",
        "classification": READ,
        "inputSchema": {"type": "object", "properties": {"central_url": {"type": "string"}}, "additionalProperties": False},
        "handler": _handle_capabilities,
    },
    {
        "name": "portforge_workspace_discover",
        "description": "Static workspace discovery for services, host ports, evidence, and conflicts.",
        "classification": READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "central_url": {"type": "string"},
                "include_local_runtime": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "handler": _handle_workspace_discover,
    },
    {
        "name": "portforge_project_inspect",
        "description": "Read-only summary of manifest services, allocations, and config targets.",
        "classification": READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "handler": _handle_project_inspect,
    },
    {
        "name": "portforge_project_plan",
        "description": "Non-mutating workflow plan with fingerprint persisted under .portforge/mcp/plans.",
        "classification": PLAN,
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
                "workspace": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "handler": _handle_project_plan,
    },
    {
        "name": "portforge_project_provision",
        "description": "Apply full workflow (allocate + optional config). Requires confirm_mutate.",
        "classification": MUTATE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "confirm_mutate": {"type": "boolean"},
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
                "plan_id": {"type": "string"},
            },
            "required": ["request_id", "confirm_mutate"],
            "additionalProperties": False,
        },
        "handler": _handle_project_provision,
    },
    {
        "name": "portforge_project_verify",
        "description": "Read workflow status and/or verify an allocation.",
        "classification": READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "allocation_id": {"type": "string"},
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "handler": _handle_project_verify,
    },
    {
        "name": "portforge_project_rollback",
        "description": "Rollback a config mutation only (does not release allocations). Requires confirm_mutate.",
        "classification": MUTATE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "mutation_id": {"type": "string"},
                "confirm_mutate": {"type": "boolean"},
                "project_root": {"type": "string"},
            },
            "required": ["mutation_id", "confirm_mutate"],
            "additionalProperties": False,
        },
        "handler": _handle_project_rollback,
    },
    {
        "name": "portforge_allocation_recommend",
        "description": "Advisory port recommendations for a manifest or a single host/purpose pair.",
        "classification": READ,
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
                "host_id": {"type": "string"},
                "purpose": {"type": "string"},
                "protocol": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "handler": _handle_allocation_recommend,
    },
    {
        "name": "portforge_allocation_create",
        "description": "Create a Central allocation from a manifest. Requires confirm_mutate.",
        "classification": MUTATE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm_mutate": {"type": "boolean"},
                "request_id": {"type": "string"},
                "project_root": {"type": "string"},
                "manifest_path": {"type": "string"},
                "central_url": {"type": "string"},
            },
            "required": ["request_id", "confirm_mutate"],
            "additionalProperties": False,
        },
        "handler": _handle_allocation_create,
    },
    {
        "name": "portforge_allocation_release",
        "description": "Release a Central allocation only (no project config rewrite). Requires confirm_mutate.",
        "classification": MUTATE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm_mutate": {"type": "boolean"},
                "allocation_id": {"type": "string"},
                "central_url": {"type": "string"},
            },
            "required": ["allocation_id", "confirm_mutate"],
            "additionalProperties": False,
        },
        "handler": _handle_allocation_release,
    },
]

def list_tool_definitions() -> list[dict]:
    return [
        {"name": spec["name"], "description": spec["description"], "inputSchema": spec["inputSchema"]}
        for spec in TOOL_SPECS
    ]


def call_tool(name: str, arguments: Optional[dict], server_defaults: dict) -> dict:
    arguments = arguments or {}
    spec = next((item for item in TOOL_SPECS if item["name"] == name), None)
    if spec is None:
        raise McpToolError("INVALID_TOOL", f"Unknown tool '{name}'.")

    if spec["classification"] == MUTATE:
        require_mutate_approval(arguments)

    try:
        result = spec["handler"](arguments, server_defaults)
    except McpToolError:
        raise
    except Exception as exc:
        raise map_exception(exc) from exc

    return scrub_secrets(result)
