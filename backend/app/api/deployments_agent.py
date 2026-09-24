"""Agent-facing deployment orchestration API (Phase 18)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.deployment import DeploymentHealthUpdate, DeploymentOut, DeploymentStatusUpdate
from ..security.auth import AuthenticatedAgent, require_agent
from ..services import deployment_service

router = APIRouter(prefix="/agent/deployments", tags=["agent-deployments"])


class DeploymentAgentStatusUpdate(DeploymentStatusUpdate):
    claim_token: str = Field(min_length=1, max_length=64)


class DeploymentAgentHealthUpdate(DeploymentHealthUpdate):
    claim_token: str = Field(min_length=1, max_length=64)


@router.post("/{deployment_id}/claim", response_model=DeploymentOut)
def claim_deployment(
    deployment_id: uuid.UUID,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> DeploymentOut:
    try:
        deployment = deployment_service.claim_deployment(
            db,
            host_id=agent.host_id,
            deployment_id=deployment_id,
        )
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentOwnershipError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(deployment)
    return DeploymentOut.model_validate(deployment)


@router.post("/{deployment_id}/status", response_model=DeploymentOut)
def report_deployment_status(
    deployment_id: uuid.UUID,
    payload: DeploymentAgentStatusUpdate,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> DeploymentOut:
    try:
        deployment = deployment_service.update_status(
            db,
            host_id=agent.host_id,
            deployment_id=deployment_id,
            claim_token=payload.claim_token,
            state=payload.state,
            failure_code=payload.failure_code,
            failure_reason=payload.failure_reason,
            revision_id=payload.revision_id,
        )
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentOwnershipError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentClaimMismatchError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(deployment)
    return DeploymentOut.model_validate(deployment)


@router.post("/{deployment_id}/health", response_model=DeploymentOut)
def report_deployment_health(
    deployment_id: uuid.UUID,
    payload: DeploymentAgentHealthUpdate,
    agent: AuthenticatedAgent = Depends(require_agent),
    db: Session = Depends(get_db),
) -> DeploymentOut:
    try:
        deployment = deployment_service.update_health(
            db,
            host_id=agent.host_id,
            deployment_id=deployment_id,
            claim_token=payload.claim_token,
            health_json=payload.health_json,
        )
    except deployment_service.DeploymentNotFoundError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentOwnershipError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentClaimMismatchError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except deployment_service.DeploymentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db.commit()
    db.refresh(deployment)
    return DeploymentOut.model_validate(deployment)
