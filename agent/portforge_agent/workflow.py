"""Phase 8D: high-level, provider-neutral workflow orchestration.

    workflow prepare    -- non-mutating preview (manifest + candidates + config-impact summary)
    workflow apply       -- allocate (Phase 8A) then, if config: mappings exist, plan+apply (Phase 8C)
    workflow status      -- read the persisted workflow record (no network needed)

This module NEVER reimplements allocation or config-mutation logic -- it
only sequences calls into `project_adapter.py` (host resolution, candidate
preview, allocation-body building), `central_client.py`
(create_allocation/release_allocation), and `config_manager.py`
(build_plan/persist_plan/apply_mutation/rollback_mutation/get_status). See
docs/phase8d_agent_integration_audit.md for the design this follows.

Idempotency and input-change detection (task §11/§12) are handled at
THIS layer with a small persisted `WorkflowRecord`
(`<project_root>/.portforge/workflows/<request_id>/record.json`) that
hashes the normalized request; Phase 8A's own request_id idempotency and
Phase 8C's own mutation-status idempotency are each still exactly what
they were -- this layer just decides WHETHER to call them again at all, so
a pure replay touches neither Central nor any project file a second time.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config_manager as cm
from .config_files import atomic_write, read_file_bytes
from .manifest import ManifestPortRequest, ProjectManifest
from .project_adapter import NormalizedHostRef, build_candidate_preview, build_normalized_request, to_allocation_body

CONTRACT_VERSION = 1
WORKFLOWS_DIR_NAME = ".portforge"


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, details: Optional[List[dict]] = None, recovery: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.recovery = recovery or {}


# ---------------------------------------------------------------------------
# Workflow record persistence
# ---------------------------------------------------------------------------


def _workflow_dir(project_root: Path, request_id: str) -> Path:
    return project_root / WORKFLOWS_DIR_NAME / "workflows" / request_id


def _hash_manifest_for_workflow(manifest: ProjectManifest) -> str:
    """Canonical fingerprint of "the request that matters" for workflow
    idempotency -- mirrors allocation_service.py's own `_hash_payload`
    approach on the backend (sorted-by-name, deterministic JSON encoding)
    so a materially unchanged manifest hashes identically regardless of
    key ordering in the YAML file.
    """
    canonical = {
        "project": manifest.project,
        "host": manifest.host,
        "requests": sorted(
            (
                {"name": r.name, "purpose": r.purpose, "protocol": r.protocol, "preferred_port": r.preferred_port}
                for r in manifest.requests
            ),
            key=lambda r: r["name"],
        ),
        "config": None,
    }
    if manifest.config is not None:
        canonical["config"] = {
            "dotenv": sorted(
                (
                    {"file": m.file, "values": dict(sorted(m.values.items()))}
                    for m in manifest.config.dotenv
                ),
                key=lambda m: m["file"],
            ),
            "compose": sorted(
                (
                    {
                        "file": m.file,
                        "services": {
                            name: sorted(
                                ({"allocation": e.allocation, "container": e.container, "protocol": e.protocol} for e in entries),
                                key=lambda e: (e["allocation"], e["container"]),
                            )
                            for name, entries in sorted(m.services.items())
                        },
                    }
                    for m in manifest.config.compose
                ),
                key=lambda m: m["file"],
            ),
        }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_workflow_record(project_root: Path, request_id: str) -> Optional[dict]:
    raw = read_file_bytes(_workflow_dir(project_root, request_id) / "record.json")
    return None if raw is None else json.loads(raw.decode("utf-8"))


def _save_workflow_record(project_root: Path, record: dict) -> dict:
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(_workflow_dir(project_root, record["request_id"]) / "record.json", json.dumps(record, indent=2).encode("utf-8"))
    return record


# ---------------------------------------------------------------------------
# prepare -- non-mutating
# ---------------------------------------------------------------------------


def prepare_workflow(client, manifest: ProjectManifest, host: NormalizedHostRef, project_root: Path) -> dict:
    candidates = build_candidate_preview(client, host, manifest.requests)

    config_files: List[dict] = []
    warnings: List[str] = []
    if manifest.config is not None:
        for m in manifest.config.dotenv:
            config_files.append({"file": m.file, "type": "dotenv"})
        for m in manifest.config.compose:
            config_files.append({"file": m.file, "type": "compose"})

    for c in candidates:
        if c["candidate_port"] is None:
            warnings.append(f"No candidate port currently available for '{c['name']}' (purpose '{c['purpose']}').")

    next_actions = ["workflow apply"]
    if not config_files:
        next_actions.append("allocate (no config mappings declared -- 'workflow apply' will only allocate)")

    return {
        "contract_version": CONTRACT_VERSION,
        "ready": len(warnings) == 0,
        "project": manifest.project,
        "host": {"id": host.id, "hostname": host.hostname},
        "requests": candidates,
        "config_files": config_files,
        "warnings": warnings,
        "next_actions": next_actions,
    }


# ---------------------------------------------------------------------------
# apply -- the only mutating workflow command
# ---------------------------------------------------------------------------


def _recovery_block(allocation_id: Optional[str], mutation_id: Optional[str]) -> dict:
    block: Dict[str, str] = {}
    if mutation_id:
        block["rollback_command"] = f"portforge config rollback {mutation_id} --project-root <dir>"
    if allocation_id:
        block["release_command"] = f"portforge allocation release {allocation_id}"
    return block


def _result_from_record(record: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": record["status"],
        "request_id": record["request_id"],
        "project": record["project"],
        "host": record["host"],
        "allocation": {
            "id": record.get("allocation_id"),
            # Persisted at the moment the allocation was actually first
            # created for this workflow -- a later replay of THIS SAME
            # call still correctly reports "yes, establishing this
            # workflow's outcome created that allocation" (the property
            # this field exists for -- see _compensate's use of it), not
            # "did THIS specific invocation just now create it."
            "created_by_this_attempt": record.get("created_by_this_attempt", False),
        },
        "ports": record.get("ports", {}),
        "config": {"applied": record.get("config_applied", False), "mutation_id": record.get("mutation_id")},
        "recovery": _recovery_block(record.get("allocation_id"), record.get("mutation_id") if record.get("config_applied") else None),
    }


def _compensate(client, project_root: Path, mutation_id: Optional[str], allocation_id: str, created_by_this_attempt: bool) -> List[str]:
    """Task §6/§7: if config planning/apply fails after a NEW allocation
    was created by THIS attempt, roll back any applied config mutation and
    release that allocation. An allocation reused via idempotent replay
    (created by an EARLIER attempt) is never released here -- only
    resources this specific attempt is responsible for.
    """
    actions: List[str] = []

    if mutation_id is not None:
        try:
            status = cm.get_status(project_root, mutation_id)
            if status["status"] == "APPLIED":
                cm.rollback_mutation(project_root, mutation_id)
                actions.append("config_rolled_back")
            else:
                # Phase 8C's own apply_mutation already leaves no partial
                # file state on failure (write-temp-then-replace, backup
                # before write) -- nothing further to undo here.
                actions.append("config_not_applied_nothing_to_roll_back")
        except cm.ConfigError:
            actions.append("config_rollback_check_failed")

    if created_by_this_attempt:
        result = client.release_allocation(allocation_id)
        actions.append("allocation_released" if result.success else "allocation_release_failed")
    else:
        actions.append("allocation_preserved_not_created_by_this_attempt")

    return actions


def apply_workflow(client, manifest: ProjectManifest, host: NormalizedHostRef, project_root: Path, request_id: str) -> dict:
    manifest_hash = _hash_manifest_for_workflow(manifest)
    existing = _load_workflow_record(project_root, request_id)

    if existing is not None:
        if existing["manifest_hash"] != manifest_hash:
            raise WorkflowError(
                "WORKFLOW_IDEMPOTENCY_CONFLICT",
                f"request_id '{request_id}' was already used with a materially different manifest/config.",
                details=[{"request_id": request_id}],
            )
        if existing["status"] in ("ALLOCATED", "APPLIED"):
            # Pure replay -- touches neither Central nor any project file.
            return _result_from_record(existing)
        # status is FAILED (a prior attempt didn't finish cleanly) -- fall
        # through and retry fresh below. `created_by_this_attempt` reflects
        # whether an earlier attempt already has an allocation on record.
    created_by_this_attempt = existing is None or not existing.get("allocation_id")

    normalized_body = to_allocation_body(build_normalized_request(manifest, host), request_id)
    alloc_result = client.create_allocation(
        project=normalized_body["project"],
        host_id=normalized_body["host_id"],
        requests=normalized_body["requests"],
        request_id=request_id,
    )

    if not alloc_result.success:
        payload = alloc_result.data if isinstance(alloc_result.data, dict) and "error" in alloc_result.data else {}
        underlying = payload.get("error", {})
        record = _save_workflow_record(
            project_root,
            {
                "request_id": request_id,
                "manifest_hash": manifest_hash,
                "project": manifest.project,
                "host": {"id": host.id, "hostname": host.hostname},
                "status": "FAILED",
                "allocation_id": None,
                "mutation_id": None,
                "config_applied": False,
                "ports": {},
                "created_at": (existing or {}).get("created_at", datetime.now(timezone.utc).isoformat()),
            },
        )
        raise WorkflowError(
            underlying.get("code", "ALLOCATION_UNAVAILABLE"),
            underlying.get("message", alloc_result.error or "Allocation failed."),
            details=underlying.get("details", []),
        )

    allocation_data = alloc_result.data
    allocation_id = allocation_data["allocation_id"]

    # Phase 8A's OWN idempotency (frozen, never redesigned here) matches
    # purely on request_id + payload hash -- it returns the SAME
    # allocation object even if that allocation was since released (e.g.
    # by an earlier workflow attempt's own compensation). A released
    # allocation can never become active again under the same request_id
    # -- retrying with a NEW request_id is the correct recovery, not
    # silently proceeding as if this were a fresh, active allocation.
    if allocation_data.get("status") != "active":
        _save_workflow_record(
            project_root,
            {
                "request_id": request_id,
                "manifest_hash": manifest_hash,
                "project": manifest.project,
                "host": {"id": host.id, "hostname": host.hostname},
                "status": "FAILED",
                "allocation_id": allocation_id,
                "created_by_this_attempt": False,
                "mutation_id": None,
                "config_applied": False,
                "ports": {},
                "created_at": (existing or {}).get("created_at", datetime.now(timezone.utc).isoformat()),
            },
        )
        raise WorkflowError(
            "WORKFLOW_STATE_INCONSISTENT",
            f"request_id '{request_id}' already maps to allocation {allocation_id}, which is no longer "
            f"active (status={allocation_data.get('status')}) -- likely released by an earlier attempt's "
            "compensation. Retry with a NEW request_id.",
            details=[{"allocation_id": allocation_id, "status": allocation_data.get("status")}],
        )

    ports = {e["name"]: e["port"] for e in allocation_data.get("allocations", [])}

    base_record = {
        "request_id": request_id,
        "manifest_hash": manifest_hash,
        "project": manifest.project,
        "host": {"id": host.id, "hostname": host.hostname},
        "allocation_id": allocation_id,
        "created_by_this_attempt": created_by_this_attempt,
        "ports": ports,
        "created_at": (existing or {}).get("created_at", datetime.now(timezone.utc).isoformat()),
    }

    if manifest.config is None or (not manifest.config.dotenv and not manifest.config.compose):
        record = _save_workflow_record(
            project_root, {**base_record, "status": "ALLOCATED", "mutation_id": None, "config_applied": False}
        )
        return _result_from_record(record)

    mutation_id: Optional[str] = None
    try:
        plan = cm.build_plan(manifest, allocation_data, project_root)
        cm.persist_plan(plan, project_root)
        mutation_id = plan.mutation_id
        cm.apply_mutation(project_root, mutation_id)
    except cm.ConfigError as exc:
        compensation_actions = _compensate(client, project_root, mutation_id, allocation_id, created_by_this_attempt)
        _save_workflow_record(
            project_root,
            {
                **base_record,
                "status": "FAILED",
                "mutation_id": mutation_id,
                "config_applied": False,
                "compensation": compensation_actions,
            },
        )
        if "allocation_release_failed" in compensation_actions or "config_rollback_check_failed" in compensation_actions:
            raise WorkflowError(
                "WORKFLOW_COMPENSATION_FAILED",
                f"Config step failed ({exc.code}: {exc.message}) and automatic compensation could not fully "
                f"complete: {', '.join(compensation_actions)}. Manual cleanup required.",
                details=[{"underlying_code": exc.code, "underlying_message": exc.message}],
                recovery=_recovery_block(allocation_id if not created_by_this_attempt else None, None),
            )
        raise WorkflowError(
            exc.code,
            exc.message,
            details=exc.details,
            recovery={"compensation_actions": compensation_actions},
        )

    record = _save_workflow_record(
        project_root, {**base_record, "status": "APPLIED", "mutation_id": mutation_id, "config_applied": True}
    )
    return _result_from_record(record)


# ---------------------------------------------------------------------------
# status -- purely local, no network
# ---------------------------------------------------------------------------


def get_workflow_status(project_root: Path, request_id: str) -> dict:
    record = _load_workflow_record(project_root, request_id)
    if record is None:
        raise WorkflowError("WORKFLOW_NOT_FOUND", f"No workflow record found for request_id '{request_id}'.")
    return _result_from_record(record)
