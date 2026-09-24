from __future__ import annotations

import re

from .models import TargetsError

_NAMESPACED_RE = re.compile(r"^[^:]+:[^:]+:.+$")


def namespace_request_id(environment: str, target_key: str, logical_request_id: str) -> str:
    env = (environment or "").strip()
    target = (target_key or "").strip()
    logical = (logical_request_id or "").strip()
    if not env or not target or not logical:
        raise TargetsError(
            "INVALID_REQUEST_ID",
            "environment, target_key, and logical_request_id must all be non-empty.",
            details=[{"environment": environment, "target_key": target_key, "logical_request_id": logical_request_id}],
        )
    return f"{env}:{target}:{logical}"


def parse_namespaced_request_id(value: str) -> tuple[str, str, str]:
    text = (value or "").strip()
    if not _NAMESPACED_RE.fullmatch(text):
        raise TargetsError(
            "INVALID_REQUEST_ID",
            "Namespaced request_id must match environment:target:logical_request_id.",
            details=[{"request_id": value}],
        )
    environment, target_key, logical = text.split(":", 2)
    if not environment or not target_key or not logical:
        raise TargetsError(
            "INVALID_REQUEST_ID",
            "Namespaced request_id parts must be non-empty.",
            details=[{"request_id": value}],
        )
    return environment, target_key, logical
