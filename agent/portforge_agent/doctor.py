"""v1.1-A: `portforge doctor` -- a READ-ONLY diagnostic aggregator.

Every check here calls an existing capability (Central's own `/api/health`,
`service_ops.status()`, `collectors.docker.is_docker_available()`,
`manifest.load_and_validate_manifest()`, `os.access` permission probes) --
this module never re-implements or duplicates diagnostic logic that
already exists elsewhere, and never mutates anything: no reservation, no
allocation, no config file write, no service install/start/stop, no git
operation, no DB write, no restart of anything. See
docs/v1.1/doctor-design.md for the design this implements.

A check that legitimately doesn't apply (no manifest in the current
directory, Central sync never configured) reports SKIP or WARN, never
FAIL -- an optional capability being absent is not the same as PortForge
being broken.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from . import service_ops
from .central_config import load_central_config
from .collectors.docker import is_docker_available
from .credentials import load_credential
from .discovery import discover_all_ports
from .manifest import ManifestError, discover_manifest_path, load_and_validate_manifest
from .version import PROTOCOL_VERSION, evaluate_central_protocol_compatibility, get_portforge_version

CONTRACT_VERSION = 1

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_SKIP = "skip"

_SEVERITY = {STATUS_OK: 0, STATUS_SKIP: 0, STATUS_WARN: 1, STATUS_ERROR: 2}
_OVERALL_BY_SEVERITY = {0: "ok", 1: "degraded", 2: "error"}


@dataclass
class DoctorCheck:
    id: str
    status: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "message": self.message, "details": self.details}


@dataclass
class DoctorReport:
    overall: str
    portforge_version: str
    protocol_version: int
    checks: List[DoctorCheck]

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "status": self.overall,
            "portforge_version": self.portforge_version,
            "protocol_version": self.protocol_version,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Individual checks -- each one is independent and never raises; any
# unexpected exception from an underlying capability is caught and turned
# into an "error"/"warn" check result, never propagated (a broken Docker
# install must not prevent every OTHER check from reporting).
# ---------------------------------------------------------------------------


def _check_cli_version() -> DoctorCheck:
    version = get_portforge_version()
    return DoctorCheck("cli_version", STATUS_OK, f"PortForge agent {version}", {"portforge_version": version})


def _check_protocol_version() -> DoctorCheck:
    return DoctorCheck(
        "protocol_version", STATUS_OK, f"Agent protocol version {PROTOCOL_VERSION}",
        {"protocol_version": PROTOCOL_VERSION},
    )


def _check_central_connectivity(client) -> "tuple[DoctorCheck, Optional[dict]]":
    if client is None:
        return DoctorCheck("central_connectivity", STATUS_SKIP, "No Central URL configured."), None
    try:
        result = client.health()
    except Exception as exc:  # pragma: no cover - CentralClient itself already catches network errors
        return DoctorCheck("central_connectivity", STATUS_ERROR, f"Central health check failed: {exc}"), None
    if not result.success:
        return DoctorCheck("central_connectivity", STATUS_ERROR, f"Central unreachable: {result.error}"), None
    return DoctorCheck("central_connectivity", STATUS_OK, "Central reachable.", dict(result.data or {})), result.data


def _check_central_health(central_data: Optional[dict]) -> DoctorCheck:
    if central_data is None:
        return DoctorCheck("central_health", STATUS_SKIP, "Central was not reachable.")
    db_status = central_data.get("database")
    if db_status == "connected":
        return DoctorCheck("central_health", STATUS_OK, "Central database connected.", {"database": db_status})
    return DoctorCheck("central_health", STATUS_WARN, f"Central database status: {db_status}", {"database": db_status})


def _check_protocol_compatibility(central_data: Optional[dict]) -> DoctorCheck:
    if central_data is None:
        return DoctorCheck("protocol_compatibility", STATUS_SKIP, "Central was not reachable.")
    central_protocol_version = central_data.get("protocol_version")
    verdict = evaluate_central_protocol_compatibility(central_protocol_version)
    status = STATUS_OK if verdict == "compatible" else STATUS_WARN
    return DoctorCheck(
        "protocol_compatibility",
        status,
        f"Protocol compatibility: {verdict} (agent={PROTOCOL_VERSION}, central={central_protocol_version})",
        {"agent_protocol_version": PROTOCOL_VERSION, "central_protocol_version": central_protocol_version, "verdict": verdict},
    )


def _check_agent_identity() -> DoctorCheck:
    config = load_central_config()
    token = load_credential()
    if not config.enabled and token is None:
        return DoctorCheck("agent_identity", STATUS_SKIP, "Central sync is not configured (optional).")
    if token is None:
        return DoctorCheck(
            "agent_identity", STATUS_WARN, "No agent credential found; run 'portforge agent enroll' first."
        )
    return DoctorCheck("agent_identity", STATUS_OK, "Agent credential present.")


def _check_agent_service() -> DoctorCheck:
    try:
        result = service_ops.status()
    except service_ops.UnsupportedPlatformError as exc:
        return DoctorCheck("agent_service", STATUS_SKIP, str(exc))
    except Exception as exc:
        return DoctorCheck("agent_service", STATUS_ERROR, f"Could not determine agent service status: {exc}")
    status = STATUS_OK if result.success else STATUS_WARN
    return DoctorCheck("agent_service", status, result.message)


def _check_docker() -> DoctorCheck:
    try:
        available = is_docker_available()
    except Exception as exc:
        return DoctorCheck("docker", STATUS_WARN, f"Could not determine Docker availability: {exc}")
    if available:
        return DoctorCheck("docker", STATUS_OK, "Docker available.")
    return DoctorCheck("docker", STATUS_WARN, "Docker not available -- native-process discovery only.")


def _check_collector() -> DoctorCheck:
    try:
        ports = discover_all_ports()
    except Exception as exc:
        return DoctorCheck("collector", STATUS_ERROR, f"Port discovery failed: {exc}")
    return DoctorCheck("collector", STATUS_OK, f"Discovery ran successfully ({len(ports)} port(s) observed).")


def _check_manifest(start_dir: Optional[str] = None) -> "tuple[DoctorCheck, Optional[Path]]":
    path = discover_manifest_path(start_dir)
    if path is None:
        return DoctorCheck("manifest", STATUS_SKIP, "No portforge.yml/.yaml in the current directory."), None
    try:
        manifest = load_and_validate_manifest(path)
    except ManifestError as exc:
        return (
            DoctorCheck("manifest", STATUS_ERROR, f"{exc.code}: {exc.message}", {"code": exc.code, "file": str(path)}),
            path,
        )
    return (
        DoctorCheck(
            "manifest", STATUS_OK, f"Manifest valid ({len(manifest.requests)} request(s)).", {"file": str(path)}
        ),
        path,
    )


def _check_filesystem(manifest_path: Optional[Path]) -> DoctorCheck:
    """Read-only: `os.access` is a pure permission query, nothing is
    created, written, or deleted.
    """
    issues: List[str] = []

    data_dir = paths.data_dir()
    probe = data_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        issues.append(f"{data_dir} is not writable")

    if manifest_path is not None:
        project_root = manifest_path.parent
        if not os.access(project_root, os.W_OK):
            issues.append(f"{project_root} is not writable")

    if issues:
        return DoctorCheck("filesystem", STATUS_WARN, "; ".join(issues), {"issues": issues})
    return DoctorCheck("filesystem", STATUS_OK, "Required directories are writable.")


def run_doctor(client=None, start_dir: Optional[str] = None) -> DoctorReport:
    """`client`: an already-constructed `CentralClient`, or None if no
    Central URL could be resolved (matching `_allocation_client()`'s own
    "no URL configured" case, which is NOT a doctor failure -- Central
    sync has always been optional).
    """
    checks: List[DoctorCheck] = [_check_cli_version(), _check_protocol_version()]

    central_check, central_data = _check_central_connectivity(client)
    checks.append(central_check)
    checks.append(_check_central_health(central_data))
    checks.append(_check_protocol_compatibility(central_data))

    checks.append(_check_agent_identity())
    checks.append(_check_agent_service())
    checks.append(_check_docker())
    checks.append(_check_collector())

    manifest_check, manifest_path = _check_manifest(start_dir)
    checks.append(manifest_check)
    checks.append(_check_filesystem(manifest_path))

    worst_severity = max((_SEVERITY[c.status] for c in checks), default=0)
    overall = _OVERALL_BY_SEVERITY[worst_severity]

    return DoctorReport(
        overall=overall, portforge_version=get_portforge_version(), protocol_version=PROTOCOL_VERSION, checks=checks
    )
