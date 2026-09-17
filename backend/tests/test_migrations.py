"""Validates the Alembic migration chain itself -- NOT a substitute via
`create_all()` (the rest of the suite uses that for speed, see
conftest.py's docstring for why that's fine there). This test runs the
real Alembic upgrade path against a fresh database and confirms:

- `upgrade head` succeeds against a database with no schema at all.
- Running `upgrade head` again is a safe no-op (no failure, no duplicate
  application).
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from .conftest import TEST_DB_NAME, _admin_url, _test_db_url

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def fresh_migration_db():
    try:
        admin_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS portforge_migration_test WITH (FORCE)"))
            conn.execute(text("CREATE DATABASE portforge_migration_test"))
        admin_engine.dispose()
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    base = _test_db_url().rsplit("/", 1)[0]
    url = f"{base}/portforge_migration_test"
    yield url

    cleanup_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with cleanup_engine.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS portforge_migration_test WITH (FORCE)"))
    cleanup_engine.dispose()


def _alembic_config(database_url: str) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_upgrade_head_on_fresh_database(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")  # must not raise

    engine = create_engine(fresh_migration_db)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).fetchall()
        }
    engine.dispose()

    assert "hosts" in tables
    assert "current_port_observations" in tables
    assert "central_reservations" in tables
    assert "alembic_version" in tables


def test_upgrade_head_twice_is_a_safe_no_op(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # must not raise the second time either

    engine = create_engine(fresh_migration_db)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    assert version is not None
