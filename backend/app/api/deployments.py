"""Client-facing deployment orchestration API (Phase 18).

Unauthenticated by design, same posture as `/api/allocations` — PortForge is a
trusted private/LAN control plane, and admin bootstrap is not required for
coding-agent deployment orchestration.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.deployment import (
    DeploymentCreateRequest,
    DeploymentOut,
    DeploymentPlanOut,
    DeploymentPlanRequest,
    DeploymentRollbackRequest,
)
from ..services import deployment_service

router = APIRouter(prefix="/deployments", tags=["deployments"])


def _deployment_out(deployment) -> DeploymentOut:
    return DeploymentOut.model_validate(deployment)


@router.post(
    "/plan",
    response_model=DeploymentPlanOut,
    summary="Build Deployment Plan",
)
def build_deployment_plan(payload: DeploymentPlanRequest) -> DeploymentPlanOut:
    """Read-only plan preview — never inserts a deployment row."""
    plan = deployment_service.build_plan(
        host_id=payload.host_id,
        project=payload.project,
        environment=payload.environment,
        target_alias=payload.target_alias,
        workspace_fingerprint=payload.workspace_fingerprint,
        services=payload.services,
        ingress_bindings=payload.ingress_bindings,
        rollback_revision_id=payload.rollback_revision_id,
    )
    return DeploymentPlanOut.model_validate(plan)


@router.post(
    "",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create Deployment",
)
def create_deployment(
    payload: DeploymentCreateRequest,
    db: Session = Depends(get_db),
) -> DeploymentOut:
    try:
        deployment = deployment_service.create_deployment(
            db,
            host_id=payload.host_id,
            project=payload.project,
            environment=payload.environment,
            request_id=payload.request_id,
            plan_hash=payload.plan_hash,
            package_uri=payload.package_uri,
            package_sha256=payload.package_sha256,
            package_manifest_sha256=payload.package_manifest_sha256,
            target_alias=payload.target_alias,
            workspace_fingerprint=payload.workspace_fingerprint,
            ports_json=payload.ports_json,
            ingress_json=payload.ingress_json,
            health_json=payload.health_json,
            rollback_revision_id=payload.rollback_revision_id,
            created_by="client",
        )
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentOwnershipError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(deployment)
    return _deployment_out(deployment)


@router.get(
    "/{deployment_id}",
    response_model=DeploymentOut,
    summary="Get Deployment",
)
def get_deployment(deployment_id: uuid.UUID, db: Session = Depends(get_db)) -> DeploymentOut:
    try:
        deployment = deployment_service.get_deployment(db, deployment_id)
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return _deployment_out(deployment)


@router.post(
    "/{deployment_id}/rollback",
    response_model=DeploymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Rollback Deployment",
)
def rollback_deployment(
    deployment_id: uuid.UUID,
    payload: DeploymentRollbackRequest,
    db: Session = Depends(get_db),
) -> DeploymentOut:
    try:
        deployment = deployment_service.request_rollback(
            db,
            deployment_id=deployment_id,
            request_id=payload.request_id,
            created_by="client",
        )
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentOwnershipError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(deployment)
    return _deployment_out(deployment)
