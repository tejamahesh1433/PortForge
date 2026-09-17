"""Shared SQLAlchemy declarative base and mixins.

`Protocol` and `PortState` are imported directly from the already-installed
`portforge_agent` package rather than redefined here -- see this package's
`__init__.py` docstring. The central server depends on the agent library
for these pure value types only (a narrow, one-directional dependency: the
agent never imports anything from `backend`); nothing about the agent's own
behavior, storage format, or tests changes because of it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Shared domain vocabulary -- see module docstring.
from portforge_agent.detection.models import Confidence as Confidence  # noqa: F401 (re-exported)
from portforge_agent.models import PortState as PortState  # noqa: F401 (re-exported)
from portforge_agent.models import Protocol as Protocol  # noqa: F401 (re-exported)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
