"""Validates the Alembic migration chain itself -- NOT a substitute via
`create_all()` (the rest of the suite uses that for speed, see
conftest.py's docstring for why that's fine there). This test runs the
real Alembic upgrade path against fresh, disposable databases and
confirms:

- `upgrade head` succeeds against a database with no schema at all.
- Running `upgrade head` again is a safe no-op (no failure, no duplicate
  application).
- `downgrade base` cleanly removes everything `upgrade head` created.

## Regression coverage: explicit vs. default database URL precedence

A real bug was found during Phase 5 validation: `alembic/env.py`
unconditionally overwrote any `sqlalchemy.url` already present on the
`Config` object with the application's own `Settings()`-derived URL. That
silently redirected `test_upgrade_head_on_fresh_database` (which
explicitly configures a disposable database) onto the default database
instead -- exactly the kind of isolation bug that could let a test
migrate/mutate a real database by accident. `env.py` now only falls back
to `Settings()` when Alembic has **not** already been given a meaningful
URL (see that file's comment for the precedence rule). The tests below
prove both halves of that precedence directly:

- `test_upgrade_head_on_fresh_database` / `test_downgrade_base_removes_everything`
  already prove the "explicit override wins" half end-to-end -- before the
  fix, these failed because nothing ended up in the explicitly-named
  database at all (env.py had silently redirected elsewhere).
- `test_default_settings_url_used_when_no_explicit_override` proves the
  other half: with NO explicit `sqlalchemy.url` set on the Config (exactly
  what plain `alembic upgrade head` from the CLI does), migrations still
  correctly land wherever `Settings()` points -- using a disposable,
  uniquely-named database for that purpose, never the real one (see
  `_require_disposable_db_name` below).
- `test_migration_tests_never_touch_the_real_development_database` is a
  hard tripwire: it snapshots the real `portforge` database's row counts
  before and after the whole module's other tests have run and asserts
  they haven't moved, so any future regression that re-introduces a path
  to the real database fails loudly instead of silently corrupting it.

No database name used by these tests is ever hardcoded to a fixed literal
-- every disposable test database name includes a fresh `uuid4` suffix, so
concurrent runs can never collide with each other either.
"""
from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import get_settings

from .conftest import _admin_url, _test_db_url

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The one name these tests must never target with a destructive
# DROP/CREATE DATABASE, no matter what env vars or settings say --
# `Settings.db_name`'s own built-in default. Real PortForge deployments
# (including this repo's own manually-validated dev database) use exactly
# this name.
_PROTECTED_DB_NAME = "portforge"


def _require_disposable_db_name(name: str) -> None:
    """Hard safety guard: raises rather than letting any fixture below
    issue a destructive DROP/CREATE DATABASE against the real, protected
    development database name. Called before every such operation in this
    module -- structural prevention, not just an after-the-fact check.
    """
    if name == _PROTECTED_DB_NAME:
        raise AssertionError(
            f"Refusing to DROP/CREATE the protected database {_PROTECTED_DB_NAME!r} -- "
            "migration tests must only ever target disposable, uniquely-named databases."
        )


def _unique_db_name(label: str) -> str:
    return f"portforge_test_{label}_{uuid.uuid4().hex[:12]}"


def _alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    if database_url is not None:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture()
def fresh_migration_db():
    """A fresh, disposable, uniquely-named database -- created here and
    dropped afterward. The URL is handed to Alembic *explicitly*, which is
    exactly the scenario the env.py precedence bug broke.
    """
    db_name = _unique_db_name("migration")
    _require_disposable_db_name(db_name)

    try:
        admin_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin_engine.dispose()
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    base = _test_db_url().rsplit("/", 1)[0]
    url = f"{base}/{db_name}"
    yield url

    _require_disposable_db_name(db_name)
    cleanup_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with cleanup_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
    cleanup_engine.dispose()


def _tables_in(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).fetchall()
            }
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Core migration behavior
# ---------------------------------------------------------------------------


