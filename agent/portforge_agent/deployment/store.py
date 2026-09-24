"""Local deployment revision store under PORTFORGE_DATA_DIR/deployments/."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Optional

from ..paths import data_dir

_CURRENT_MARKER = "current"
_PREVIOUS_MARKER = "previous-known-good"
_REVISIONS_DIR = "revisions"


class DeploymentStoreError(ValueError):
    """Raised when deployment store paths or transitions are invalid."""


def deployments_root() -> Path:
    return data_dir() / "deployments"


def _sanitize_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return cleaned or "unknown"


def deployment_root(project: str, environment: str, deployment_id: str) -> Path:
    return (
        deployments_root()
        / _sanitize_component(project)
        / _sanitize_component(environment)
        / _sanitize_component(deployment_id)
    )


def resolve_within_root(root: Path, relative: str) -> Path:
    """Resolve a relative path and ensure it stays inside root."""
    base = root.resolve(strict=False)
    normalized = relative.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise DeploymentStoreError(f"Path escapes deployment root: {relative!r}")
    candidate = (root / normalized).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise DeploymentStoreError(
            f"Path {relative!r} resolves outside deployment root {base}"
        ) from exc
    return candidate


def revision_dir(project: str, environment: str, deployment_id: str, revision_id: str) -> Path:
    root = deployment_root(project, environment, deployment_id)
    return resolve_within_root(root, f"{_REVISIONS_DIR}/{_sanitize_component(revision_id)}")


def stage_revision_dir(project: str, environment: str, deployment_id: str, revision_id: str) -> Path:
    path = revision_dir(project, environment, deployment_id, revision_id)
    path.mkdir(parents=True, exist_ok=False)
    return path


def _read_marker(root: Path, name: str) -> Optional[str]:
    marker = root / name
    if not marker.is_file():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    return value or None


def _write_marker(root: Path, name: str, revision_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / name
    marker.write_text(revision_id, encoding="utf-8")


def current_revision_id(project: str, environment: str, deployment_id: str) -> Optional[str]:
    return _read_marker(deployment_root(project, environment, deployment_id), _CURRENT_MARKER)


def previous_known_good_revision_id(project: str, environment: str, deployment_id: str) -> Optional[str]:
    return _read_marker(
        deployment_root(project, environment, deployment_id),
        _PREVIOUS_MARKER,
    )


def activate_revision(
    project: str,
    environment: str,
    deployment_id: str,
    revision_id: str,
    *,
    mark_known_good: bool = True,
) -> Path:
    """Point current at revision_id; preserve prior current as previous-known-good."""
    root = deployment_root(project, environment, deployment_id)
    rev_path = revision_dir(project, environment, deployment_id, revision_id)
    if not rev_path.is_dir():
        raise DeploymentStoreError(f"Revision directory does not exist: {rev_path}")

    prior = current_revision_id(project, environment, deployment_id)
    if prior and prior != revision_id and mark_known_good:
        _write_marker(root, _PREVIOUS_MARKER, prior)

    _write_marker(root, _CURRENT_MARKER, revision_id)
    metadata = {
        "project": project,
        "environment": environment,
        "deployment_id": deployment_id,
        "revision_id": revision_id,
        "previous_revision_id": prior,
    }
    (root / "metadata.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    return rev_path


def remove_revision(project: str, environment: str, deployment_id: str, revision_id: str) -> None:
    path = revision_dir(project, environment, deployment_id, revision_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
