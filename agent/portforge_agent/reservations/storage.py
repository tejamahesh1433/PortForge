"""Durable local reservation storage: JSON on disk, atomic writes.

Why JSON, not SQLite/PostgreSQL: Phase 4's storage need is a small,
infrequently-written list of records read wholesale and rewritten wholesale
(a full reservation list is realistically dozens of entries, not millions)
under an explicit external lock (see lock.py) -- a real database's
transaction machinery would add a dependency and complexity without solving
a problem JSON + atomic-replace doesn't already solve at this scale. A
central multi-host server is exactly where a real database earns its keep
(later phase).

Safety: a malformed, unreadable, or schema-mismatched file is NEVER
silently discarded or overwritten -- `load()` raises `ReservationStorageError`
instead, so the user's data stays on disk exactly as it was until they
(or a future migration) deal with it. A single malformed *entry* inside an
otherwise well-formed file is treated the same way (fails the whole load)
rather than being silently dropped -- see Reservation.from_dict -- because
silently dropping one entry on load would permanently delete it the next
time anything calls save() (which always rewrites the whole file).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List

from .models import Reservation

SCHEMA_VERSION = 1


class ReservationStorageError(Exception):
    """The reservation file exists but can't be trusted as-is.

    Covers: malformed JSON, unexpected shape, unsupported schema version,
    and permission errors. Callers (the CLI) should surface this clearly
    and refuse to proceed with reservation operations rather than papering
    over it.
    """


class ReservationStore:
    """Loads/saves the full reservation list for one local data file.

    Not itself concurrency-safe across processes -- callers doing a
    read-modify-write cycle must hold `reservations.lock.reservation_lock`
    around the whole load()...save() sequence (see recommend.py and the
    CLI's reserve/release commands for the pattern).
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> List[Reservation]:
        if not self.path.exists():
            return []

        try:
            text = self.path.read_text(encoding="utf-8")
        except PermissionError as exc:
            raise ReservationStorageError(f"Permission denied reading {self.path}") from exc
        except OSError as exc:
            raise ReservationStorageError(f"Failed to read {self.path}: {exc}") from exc

        if not text.strip():
            return []  # an empty file is "no reservations yet", not malformed

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReservationStorageError(
                f"Malformed reservation file {self.path}: {exc}. "
                "The file has not been modified; fix or remove it manually."
            ) from exc

        if not isinstance(data, dict):
            raise ReservationStorageError(
                f"Unexpected reservation file shape in {self.path} (expected a JSON object)"
            )

        schema_version = data.get("schema_version")
        if schema_version != SCHEMA_VERSION:
            raise ReservationStorageError(
                f"Unsupported reservation schema version {schema_version!r} in {self.path} "
                f"(this version of PortForge understands schema {SCHEMA_VERSION}). "
                "Refusing to load or modify the file to avoid data loss."
            )

        raw_reservations = data.get("reservations")
        if not isinstance(raw_reservations, list):
            raise ReservationStorageError(f"Malformed 'reservations' list in {self.path}")

        try:
            return [Reservation.from_dict(r) for r in raw_reservations]
        except ValueError as exc:
            raise ReservationStorageError(f"Malformed reservation entry in {self.path}: {exc}") from exc

    def save(self, reservations: List[Reservation]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReservationStorageError(
                f"Could not create reservation directory {self.path.parent}: {exc}"
            ) from exc

        payload = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "reservations": [r.to_dict() for r in reservations],
            },
            indent=2,
        )

        # Write-temp-then-replace: a crash mid-write leaves the original
        # file (or nothing, on the very first save) untouched, never a
        # half-written reservations.json. os.replace() is atomic on both
        # POSIX (rename(2)) and Windows (MoveFileExW + MOVEFILE_REPLACE_EXISTING).
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".reservations-", suffix=".tmp", dir=str(self.path.parent)
            )
        except OSError as exc:
            raise ReservationStorageError(f"Could not create temp file for {self.path}: {exc}") from exc

        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except PermissionError as exc:
            _best_effort_unlink(tmp_path)
            raise ReservationStorageError(f"Permission denied writing {self.path}: {exc}") from exc
        except OSError as exc:
            _best_effort_unlink(tmp_path)
            raise ReservationStorageError(f"Failed to write {self.path}: {exc}") from exc


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
