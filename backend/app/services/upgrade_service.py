"""Safe agent upgrade management service (Phase 10).

Every operation here enforces the strict authorization and state-machine
rules described in docs/design/agent-upgrade-management.md. Nothing here
executes arbitrary code on a host -- it only creates, transitions, and
cancels structured, narrowly-scoped upgrade records.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host_upgrade import HostUpgrade
from ..repositories.host_repository import HostRepository
from ..repositories.upgrade_repository import UpgradeRepository
from ..schemas.health_status import HostHealthState, derive_health_state
from ..services.version_compare import compare as version_compare

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}
NON_TERMINAL_STATES = {
    "APPROVED", "WAITING_FOR_AGENT", "DOWNLOADING", "VERIFYING",
    "INSTALLING", "RESTARTING", "VERIFYING_HEALTH",
}

# Ordered for forward-only transition validation (agent-reportable states)
_AGENT_STATES_ORDER = [
    "APPROVED",
    "WAITING_FOR_AGENT",
    "DOWNLOADING",
    "VERIFYING",
    "INSTALLING",
    "RESTARTING",
    "VERIFYING_HEALTH",
    "SUCCEEDED",
]
_AGENT_SETTABLE = set(_AGENT_STATES_ORDER[2:])  # DOWNLOADING … SUCCEEDED + FAILED
_AGENT_SETTABLE.add("FAILED")


class UpgradeError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class UpgradeNotFoundError(UpgradeError):
    def __init__(self, message: str = "Upgrade not found."):
        super().__init__(message, status_code=404)


class UpgradeOwnershipError(UpgradeError):
    def __init__(self, message: str = "Upgrade does not belong to this host."):
        super().__init__(message, status_code=403)


def create_upgrade(
    db: Session,
    host_id: uuid.UUID,
    target_version: str,
    artifact_url: str,
    artifact_sha256: str,
    artifact_filename: Optional[str] = None,
    request_id: Optional[str] = None,
    created_by: str = "admin",
    previous_artifact_url: Optional[str] = None,
    previous_artifact_sha256: Optional[str] = None,
) -> HostUpgrade:
    """Create an approved upgrade for the given host.

    Enforces:
    - Host must be ACTIVE (not DECOMMISSIONED)
    - Target version must not be older than current agent version
    - Artifact coordinates must match Central's configured artifact when the
      target version matches the configured target version
    - At most one non-terminal upgrade per host (unless idempotent request_id)
    """
    host_repo = HostRepository(db)
    upgrade_repo = UpgradeRepository(db)

    host = host_repo.get(host_id)
    if host is None:
        raise UpgradeNotFoundError("Host not found.")

    if (host.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
        raise UpgradeError("Cannot create upgrade for a decommissioned host.", status_code=409)

    # Idempotent: same request_id for same host returns the existing row
    if request_id:
        existing = upgrade_repo.get_by_request_id_for_host(host_id, request_id)
        if existing is not None:
            return existing

    # Reject downgrade (target < current agent version)
    if host.agent_version:
        try:
            cmp = version_compare(target_version, host.agent_version)
            if cmp < 0:
                raise UpgradeError(
                    f"Target version {target_version!r} is older than current "
                    f"agent version {host.agent_version!r}. "
                    "Use the rollback endpoint for intentional downgrades.",
                    status_code=422,
                )
        except UpgradeError:
            raise
        except Exception:
            # Unparseable version strings: still allow (operator's responsibility)
            pass

    # Validate artifact matches configured settings for this version
    settings = get_settings()
    if (
        settings.update_target_version
        and settings.update_artifact_url
        and settings.update_artifact_sha256
        and target_version == settings.update_target_version
    ):
        if (
            artifact_url != settings.update_artifact_url
            or artifact_sha256 != settings.update_artifact_sha256
        ):
            raise UpgradeError(
                "Artifact URL or SHA256 does not match the configured artifact "
                "for this target version.",
                status_code=422,
            )

    # One non-terminal upgrade per host
    non_terminal = upgrade_repo.get_non_terminal_for_host(host_id)
    if non_terminal is not None:
        raise UpgradeError(
            f"Host already has a pending upgrade in state {non_terminal.state!r}. "
            "Wait for it to reach a terminal state before creating another.",
            status_code=409,
        )

    # Initial state depends on whether host is currently reachable
    now = datetime.now(timezone.utc)
    health_state, _, _ = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now,
    )
    initial_state = "WAITING_FOR_AGENT" if health_state == HostHealthState.OFFLINE else "APPROVED"

    upgrade = HostUpgrade(
        id=uuid.uuid4(),
        host_id=host_id,
        request_id=request_id,
        state=initial_state,
        target_version=target_version,
        artifact_url=artifact_url,
        artifact_sha256=artifact_sha256,
        artifact_filename=artifact_filename,
        previous_version=host.agent_version,
        previous_artifact_url=previous_artifact_url,
        previous_artifact_sha256=previous_artifact_sha256,
        failure_reason=None,
        approved_at=now if initial_state == "APPROVED" else None,
        claimed_at=None,
        completed_at=None,
        created_by=created_by,
    )
    db.add(upgrade)
    db.flush()
    return upgrade


def update_upgrade_status(
    db: Session,
    host_id: uuid.UUID,
    upgrade_id: uuid.UUID,
    new_state: str,
    failure_reason: Optional[str] = None,
    reported_version: Optional[str] = None,
) -> HostUpgrade:
    """Agent-only: report progress or result for a specific upgrade.

    Validates that:
    - The upgrade belongs to this host
    - The new state is agent-settable
    - The transition is forward-only (or to FAILED from any non-terminal)
    - SUCCEEDED requires reported_version == target_version
    """
    upgrade_repo = UpgradeRepository(db)
    upgrade = upgrade_repo.get(upgrade_id)
    if upgrade is None:
        raise UpgradeNotFoundError()

    if upgrade.host_id != host_id:
        raise UpgradeOwnershipError()

    if upgrade.state in TERMINAL_STATES:
        raise UpgradeError(
            f"Upgrade is already in terminal state {upgrade.state!r}.",
            status_code=409,
        )

    if new_state not in _AGENT_SETTABLE:
        raise UpgradeError(
            f"State {new_state!r} is not agent-settable. "
            f"Valid states: {sorted(_AGENT_SETTABLE)}",
            status_code=422,
        )

    # Forward-only for non-FAILED transitions
    if new_state != "FAILED":
        current_order = _AGENT_STATES_ORDER.index(upgrade.state) if upgrade.state in _AGENT_STATES_ORDER else -1
        new_order = _AGENT_STATES_ORDER.index(new_state) if new_state in _AGENT_STATES_ORDER else -1
        if new_order <= current_order:
            raise UpgradeError(
                f"Cannot transition from {upgrade.state!r} to {new_state!r}: "
                "transitions must move forward.",
                status_code=422,
            )

    # SUCCEEDED requires the agent to confirm it's actually running the target version
    if new_state == "SUCCEEDED":
        if not reported_version or reported_version != upgrade.target_version:
            raise UpgradeError(
                f"SUCCEEDED requires reported_version == target_version "
                f"({upgrade.target_version!r}), got {reported_version!r}.",
                status_code=422,
            )

    now = datetime.now(timezone.utc)

    # Set claimed_at on first agent status report
    if upgrade.claimed_at is None:
        upgrade.claimed_at = now

    upgrade.state = new_state

    if new_state == "FAILED":
        upgrade.failure_reason = failure_reason
        upgrade.completed_at = now
        # Surface the failure on the host row for fleet diagnostics
        host_repo = HostRepository(db)
        host = host_repo.get(host_id)
        if host is not None:
            host.last_error = failure_reason or "Upgrade failed without reason."

    elif new_state == "SUCCEEDED":
        upgrade.completed_at = now
        # Clear last_error on success
        host_repo = HostRepository(db)
        host = host_repo.get(host_id)
        if host is not None:
            host.last_error = None

    db.flush()
    return upgrade


def rollback_upgrade(
    db: Session,
    upgrade_id: uuid.UUID,
    created_by: str = "admin",
) -> HostUpgrade:
    """Create a new upgrade targeting the previous artifact metadata.

    Requires the original upgrade to have previous_version,
    previous_artifact_url, and previous_artifact_sha256 set.
    """
    upgrade_repo = UpgradeRepository(db)
    original = upgrade_repo.get(upgrade_id)
    if original is None:
        raise UpgradeNotFoundError()

    if not original.previous_version:
        raise UpgradeError(
            "Cannot rollback: previous version metadata not available on this upgrade.",
            status_code=422,
        )
    if not original.previous_artifact_url or not original.previous_artifact_sha256:
        raise UpgradeError(
            "Cannot rollback: previous artifact URL/SHA256 not stored on this upgrade. "
            "Provide previous_artifact_url and previous_artifact_sha256 when creating "
            "upgrades to enable rollback.",
            status_code=422,
        )

    host_repo = HostRepository(db)
    host = host_repo.get(original.host_id)
    if host is None:
        raise UpgradeError("Host not found.", status_code=404)

    if (host.lifecycle_state or "ACTIVE") == "DECOMMISSIONED":
        raise UpgradeError("Cannot rollback for a decommissioned host.", status_code=409)

    # Reject if there's already an active upgrade (other than the original being rolled back)
    non_terminal = upgrade_repo.get_non_terminal_for_host(original.host_id)
    if non_terminal is not None and non_terminal.id != original.id:
        raise UpgradeError(
            f"Host already has a pending upgrade in state {non_terminal.state!r}.",
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    settings = get_settings()
    health_state, _, _ = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now,
    )
    initial_state = "WAITING_FOR_AGENT" if health_state == HostHealthState.OFFLINE else "APPROVED"

    rollback_upgrade_row = HostUpgrade(
        id=uuid.uuid4(),
        host_id=original.host_id,
        request_id=None,
        state=initial_state,
        target_version=original.previous_version,
        artifact_url=original.previous_artifact_url,
        artifact_sha256=original.previous_artifact_sha256,
        artifact_filename=None,
        previous_version=host.agent_version,
        previous_artifact_url=original.artifact_url,
        previous_artifact_sha256=original.artifact_sha256,
        failure_reason=None,
        approved_at=now if initial_state == "APPROVED" else None,
        claimed_at=None,
        completed_at=None,
        created_by=created_by,
    )
    db.add(rollback_upgrade_row)

    # Mark the original as ROLLED_BACK (whether terminal or not)
    if original.state not in TERMINAL_STATES:
        original.state = "ROLLED_BACK"
        original.completed_at = original.completed_at or now
    elif original.state == "SUCCEEDED":
        original.state = "ROLLED_BACK"
        original.completed_at = original.completed_at or now

    db.flush()
    return rollback_upgrade_row


def fail_active_upgrades_for_host(
    db: Session, host_id: uuid.UUID, reason: str, now: datetime
) -> int:
    """Cancel any non-terminal upgrades for a host (called on decommission)."""
    return UpgradeRepository(db).fail_active_for_host(host_id, reason, now)
