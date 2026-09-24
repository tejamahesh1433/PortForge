"""Typed upgrade status read model (Phase 22).

Derives operator-facing status from existing Host + HostUpgrade rows.
No new persisted columns. Never rewrites historical FAILED → SUCCEEDED.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host import Host
from ..models.host_upgrade import HostUpgrade
from ..repositories.host_repository import HostRepository
from ..repositories.upgrade_repository import UpgradeRepository
from ..schemas.health_status import HostHealthState, derive_health_state
from ..schemas.upgrade import UpgradeStatusOut
from ..services.upgrade_service import TERMINAL_STATES

_CANCEL_SAFE = {"APPROVED", "WAITING_FOR_AGENT", "DOWNLOADING", "VERIFYING"}

# Typed waiting / failure codes (stable strings for UI — not DB enums)
WAITING_FOR_AGENT = "WAITING_FOR_AGENT"
WAITING_FOR_RESTART = "WAITING_FOR_RESTART"
WAITING_FOR_HEALTH = "WAITING_FOR_HEALTH"
HOST_OFFLINE = "HOST_OFFLINE"
HOST_DECOMMISSIONED = "HOST_DECOMMISSIONED"
HOST_STALE = "HOST_STALE"

OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
STUCK_TIMEOUT = "STUCK_TIMEOUT"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
ARTIFACT_INVALID = "ARTIFACT_INVALID"
ARTIFACT_DOWNLOAD_FAILED = "ARTIFACT_DOWNLOAD_FAILED"
UPGRADE_FAILED = "UPGRADE_FAILED"
ROLLOUT_STOPPED = "ROLLOUT_STOPPED"

ACTION_VIEW = "VIEW"
ACTION_RETRY = "RETRY"
ACTION_CANCEL = "CANCEL"
ACTION_ROLLBACK = "ROLLBACK"

# Permanent failure codes — operator may still create a new attempt, but
# automatic retry is never appropriate.
_PERMANENT_CODES = {
    CHECKSUM_MISMATCH,
    ARTIFACT_INVALID,
    HOST_DECOMMISSIONED,
}


def classify_failure_code(failure_reason: Optional[str]) -> Optional[str]:
    """Map stored failure_reason text to a stable typed code."""
    if not failure_reason:
        return None
    fr = failure_reason.strip()
    low = fr.lower()

    if fr == "operator_cancelled" or low.startswith("operator_cancelled"):
        return OPERATOR_CANCELLED
    if fr.startswith("stuck_timeout:"):
        return STUCK_TIMEOUT
    if fr == "host_decommissioned" or "decommission" in low:
        return HOST_DECOMMISSIONED
    if "checksum" in low or "sha256" in low or "sha-256" in low:
        return CHECKSUM_MISMATCH
    if "untrusted" in low or "artifact" in low and ("invalid" in low or "uri" in low or "https" in low):
        return ARTIFACT_INVALID
    if low.startswith("download failed") or "download failed" in low:
        return ARTIFACT_DOWNLOAD_FAILED
    return UPGRADE_FAILED


def failure_summary_for(code: Optional[str], failure_reason: Optional[str], state: str) -> Optional[str]:
    """Short operator-facing explanation (no stack traces)."""
    if state != "FAILED" and not failure_reason:
        return None
    mapping = {
        OPERATOR_CANCELLED: "Upgrade cancelled by operator.",
        STUCK_TIMEOUT: "Upgrade stopped making progress and was marked failed (stuck timeout).",
        HOST_DECOMMISSIONED: "Host was decommissioned; upgrade cancelled.",
        CHECKSUM_MISMATCH: "Upgrade failed: checksum mismatch; automatic retry disabled.",
        ARTIFACT_INVALID: "Upgrade failed: invalid or untrusted artifact; automatic retry disabled.",
        ARTIFACT_DOWNLOAD_FAILED: "Upgrade failed after temporary artifact download failure(s).",
        UPGRADE_FAILED: "Upgrade failed.",
    }
    if code and code in mapping:
        base = mapping[code]
        if code == STUCK_TIMEOUT and failure_reason and ":" in failure_reason:
            stuck_state = failure_reason.split(":", 1)[1]
            return f"{base} Last state: {stuck_state}."
        return base
    if failure_reason:
        # Truncate raw reason for display; never prefer as primary over typed.
        return failure_reason[:240]
    return None


def waiting_reason_for(
    state: str,
    *,
    host_health: str,
    host_lifecycle: str,
) -> Optional[str]:
    if host_lifecycle == "DECOMMISSIONED":
        return HOST_DECOMMISSIONED
    if state == "WAITING_FOR_AGENT":
        return WAITING_FOR_AGENT
    if state == "APPROVED" and host_health == HostHealthState.OFFLINE.value:
        return HOST_OFFLINE
    if state == "RESTARTING":
        return WAITING_FOR_RESTART
    if state == "VERIFYING_HEALTH":
        return WAITING_FOR_HEALTH
    return None


def progress_status_for(state: str, waiting_reason: Optional[str]) -> str:
    if state == "SUCCEEDED":
        return "SUCCEEDED"
    if state == "FAILED":
        return "FAILED"
    if state == "ROLLED_BACK":
        return "ROLLED_BACK"
    if state in ("APPROVED", "WAITING_FOR_AGENT"):
        return "WAITING" if waiting_reason else "PENDING"
    if state in ("DOWNLOADING", "VERIFYING", "INSTALLING"):
        return "IN_PROGRESS"
    if state in ("RESTARTING", "VERIFYING_HEALTH"):
        return "WAITING"
    return "UNKNOWN"


def reconciliation_status_for(
    state: str,
    *,
    current_version: Optional[str],
    target_version: str,
) -> str:
    """Separate runtime truth from control-plane history."""
    matches = bool(current_version and current_version == target_version)
    if state in ("RESTARTING", "VERIFYING_HEALTH"):
        return "PENDING" if not matches else "MATCHED"
    if state == "SUCCEEDED":
        return "MATCHED" if matches else "DIVERGED"
    if state == "FAILED":
        # Historical FAILED must stay FAILED even if runtime later matches target.
        return "RUNTIME_OK_HISTORY_FAILED" if matches else "NOT_APPLICABLE"
    if state == "ROLLED_BACK":
        return "NOT_APPLICABLE"
    return "NOT_APPLICABLE"


def explanation_for(
    *,
    state: str,
    waiting_reason: Optional[str],
    failure_code: Optional[str],
    failure_summary: Optional[str],
    current_version: Optional[str],
    target_version: str,
    reconciliation: str,
    attempt_index: int,
) -> str:
    if state == "SUCCEEDED":
        return f"Upgrade succeeded; runtime reports {current_version or target_version}."
    if state == "ROLLED_BACK":
        return "Upgrade was rolled back; a replacement attempt may exist."
    if state == "FAILED":
        parts = [failure_summary or "Upgrade failed."]
        if reconciliation == "RUNTIME_OK_HISTORY_FAILED":
            parts.append(
                f"Runtime is currently healthy on {current_version} "
                f"(target was {target_version}); historical attempt remains FAILED."
            )
        if attempt_index > 1:
            parts.append(f"Attempt {attempt_index}.")
        return " ".join(parts)
    if waiting_reason == WAITING_FOR_AGENT:
        return "Waiting for agent heartbeat."
    if waiting_reason == HOST_OFFLINE:
        return "Host is offline; upgrade is approved but not yet claimed."
    if waiting_reason == WAITING_FOR_RESTART:
        return "Waiting for post-restart heartbeat at the target version."
    if waiting_reason == WAITING_FOR_HEALTH:
        return "Post-restart heartbeat matched target; finalizing health verification."
    if waiting_reason == HOST_DECOMMISSIONED:
        return "Host is decommissioned."
    if state == "DOWNLOADING":
        return "Downloading artifact (agent may retry transient download errors locally)."
    if state == "VERIFYING":
        return "Verifying artifact checksum."
    if state == "INSTALLING":
        return "Installing package (cancellation not available)."
    if state == "APPROVED":
        return "Upgrade approved; waiting for agent to claim."
    return f"Upgrade in state {state}."


def attempt_index_for(upgrade: HostUpgrade, siblings: list[HostUpgrade]) -> tuple[int, int]:
    """1-based attempt index among same-host same-artifact lineage."""
    same = [
        u
        for u in siblings
        if u.artifact_sha256 == upgrade.artifact_sha256
        and u.target_version == upgrade.target_version
    ]
    if not same:
        return 1, 1

    def _key(u: HostUpgrade):
        ts = u.created_at
        if ts is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    same_sorted = sorted(same, key=_key)
    for i, u in enumerate(same_sorted, start=1):
        if u.id == upgrade.id:
            return i, len(same_sorted)
    return len(same_sorted), len(same_sorted)


def operator_actions_for(
    upgrade: HostUpgrade,
    *,
    host: Host,
    has_non_terminal: bool,
) -> list[str]:
    actions = [ACTION_VIEW]
    lifecycle = host.lifecycle_state or "ACTIVE"
    if lifecycle == "DECOMMISSIONED":
        return actions

    if upgrade.state in _CANCEL_SAFE:
        actions.append(ACTION_CANCEL)

    if upgrade.state == "FAILED" and not has_non_terminal:
        actions.append(ACTION_RETRY)

    if (
        upgrade.state in TERMINAL_STATES
        and not has_non_terminal
        and upgrade.previous_version
        and upgrade.previous_artifact_url
        and upgrade.previous_artifact_sha256
    ):
        actions.append(ACTION_ROLLBACK)

    return actions


def build_upgrade_status(
    db: Session,
    upgrade: HostUpgrade,
    *,
    host: Optional[Host] = None,
    now: Optional[datetime] = None,
) -> UpgradeStatusOut:
    if now is None:
        now = datetime.now(timezone.utc)
    if host is None:
        host = HostRepository(db).get(upgrade.host_id)
    if host is None:
        raise ValueError("Host not found for upgrade.")

    settings = get_settings()
    health_state, _, _ = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now,
    )
    host_health = health_state.value if hasattr(health_state, "value") else str(health_state)
    host_lifecycle = host.lifecycle_state or "ACTIVE"
    current_version = host.agent_version
    target = upgrade.target_version
    runtime_matches = bool(current_version and current_version == target)

    failure_code = classify_failure_code(upgrade.failure_reason) if upgrade.state == "FAILED" else None
    # Also classify for cancelled-looking reasons on FAILED only
    if upgrade.state == "FAILED" and failure_code is None:
        failure_code = UPGRADE_FAILED

    waiting = waiting_reason_for(
        upgrade.state,
        host_health=host_health,
        host_lifecycle=host_lifecycle,
    )
    # Host OFFLINE while RESTARTING must NOT auto-imply FAILED — keep WAITING_FOR_RESTART
    if upgrade.state == "RESTARTING" and host_health == HostHealthState.OFFLINE.value:
        waiting = WAITING_FOR_RESTART
    if upgrade.state == "RESTARTING" and host_health == HostHealthState.STALE.value:
        waiting = WAITING_FOR_RESTART

    progress = progress_status_for(upgrade.state, waiting)
    recon = reconciliation_status_for(
        upgrade.state,
        current_version=current_version,
        target_version=target,
    )
    fsum = failure_summary_for(failure_code, upgrade.failure_reason, upgrade.state)

    siblings = list(UpgradeRepository(db).list_for_host(upgrade.host_id))
    attempt_index, related = attempt_index_for(upgrade, siblings)

    non_terminal = UpgradeRepository(db).get_non_terminal_for_host(upgrade.host_id)
    # For RETRY eligibility: any non-terminal blocks (including self if somehow non-terminal)
    has_any_nt = non_terminal is not None and (
        upgrade.state in TERMINAL_STATES or non_terminal.id != upgrade.id
    )
    # When viewing the active non-terminal row itself, do not treat it as blocking retry of itself
    if non_terminal is not None and non_terminal.id == upgrade.id:
        has_any_nt = False

    actions = operator_actions_for(upgrade, host=host, has_non_terminal=has_any_nt)

    # Retryable = operator may RETRY and failure is not permanent-class
    # (permanent still allows RETRY action — operator explicit — but flag False)
    retryable = ACTION_RETRY in actions and failure_code not in _PERMANENT_CODES

    explanation = explanation_for(
        state=upgrade.state,
        waiting_reason=waiting,
        failure_code=failure_code,
        failure_summary=fsum,
        current_version=current_version,
        target_version=target,
        reconciliation=recon,
        attempt_index=attempt_index,
    )

    return UpgradeStatusOut(
        upgrade_id=upgrade.id,
        host_id=upgrade.host_id,
        hostname=host.hostname,
        current_version=current_version,
        host_health=host_health,
        host_lifecycle=host_lifecycle,
        runtime_matches_target=runtime_matches,
        upgrade_state=upgrade.state,
        target_version=target,
        request_id=upgrade.request_id,
        progress_status=progress,
        waiting_reason=waiting,
        failure_code=failure_code,
        failure_summary=fsum,
        explanation=explanation,
        attempt_index=attempt_index,
        related_attempt_count=related,
        retryable=retryable,
        reconciliation_status=recon,
        operator_actions=actions,
        failure_reason=upgrade.failure_reason,
        updated_at=upgrade.updated_at,
        claimed_at=upgrade.claimed_at,
        completed_at=upgrade.completed_at,
        created_at=upgrade.created_at,
    )


def get_upgrade_status(db: Session, upgrade_id: uuid.UUID) -> UpgradeStatusOut:
    upgrade = UpgradeRepository(db).get(upgrade_id)
    if upgrade is None:
        from ..services.upgrade_service import UpgradeNotFoundError

        raise UpgradeNotFoundError()
    return build_upgrade_status(db, upgrade)


def get_host_upgrade_status(db: Session, host_id: uuid.UUID) -> Optional[UpgradeStatusOut]:
    """Active non-terminal upgrade, else most recent terminal row."""
    host = HostRepository(db).get(host_id)
    if host is None:
        from ..services.upgrade_service import UpgradeNotFoundError

        raise UpgradeNotFoundError("Host not found.")
    repo = UpgradeRepository(db)
    active = repo.get_non_terminal_for_host(host_id)
    if active is not None:
        return build_upgrade_status(db, active, host=host)
    rows = repo.list_for_host(host_id)
    if not rows:
        return None
    return build_upgrade_status(db, rows[0], host=host)


def compact_summary_fields(status: UpgradeStatusOut) -> dict:
    """Fields to merge into fleet UpgradeSummary (additive)."""
    return {
        "progress_status": status.progress_status,
        "waiting_reason": status.waiting_reason,
        "failure_code": status.failure_code,
        "explanation": status.explanation,
        "operator_actions": status.operator_actions,
    }
