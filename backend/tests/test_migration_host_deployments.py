"""Alembic migration tests for Phase 18 host_deployments / deployment_revisions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from .test_migrations import _alembic_config, _tables_in, fresh_migration_db

PREVIOUS_HEAD = "c3d4e5f6a1b2"
NEW_HEAD = "d5e6f7a8b9c0"

_DEPLOYMENT_INSERT = """
INSERT INTO host_deployments (
    id, host_id, project, environment, request_id, state, plan_hash,
    package_uri, package_sha256, package_manifest_sha256, created_at, updated_at
) VALUES (
    :id, :host_id, :project, :environment, :request_id, :state, :plan_hash,
    :package_uri, :package_sha256, :package_manifest_sha256, :now, :now
)
"""


def _insert_host(conn, host_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            """
            INSERT INTO hosts (
                id, hostname, operating_system, docker_available,
                first_seen, last_seen, status, lifecycle_state,
                created_at, updated_at
            ) VALUES (
                :id, 'test-host', 'linux', false,
                :now, :now, 'online', 'ACTIVE',
                :now, :now
            )
            """
        ),
        {"id": host_id, "now": now},
    )


def test_upgrade_head_creates_deployment_tables(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")

    tables = _tables_in(fresh_migration_db)
    assert "host_deployments" in tables
    assert "deployment_revisions" in tables


def test_downgrade_removes_deployment_tables(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")
    assert "host_deployments" in _tables_in(fresh_migration_db)

    command.downgrade(cfg, PREVIOUS_HEAD)

    tables = _tables_in(fresh_migration_db)
    assert "host_deployments" not in tables
    assert "deployment_revisions" not in tables
    assert "hosts" in tables


def test_partial_unique_active_deployment(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, NEW_HEAD)

    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    base = {
        "host_id": host_id,
        "project": "my-app",
        "environment": "staging",
        "state": "APPROVED",
        "plan_hash": "a" * 64,
        "package_uri": "https://artifacts.example/pkg.tar.gz",
        "package_sha256": "b" * 64,
        "package_manifest_sha256": "c" * 64,
        "now": now,
    }

    engine = create_engine(fresh_migration_db)
    with engine.begin() as conn:
        _insert_host(conn, host_id)
        conn.execute(
            text(_DEPLOYMENT_INSERT),
            {**base, "id": uuid.uuid4(), "request_id": "req-1"},
        )

    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(_DEPLOYMENT_INSERT),
                {**base, "id": uuid.uuid4(), "request_id": "req-2"},
            )

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE host_deployments SET state = 'SUCCEEDED' WHERE request_id = 'req-1'")
        )
        conn.execute(
            text(_DEPLOYMENT_INSERT),
            {**base, "id": uuid.uuid4(), "request_id": "req-3"},
        )
    engine.dispose()
