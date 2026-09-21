"""Shared host-scoped PostgreSQL advisory lock.

Extracted from `ingestion_service.py`'s `_acquire_host_ingestion_lock`
(Phase 6) -- identical SQL, now used by both snapshot ingestion and Phase
8A allocation. Deliberately the SAME lock key space (`hashtext(host_id)`)
for both call sites, not two independent locks: an allocation and a
concurrent snapshot ingestion for the *same* host are both, semantically,
"the thing that decides what's true about this host's ports right now",
and serializing them against each other is strictly safer than two lock
spaces unaware of one another. See docs/phase8a_allocation_audit.md §4.

Transaction-scoped (`pg_advisory_xact_lock`): automatically released on
COMMIT or ROLLBACK, no manual unlock, and scoped per-host so concurrent
traffic for *different* hosts is entirely unaffected.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def acquire_host_lock(db: Session, host_id: uuid.UUID) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:host_id))"), {"host_id": str(host_id)})
