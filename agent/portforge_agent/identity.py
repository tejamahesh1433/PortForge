"""Persistent host identity (Phase 5).

A UUID generated exactly once per PortForge installation, stored locally,
and reused for the lifetime of that installation -- it survives process
restarts and hostname changes, unlike the Phase 1-4 hostname-based
`host_id` it replaces (see platform.py's `get_host_id()`).

Storage location mirrors reservations.json (see paths.py):

    Windows:  %LOCALAPPDATA%\\PortForge\\host.json
    macOS:    ~/Library/Application Support/PortForge/host.json
    Linux:    $XDG_DATA_HOME/portforge/host.json, else ~/.local/share/portforge/host.json

Safety guarantees, same discipline as reservations/storage.py:

- Atomic writes (temp file + fsync + os.replace()).
- A malformed identity file is NEVER silently replaced with a fresh UUID --
  that could make one physical machine appear as two different hosts to
  the central server (the old UUID's history orphaned, a new one created).
  `load_host_identity()` raises `HostIdentityError` instead, exactly like
  a malformed reservations.json does, so the failure is loud and the
  original file stays on disk for a human to fix or remove.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import paths
from . import platform as pf

logger = logging.getLogger("portforge_agent.identity")

SCHEMA_VERSION = 1


class HostIdentityError(Exception):
    """The host identity file exists but can't be trusted as-is (malformed,
    unsupported schema, permission error). Never silently replaced.
    """


@dataclass(frozen=True)
class HostIdentity:
    host_id: str  # str(uuid.uuid4())
    created_at: datetime
    hostname_at_creation: str


def identity_path() -> Path:
    return paths.data_dir() / "host.json"


def load_host_identity(path: Optional[Path] = None) -> Optional[HostIdentity]:
    """Load the existing identity, or None if none has been created yet.

    Raises HostIdentityError for anything that looks like a real identity
    file but can't be trusted -- callers must never treat that as "absent"
    and generate a replacement.
    """
    path = path or identity_path()
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except PermissionError as exc:
        raise HostIdentityError(f"Permission denied reading {path}") from exc
    except OSError as exc:
        raise HostIdentityError(f"Failed to read {path}: {exc}") from exc

    if not text.strip():
        return None  # an empty file is treated the same as "not created yet"

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HostIdentityError(
            f"Malformed host identity file {path}: {exc}. A malformed identity file is "
            "never auto-replaced -- doing so could make this machine appear as a new "
            "host to the central server. Fix or remove the file manually."
        ) from exc

    if not isinstance(data, dict):
        raise HostIdentityError(f"Unexpected host identity file shape in {path} (expected a JSON object)")

    if data.get("schema_version") != SCHEMA_VERSION:
        raise HostIdentityError(
            f"Unsupported host identity schema version {data.get('schema_version')!r} in {path} "
            f"(expected {SCHEMA_VERSION}). Refusing to load or replace it."
        )

    try:
        host_id = str(uuid.UUID(str(data["host_id"])))  # validates format
        created_at = _parse_datetime(data["created_at"])
        hostname_at_creation = str(data.get("hostname_at_creation") or "")
    except (KeyError, TypeError, ValueError) as exc:
        raise HostIdentityError(f"Malformed host identity fields in {path}: {exc}") from exc

    return HostIdentity(host_id=host_id, created_at=created_at, hostname_at_creation=hostname_at_creation)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"expected an ISO 8601 string, got {value!r}")


def _save_host_identity(identity: HostIdentity, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "host_id": identity.host_id,
            "created_at": identity.created_at.isoformat(),
            "hostname_at_creation": identity.hostname_at_creation,
        },
        indent=2,
    )

    fd, tmp_name = tempfile.mkstemp(prefix=".host-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def get_or_create_host_identity(path: Optional[Path] = None) -> HostIdentity:
    """The main entry point: load the existing identity, or generate and
    persist a new one on first run. Generated exactly once -- every
    subsequent call (this process or a future one) returns the same UUID.
    """
    path = path or identity_path()

    existing = load_host_identity(path)  # HostIdentityError propagates -- never swallowed
    if existing is not None:
        return existing

    identity = HostIdentity(
        host_id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        hostname_at_creation=pf.get_hostname(),
    )
    _save_host_identity(identity, path)
    return identity
