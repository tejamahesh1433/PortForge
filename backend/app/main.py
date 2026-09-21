"""FastAPI app factory. Deliberately thin -- every actual route lives in
`api/`, grouped by resource; this file only wires routers together.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import activity, agents, conflicts, health, hosts, ports, projects, recommendations, reservations
from .config import get_settings


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

    return app


app = create_app()
