"""Shared test fixtures.

Uses a REAL PostgreSQL database (a separate `portforge_test` database, so
tests never touch the development database used for manual validation) --
per the project brief, PostgreSQL is the target database and Alembic
migration correctness is validated separately (see test_migrations.py,
which runs actual `alembic upgrade head` -- this conftest uses
`Base.metadata.create_all()` for the rest of the suite purely for speed,
which is standard practice as long as migration correctness itself has
its own dedicated, real test).

If PostgreSQL isn't reachable, the whole backend test session is skipped
(not failed) with a clear reason -- these tests need a real database by
design and shouldn't produce confusing failures in an environment that
simply doesn't have one running.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("PORTFORGE_DB_HOST", "localhost")
os.environ.setdefault("PORTFORGE_DB_HOST_PORT", "55432")
os.environ.setdefault("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "test-admin-bootstrap-token")

from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

TEST_DB_NAME = "portforge_test"


def _admin_url() -> str:
    settings = get_settings()
    base = settings.sqlalchemy_database_url.rsplit("/", 1)[0]
    return f"{base}/postgres"


def _test_db_url() -> str:
    settings = get_settings()
    base = settings.sqlalchemy_database_url.rsplit("/", 1)[0]
    return f"{base}/{TEST_DB_NAME}"


@pytest.fixture(scope="session")
def engine():
    try:
        admin_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
        admin_engine.dispose()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"PostgreSQL is not reachable at {_admin_url()}: {exc}")

    test_engine = create_engine(_test_db_url(), future=True)
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db_session(engine) -> Session:
    """One test = one outer transaction, rolled back at the end -- fast,
    isolated, no cross-test data leakage.

    Services under test call `db.commit()` themselves (that's real
    application behavior we want to exercise) -- `join_transaction_mode=
    "create_savepoint"` (SQLAlchemy 2.0's documented pattern for this
    exact situation) makes each such commit only release a SAVEPOINT
    nested inside our outer transaction, rather than ending it, so the
    final rollback below still discards everything the test did.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session) -> TestClient:
    app = create_app()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def now() -> datetime:
    return datetime.now(timezone.utc)
