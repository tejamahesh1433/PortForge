"""FastAPI app factory. Deliberately thin -- every actual route lives in
`api/`, grouped by resource; this file only wires routers together.
"""
from __future__ import annotations

from fastapi import FastAPI

from .api import agents, conflicts, health, hosts, ports, projects, recommendations, reservations
from .config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PortForge Central",
        version=settings.version,
        description="Central registry for multi-host PortForge port observations, reservations, and suggestions.",
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(hosts.router, prefix="/api")
    app.include_router(ports.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(reservations.router, prefix="/api")
    app.include_router(recommendations.router, prefix="/api")
    app.include_router(conflicts.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")

    return app


app = create_app()
