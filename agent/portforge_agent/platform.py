"""Dynamic operating-system detection for the PortForge agent.

Nothing in this module hardcodes a machine name, path, or port. All values
are derived at runtime from the Python standard library so the agent behaves
correctly on whatever computer it is installed on.
"""
from __future__ import annotations

import logging
import platform as _stdlib_platform
import socket
from enum import Enum

logger = logging.getLogger("portforge_agent.platform")


class OperatingSystem(str, Enum):
    """Normalized operating system identifier used throughout PortForge."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


def detect_os() -> OperatingSystem:
    """Detect the current operating system using ``platform.system()``.

    Returns ``OperatingSystem.UNKNOWN`` instead of raising when running on an
    operating system PortForge does not yet support, so callers can decide
    how to degrade gracefully.
    """
    system = _stdlib_platform.system().strip().lower()
    if system == "windows":
        return OperatingSystem.WINDOWS
    if system == "darwin":
        return OperatingSystem.MACOS
    if system == "linux":
        return OperatingSystem.LINUX
    return OperatingSystem.UNKNOWN


def get_os_version() -> str:
    """Return a human-readable OS version string, e.g. ``'Windows-10-10.0.26200'``."""
    version = _stdlib_platform.platform(aliased=True, terse=False)
    return version or "unknown"


def get_hostname() -> str:
    """Return this machine's hostname, resolved dynamically (never hardcoded)."""
    try:
        return socket.gethostname()
    except OSError:
        return "unknown-host"


def get_host_id() -> str:
    """Return a stable identifier for this host.

    Phase 5: a persisted UUID (see identity.py), generated once and reused
    across restarts and hostname changes -- this is what makes it safe to
    use as the primary key the central server aggregates by. Every caller
    already treated this as an opaque string (write path and read path
    both call this function fresh, and nothing compares it against a
    literal hostname), so switching the underlying value is safe for
    existing Phase 1-4 behavior as long as existing local reservations are
    migrated -- see reservations/migration.py.

    Falls back to the hostname only if the identity file genuinely cannot
    be created (e.g. a read-only home directory) -- extremely rare, and
    logged rather than silently degrading forever.
    """
    from . import identity  # local import: avoids a module-load-time cycle with paths.py

    try:
        return identity.get_or_create_host_identity().host_id
    except identity.HostIdentityError:
        raise  # a malformed identity file must fail loudly, never fall back silently
    except Exception as exc:  # pragma: no cover - only unexpected environment failures
        logger.warning("Could not establish persistent host identity, falling back to hostname: %s", exc)
        return get_hostname()
