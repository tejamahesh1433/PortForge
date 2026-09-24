from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config_files import ConfigPathError, atomic_write, read_file_bytes, resolve_within_root, sha256_of_path
from ..manifest import ProjectManifest
from ..workflow import _hash_manifest_for_workflow
from .errors import McpToolError

PLANS_DIR = ".portforge/mcp/plans"


def _config_target_files(manifest: ProjectManifest) -> list[str]:
    if manifest.config is None:
        return []
    files: list[str] = []
    for mapping in manifest.config.dotenv:
        files.append(mapping.file)
    for mapping in manifest.config.compose:
        files.append(mapping.file)
    for mapping in manifest.config.kubernetes:
        files.append(mapping.file)
    return sorted(set(files))


def compute_plan_fingerprint(manifest: ProjectManifest, project_root: Path) -> str:
    manifest_hash = _hash_manifest_for_workflow(manifest)
    file_hashes: dict[str, str] = {}
    for relative_file in _config_target_files(manifest):
        try:
            resolved = resolve_within_root(project_root, relative_file)
            file_hashes[relative_file] = sha256_of_path(resolved) or "MISSING"
        except ConfigPathError:
            file_hashes[relative_file] = "MISSING"

    canonical = {"manifest_hash": manifest_hash, "files": file_hashes}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plans_dir(project_root: Path) -> Path:
    return project_root / PLANS_DIR


def save_plan(project_root: Path, plan_payload: dict[str, Any]) -> str:
    plan_id = uuid.uuid4().hex
    plan_payload = {
        **plan_payload,
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _plans_dir(project_root) / f"{plan_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(plan_payload, indent=2).encode("utf-8"))
    return plan_id


def load_plan(project_root: Path, plan_id: str) -> dict:
    path = _plans_dir(project_root) / f"{plan_id}.json"
    raw = read_file_bytes(path)
    if raw is None:
        raise McpToolError("PLAN_NOT_FOUND", f"No MCP plan record found for plan_id '{plan_id}'.")
    return json.loads(raw.decode("utf-8"))


def verify_plan_fresh(project_root: Path, plan_id: str, manifest: ProjectManifest) -> None:
    record = load_plan(project_root, plan_id)
    current = compute_plan_fingerprint(manifest, project_root)
    stored = record.get("plan_hash")
    if stored != current:
        raise McpToolError(
            "CONFIG_CHANGED_SINCE_PLAN",
            "Project manifest or config target files changed since the plan was created.",
            details=[{"plan_id": plan_id, "stored_hash": stored, "current_hash": current}],
            recovery={"action": "Call portforge_project_plan again before provisioning."},
        )
