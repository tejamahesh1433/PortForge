from __future__ import annotations

import re
from typing import Any

_DENY_KEYS = frozenset(
    {
        "authorization",
        "admin_bootstrap",
        "admin_token",
        "bootstrap_token",
        "enrollment_token",
        "agent_credential",
        "agent_token",
        "database_password",
        "db_password",
        "password",
        "secret",
        "api_key",
        "bearer",
    }
)

_STRING_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"enroll-[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"^secret$", re.IGNORECASE),
    re.compile(r"^enroll-tok$", re.IGNORECASE),
)

_REDACTED = "[REDACTED]"


def _scrub_string(value: str) -> str:
    scrubbed = value
    for pattern in _STRING_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    if scrubbed != value:
        return scrubbed
    lower = value.lower()
    if any(token in lower for token in ("bearer ", "enroll-", "password=", "api_key=")):
        return _REDACTED
    return value


def scrub_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _DENY_KEYS:
                out[key] = _REDACTED
            else:
                out[key] = scrub_secrets(value)
        return out
    if isinstance(obj, list):
        return [scrub_secrets(item) for item in obj]
    if isinstance(obj, str):
        return _scrub_string(obj)
    return obj


def scrub_text(value: str) -> str:
    """Scrub a free-form string (e.g. central_error) the same way as structured values."""
    return _scrub_string(value)