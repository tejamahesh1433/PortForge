"""Fleet upgrade rollout orchestration (Phase 21).

Uses the shared ``request_id`` as the logical rollout identifier.  No new
tables — all state lives in host_upgrades rows, so the rollout view is
always derived from those rows.

Design:
- create_rollout: validates hosts, creates upgrade rows for canary slice,
  returns aggregate + member dispositions (including skipped hosts that
  have no DB row).
- advance_rollout: re-receives the full host set + policy (since policy is
  not persisted), runs stuck recovery, enforces canary/stop rules, creates
  additional rows up to remaining concurrency slots.
- get_rollout: aggregate from DB rows only (skipped hosts not visible here).
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
from ..schemas.upgrade import RolloutMemberOut, RolloutOut
from ..services.upgrade_recovery_service import recover_stuck_upgrades
from ..services.upgrade_service import (
    UpgradeError,
    create_upgrade,
)

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}

# Disposition strings
_D_UPGRADING = "upgrading"
_D_SKIPPED_CURRENT = "skipped_current"
_D_SKIPPED_OFFLINE = "skipped_offline"
_D_SKIPPED_DECOMMISSIONED = "skipped_decommissioned"
_D_OFFLINE_FAILED = "offline_failed"


def _derive_rollout_status(
    rows: list[HostUpgrade],
    *,
    has_failed: bool,
    stop_on_failure: bool,
    canary_complete: bool,
    canary_has_failed: bool,
) -> str:
    in_flight = [r for r in rows if r.state not in TERMINAL_STATES]
    if in_flight:
        if (stop_on_failure and has_failed) or (stop_on_failure and canary_has_failed):
            return "paused"
        return "active"
    # all terminal
    if has_failed and stop_on_failure:
        return "paused"
    all_succeeded = all(r.state == "SUCCEEDED" for r in rows)
    if all_succeeded:
        return "completed"
    return "paused"


def _eligible_hosts(
    db: Session,
    host_ids: list[uuid.UUID],
    *,
    target_version: str,
    skip_if_current: bool,
    offline_policy: str,
    now: datetime,
) -> tuple[list[uuid.UUID], list[RolloutMemberOut]]:
    """Compute which hosts actually need an upgrade row.

    Returns:
        (eligible_host_ids, skip_members)

    eligible_host_ids: sorted host UUIDs that should get upgrade rows
    skip_members: RolloutMemberOut entries for skipped hosts (no row)
    """
    settings = get_settings()
    host_repo = HostRepository(db)
    # Sort for deterministic canary selection
    sorted_ids = sorted(host_ids)

    eligible: list[uuid.UUID] = []
    skips: list[RolloutMemberOut] = []

    for hid in sorted_ids:
        host = host_repo.get(hid)
        if host is None:
            # Unknown host — treat same as decommissioned
            skips.append(RolloutMemberOut(host_id=hid, disposition=_D_SKIPPED_DECOMMISSIONED))
            continue

        lc = host.lifecycle_state or "ACTIVE"
        if lc == "DECOMMISSIONED":
            skips.append(RolloutMemberOut(host_id=hid, disposition=_D_SKIPPED_DECOMMISSIONED))
            continue

        # Check online status
        health_state, _, _ = derive_health_state(
            host.last_seen,
            settings.host_stale_after_seconds,
            settings.host_offline_after_seconds,
            now,
        )
        is_offline = health_state == HostHealthState.OFFLINE

        if is_offline:
            if offline_policy == "SKIP":
                skips.append(RolloutMemberOut(host_id=hid, disposition=_D_SKIPPED_OFFLINE))
                continue
            elif offline_policy == "FAIL":
                skips.append(RolloutMemberOut(host_id=hid, disposition=_D_OFFLINE_FAILED))
                continue
            # WAIT falls through: create_upgrade will set WAITING_FOR_AGENT

        if skip_if_current and host.agent_version == target_version:
            skips.append(RolloutMemberOut(host_id=hid, disposition=_D_SKIPPED_CURRENT))
            continue

        eligible.append(hid)

    return eligible, skips


def create_rollout(
    db: Session,
    *,
    host_ids: list[uuid.UUID],
    target_version: str,
    artifact_url: str,
    artifact_sha256: str,
    artifact_filename: Optional[str] = None,
    canary_size: int = 0,
    concurrency: int = 1,
    stop_on_failure: bool = True,
    offline_policy: str = "WAIT",
    skip_if_current: bool = True,
    request_id: Optional[str] = None,
    previous_artifact_url: Optional[str] = None,
    previous_artifact_sha256: Optional[str] = None,
) -> RolloutOut:
    """Create upgrade rows for the first canary_size eligible hosts.

    canary_size=0 means create for ALL eligible hosts (up to concurrency).
    The request_id becomes the rollout's logical key.
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    now = datetime.now(timezone.utc)
    eligible, skip_members = _eligible_hosts(
        db, host_ids,
        target_version=target_version,
        skip_if_current=skip_if_current,
        offline_policy=offline_policy,
        now=now,
    )

    # Canary slice: 0 means all eligible
    if canary_size == 0 or canary_size >= len(eligible):
        first_batch = eligible
    else:
        first_batch = eligible[:canary_size]

    # Further bound by concurrency
    first_batch = first_batch[:concurrency]

    members: list[RolloutMemberOut] = list(skip_members)
    upgrade_repo = UpgradeRepository(db)

    for hid in first_batch:
        # Idempotent: if this host already has a row for this request_id, use it
        existing = upgrade_repo.get_by_request_id_for_host(hid, request_id)
        if existing is not None:
            members.append(RolloutMemberOut(
                host_id=hid,
                disposition=_D_UPGRADING,
                upgrade_id=existing.id,
                state=existing.state,
            ))
            continue

        try:
            upgrade = create_upgrade(
                db,
                host_id=hid,
                target_version=target_version,
                artifact_url=artifact_url,
                artifact_sha256=artifact_sha256,
                artifact_filename=artifact_filename,
                request_id=request_id,
                created_by="admin",
                previous_artifact_url=previous_artifact_url,
                previous_artifact_sha256=previous_artifact_sha256,
            )
            members.append(RolloutMemberOut(
                host_id=hid,
                disposition=_D_UPGRADING,
                upgrade_id=upgrade.id,
                state=upgrade.state,
            ))
        except UpgradeError as exc:
            # Non-fatal per host (e.g. already has non-terminal upgrade):
            # record as failed disposition without aborting the rollout.
            members.append(RolloutMemberOut(
                host_id=hid,
                disposition=_D_OFFLINE_FAILED,
            ))

    # Build aggregate from DB rows for this request_id
    rows = list(upgrade_repo.list_by_request_id(request_id))
    return _build_rollout_out(request_id, rows, members, stop_on_failure=stop_on_failure)


