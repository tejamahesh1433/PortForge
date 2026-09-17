"""Central server client configuration.

Stored in its own protected file in the PortForge data directory (see
paths.py) -- deliberately NOT in a project's `.portforge.yml`, which is
meant to be committed alongside a project's source and is exactly where a
secret must never live. Central sync is entirely opt-in: absent this file
(or with `enabled: false`), nothing in the agent ever attempts a network
call to a central server -- see agent/README.md "Offline behavior".

The token is a write-only value as far as any output PortForge produces:
`central status` and `central enroll` never print it, and it's excluded
from every log line this module emits.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import paths


@dataclass(frozen=True)
class CentralConfig:
    enabled: bool = False
    url: Optional[str] = None
    token: Optional[str] = None

    def is_usable(self) -> bool:
        return self.enabled and bool(self.url) and bool(self.token)


def central_config_path() -> Path:
    return paths.data_dir() / "central.json"


def load_central_config(path: Optional[Path] = None) -> CentralConfig:
    """Never raises -- a missing or malformed central.json simply means
    "central sync is not configured," which must never block local
    operation. Unlike reservations.json/host.json, this file holds no
    irreplaceable user data, so degrading to "disabled" on any problem is
    the right failure mode here (contrast with identity.py/storage.py,
    where the same kind of file being malformed is deliberately fatal).
    """
    path = path or central_config_path()
    if not path.exists():
        return CentralConfig()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CentralConfig()

    if not isinstance(data, dict):
        return CentralConfig()

    return CentralConfig(
        enabled=bool(data.get("enabled", False)),
        url=data.get("url") if isinstance(data.get("url"), str) else None,
        token=data.get("token") if isinstance(data.get("token"), str) else None,
    )


def save_central_config(config: CentralConfig, path: Optional[Path] = None) -> None:
    import os
    import tempfile

    path = path or central_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"enabled": config.enabled, "url": config.url, "token": config.token}, indent=2
    )

    fd, tmp_name = tempfile.mkstemp(prefix=".central-", suffix=".tmp", dir=str(path.parent))
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
