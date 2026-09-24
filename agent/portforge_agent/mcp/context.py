from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..central_client import CentralClient
from ..manifest import ManifestError, discover_manifest_path, load_and_validate_manifest
from ..project_adapter import NormalizedHostRef, resolve_host_ref
from .errors import McpToolError


def resolve_central_url(url: Optional[str]) -> str:
    if url:
        return url

    env_url = os.environ.get("PORTFORGE_CENTRAL_URL")
    if env_url:
        return env_url

    from ..central_config import load_central_config

    config = load_central_config()
    if config.url:
        return config.url

    raise McpToolError(
        "CENTRAL_UNAVAILABLE",
        "No Central server URL configured. Pass central_url, set PORTFORGE_CENTRAL_URL, "
        "or run `portforge central enroll --url ...` first.",
    )


def make_client(url: Optional[str]) -> CentralClient:
    return CentralClient(resolve_central_url(url))


def resolve_manifest_path(
    project_root: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> Path:
    if manifest_path:
        return Path(manifest_path)

    start_dir = str(project_root) if project_root else None
    path = discover_manifest_path(start_dir=start_dir, walk_up=project_root is None)
    if path is not None:
        return path

    raise McpToolError(
        "MANIFEST_NOT_FOUND",
        "No portforge.yml manifest found. Pass manifest_path or run from a project directory.",
    )


def resolve_project_root(
    project_root: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> Path:
    if project_root:
        return Path(project_root)

    if manifest_path:
        return Path(manifest_path).parent

    path = discover_manifest_path(walk_up=True)
    return path.parent if path is not None else Path.cwd()


def load_manifest_and_host(
    url: Optional[str],
    project_root: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> tuple[CentralClient, object, NormalizedHostRef, Path]:
    client = make_client(url)

    try:
        path = resolve_manifest_path(project_root, manifest_path)
        manifest = load_and_validate_manifest(path)
    except ManifestError as exc:
        raise McpToolError(
            "INVALID_PROJECT_MANIFEST"
            if exc.code in ("MANIFEST_INVALID", "MANIFEST_PARSE_ERROR", "MANIFEST_PARSE")
            else exc.code,
            exc.message,
            exc.details,
        ) from exc

    host, host_error, host_code = resolve_host_ref(client, manifest.host)
    if host_error:
        raise McpToolError(host_code or "HOST_NOT_FOUND", host_error)

    root = resolve_project_root(project_root, str(path))
    return client, manifest, host, root
