"""One-time migration of legacy (Phase 4) hostname-based reservation
`host_id`s to the Phase 5 persistent UUID host identity.

Idempotent by construction: it looks for reservations whose `host_id`
matches this machine's *current* hostname (the only `host_id` Phase 4 ever
wrote) and rewrites them to the persistent UUID, preserving
`reservation_id`, timestamps, and ownership. After the first successful
run there are no such reservations left, so every subsequent call is a
fast no-op -- no separate "already migrated" flag is needed.

Called once per CLI invocation, at startup (see cli.py's `main()`), fully
outside of any already-held reservation lock -- deliberately **not**
embedded in `platform.get_host_id()` itself. A plain getter silently
rewriting persistent storage as a side effect would be surprising, and
would risk a future nested-lock deadlock if any caller ever ends up
calling `get_host_id()` while already holding the reservation lock (none
currently do, but a getter shouldn't rely on that staying true forever).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import List

from .. import platform as pf
from ..paths import lock_path as default_lock_path
from ..paths import reservations_path as default_reservations_path
from .lock import reservation_lock
from .models import Reservation
from .storage import ReservationStorageError, ReservationStore

logger = logging.getLogger("portforge_agent.reservations.migration")


def migrate_legacy_reservations(new_host_id: str, legacy_host_id: str) -> int:
    """Rewrite any reservation whose host_id == legacy_host_id to new_host_id.

    Returns the number of reservations migrated (0 if nothing needed it).
    Safe to call repeatedly -- a second call finds nothing left to migrate.
    """
    if not legacy_host_id or new_host_id == legacy_host_id:
        return 0

    store = ReservationStore(default_reservations_path())

    # Cheap check without the lock first: the common, steady-state case
    # (nothing to migrate -- either a fresh install, or migration already
    # ran on a prior invocation) never needs to touch the lock at all.
    try:
        reservations = store.load()
    except ReservationStorageError as exc:
        logger.warning("Skipping reservation migration: %s", exc)
        return 0

    if not any(r.host_id == legacy_host_id for r in reservations):
        return 0

    with reservation_lock(default_lock_path()):
        try:
            reservations = store.load()  # reload fresh under the lock
        except ReservationStorageError as exc:
            logger.warning("Skipping reservation migration: %s", exc)
            return 0

        migrated: List[Reservation] = []
        migrated_count = 0
        for r in reservations:
            if r.host_id == legacy_host_id:
                # Only host_id changes -- reservation_id, timestamps,
                # project/service/purpose/notes are all preserved exactly.
                migrated.append(dataclasses.replace(r, host_id=new_host_id))
                migrated_count += 1
            else:
                migrated.append(r)

        if migrated_count:
            store.save(migrated)
            logger.info(
                "Migrated %d reservation(s) from legacy host_id '%s' to persistent host_id '%s'",
                migrated_count,
                legacy_host_id,
                new_host_id,
            )

        return migrated_count


def migrate_if_needed(new_host_id: str) -> int:
    """Convenience wrapper using this machine's current hostname as the
    legacy host_id -- the only value Phase 4 ever wrote as `host_id`.
    """
    return migrate_legacy_reservations(new_host_id, pf.get_hostname())
