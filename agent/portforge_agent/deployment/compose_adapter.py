"""Typed Docker Compose adapter — fixed argv only, no caller-supplied flags."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ..subprocess_util import run_subprocess

# Fixed tail segments — never extended from Central/MCP payloads.
_COMPOSE_SUBCOMMAND = "compose"
_VALIDATE_TAIL = ["config"]
_APPLY_TAIL = ["up", "-d", "--remove-orphans"]
_STOP_TAIL = ["stop"]
_DOWN_TAIL = ["down", "--remove-orphans"]
_PS_TAIL = ["ps", "-a", "--format", "json"]

_PROJECT_NAME_MAX = 63


class ComposeAdapterError(RuntimeError):
    """Base error for compose adapter failures."""


class DockerUnavailableError(ComposeAdapterError):
    """Docker CLI or daemon is not usable."""


class ComposeValidationError(ComposeAdapterError):
    """Compose config validation failed."""


class ComposeApplyError(ComposeAdapterError):
    """Compose up/stop/down failed."""


def compose_project_name(project: str, environment: str, host_id: str) -> str:
    """Stable sanitized Compose project name from project + environment + host."""
    def _part(value: str, limit: int = 20) -> str:
        cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
        return (cleaned or "unknown")[:limit]

    host_part = re.sub(r"[^a-z0-9]+", "", host_id.lower())[:12] or "host"
    name = f"pf-{_part(project)}-{_part(environment)}-{host_part}"
    return name[:_PROJECT_NAME_MAX]


def _resolve_docker_executable() -> str:
    from .. import platform as pf

    found = shutil.which("docker")
    if found:
        return found

    if pf.detect_os() == pf.OperatingSystem.MACOS:
        import os

        for directory in pf.MACOS_EXTRA_BIN_DIRS:
            candidate = f"{directory}/docker"
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    raise DockerUnavailableError("Required command not found: docker")


def _resolve_compose_files(workdir: Path, compose_files: Sequence[str]) -> List[str]:
    if not compose_files:
        raise ComposeValidationError("compose_files must not be empty")
    resolved: List[str] = []
    root = workdir.resolve(strict=False)
    for relative in compose_files:
        if not isinstance(relative, str) or not relative.strip():
            raise ComposeValidationError("compose file path must be a non-empty string")
        normalized = relative.replace("\\", "/").lstrip("/")
        if normalized.startswith("../") or "/../" in f"/{normalized}/":
            raise ComposeValidationError(f"compose file escapes workdir: {relative}")
        candidate = (workdir / normalized).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ComposeValidationError(
                f"compose file {relative!r} resolves outside workdir {root}"
            ) from exc
        if not candidate.is_file():
            raise ComposeValidationError(f"compose file not found: {relative}")
        resolved.append(str(candidate))
    return resolved


def _build_argv(
    docker_exe: str,
    compose_files: Sequence[str],
    project_name: str,
    tail: Sequence[str],
) -> List[str]:
    argv = [docker_exe, _COMPOSE_SUBCOMMAND]
    for compose_file in compose_files:
        argv.extend(["-f", compose_file])
    argv.extend(["-p", project_name])
    argv.extend(tail)
    return argv


def _run_compose(
    argv: List[str],
    *,
    workdir: Path,
    timeout: float,
    error_cls: type[ComposeAdapterError],
) -> subprocess.CompletedProcess:
    try:
        return run_subprocess(
            argv,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DockerUnavailableError("Required command not found: docker") from exc
    except subprocess.TimeoutExpired as exc:
        raise error_cls(f"docker compose timed out after {timeout}s") from exc


def validate(compose_files: Sequence[str], project_name: str, workdir: Path) -> None:
    """Run fixed `docker compose -f ... -p name config` only."""
    docker_exe = _resolve_docker_executable()
    files = _resolve_compose_files(workdir, compose_files)
    argv = _build_argv(docker_exe, files, project_name, _VALIDATE_TAIL)
    result = _run_compose(argv, workdir=workdir, timeout=60.0, error_cls=ComposeValidationError)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ComposeValidationError(detail or "compose config validation failed")


def apply(compose_files: Sequence[str], project_name: str, workdir: Path) -> None:
    """Run fixed `docker compose ... up -d --remove-orphans`."""
    docker_exe = _resolve_docker_executable()
    files = _resolve_compose_files(workdir, compose_files)
    argv = _build_argv(docker_exe, files, project_name, _APPLY_TAIL)
    result = _run_compose(argv, workdir=workdir, timeout=300.0, error_cls=ComposeApplyError)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ComposeApplyError(detail or "compose up failed")


def stop(compose_files: Sequence[str], project_name: str, workdir: Path) -> None:
    """Run fixed `docker compose ... stop` for rollback."""
    docker_exe = _resolve_docker_executable()
    files = _resolve_compose_files(workdir, compose_files)
    argv = _build_argv(docker_exe, files, project_name, _STOP_TAIL)
    result = _run_compose(argv, workdir=workdir, timeout=120.0, error_cls=ComposeApplyError)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ComposeApplyError(detail or "compose stop failed")


def down(compose_files: Sequence[str], project_name: str, workdir: Path) -> None:
    """Run fixed `docker compose ... down --remove-orphans` for rollback."""
    docker_exe = _resolve_docker_executable()
    files = _resolve_compose_files(workdir, compose_files)
    argv = _build_argv(docker_exe, files, project_name, _DOWN_TAIL)
    result = _run_compose(argv, workdir=workdir, timeout=180.0, error_cls=ComposeApplyError)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ComposeApplyError(detail or "compose down failed")


def inspect_status(compose_files: Sequence[str], project_name: str, workdir: Path) -> Dict[str, Any]:
    """Run `docker compose ps -a --format json` when available; normalize output."""
    docker_exe = _resolve_docker_executable()
    files = _resolve_compose_files(workdir, compose_files)
    argv = _build_argv(docker_exe, files, project_name, _PS_TAIL)
    result = _run_compose(argv, workdir=workdir, timeout=60.0, error_cls=ComposeAdapterError)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return {"available": False, "services": [], "detail": detail or "compose ps failed"}

    raw = (result.stdout or "").strip()
    services: List[Dict[str, Any]] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                services = [item for item in parsed if isinstance(item, dict)]
            elif isinstance(parsed, dict):
                services = [parsed]
        except json.JSONDecodeError:
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    services.append(item)

    normalized: List[Dict[str, Any]] = []
    for item in services:
        normalized.append(
            {
                "name": item.get("Name") or item.get("name") or item.get("Service") or item.get("service"),
                "state": item.get("State") or item.get("state") or item.get("Status") or item.get("status"),
                "health": item.get("Health") or item.get("health"),
            }
        )

    return {"available": True, "services": normalized, "detail": None}


# Expose argv builders for tests — not part of the public runtime API.
def build_validate_argv(docker_exe: str, compose_files: Sequence[str], project_name: str) -> List[str]:
    return _build_argv(docker_exe, list(compose_files), project_name, _VALIDATE_TAIL)


def build_apply_argv(docker_exe: str, compose_files: Sequence[str], project_name: str) -> List[str]:
    return _build_argv(docker_exe, list(compose_files), project_name, _APPLY_TAIL)


def build_stop_argv(docker_exe: str, compose_files: Sequence[str], project_name: str) -> List[str]:
    return _build_argv(docker_exe, list(compose_files), project_name, _STOP_TAIL)


def build_down_argv(docker_exe: str, compose_files: Sequence[str], project_name: str) -> List[str]:
    return _build_argv(docker_exe, list(compose_files), project_name, _DOWN_TAIL)


def build_ps_argv(docker_exe: str, compose_files: Sequence[str], project_name: str) -> List[str]:
    return _build_argv(docker_exe, list(compose_files), project_name, _PS_TAIL)
