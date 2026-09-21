"""Regression coverage for CORS: Phase 7A's minimal read-only addition (the
Next.js dashboard needs browser-based cross-origin GET access to the read
APIs during local dev), extended in Phase 7C.4 once the dashboard's
reservation create/release actions became real, unauthenticated cross-origin
writes. Verifies the configured origin is allowed, an unconfigured origin is
not, the dashboard's actual write methods (POST/DELETE) are permitted, and a
method nothing in the dashboard uses (PUT) is still rejected -- never a
wildcard, never an unbounded method allowlist.

get_settings() is @lru_cache'd (see app/config.py), so every test here
clears that cache both before (to pick up its own monkeypatched env var)
and after (so it never leaks a stale Settings instance into an unrelated
later test) -- the same pattern tests/test_migrations.py already
established for the same reason.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_configured_origin_receives_cors_headers(monkeypatch):
    monkeypatch.setenv("PORTFORGE_CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_unconfigured_origin_is_not_allowed(monkeypatch):
    monkeypatch.setenv("PORTFORGE_CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in response.headers


def test_dashboard_reservation_write_methods_are_allowed_cross_origin(monkeypatch):
    """POST (create) and DELETE (release) are the dashboard's actual write
    methods (see dashboard/lib/api/resources.ts) -- both must clear the
    browser's CORS preflight, or the request never reaches the route
    handler at all regardless of what that handler itself allows.
    """
    monkeypatch.setenv("PORTFORGE_CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    app = create_app()
    with TestClient(app) as client:
        for method in ("POST", "DELETE"):
            response = client.options(
                "/api/reservations/dashboard",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": method,
                },
            )
            assert response.status_code == 200, method
            assert method in response.headers.get("access-control-allow-methods", ""), method


def test_unlisted_mutating_cross_origin_method_is_not_allowed(monkeypatch):
    monkeypatch.setenv("PORTFORGE_CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/api/reservations/dashboard",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
            },
        )
    # Starlette's CORSMiddleware rejects a disallowed preflight method with
    # HTTP 400 -- it still echoes access-control-allow-origin on that
    # response (explaining *which* origin it evaluated), so the real
    # "was this allowed" signal is the status code plus PUT's absence
    # from the allowed-methods list, not the origin header's presence.
    assert response.status_code == 400
    assert "PUT" not in response.headers.get("access-control-allow-methods", "")


def test_empty_cors_origins_disables_middleware_entirely(monkeypatch):
    monkeypatch.setenv("PORTFORGE_CORS_ALLOWED_ORIGINS", "")
    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in response.headers


def test_cors_allowed_origins_list_parses_comma_separated_env_value():
    settings = Settings(cors_allowed_origins="http://localhost:3000, http://127.0.0.1:3000 ,")
    assert settings.cors_allowed_origins_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_allowed_origins_default_covers_both_local_dev_addresses():
    settings = Settings()
    assert "http://localhost:3000" in settings.cors_allowed_origins_list
    assert "http://127.0.0.1:3000" in settings.cors_allowed_origins_list
