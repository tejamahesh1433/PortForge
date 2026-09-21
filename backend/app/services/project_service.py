from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.activity import ActivityEvent
from ..models.host import Host
from ..models.port_observation import CurrentPortObservation
from ..models.reservation import CentralReservation
from ..schemas.activity import ActivityEventOut
from ..schemas.common import Page
from ..schemas.health_status import derive_health_state
from ..schemas.port import PortObservationOut
from ..schemas.project import ProjectDetailOut, ProjectHostOut, ProjectOut
from ..schemas.reservation import ReservationOut
from .conflict_service import list_conflicts


def _reservation_out(row: CentralReservation) -> ReservationOut:
    return ReservationOut(
        id=row.id,
        host_id=row.host_id,
        port=row.port,
        protocol=row.protocol.value,
        bind_address=row.bind_address,
        project=row.project,
        service=row.service,
        purpose=row.purpose,
        notes=row.notes,
        local_reservation_id=row.local_reservation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _health(host: Host, now: datetime) -> tuple[str, str, int, int | None]:
    settings = get_settings()
    state, reason, age = derive_health_state(
        host.last_seen,
        settings.host_stale_after_seconds,
        settings.host_offline_after_seconds,
        now,
    )
    snapshot_age = None
    if host.last_scan_observed_at:
        observed_at = host.last_scan_observed_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        snapshot_age = max(0, int((now - observed_at).total_seconds()))
    return state.value, reason.value, age, snapshot_age


def _project_activity(db: Session, project_name: str, limit: int) -> list[ActivityEvent]:
    candidates = db.scalars(
        select(ActivityEvent)
        .where(ActivityEvent.identity_context == project_name)
        .order_by(ActivityEvent.timestamp.desc())
        .limit(limit)
    ).all()
    metadata_candidates = db.scalars(
        select(ActivityEvent)
        .where(ActivityEvent.metadata_json["project_name"].astext == project_name)
        .order_by(ActivityEvent.timestamp.desc())
        .limit(limit)
    ).all()
    by_id = {event.id: event for event in [*candidates, *metadata_candidates]}
    return sorted(by_id.values(), key=lambda event: event.timestamp, reverse=True)[:limit]


def list_projects(db: Session, limit: int = 200, offset: int = 0) -> list[ProjectOut]:
    binding_rows = db.execute(
        select(CurrentPortObservation, Host)
        .join(Host, Host.id == CurrentPortObservation.host_id)
        .where(CurrentPortObservation.project_name.is_not(None))
        .order_by(CurrentPortObservation.project_name)
    ).all()
    reservation_rows = db.scalars(select(CentralReservation)).all()
    conflicts = list_conflicts(db)

    bindings_by_project: dict[str, list[tuple[CurrentPortObservation, Host]]] = defaultdict(list)
    reservations_by_project: Counter[str] = Counter()
    conflicts_by_project: Counter[str] = Counter()
    for binding, host in binding_rows:
        if binding.project_name:
            bindings_by_project[binding.project_name].append((binding, host))
    for reservation in reservation_rows:
        reservations_by_project[reservation.project] += 1
    for conflict in conflicts:
        conflicts_by_project[conflict.reserved_for_project] += 1
        if conflict.actual_project and conflict.actual_project != conflict.reserved_for_project:
            conflicts_by_project[conflict.actual_project] += 1

    names = sorted(set(bindings_by_project) | set(reservations_by_project))[offset : offset + limit]
    activity_by_project: dict[str, datetime] = {}
    if names:
        activity_rows = db.scalars(
            select(ActivityEvent).where(
                or_(
                    ActivityEvent.identity_context.in_(names),
                    ActivityEvent.metadata_json["project_name"].astext.in_(names),
                )
            ).order_by(ActivityEvent.timestamp.desc())
        ).all()
        for event in activity_rows:
            metadata_project = (event.metadata_json or {}).get("project_name")
            linked_project = metadata_project if metadata_project in names else event.identity_context
            if linked_project in names and linked_project not in activity_by_project:
                activity_by_project[linked_project] = event.timestamp
    now = datetime.now(timezone.utc)
    results: list[ProjectOut] = []
    for name in names:
        rows = bindings_by_project.get(name, [])
        hosts = {host.id: host for _, host in rows}
        states = Counter(_health(host, now)[0] for host in hosts.values())
        results.append(
            ProjectOut(
                project_name=name,
                host_count=len(hosts),
                port_count=len(rows),
                process_count=sum(1 for binding, _ in rows if binding.source == "process"),
                docker_binding_count=sum(1 for binding, _ in rows if binding.source == "docker"),
                container_count=len({binding.container_id for binding, _ in rows if binding.container_id}),
                reservation_count=reservations_by_project[name],
                conflict_count=conflicts_by_project[name],
                healthy_host_count=states["HEALTHY"],
                stale_host_count=states["STALE"],
                offline_host_count=states["OFFLINE"],
                last_activity=activity_by_project.get(name),
                hosts=sorted(host.hostname for host in hosts.values()),
            )
        )
    return results


def get_project(
    db: Session,
    project_name: str,
    port_limit: int = 200,
    port_offset: int = 0,
    reservation_limit: int = 100,
    reservation_offset: int = 0,
    activity_limit: int = 50,
) -> ProjectDetailOut | None:
    all_binding_rows = db.execute(
        select(CurrentPortObservation, Host)
        .join(Host, Host.id == CurrentPortObservation.host_id)
        .where(CurrentPortObservation.project_name == project_name)
        .order_by(Host.hostname, CurrentPortObservation.port, CurrentPortObservation.protocol)
    ).all()
    reservation_rows = db.scalars(
        select(CentralReservation)
        .where(CentralReservation.project == project_name)
        .order_by(CentralReservation.updated_at.desc())
    ).all()
    if not all_binding_rows and not reservation_rows:
        return None

    host_ids = {binding.host_id for binding, _ in all_binding_rows} | {row.host_id for row in reservation_rows}
    hosts = {
        host.id: host
        for host in db.scalars(select(Host).where(Host.id.in_(host_ids))).all()
    }
    binding_counts = Counter(binding.host_id for binding, _ in all_binding_rows)
    now = datetime.now(timezone.utc)
    host_details = []
    states: Counter[str] = Counter()
    for host in sorted(hosts.values(), key=lambda item: item.hostname):
        state, reason, age, snapshot_age = _health(host, now)
        states[state] += 1
        host_details.append(
            ProjectHostOut(
                host_id=host.id,
                hostname=host.hostname,
                operating_system=host.operating_system,
                docker_available=host.docker_available,
                health_state=state,
                health_reason=reason,
                age_seconds=age,
                snapshot_age_seconds=snapshot_age,
                binding_count=binding_counts[host.id],
            )
        )

    page_rows = all_binding_rows[port_offset : port_offset + port_limit]
    ports = [
        PortObservationOut.from_observation(binding, hostname=host.hostname)
        for binding, host in page_rows
    ]
    project_conflicts = [
        conflict
        for conflict in list_conflicts(db)
        if conflict.reserved_for_project == project_name or conflict.actual_project == project_name
    ]
    activity = _project_activity(db, project_name, activity_limit)

    return ProjectDetailOut(
        project_name=project_name,
        host_count=len(hosts),
        port_count=len(all_binding_rows),
        process_count=sum(1 for binding, _ in all_binding_rows if binding.source == "process"),
        docker_binding_count=sum(1 for binding, _ in all_binding_rows if binding.source == "docker"),
        container_count=len({binding.container_id for binding, _ in all_binding_rows if binding.container_id}),
        reservation_count=len(reservation_rows),
        conflict_count=len(project_conflicts),
        healthy_host_count=states["HEALTHY"],
        stale_host_count=states["STALE"],
        offline_host_count=states["OFFLINE"],
        last_activity=activity[0].timestamp if activity else None,
        hosts=sorted(host.hostname for host in hosts.values()),
        host_details=host_details,
        ports=Page(items=ports, total=len(all_binding_rows), limit=port_limit, offset=port_offset),
        reservations=Page(
            items=[_reservation_out(row) for row in reservation_rows[reservation_offset : reservation_offset + reservation_limit]],
            total=len(reservation_rows),
            limit=reservation_limit,
            offset=reservation_offset,
        ),
        conflicts=project_conflicts,
        activity=[ActivityEventOut.model_validate(event) for event in activity],
    )
