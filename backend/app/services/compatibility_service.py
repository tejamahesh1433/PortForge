"""v1.1-A: agent<->Central protocol-version compatibility evaluation.

Deliberately NOT persisted anywhere -- no Host column, no migration. This
is a pure, stateless function of "what protocol_version did THIS request
report," computed fresh per enroll/heartbeat call and returned in that
same response. See docs/v1.1/version-compatibility.md for the full design
this implements, and docs/v1.1/architecture-audit.md §2 for why this is
deliberately a SEPARATE number from `Settings.version` (the package's own
semantic version) -- a release that only changes unrelated code shouldn't
produce a compatibility warning.

Evaluation is advisory only: it is never used to reject a request, and
callers (api/agents.py) must not gate enroll/heartbeat/allocation/
reservation/workflow behavior on its result -- see api/agents.py's own
handlers, which always proceed regardless of the verdict.
"""
from __future__ import annotations

from typing import Optional

# Bumped only when the enroll/heartbeat request or response SHAPE changes
# in a way an older counterpart couldn't safely ignore (a new required
# field, a removed field, a changed meaning for an existing field) --
# never for a purely additive change. Mirrors the identical reasoning
# already established for agent/portforge_agent/version.py::PROTOCOL_VERSION
# (the same number, from Central's side of the same contract).
PROTOCOL_VERSION = 1

COMPATIBLE = "compatible"
WARNING = "warning"
UNKNOWN = "unknown"


def evaluate_protocol_compatibility(agent_protocol_version: Optional[int]) -> str:
    """`agent_protocol_version` is whatever the request body reported (or
    None if the agent predates this field, or omitted it). Never raises --
    every input, including a nonsensical one, resolves to one of the three
    advisory states.
    """
    if agent_protocol_version is None:
        return UNKNOWN
    if agent_protocol_version == PROTOCOL_VERSION:
        return COMPATIBLE
    return WARNING
