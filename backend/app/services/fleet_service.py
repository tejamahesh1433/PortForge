"""Fleet intelligence read service (Phase 9).

Provides the operator-facing fleet view: all hosts enriched with
update_availability, active upgrade summary, and Phase 9 runtime fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.host import Host
from ..repositories.upgrade_repository import UpgradeRepository
from ..schemas.health_status import derive_health_state
from ..schemas.host import FleetHostOut
from ..schemas.upgrade import UpgradeSummary
from ..services import compatibility_service
from ..services.upgrade_status_service import build_upgrade_status
from ..services.version_compare import update_availability


def _build_fleet_out(
    db: Session,
    host: Host,
    now: datetime,
    active_upgrade_map: dict[uuid.UUID, object],
) -> FleetHostOut:
    settings = get_settings()
    health_state, health_reason, age_seconds = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now,
    )
    avail = update_availability(
        getattr(host, "agent_version", None), settings.update_target_version
    )

    active_upgrade = active_upgrade_map.get(host.id)
    active_upgrade_out = None
    if active_upgrade is not None:
        try:
            status = build_upgrade_status(db, active_upgrade, host=host, now=now)
            active_upgrade_out = UpgradeSummary(
                id=active_upgrade.id,
                state=active_upgrade.state,
                target_version=active_upgrade.target_version,
                progress_status=status.progress_status,
                waiting_reason=status.waiting_reason,
                failure_code=status.failure_code,
                explanation=status.explanation,
                operator_actions=status.operator_actions,
            )
        except Exception:
            active_upgrade_out = UpgradeSummary(
                id=active_upgrade.id,
                state=active_upgrade.state,
                target_version=active_upgrade.target_version,
            )

    return FleetHostOut(
        id=host.id,
        hostname=host.hostname,
        display_name=host.display_name,
        operating_system=host.operating_system,
        os_version=host.os_version,
        architecture=host.architecture,
        agent_version=host.agent_version,
        docker_available=host.docker_available,
        first_seen=host.first_seen,
        status=host.status,
        health_state=health_state.value,
        health_reason=health_reason.value,
        age_seconds=age_seconds,
        protocol_version=host.protocol_version,
        protocol_compatibility=compatibility_service.evaluate_protocol_compatibility(
            host.protocol_version
        ),
        lifecycle_state=host.lifecycle_state or "ACTIVE",
        decommissioned_at=host.decommissioned_at,
        decommission_reason=host.decommission_reason,
        last_heartbeat=host.last_seen,
        last_sync=host.last_scan_observed_at,
        update_availability=avail,
        target_version=settings.update_target_version,
        contract_version=getattr(host, "contract_version", None),
        python_version=getattr(host, "python_version", None),
        last_error=getattr(host, "last_error", None),
        active_upgrade=active_upgrade_out,
    )


def get_fleet(
    db: Session,
    lifecycle_state: Optional[str] = None,
    health_state_filter: Optional[str] = None,
    update_availability_filter: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[FleetHostOut], int]:
    """Return a filtered, paginated fleet view."""
    stmt = select(Host)
    if lifecycle_state:
        stmt = stmt.where(Host.lifecycle_state == lifecycle_state)
    if q:
        stmt = stmt.where(Host.hostname.ilike(f"%{q}%"))
    stmt = stmt.order_by(Host.last_seen.desc())

    all_hosts = db.execute(stmt).scalars().all()

    now = datetime.now(timezone.utc)
    upgrade_repo = UpgradeRepository(db)
    host_ids = [h.id for h in all_hosts]
    active_upgrade_map = upgrade_repo.get_active_summaries_for_hosts(host_ids)

    fleet_items = [_build_fleet_out(db, h, now, active_upgrade_map) for h in all_hosts]

    # In-Python filters for derived fields
    if health_state_filter:
        fleet_items = [f for f in fleet_items if f.health_state == health_state_filter]
    if update_availability_filter:
        fleet_items = [f for f in fleet_items if f.update_availability == update_availability_filter]

    total = len(fleet_items)
    return fleet_items[offset : offset + limit], total


def get_fleet_host(db: Session, host_id: uuid.UUID) -> Optional[FleetHostOut]:
    """Return the fleet view for a single host."""
    from ..repositories.host_repository import HostRepository

    host = HostRepository(db).get(host_id)
    if host is None:
        return None

    now = datetime.now(timezone.utc)
    upgrade_repo = UpgradeRepository(db)
    active_upgrade_map = upgrade_repo.get_active_summaries_for_hosts([host_id])

    return _build_fleet_out(db, host, now, active_upgrade_map)