def _canary_host_ids(
    host_ids: list[uuid.UUID],
    eligible: list[uuid.UUID],
    rows: list[HostUpgrade],
    canary_size: int,
) -> set[uuid.UUID]:
    """Stable canary set across create/advance.

    Uses sorted(host_ids) order, including hosts that already have a rollout
    row (so a completed canary is not dropped when skip_if_current excludes it
    from the live eligible list).
    """
    if canary_size <= 0:
        return set()
    hosted = {r.host_id for r in rows}
    eligible_set = set(eligible)
    pool = [h for h in sorted(host_ids) if h in eligible_set or h in hosted]
    return set(pool[:canary_size])


def _build_members_from_rows(
    rows: list[HostUpgrade],
    skip_members: list[RolloutMemberOut],
) -> list[RolloutMemberOut]:
    hosted = {r.host_id for r in rows}
    row_members = [
        RolloutMemberOut(
            host_id=r.host_id,
            disposition=_D_UPGRADING,
            upgrade_id=r.id,
            state=r.state,
        )
        for r in rows
    ]
    # Do not duplicate hosts that already have upgrade rows.
    skips = [m for m in skip_members if m.host_id not in hosted]
    return skips + row_members


def advance_rollout(
    db: Session,
    request_id: str,
    *,
    host_ids: list[uuid.UUID],
    target_version: str,
    artifact_url: str,
    artifact_sha256: str,
    artifact_filename: Optional[str] = None,
    canary_size: int = 0,
    concurrency: int = 1,
    stop_on_failure: bool = True,
    offline_policy: str = "WAIT",
    skip_if_current: bool = True,
    previous_artifact_url: Optional[str] = None,
    previous_artifact_sha256: Optional[str] = None,
) -> RolloutOut:
    """Advance the rollout by creating upgrade rows for the next eligible batch.

    Steps:
    1. Run stuck recovery on all existing rows for this request_id.
    2. If stop_on_failure and any row is FAILED → return paused (no new rows).
    3. Compute canary hosts (same stable sort as create_rollout).
    4. If any canary host is non-terminal → wait (not all canary succeeded).
    5. Count in-flight; slots = max(0, concurrency - in_flight).
    6. Create upgrade rows for next eligible hosts up to slots.
    """
    now = datetime.now(timezone.utc)
    upgrade_repo = UpgradeRepository(db)

    # Step 1: recover stuck upgrades for this rollout
    all_rows = list(upgrade_repo.list_by_request_id(request_id))
    if all_rows:
        recover_stuck_upgrades(db, now=now)
        # Refresh after recovery
        db.expire_all()
        all_rows = list(upgrade_repo.list_by_request_id(request_id))

    # Step 2: stop_on_failure check
    has_failed = any(r.state == "FAILED" for r in all_rows)
    if stop_on_failure and has_failed:
        eligible, skip_members = _eligible_hosts(
            db, host_ids,
            target_version=target_version,
            skip_if_current=skip_if_current,
            offline_policy=offline_policy,
            now=now,
        )
        members = _build_members_from_rows(all_rows, skip_members)
        return _build_rollout_out(request_id, all_rows, members, stop_on_failure=stop_on_failure)

    # Step 3 & 4: canary check
    eligible, skip_members = _eligible_hosts(
        db, host_ids,
        target_version=target_version,
        skip_if_current=skip_if_current,
        offline_policy=offline_policy,
        now=now,
    )

    if canary_size > 0:
        canary_hosts = _canary_host_ids(host_ids, eligible, all_rows, canary_size)
        canary_rows = [r for r in all_rows if r.host_id in canary_hosts]
        # Canary hosts that still need a row and have none → not started yet; wait only
        # applies once canary rows exist and are non-terminal.
        canary_non_terminal = [r for r in canary_rows if r.state not in TERMINAL_STATES]
        canary_failed = [r for r in canary_rows if r.state == "FAILED"]
        canary_missing = [h for h in canary_hosts if h not in {r.host_id for r in all_rows}]

        if canary_missing or canary_non_terminal:
            # Canary still in progress or not yet created — wait
            members = _build_members_from_rows(all_rows, skip_members)
            return _build_rollout_out(request_id, all_rows, members, stop_on_failure=stop_on_failure)

        if stop_on_failure and canary_failed:
            members = _build_members_from_rows(all_rows, skip_members)
            return _build_rollout_out(request_id, all_rows, members, stop_on_failure=stop_on_failure)

    # Step 5: compute slots
    in_flight = [r for r in all_rows if r.state not in TERMINAL_STATES]
    slots = max(0, concurrency - len(in_flight))

    if slots == 0:
        members = _build_members_from_rows(all_rows, skip_members)
        return _build_rollout_out(request_id, all_rows, members, stop_on_failure=stop_on_failure)

    # Step 6: find hosts without a row for this request_id
    hosted_ids = {r.host_id for r in all_rows}
    remaining = [hid for hid in eligible if hid not in hosted_ids]

    new_members: list[RolloutMemberOut] = []
    for hid in remaining[:slots]:
        try:
            upgrade = create_upgrade(
                db,
                host_id=hid,
                target_version=target_version,
                artifact_url=artifact_url,
                artifact_sha256=artifact_sha256,
                artifact_filename=artifact_filename,
                request_id=request_id,
                created_by="admin",
                previous_artifact_url=previous_artifact_url,
                previous_artifact_sha256=previous_artifact_sha256,
            )
            new_members.append(RolloutMemberOut(
                host_id=hid,
                disposition=_D_UPGRADING,
                upgrade_id=upgrade.id,
                state=upgrade.state,
            ))
        except UpgradeError:
            new_members.append(RolloutMemberOut(
                host_id=hid,
                disposition=_D_OFFLINE_FAILED,
            ))

    # Refresh rows after new inserts
    db.expire_all()
    all_rows = list(upgrade_repo.list_by_request_id(request_id))
    members = _build_members_from_rows(all_rows, skip_members)
    return _build_rollout_out(request_id, all_rows, members, stop_on_failure=stop_on_failure)


