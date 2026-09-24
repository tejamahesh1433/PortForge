"""FastAPI app factory. Deliberately thin -- every actual route lives in
`api/`, grouped by resource; this file only wires routers together.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import (
    activity,
    agents,
    allocations,
    conflicts,
    fleet,
    health,
    hosts,
    ports,
    projects,
    recommendations,
    reservations,
)
from .api.deployments import router as deployments_router
from .api.deployments_agent import router as deployments_agent_router
from .api.upgrades import host_upgrades_router, rollouts_router, upgrades_router
from .config import get_settings
from .services.allocation_service import AllocationError


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PortForge Central",
        version=settings.version,
        description="Central registry for multi-host PortForge port observations, reservations, and suggestions.",
    )

    # Phase 7A: the Next.js dashboard runs on a different origin (typically
    # localhost:3000) during local development and needs browser-based
    # cross-origin access to these APIs. Only installed at all when
    # cors_allowed_origins is non-empty (see config.py) -- an empty list
    # means no CORSMiddleware is added, leaving cross-origin browser
    # requests blocked exactly as before this change for any deployment
    # that never sets it. Never a wildcard origin.
    #
    # Phase 7C.4: POST/DELETE were added alongside GET/HEAD/OPTIONS once the
    # dashboard's reservation create/release actions became real,
    # unauthenticated cross-origin writes (PortForge runs as a trusted
    # private/LAN control plane -- no login/session/token architecture is in
    # scope, see docs/phase7c4_ux_audit.md). Without this, the browser's own
    # CORS preflight for those requests is rejected before it ever reaches
    # the route handler, regardless of what the handler itself allows.
    allowed_origins = settings.cors_allowed_origins_list
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "HEAD", "OPTIONS", "POST", "DELETE"],
            allow_headers=["*"],
        )

    app.include_router(health.router, prefix="/api")
    app.include_router(hosts.router, prefix="/api")
    app.include_router(ports.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(reservations.router, prefix="/api")
    app.include_router(recommendations.router, prefix="/api")
    app.include_router(conflicts.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(activity.router, prefix="/api")
    app.include_router(allocations.router, prefix="/api")
    app.include_router(fleet.router, prefix="/api")
    app.include_router(host_upgrades_router, prefix="/api")
    app.include_router(upgrades_router, prefix="/api")
    app.include_router(rollouts_router, prefix="/api")
    app.include_router(deployments_router, prefix="/api")
    app.include_router(deployments_agent_router, prefix="/api")

    # Phase 8A: allocation errors are machine-readable for coding-agent
    # consumers (see docs/phase8a_agent_allocation.md "Error contract") --
    # `{"error": {"code": ..., "message": ..., "details": [...]}}` at the
    # top level, deliberately NOT FastAPI's standard `{"detail": "..."}`
    # shape every other endpoint in this app uses (that shape assumes a
    # plain string, per the dashboard client's own parsing -- see
    # lib/api/client.ts -- so reusing it for a rich object here would be a
    # real inconsistency, not a simplification). Scoped to AllocationError
    # only: no other route in this app ever raises it, so every other
    # endpoint's error shape is completely unaffected.
    @app.exception_handler(AllocationError)
    async def _allocation_error_handler(request: Request, exc: AllocationError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    return app


app = create_app()
