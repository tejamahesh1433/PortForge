"""v1.1-A: the single canonical source of the installed package's own
version, plus the agent<->Central wire-protocol version.

Before this module existed, "1.0.0" was hardcoded independently in FIVE
places beyond `agent/pyproject.toml` itself: `__init__.py::__version__`,
`central_sync.py::AGENT_VERSION`, an inline literal in
`runtime/agent.py`'s heartbeat call, and two more inline literals in
`cli_agent.py` (`agent enroll` and `agent test`) -- undeclared,
independent sources of truth that happened to agree by coincidence, not
by any enforced link (the architecture audit's own scan found only the
first two of these; the rest turned up during this increment's
implementation -- see docs/v1.1/architecture-audit.md §2 and
docs/v1.1/version-compatibility.md). `get_portforge_version()` is now the
only place any code reads "what version am I" from; nothing else in this
package should hardcode a version string.

`PROTOCOL_VERSION` is a SEPARATE, small integer -- the shape of the
agent<->Central enroll/heartbeat wire contract, not the package's own
semantic version. See docs/v1.1/version-compatibility.md for why these
are deliberately not the same number: a package release that only fixes
a collector bug doesn't change the wire contract at all, and conflating
the two would produce constant false-positive compatibility warnings.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Bumped only when the agent<->Central enroll/heartbeat request or
# response shape changes in a way an older counterpart couldn't safely
# ignore (a new REQUIRED field, a removed field, a changed meaning for an
# existing field). A purely additive change (a new optional field) does
# NOT bump this -- mirrors how manifest.py's SUPPORTED_MANIFEST_VERSION
# treats `config:` as additive without a version bump.
PROTOCOL_VERSION = 1


def get_portforge_version() -> str:
    try:
        return _pkg_version("portforge-agent")
    except PackageNotFoundError:  # pragma: no cover - only if run from an unpackaged checkout
        return "unknown"


COMPATIBLE = "compatible"
WARNING = "warning"
UNKNOWN = "unknown"


def evaluate_central_protocol_compatibility(central_protocol_version) -> str:
    """The agent-side mirror of
    backend/app/services/compatibility_service.py::evaluate_protocol_compatibility
    -- same three-state vocabulary and logic, independently implemented
    because the agent and backend are separate deployables that can't
    share a Python module (see docs/v1.1/architecture-audit.md §2's
    identical note about DEFAULT_RANGES). `central_protocol_version` is
    whatever `/api/health` reported (or None if unreachable/predates this
    field). Never raises.
    """
    if central_protocol_version is None:
        return UNKNOWN
    if central_protocol_version == PROTOCOL_VERSION:
        return COMPATIBLE
    return WARNING
