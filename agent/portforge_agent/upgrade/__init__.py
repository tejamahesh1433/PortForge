"""Agent-side safe upgrade execution (Phase 10).

Exposes `run_upgrade` as the single entry point for the runtime loop.
"""
from .handler import run_upgrade, UpgradeValidationError, UpgradeSHA256Error

__all__ = ["run_upgrade", "UpgradeValidationError", "UpgradeSHA256Error"]
