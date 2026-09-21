"""PortForge Agent: cross-platform local port discovery engine."""

from .version import get_portforge_version

# v1.1-A: derived from the canonical source (agent/pyproject.toml via
# importlib.metadata), not a fourth independent hardcoded literal -- see
# version.py's own docstring and docs/v1.1/architecture-audit.md §2.
__version__ = get_portforge_version()
