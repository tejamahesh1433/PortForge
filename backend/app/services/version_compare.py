"""Semantic version comparison using ``packaging.version.Version``.

Always use this module for version comparisons -- never lexicographic string
comparison, which breaks on e.g. "1.10.0" < "1.9.0".
"""
from __future__ import annotations

from packaging.version import InvalidVersion, Version


def compare(a: str, b: str) -> int:
    """Return -1 if a < b, 0 if a == b, 1 if a > b.

    Returns 0 for any unparseable version pair rather than raising -- callers
    that care about parseability should use ``update_availability`` which
    returns UNKNOWN in those cases.
    """
    try:
        va, vb = Version(a), Version(b)
    except InvalidVersion:
        return 0
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0


def update_availability(agent_version: str | None, target_version: str | None) -> str:
    """Compute update availability for a host.

    Returns one of: CURRENT | UPDATE_AVAILABLE | UNKNOWN | UNSUPPORTED.

    - UNKNOWN: either version is missing or unparseable
    - CURRENT: agent == target
    - UPDATE_AVAILABLE: agent < target
    - UNSUPPORTED: agent > target (agent is newer than blessed target)
    """
    if not agent_version or not target_version:
        return "UNKNOWN"
    try:
        av = Version(agent_version)
        tv = Version(target_version)
    except InvalidVersion:
        return "UNKNOWN"
    if av == tv:
        return "CURRENT"
    if av < tv:
        return "UPDATE_AVAILABLE"
    return "UNSUPPORTED"
