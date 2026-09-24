"""Central deployment orchestration service (Phase 18).

Creates and transitions structured deployment attempts only — no generic
remote command execution.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.deployment_revision import DeploymentRevision
from ..models.host_deployment import HostDeployment
from ..repositories.deployment_repository import DeploymentRepository
from ..repositories.host_repository import HostRepository
from .deployment_json import (
    DeploymentJsonError,
    validate_health_json,
    validate_ingress_json,
    validate_ports_json,
)
from .deployment_states import NON_TERMINAL_STATES, TERMINAL_STATES

CLAIM_LEASE_SECONDS = 300

_AGENT_STATES_ORDER = [
    "APPROVED",
    "PREPARING",
    "TRANSFERRING",
    "STARTING",
    "VERIFYING",
    "SUCCEEDED",
]
_AGENT_SETTABLE = set(_AGENT_STATES_ORDER[1:]) | {"FAILED", "ROLLING_BACK", "ROLLED_BACK"}


class DeploymentError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class DeploymentNotFoundError(DeploymentError):
    def __init__(self, message: str = "Deployment not found."):
        super().__init__(message, status_code=404)


class DeploymentOwnershipError(DeploymentError):
    def __init__(self, message: str = "Deployment does not belong to this host."):
        super().__init__(message, status_code=403)


class DeploymentClaimMismatchError(DeploymentError):
    def __init__(self, message: str = "Invalid or expired deployment claim token."):
        super().__init__(message, status_code=409, code="DEPLOYMENT_CLAIM_MISMATCH")


def validate_package_uri(package_uri: str) -> None:
    parsed = urlparse(package_uri)
    if parsed.scheme != "https":
        raise DeploymentError(
            "package_uri must use https://",
            status_code=422,
        )
    if not parsed.netloc:
        raise DeploymentError("package_uri must include a host.", status_code=422)

    settings = get_settings()
    allowlist = settings.deployment_artifact_hosts_list
    if allowlist:
        host = parsed.hostname or parsed.netloc.split(":")[0]
        if host not in allowlist:
            raise DeploymentError(
                f"package_uri host {host!r} is not in the approved artifact host allowlist.",
                status_code=422,
            )


def _compute_plan_hash(plan_body: dict[str, Any]) -> str:
    canonical = json.dumps(plan_body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan(
    *,
    host_id: uuid.UUID,
    project: str,
    environment: str,
    target_alias: str | None = None,
    workspace_fingerprint: str | None = None,
    services: list[dict[str, Any]] | None = None,
    ingress_bindings: list[dict[str, Any]] | None = None,
    rollback_revision_id: str | None = None,
) -> dict[str, Any]:
    """Read-only plan assembly — never inserts into the database."""
    plan_body: dict[str, Any] = {
        "host_id": str(host_id),
        "project": project,
        "environment": environment,
        "target_alias": target_alias,
        "workspace_fingerprint": workspace_fingerprint,
        "services": services or [],
        "ingress_bindings": ingress_bindings or [],
        "rollback_revision_id": rollback_revision_id,
    }
    plan_hash = _compute_plan_hash(plan_body)
    return {**plan_body, "plan_hash": plan_hash}


def create_deployment(
    db: Session,
    *,
    host_id: uuid.UUID,
    project: str,
    environment: str,
    request_id: str,
    plan_hash: str,
    package_uri: str,
    package_sha256: str,
    package_manifest_sha256: str,
    target_alias: str | None = None,
    workspace_fingerprint: str | None = None,
    ports_json: dict | None = None,
    ingress_json: dict | None = None,
    health_json: dict | None = None,
    rollback_revision_id: uuid.UUID | None = None,
    created_by: str = "admin",
) -> HostDeployment:
    host_repo = HostRepository(db)
    deployment_repo = DeploymentRepository(db)

    host = host_repo.get(host_id)
    if host is None:
        raise DeploymentNotFoundError("Host not found.")

    if (host.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
        raise DeploymentError(
            "Cannot create deployment for a decommissioned host.",
            status_code=409,
        )

    existing = deployment_repo.get_by_request_id_for_host(host_id, request_id)
    if existing is not None:
        return existing

    validate_package_uri(package_uri)
    try:
        ports_json = validate_ports_json(ports_json)
        ingress_json = validate_ingress_json(ingress_json)
        health_json = validate_health_json(health_json)
    except DeploymentJsonError as exc:
        raise DeploymentError(str(exc), status_code=422) from exc

    if rollback_revision_id is not None:
        revision = deployment_repo.get_revision(rollback_revision_id)
        if revision is None:
            raise DeploymentError("rollback_revision_id not found.", status_code=422)
        if revision.host_id != host_id:
            raise DeploymentOwnershipError()
        if not revision.is_known_good:
            raise DeploymentError(
                "rollback_revision_id does not reference a known-good revision.",
                status_code=422,
            )

    active = deployment_repo.get_non_terminal_for_scope(host_id, project, environment)
    if active is not None:
        raise DeploymentError(
            f"An active deployment already exists in state {active.state!r} "
            f"for {project!r}/{environment!r}.",
            status_code=409,
            code="DEPLOYMENT_ACTIVE_CONFLICT",
        )

    now = datetime.now(timezone.utc)
    deployment = HostDeployment(
        id=uuid.uuid4(),
        host_id=host_id,
        project=project,
        environment=environment,
        target_alias=target_alias,
        request_id=request_id,
        state="APPROVED",
        plan_hash=plan_hash,
        workspace_fingerprint=workspace_fingerprint,
        package_uri=package_uri,
        package_sha256=package_sha256,
        package_manifest_sha256=package_manifest_sha256,
        rollback_revision_id=rollback_revision_id,
        ports_json=ports_json,
        ingress_json=ingress_json,
        health_json=health_json,
        approved_at=now,
        created_by=created_by,
    )
    try:
        with db.begin_nested():
            db.add(deployment)
            db.flush()
    except IntegrityError as exc:
        raced = deployment_repo.get_by_request_id_for_host(host_id, request_id)
        if raced is not None:
            return raced
        raise DeploymentError(
            "An active deployment already exists for this project/environment/host.",
            status_code=409,
            code="DEPLOYMENT_ACTIVE_CONFLICT",
        ) from exc
    return deployment


def get_deployment(db: Session, deployment_id: uuid.UUID) -> HostDeployment:
    deployment = DeploymentRepository(db).get(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError()
    return deployment


def claim_deployment(
    db: Session,
    *,
    host_id: uuid.UUID,
    deployment_id: uuid.UUID,
) -> HostDeployment:
    deployment_repo = DeploymentRepository(db)
    deployment = deployment_repo.get(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError()

    if deployment.host_id != host_id:
        raise DeploymentOwnershipError()

    if deployment.state in TERMINAL_STATES:
        raise DeploymentError(
            f"Deployment is already in terminal state {deployment.state!r}.",
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    renew = (
        deployment.claim_token is None
        or deployment.claim_expires_at is None
        or deployment.claim_expires_at <= now
    )
    if renew:
        deployment.claim_token = secrets.token_urlsafe(32)
        deployment.claim_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        if deployment.claimed_at is None:
            deployment.claimed_at = now
        db.flush()
    return deployment


def _verify_claim(deployment: HostDeployment, claim_token: str) -> None:
    now = datetime.now(timezone.utc)
    if (
        deployment.claim_token is None
        or deployment.claim_expires_at is None
        or deployment.claim_expires_at <= now
        or not secrets.compare_digest(deployment.claim_token, claim_token)
    ):
        raise DeploymentClaimMismatchError()


def update_status(
    db: Session,
    *,
    host_id: uuid.UUID,
    deployment_id: uuid.UUID,
    claim_token: str,
    state: str,
    failure_code: str | None = None,
    failure_reason: str | None = None,
    revision_id: str | None = None,
) -> HostDeployment:
    deployment_repo = DeploymentRepository(db)
    deployment = deployment_repo.get(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError()

    if deployment.host_id != host_id:
        raise DeploymentOwnershipError()

    if deployment.state in TERMINAL_STATES:
        raise DeploymentError(
            f"Deployment is already in terminal state {deployment.state!r}.",
            status_code=409,
        )

    _verify_claim(deployment, claim_token)

    if state not in _AGENT_SETTABLE:
        raise DeploymentError(
            f"State {state!r} is not agent-settable. Valid states: {sorted(_AGENT_SETTABLE)}",
            status_code=422,
        )

    if state not in {"FAILED", "ROLLED_BACK"}:
        current_order = (
            _AGENT_STATES_ORDER.index(deployment.state)
            if deployment.state in _AGENT_STATES_ORDER
            else -1
        )
        new_order = (
            _AGENT_STATES_ORDER.index(state) if state in _AGENT_STATES_ORDER else -1
        )
        if new_order <= current_order:
            raise DeploymentError(
                f"Cannot transition from {deployment.state!r} to {state!r}: "
                "transitions must move forward.",
                status_code=422,
            )

    now = datetime.now(timezone.utc)
    deployment.state = state
    deployment.last_agent_update_at = now

    if state == "FAILED":
        deployment.failure_code = failure_code
        deployment.failure_reason = failure_reason
        deployment.completed_at = now
    elif state == "SUCCEEDED":
        if not revision_id:
            raise DeploymentError(
                "SUCCEEDED requires revision_id from the agent.",
                status_code=422,
            )
        revision = DeploymentRevision(
            id=uuid.uuid4(),
            host_id=host_id,
            project=deployment.project,
            environment=deployment.environment,
            revision_id=revision_id,
            package_sha256=deployment.package_sha256,
            package_manifest_sha256=deployment.package_manifest_sha256,
            source_deployment_id=deployment.id,
            is_known_good=True,
        )
        db.add(revision)
        db.flush()
        deployment.result_revision_id = revision.id
        deployment.completed_at = now
    elif state == "ROLLED_BACK":
        deployment.completed_at = now

    db.flush()
    return deployment


def update_health(
    db: Session,
    *,
    host_id: uuid.UUID,
    deployment_id: uuid.UUID,
    claim_token: str,
    health_json: dict,
) -> HostDeployment:
    deployment_repo = DeploymentRepository(db)
    deployment = deployment_repo.get(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError()

    if deployment.host_id != host_id:
        raise DeploymentOwnershipError()

    if deployment.state in TERMINAL_STATES:
        raise DeploymentError(
            f"Deployment is already in terminal state {deployment.state!r}.",
            status_code=409,
        )

    _verify_claim(deployment, claim_token)
    try:
        deployment.health_json = validate_health_json(health_json)
    except DeploymentJsonError as exc:
        raise DeploymentError(str(exc), status_code=422) from exc

    deployment.last_agent_update_at = datetime.now(timezone.utc)
    db.flush()
    return deployment


def request_rollback(
    db: Session,
    *,
    deployment_id: uuid.UUID,
    request_id: str,
    created_by: str = "admin",
) -> HostDeployment:
    """Create a new APPROVED rollback attempt targeting a known-good revision."""
    deployment_repo = DeploymentRepository(db)
    original = deployment_repo.get(deployment_id)
    if original is None:
        raise DeploymentNotFoundError()

    host_repo = HostRepository(db)
    host = host_repo.get(original.host_id)
    if host is None:
        raise DeploymentNotFoundError("Host not found.")

    if (host.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
        raise DeploymentError(
            "Cannot rollback deployment for a decommissioned host.",
            status_code=409,
        )

    existing = deployment_repo.get_by_request_id_for_host(original.host_id, request_id)
    if existing is not None:
        return existing

    rollback_revision = _select_rollback_target_revision(deployment_repo, original)

    if rollback_revision is None:
        raise DeploymentError(
            "No known-good revision available for rollback.",
            status_code=422,
            code="NO_ROLLBACK_REVISION",
        )

    revision = deployment_repo.get_revision(rollback_revision)
    if revision is None or not revision.is_known_good:
        raise DeploymentError(
            "Rollback revision is not known-good.",
            status_code=422,
        )

    source_deployment = None
    if revision.source_deployment_id is not None:
        source_deployment = deployment_repo.get(revision.source_deployment_id)

    package_uri = source_deployment.package_uri if source_deployment else original.package_uri
    package_sha256 = revision.package_sha256
    package_manifest_sha256 = revision.package_manifest_sha256

    return create_deployment(
        db,
        host_id=original.host_id,
        project=original.project,
        environment=original.environment,
        request_id=request_id,
        plan_hash=original.plan_hash,
        package_uri=package_uri,
        package_sha256=package_sha256,
        package_manifest_sha256=package_manifest_sha256,
        target_alias=original.target_alias,
        workspace_fingerprint=original.workspace_fingerprint,
        ports_json=original.ports_json,
        ingress_json=original.ingress_json,
        rollback_revision_id=rollback_revision,
        created_by=created_by,
    )


def get_pending_for_host(db: Session, host_id: uuid.UUID) -> Optional[HostDeployment]:
    return DeploymentRepository(db).get_pending_for_host(host_id)


def _select_rollback_target_revision(
    deployment_repo: DeploymentRepository,
    original: HostDeployment,
) -> uuid.UUID | None:
    from sqlalchemy import select

    stmt = (
        select(DeploymentRevision)
        .where(
            DeploymentRevision.host_id == original.host_id,
            DeploymentRevision.project == original.project,
            DeploymentRevision.environment == original.environment,
            DeploymentRevision.is_known_good.is_(True),
        )
        .order_by(DeploymentRevision.created_at.desc())
    )
    candidates = deployment_repo.db.execute(stmt).scalars().all()
    for revision in candidates:
        if revision.id != original.result_revision_id:
            return revision.id
    return None