def get_rollout(db: Session, request_id: str) -> RolloutOut:
    """Return aggregate rollout status from DB rows only.

    Note: skipped hosts (those with no DB row) are not included in members
    here.  Clients should use the create/advance response for full membership.
    """
    upgrade_repo = UpgradeRepository(db)
    # Run stuck recovery first so counts are always current
    recover_stuck_upgrades(db)
    db.expire_all()
    rows = list(upgrade_repo.list_by_request_id(request_id))
    members = [
        RolloutMemberOut(
            host_id=r.host_id,
            disposition=_D_UPGRADING,
            upgrade_id=r.id,
            state=r.state,
        )
        for r in rows
    ]
    return _build_rollout_out(request_id, rows, members, stop_on_failure=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_rollout_out(
    request_id: str,
    rows: list[HostUpgrade],
    members: list[RolloutMemberOut],
    *,
    stop_on_failure: bool,
) -> RolloutOut:
    succeeded = sum(1 for r in rows if r.state == "SUCCEEDED")
    failed = sum(1 for r in rows if r.state == "FAILED")
    in_flight = sum(1 for r in rows if r.state not in TERMINAL_STATES and r.state != "WAITING_FOR_AGENT")
    waiting = sum(1 for r in rows if r.state == "WAITING_FOR_AGENT")
    skipped = sum(1 for m in members if m.disposition != _D_UPGRADING)
    total = len(members)

    has_failed = failed > 0
    all_terminal = all(r.state in TERMINAL_STATES for r in rows) if rows else True
    non_terminal = [r for r in rows if r.state not in TERMINAL_STATES]

    if non_terminal:
        if stop_on_failure and has_failed:
            status = "paused"
        else:
            status = "active"
    else:
        if stop_on_failure and has_failed:
            status = "paused"
        elif rows and all(r.state == "SUCCEEDED" for r in rows):
            status = "completed"
        elif not rows:
            status = "active"
        else:
            status = "paused"

    return RolloutOut(
        request_id=request_id,
        status=status,
        succeeded=succeeded,
        failed=failed,
        in_flight=in_flight,
        waiting=waiting,
        skipped=skipped,
        total=total,
        members=members,
    )
