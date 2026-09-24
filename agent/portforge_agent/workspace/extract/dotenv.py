from __future__ import annotations

import re
from typing import List

_PORT_KEY_RE = re.compile(r"(?i)(^|_)port(_|$)|host_port|published_port|_PORT$")
_SECRET_KEY_RE = re.compile(r"(?i)password|secret|token|api_key|authorization")


def _looks_port_key(key: str) -> bool:
    if _SECRET_KEY_RE.search(key):
        return False
    if key.endswith("_PORT"):
        return True
    return bool(_PORT_KEY_RE.search(key))


def _parse_port_value(raw: str) -> int | None:
    value = raw.strip().strip('"').strip("'")
    if not value.isdigit():
        return None
    port = int(value)
    if 1 <= port <= 65535:
        return port
    return None


def parse_dotenv_ports(text: str) -> List[dict]:
    results: List[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not _looks_port_key(key):
            continue
        port = _parse_port_value(value)
        if port is None:
            continue
        results.append({"key": key, "port": port})
    return results
