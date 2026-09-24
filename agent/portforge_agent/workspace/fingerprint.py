from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from ..config_files import resolve_within_root, sha256_of_path


def _host_intents(intent_summary: dict) -> List[dict]:
    services = intent_summary.get("services") or []
    intents: List[dict] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        for requirement in service.get("port_requirements") or []:
            if not isinstance(requirement, dict):
                continue
            if requirement.get("role") != "host":
                continue
            if requirement.get("classification") not in {"EXPLICIT", "INFERRED"}:
                continue
            intents.append(
                {
                    "service": requirement.get("service") or service.get("name"),
                    "port": requirement.get("port"),
                    "protocol": requirement.get("protocol", "tcp"),
                }
            )
    return sorted(intents, key=lambda item: (str(item.get("service")), item.get("port") or 0, item.get("protocol")))


def compute_workspace_fingerprint(project_root: Path, relative_paths: List[str], intent_summary: dict) -> str:
    root = project_root.resolve(strict=True)
    file_hashes: Dict[str, str] = {}
    for relative in sorted(set(relative_paths)):
        try:
            resolved = resolve_within_root(root, relative)
            file_hashes[relative] = sha256_of_path(resolved) or "MISSING"
        except Exception:
            file_hashes[relative] = "MISSING"

    payload = {
        "files": file_hashes,
        "host_port_intents": _host_intents(intent_summary),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
