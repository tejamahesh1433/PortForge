"""Phase 8C: safe config plan/apply/status/rollback orchestration.

    committed allocation (Phase 8A, via GET /api/allocations/{id})
      -> ownership verification (project/host/status match the manifest)
      -> plan (dotenv_editor.py / compose_editor.py compute proposed changes,
         nothing written, a mutation record IS persisted for apply to use)
      -> apply (re-verify precondition hashes, back up originals, write
         atomically, all-or-nothing across every target file)
      -> status (read-only)
      -> rollback (re-verify post-apply hashes, restore exact original bytes)

Mirrors manifest.py/project_adapter.py's separation: this module is pure
orchestration logic (no argparse), `cli.py`'s `config` commands just wire
it up. See docs/phase8c_config_audit.md for the design this follows and
docs/phase8c_safe_config.md for the external contract.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .compose_editor import ComposeError, apply_port_mapping, dump_compose, load_compose
from .config_files import ConfigPathError, atomic_write, read_file_bytes, resolve_within_root, sha256_bytes, sha256_of_path
from .dotenv_editor import DotenvError, compute_dotenv_update
from .k8s_editor import (
    DEFAULT_NODEPORT_RANGE,
    KubernetesError,
    apply_host_port,
    apply_node_port,
    dump_kubernetes_documents,
    load_kubernetes_documents,
)
from .manifest import ProjectManifest

MUTATIONS_DIR_NAME = ".portforge"

# A dotenv key name that looks like it holds a secret -- its BEFORE value
# (whatever the user already had in the file) is redacted from every
# output (plan/apply/status), never printed. Phase 8C's AFTER values are
# always allocated port integers, never secrets, so they're never redacted
# -- see docs/phase8c_config_audit.md and docs/phase8c_safe_config.md
# "Secret-safe logging".
_SENSITIVE_KEY_RE = re.compile(r"(SECRET|PASSWORD|TOKEN|KEY|CREDENTIAL)", re.IGNORECASE)


class ConfigError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Optional[List[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def _redact_if_sensitive(key: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return "<redacted>" if _SENSITIVE_KEY_RE.search(key) else value


# ---------------------------------------------------------------------------
# Allocation ownership verification (Phase 8C §18)
# ---------------------------------------------------------------------------


def verify_allocation_ownership(allocation_data: Dict[str, Any], manifest: ProjectManifest, resolved_host_id: str) -> None:
    if allocation_data.get("status") != "active":
        raise ConfigError(
            "ALLOCATION_INACTIVE",
            f"Allocation {allocation_data.get('allocation_id')} is not active "
            f"(status={allocation_data.get('status')}); refusing to plan/apply config against it.",
            status_code=409,
        )
    if allocation_data.get("project") != manifest.project:
        raise ConfigError(
            "ALLOCATION_PROJECT_MISMATCH",
            f"Allocation belongs to project '{allocation_data.get('project')}', "
            f"but the manifest declares project '{manifest.project}'.",
            status_code=409,
        )
    if allocation_data.get("host", {}).get("id") != resolved_host_id:
        raise ConfigError(
            "ALLOCATION_HOST_MISMATCH",
            f"Allocation belongs to host '{allocation_data.get('host', {}).get('hostname')}', "
            f"but the manifest's target.host resolves to a different host.",
            status_code=409,
        )

    if manifest.config is not None:
        ports_by_name = {entry["name"] for entry in allocation_data.get("allocations", [])}
        referenced = set()
        for mapping in manifest.config.dotenv:
            referenced.update(mapping.values.values())
        for mapping in manifest.config.compose:
            for entries in mapping.services.values():
                referenced.update(e.allocation for e in entries)
        for mapping in manifest.config.kubernetes:
            referenced.update(hp.allocation for hp in mapping.host_ports)
            referenced.update(np.allocation for np in mapping.node_ports)
        missing = sorted(referenced - ports_by_name)
        if missing:
            raise ConfigError(
                "CONFIG_MAPPING_INVALID",
                f"Config mapping references request name(s) not present in this allocation: {', '.join(missing)}.",
                status_code=409,
                details=[{"name": name} for name in missing],
            )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class PlannedFile:
    relative_path: str
    kind: str  # "dotenv" | "compose"
    action: str  # "will_create" | "will_update" | "no_change"
    before_hash: Optional[str]
    new_content: str
    changes: List[dict] = field(default_factory=list)


@dataclass
class ConfigPlan:
    mutation_id: str
    allocation_id: str
    project: str
    project_root: str
    created_at: str
    files: List[PlannedFile]


def _ports_by_name(allocation_data: Dict[str, Any]) -> Dict[str, int]:
    return {entry["name"]: entry["port"] for entry in allocation_data.get("allocations", [])}


def _plan_dotenv_file(project_root: Path, mapping, ports_by_name: Dict[str, int]) -> PlannedFile:
    try:
        target = resolve_within_root(project_root, mapping.file)
    except ConfigPathError as exc:
        raise ConfigError("CONFIG_PATH_OUTSIDE_PROJECT", str(exc), status_code=400)

    original_bytes = read_file_bytes(target)
    original_text = original_bytes.decode("utf-8") if original_bytes is not None else None
    resolved_values = {env_key: str(ports_by_name[alloc_name]) for env_key, alloc_name in mapping.values.items()}

    try:
        new_text, changes = compute_dotenv_update(original_text, resolved_values)
    except DotenvError as exc:
        raise ConfigError(exc.code, exc.message, status_code=409, details=exc.details)

    action = "will_create" if original_bytes is None else ("will_update" if changes else "no_change")
    return PlannedFile(
        relative_path=mapping.file,
        kind="dotenv",
        action=action,
        before_hash=None if original_bytes is None else sha256_bytes(original_bytes),
        new_content=new_text,
        changes=[
            {"key": c.key, "before": _redact_if_sensitive(c.key, c.before), "after": c.after, "action": c.action}
            for c in changes
        ],
    )


def _plan_compose_file(project_root: Path, mapping, ports_by_name: Dict[str, int]) -> PlannedFile:
    try:
        target = resolve_within_root(project_root, mapping.file)
    except ConfigPathError as exc:
        raise ConfigError("CONFIG_PATH_OUTSIDE_PROJECT", str(exc), status_code=400)

    original_bytes = read_file_bytes(target)
    if original_bytes is None:
        # Unlike dotenv, Compose files are never created from scratch by
        # Phase 8C -- inventing a base compose.yaml structure is out of
        # scope (see docs/phase8c_config_audit.md's decisions table).
        raise ConfigError(
            "CONFIG_FILE_NOT_FOUND", f"Compose file '{mapping.file}' does not exist.", status_code=404
        )

    data = load_compose(original_bytes.decode("utf-8"))
    changes: List[dict] = []
    for service_name, port_entries in mapping.services.items():
        for entry in port_entries:
            host_port = ports_by_name[entry.allocation]
            try:
                data, change = apply_port_mapping(data, service_name, entry.container, entry.protocol, host_port)
            except ComposeError as exc:
                raise ConfigError(exc.code, exc.message, status_code=409, details=exc.details)
            changes.append(
                {
                    "service": change.service,
                    "container_port": change.container_port,
                    "protocol": change.protocol,
                    "before": change.before_host,
                    "after": change.after_host,
                    "action": change.action,
                }
            )

    new_text = dump_compose(data)
    action = "will_update" if new_text != original_bytes.decode("utf-8") else "no_change"
    return PlannedFile(
        relative_path=mapping.file,
        kind="compose",
        action=action,
        before_hash=sha256_bytes(original_bytes),
        new_content=new_text,
        changes=changes,
    )


def _plan_kubernetes_file(project_root: Path, mapping, ports_by_name: Dict[str, int]) -> PlannedFile:
    try:
        target = resolve_within_root(project_root, mapping.file)
    except ConfigPathError as exc:
        raise ConfigError("CONFIG_PATH_OUTSIDE_PROJECT", str(exc), status_code=400)

    original_bytes = read_file_bytes(target)
    if original_bytes is None:
        # Same posture as Compose (§9 above): PortForge never invents a
        # base Kubernetes manifest from scratch.
        raise ConfigError(
            "CONFIG_FILE_NOT_FOUND", f"Kubernetes file '{mapping.file}' does not exist.", status_code=404
        )

    try:
        docs = load_kubernetes_documents(original_bytes.decode("utf-8"))
    except KubernetesError as exc:
        raise ConfigError(exc.code, exc.message, status_code=409, details=exc.details)

    changes: List[dict] = []
    for hp in mapping.host_ports:
        host_port = ports_by_name[hp.allocation]
        try:
            docs, change = apply_host_port(
                docs, hp.kind, hp.name, hp.namespace, hp.container, hp.container_port, hp.protocol, host_port
            )
        except KubernetesError as exc:
            raise ConfigError(exc.code, exc.message, status_code=409, details=exc.details)
        changes.append(
            {
                "kind": change.kind, "name": change.name, "field": change.field, "container": change.container,
                "match_port": change.match_port, "allocation": hp.allocation,
                "before": change.before, "after": change.after, "action": change.action,
            }
        )

    for np in mapping.node_ports:
        node_port = ports_by_name[np.allocation]
        # v1.1-C §5: PortForge has no authoritative way to discover a live
        # cluster's actual configured NodePort range (config plan/apply
        # never touches a live cluster -- see task §21). Validate against
        # the documented supported-local-cluster assumption instead of
        # silently writing a value that kind/Docker Desktop Kubernetes
        # would then reject.
        low, high = DEFAULT_NODEPORT_RANGE
        if not (low <= node_port <= high):
            raise ConfigError(
                "KUBERNETES_NODEPORT_OUT_OF_RANGE",
                f"Allocation '{np.allocation}' resolved to port {node_port}, which is outside the supported "
                f"local-cluster NodePort range {low}-{high} (kind / Docker Desktop Kubernetes default). Give "
                f"'ports.{np.allocation}' a 'preferred' value in that range.",
                status_code=409,
                details=[{"allocation": np.allocation, "port": node_port, "range": [low, high]}],
            )
        try:
            docs, change = apply_node_port(docs, np.name, np.namespace, np.service_port, np.protocol, node_port)
        except KubernetesError as exc:
            raise ConfigError(exc.code, exc.message, status_code=409, details=exc.details)
        changes.append(
            {
                "kind": change.kind, "name": change.name, "field": change.field, "container": change.container,
                "match_port": change.match_port, "allocation": np.allocation,
                "before": change.before, "after": change.after, "action": change.action,
            }
        )

    new_text = dump_kubernetes_documents(docs)
    original_text = original_bytes.decode("utf-8")
    action = "will_update" if new_text != original_text else "no_change"
    return PlannedFile(
        relative_path=mapping.file,
        kind="kubernetes",
        action=action,
        before_hash=sha256_bytes(original_bytes),
        new_content=new_text,
        changes=changes,
    )


def build_plan(manifest: ProjectManifest, allocation_data: Dict[str, Any], project_root: Path) -> ConfigPlan:
    if manifest.config is None or not (manifest.config.dotenv or manifest.config.compose or manifest.config.kubernetes):
        raise ConfigError(
            "CONFIG_NOT_DECLARED", "This manifest declares no 'config' mappings -- nothing to plan.", status_code=400
        )

    ports_by_name = _ports_by_name(allocation_data)
    files: List[PlannedFile] = []
    for mapping in manifest.config.dotenv:
        files.append(_plan_dotenv_file(project_root, mapping, ports_by_name))
    for mapping in manifest.config.compose:
        files.append(_plan_compose_file(project_root, mapping, ports_by_name))
    for mapping in manifest.config.kubernetes:
        files.append(_plan_kubernetes_file(project_root, mapping, ports_by_name))

    return ConfigPlan(
        mutation_id=str(uuid.uuid4()),
        allocation_id=allocation_data["allocation_id"],
        project=manifest.project,
        project_root=str(project_root),
        created_at=datetime.now(timezone.utc).isoformat(),
        files=files,
    )


def plan_to_json(plan: ConfigPlan) -> dict:
    return {
        "committed": False,
        "mutation_id": plan.mutation_id,
        "allocation_id": plan.allocation_id,
        "project": plan.project,
        "changes": [
            {
                "file": f.relative_path,
                "type": f.kind,
                "action": f.action,
                "changes": f.changes,
            }
            for f in plan.files
        ],
    }


# ---------------------------------------------------------------------------
# Persistence (mutation record) -- see docs/phase8c_config_audit.md
# "Backup/mutation storage"
# ---------------------------------------------------------------------------


def _mutations_root(project_root: Path) -> Path:
    return project_root / MUTATIONS_DIR_NAME / "mutations"


def _mutation_dir(project_root: Path, mutation_id: str) -> Path:
    return _mutations_root(project_root) / mutation_id


def _safe_backup_name(relative_path: str) -> str:
    return relative_path.replace("/", "__").replace("\\", "__")


def persist_plan(plan: ConfigPlan, project_root: Path) -> None:
    mutation_dir = _mutation_dir(project_root, plan.mutation_id)
    record = {
        "mutation_id": plan.mutation_id,
        "allocation_id": plan.allocation_id,
        "project": plan.project,
        "project_root": plan.project_root,
        "created_at": plan.created_at,
        "status": "PLANNED",
        "files": [
            {
                "relative_path": f.relative_path,
                "kind": f.kind,
                "action": f.action,
                "before_hash": f.before_hash,
                "new_content": f.new_content,
            }
            for f in plan.files
        ],
    }
    atomic_write(mutation_dir / "record.json", json.dumps(record, indent=2).encode("utf-8"))


def _load_record(project_root: Path, mutation_id: str) -> dict:
    path = _mutation_dir(project_root, mutation_id) / "record.json"
    raw = read_file_bytes(path)
    if raw is None:
        raise ConfigError("CONFIG_MUTATION_NOT_FOUND", f"No mutation record found for '{mutation_id}'.", status_code=404)
    return json.loads(raw.decode("utf-8"))


def _save_record(project_root: Path, record: dict) -> None:
    path = _mutation_dir(project_root, record["mutation_id"]) / "record.json"
    atomic_write(path, json.dumps(record, indent=2).encode("utf-8"))


def find_latest_planned_mutation(project_root: Path, allocation_id: str) -> Optional[str]:
    """Used by `config apply <manifest> --allocation <id>` (task §4's own
    CLI shape takes the same inputs as `plan`, not a raw mutation id) to
    locate the most recently persisted PLANNED mutation for this exact
    (project_root, allocation_id) pair. See
    docs/phase8c_safe_config.md "plan/apply relationship" for why apply
    requires a prior plan rather than silently re-planning inline.
    """
    root = _mutations_root(project_root)
    if not root.is_dir():
        return None

    candidates = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        record_path = entry / "record.json"
        raw = read_file_bytes(record_path)
        if raw is None:
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if record.get("allocation_id") == allocation_id and record.get("status") == "PLANNED":
            candidates.append(record)

    if not candidates:
        return None
    candidates.sort(key=lambda r: r["created_at"], reverse=True)
    return candidates[0]["mutation_id"]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_mutation(project_root: Path, mutation_id: str) -> dict:
    record = _load_record(project_root, mutation_id)

    if record["status"] == "APPLIED":
        return record  # idempotent -- see docs/phase8c_safe_config.md "Apply idempotency"

    # Precondition: EVERY file's live content must still match the hash
    # captured at plan time, checked for ALL files before writing ANY of
    # them (task §20/§21).
    for f in record["files"]:
        target = resolve_within_root(project_root, f["relative_path"])
        live_hash = sha256_of_path(target)
        if live_hash != f["before_hash"]:
            raise ConfigError(
                "CONFIG_CHANGED_SINCE_PLAN",
                f"'{f['relative_path']}' has changed since this plan was generated -- refusing to overwrite "
                "possibly-uncommitted edits. Run 'config plan' again.",
                status_code=409,
                details=[{"file": f["relative_path"]}],
            )

    mutation_dir = _mutation_dir(project_root, mutation_id)
    # (relative_path, resolved target, original bytes or None) for every
    # file, computed and backed up BEFORE any real target is written.
    prepared: List[tuple] = []
    for f in record["files"]:
        target = resolve_within_root(project_root, f["relative_path"])
        original_bytes = read_file_bytes(target)  # None if the file is being created
        if original_bytes is not None:
            backup_path = mutation_dir / "files" / _safe_backup_name(f["relative_path"])
            atomic_write(backup_path, original_bytes)
        prepared.append((f, target, original_bytes))

    written: List[tuple] = []
    try:
        for f, target, _original_bytes in prepared:
            atomic_write(target, f["new_content"].encode("utf-8"))
            f["after_hash"] = sha256_bytes(f["new_content"].encode("utf-8"))
            written.append((target, _original_bytes))
    except OSError as exc:
        # All-or-nothing: restore every target already written this call,
        # from the backup just taken above, before surfacing the failure
        # (task §21).
        for target, original_bytes in written:
            if original_bytes is not None:
                atomic_write(target, original_bytes)
            else:
                target.unlink(missing_ok=True)
        raise ConfigError("CONFIG_APPLY_FAILED", f"Failed to apply config mutation: {exc}", status_code=500)

    record["status"] = "APPLIED"
    record["applied_at"] = datetime.now(timezone.utc).isoformat()
    _save_record(project_root, record)
    return record


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status(project_root: Path, mutation_id: str) -> dict:
    record = _load_record(project_root, mutation_id)
    return {
        "mutation_id": record["mutation_id"],
        "allocation_id": record["allocation_id"],
        "project": record["project"],
        "status": record["status"],
        "created_at": record["created_at"],
        "applied_at": record.get("applied_at"),
        "rolled_back_at": record.get("rolled_back_at"),
        "files": [
            {"file": f["relative_path"], "type": f["kind"], "action": f["action"]} for f in record["files"]
        ],
    }


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback_mutation(project_root: Path, mutation_id: str) -> dict:
    record = _load_record(project_root, mutation_id)

    if record["status"] == "ROLLED_BACK":
        return record  # idempotent, same spirit as apply
    if record["status"] != "APPLIED":
        raise ConfigError(
            "CONFIG_ROLLBACK_FAILED",
            f"Mutation '{mutation_id}' has status '{record['status']}', not APPLIED -- nothing to roll back.",
            status_code=409,
        )

    mutation_dir = _mutation_dir(project_root, mutation_id)

    # Precondition: every file must still match what apply left it as,
    # checked for ALL files before restoring ANY of them (task §24).
    for f in record["files"]:
        target = resolve_within_root(project_root, f["relative_path"])
        live_hash = sha256_of_path(target)
        if live_hash != f.get("after_hash"):
            raise ConfigError(
                "CONFIG_CHANGED_SINCE_APPLY",
                f"'{f['relative_path']}' has changed since PortForge applied this mutation -- refusing to "
                "overwrite possibly-uncommitted edits.",
                status_code=409,
                details=[{"file": f["relative_path"]}],
            )

    for f in record["files"]:
        target = resolve_within_root(project_root, f["relative_path"])
        backup_path = mutation_dir / "files" / _safe_backup_name(f["relative_path"])
        original = read_file_bytes(backup_path)
        if original is None:
            # The file didn't exist before apply (it was newly created) --
            # rolling back means removing it.
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, original)

    record["status"] = "ROLLED_BACK"
    record["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _save_record(project_root, record)
    return record
