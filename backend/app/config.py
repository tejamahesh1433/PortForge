"""Central server configuration.

Everything here comes from environment variables (optionally loaded from a
local `.env` file for development) -- nothing is hardcoded, per the same
"no hardcoded machine names/paths/ports" discipline the agent follows. In
particular, the PostgreSQL host port and the API's own host port are both
configurable specifically so they never blindly collide with a port an
existing project already uses on the developer's machine (see
docker-compose.yml and README "Choosing ports").
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTFORGE_", env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------
    # A full DATABASE_URL always wins if set; otherwise it's assembled
    # from the individual DB_* pieces below (convenient for docker-compose
    # env files where each piece is set separately).
    database_url: str | None = None
    # 127.0.0.1, not "localhost": on Docker Desktop + WSL2, "localhost"
    # resolves IPv6-first for a brand-new connection and that lookup can
    # stall 100+ seconds (pre-existing, previously-diagnosed networking
    # quirk -- see docs/architecture.md and the Phase 6 physical-sync
    # investigation). That stall doesn't just slow one request: every
    # concurrent request needing a new pooled connection stalls the same
    # way, and enough of them piling up during that window exhausts
    # QueuePool's 5+10 capacity even though every session is closed
    # correctly -- see database.py's get_db(). 127.0.0.1 skips the DNS/
    # address-family resolution entirely and connects immediately.
    db_host: str = "127.0.0.1"
    db_host_port: int = 55432  # PORTFORGE_DB_HOST_PORT -- see docker-compose.yml
    db_name: str = "portforge"
    db_user: str = "portforge"
    db_password: str = "portforge"

    # --- API ------------------------------------------------------------
    api_host_port: int = 58000  # PORTFORGE_API_HOST_PORT
    environment: str = "development"
    version: str = "1.0.0"

    # --- Security ---------------------------------------------------------
    # Required to mint enrollment tokens (see security/tokens.py). Deliberately
    # has NO default -- an admin must set it explicitly. Never logged, never
    # echoed in any API response (see api/health.py and security/tokens.py).
    admin_bootstrap_token: str | None = None

    # --- Ingestion limits ---------------------------------------------------
    max_observations_per_snapshot: int = 5000
    max_string_length: int = 4096

    # --- CORS (Phase 7A dashboard) -------------------------------------------
    # Comma-separated allowed origins for the Next.js dashboard's local dev
    # server -- never a wildcard, and never hardcoded beyond this narrow,
    # explicit, configurable default of the two equivalent local dev
    # addresses. Empty string disables CORS entirely (no middleware
    # installed -- see main.py), which is the right default for any
    # deployment that doesn't need browser-based cross-origin access at all.
    cors_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Health & Diagnostics (Phase 7C.2) -----------------------------------
    host_stale_after_seconds: int = 120
    host_offline_after_seconds: int = 300

    @model_validator(mode="after")
    def check_health_thresholds(self) -> "Settings":
        if self.host_stale_after_seconds >= self.host_offline_after_seconds:
            raise ValueError("host_stale_after_seconds must be less than host_offline_after_seconds")
        return self

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_host_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
