"""FastAPI auth dependencies.

Two independent credential types, kept conceptually separate (see
api/agents.py's module docstring for why):

- `require_admin`: the bootstrap admin token (`PORTFORGE_ADMIN_BOOTSTRAP_TOKEN`),
  used only to mint enrollment tokens. Never accepted for anything else.
- `require_agent`: a per-host agent credential, established during
  enrollment (see api/agents.py) and required for every
  heartbeat/observation-ingestion/reservation-sync call. Resolves to the
  authenticated Host's UUID -- callers must use *that* value, never trust
  a `host_id` a request body merely claims to be (see api/agents.py).

Neither dependency ever echoes the presented token back in a response or
log line.
"""
from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..repositories.agent_repository import AgentRepository
from .tokens import hash_token

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedAgent:
    host_id: uuid.UUID


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    settings = get_settings()
    if not settings.admin_bootstrap_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Admin bootstrap token is not configured on this server.",
        )
    if credentials is None or not hmac.compare_digest(credentials.credentials, settings.admin_bootstrap_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing admin credentials.")


def require_agent(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthenticatedAgent:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")

    token_hash = hash_token(credentials.credentials)
    repo = AgentRepository(db)
    credential = repo.get_active_credential_by_token_hash(token_hash)
    if credential is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked agent credential.")

    return AuthenticatedAgent(host_id=credential.host_id)