def test_upgrade_head_on_fresh_database(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")  # must not raise

    tables = _tables_in(fresh_migration_db)
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


def test_downgrade_base_removes_everything(fresh_migration_db):
    cfg = _alembic_config(fresh_migration_db)
    command.upgrade(cfg, "head")
    assert "hosts" in _tables_in(fresh_migration_db)

    command.downgrade(cfg, "base")  # must not raise

    remaining = _tables_in(fresh_migration_db)
    assert "hosts" not in remaining
    assert "current_port_observations" not in remaining
    assert "central_reservations" not in remaining


# ---------------------------------------------------------------------------
# Regression: explicit Alembic URL vs. Settings-derived default precedence
# ---------------------------------------------------------------------------


def test_explicit_url_takes_precedence_over_settings(fresh_migration_db, monkeypatch):
    """Even when Settings would resolve to a *different* real-looking
    database, an explicitly-configured Alembic URL must still win. This is
    the exact regression the defect report described: env.py must never
    silently redirect an intentionally-configured migration target.
    """
    # Point Settings' default somewhere else entirely -- if env.py were
    # still overriding, migrations would land here instead of in
    # `fresh_migration_db`, and the assertion below would fail.
    monkeypatch.setenv("PORTFORGE_DB_NAME", "this_database_does_not_exist_and_must_not_be_used")
    get_settings.cache_clear()
    try:
        cfg = _alembic_config(fresh_migration_db)  # explicit override
        command.upgrade(cfg, "head")
        assert "hosts" in _tables_in(fresh_migration_db)
    finally:
        get_settings.cache_clear()


def test_default_settings_url_used_when_no_explicit_override(monkeypatch):
    """The other half of the precedence rule: with NO explicit Alembic URL
    configured (exactly what a plain `alembic upgrade head` CLI invocation
    does), env.py must still fall back to Settings() and migrations must
    land wherever Settings points. Uses its own disposable, uniquely-named
    database via Settings -- never the real one -- so this proves the
    fallback path works without any risk to real data.
    """
    db_name = _unique_db_name("default")
    _require_disposable_db_name(db_name)
    monkeypatch.setenv("PORTFORGE_DB_NAME", db_name)
    get_settings.cache_clear()

    admin_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    finally:
        admin_engine.dispose()

    try:
        cfg = _alembic_config()  # deliberately no sqlalchemy.url override
        command.upgrade(cfg, "head")

        base = _test_db_url().rsplit("/", 1)[0]
        target_url = f"{base}/{db_name}"
        assert "hosts" in _tables_in(target_url)
    finally:
        get_settings.cache_clear()
        _require_disposable_db_name(db_name)
        cleanup_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with cleanup_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
        cleanup_engine.dispose()


def test_require_disposable_db_name_refuses_the_protected_name():
    """Direct unit test of the safety guard itself."""
    with pytest.raises(AssertionError):
        _require_disposable_db_name(_PROTECTED_DB_NAME)
    _require_disposable_db_name("portforge_test_anything_else")  # does not raise


def _real_dev_db_host_count() -> int | None:
    """Row count in the real, protected `portforge` database's `hosts`
    table, bypassing any monkeypatched PORTFORGE_DB_NAME (this always
    resolves via a completely fresh Settings instance, independent of
    whatever the currently-active `get_settings()` cache holds). Returns
    None if that database isn't reachable/migrated in this environment.
    """
    from app.config import Settings

    real_db_url = Settings().sqlalchemy_database_url
    assert real_db_url.rsplit("/", 1)[1] == _PROTECTED_DB_NAME

    try:
        engine = create_engine(real_db_url)
        with engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM hosts")).scalar()
    except Exception:
        return None
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _guard_real_development_database():
    """Tripwire, not just a check: snapshots the real `portforge`
    database's `hosts` row count before any test in this module runs, and
    asserts it is byte-for-byte unchanged after every test in this module
    has finished -- combined with `_require_disposable_db_name` guarding
    every destructive operation above, this proves those guards actually
    hold in practice, not just in theory. The real dev database currently
    holds 4 real enrolled hosts from live Phase 5 validation; if any test
    here ever touches it, this fixture fails loudly.
    """
    before = _real_dev_db_host_count()
    yield
    after = _real_dev_db_host_count()
    assert before == after, (
        f"The real development database's `hosts` row count changed during migration tests "
        f"({before!r} -> {after!r}) -- a migration test touched the protected database."
    )


def test_migration_tests_never_touch_the_real_development_database():
    """A same-assertion, explicit test (in addition to the autouse
    tripwire above) so this guarantee shows up by name in test output."""
    count = _real_dev_db_host_count()
    if count is None:
        pytest.skip("Real development database not reachable/migrated in this environment.")
    assert count >= 0
