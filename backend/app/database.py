"""SQLAlchemy engine/session setup.

Modern SQLAlchemy 2.0 patterns throughout: a typed `DeclarativeBase` (see
models/base.py), `Mapped`/`mapped_column` annotations in every model, and a
single `sessionmaker` shared by the whole app. Database access is never
scattered ad hoc through API routes -- routes depend on a `Session` via
`get_db()`, and all querying happens in `repositories/` (see that
package's docstring).
"""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.sqlalchemy_database_url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a Session, always closed after the request."""
    session_factory = get_sessionmaker()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def reset_engine_for_testing() -> None:
    """Test-only: drop cached engine/sessionmaker so a fresh Settings()
    (e.g. pointed at a different database_url) takes effect.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
