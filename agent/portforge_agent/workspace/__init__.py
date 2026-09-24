"""Phase 16: static workspace discovery and coordinated port planning."""

from .discover import discover_workspace, workspace_to_json
from .plan import build_manifest_draft, plan_workspace

__all__ = [
    "discover_workspace",
    "workspace_to_json",
    "plan_workspace",
    "build_manifest_draft",
]
