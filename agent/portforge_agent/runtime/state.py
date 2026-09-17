"""Runtime state persistence for PortForge Agent.

Stores non-secret metadata (e.g., last scan time, observation counts,
sequence numbers) safely across restarts.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import paths


@dataclass
class RuntimeState:
    last_scan: Optional[float] = None
    last_sync: Optional[float] = None
    last_sync_error: Optional[str] = None
    last_heartbeat: Optional[float] = None
    last_observation_count: int = 0
    agent_version: str = ""
    sequence_id: int = 0


def state_path() -> Path:
    return paths.data_dir() / "runtime_state.json"


def load_state() -> RuntimeState:
    """Loads runtime state, returning a fresh default object if missing/invalid."""
    path = state_path()
    if not path.exists():
        return RuntimeState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return RuntimeState()
        return RuntimeState(
            last_scan=data.get("last_scan"),
            last_sync=data.get("last_sync"),
            last_sync_error=data.get("last_sync_error"),
            last_heartbeat=data.get("last_heartbeat"),
            last_observation_count=data.get("last_observation_count", 0),
            agent_version=data.get("agent_version", ""),
            sequence_id=data.get("sequence_id", 0)
        )
    except (OSError, ValueError):
        return RuntimeState()


def save_state(state: RuntimeState) -> None:
    """Atomically saves the runtime state."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    payload = json.dumps({
        "last_scan": state.last_scan,
        "last_sync": state.last_sync,
        "last_sync_error": state.last_sync_error,
        "last_heartbeat": state.last_heartbeat,
        "last_observation_count": state.last_observation_count,
        "agent_version": state.agent_version,
        "sequence_id": state.sequence_id
    }, indent=2)
    
    import tempfile
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(path.parent))
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
