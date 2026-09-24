"""Agent-side constrained Compose deployment (Phase 18).

Exposes `process_pending_deployment` as the runtime entry point.
"""
from .handler import process_pending_deployment

__all__ = ["process_pending_deployment"]
