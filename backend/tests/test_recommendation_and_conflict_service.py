"""Tests for the central "suggestion" (never "verified") recommendation
service, and the central conflict detection service.
"""
import uuid
from datetime import datetime, timezone

from app.models.host import Host
from app.models.port_observation import CurrentPortObservation
from app.services import conflict_service, reservation_service
from app.services.recommendation_service import suggest_port


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def _add_observation(db, host_id, port, project_name=None, process_name="app.exe"):
    now = datetime.now(timezone.utc)
    db.add(
        CurrentPortObservation(
            host_id=host_id,
            port=port,
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            process_name=process_name,
            project_name=project_name,
            first_seen=now,
            last_seen=now,
            observed_at=now,
            scan_id=uuid.uuid4(),
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Recommendation service -- always "central_suggestion"
# ---------------------------------------------------------------------------


def test_suggestion_is_always_labeled_central_suggestion(db_session):
    host_id = _make_host(db_session)
    result = suggest_port(db_session, host_id, "api")
    assert result.verification == "central_suggestion"
    assert "not a live check" in result.basis.lower() or "not" in result.basis.lower()


def test_suggestion_skips_occupied_ports(db_session):
    host_id = _make_host(db_session)
    _add_observation(db_session, host_id, 8000)
    result = suggest_port(db_session, host_id, "api")
    assert result.recommended_port != 8000
    assert 8000 in result.known_conflicts_excluded


def test_suggestion_skips_reserved_ports(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8000, "tcp", None, "deeptrace", None, None, None, None
    )
    result = suggest_port(db_session, host_id, "api")
    assert result.recommended_port != 8000
    assert 8000 in result.known_conflicts_excluded


def test_suggestion_unknown_service_type(db_session):
    host_id = _make_host(db_session)
    result = suggest_port(db_session, host_id, "no-such-type")
    assert result.recommended_port is None


def test_suggestion_scoped_per_host(db_session):
    host_a = _make_host(db_session)
    host_b = _make_host(db_session)
    _add_observation(db_session, host_a, 8000)

    result_a = suggest_port(db_session, host_a, "api")
    result_b = suggest_port(db_session, host_b, "api")

    assert result_a.recommended_port != 8000
    assert result_b.recommended_port == 8000  # free on host_b


# ---------------------------------------------------------------------------
# Conflict service
# ---------------------------------------------------------------------------


def test_no_conflict_when_reserved_but_not_active(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", None, None, None, None
    )
    conflicts = conflict_service.list_conflicts(db_session, host_id=host_id)
    assert conflicts == []


def test_no_conflict_when_active_matches_reservation_project(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", None, None, None, None
    )
    _add_observation(db_session, host_id, 8003, project_name="DeepTrace")  # case-insensitive match

    conflicts = conflict_service.list_conflicts(db_session, host_id=host_id)
    assert conflicts == []


def test_conflict_when_active_project_differs(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", "api", None, None, None
    )
    _add_observation(db_session, host_id, 8003, project_name="another-project")

    conflicts = conflict_service.list_conflicts(db_session, host_id=host_id)
    assert len(conflicts) == 1
    assert conflicts[0].reserved_for_project == "deeptrace"
    assert conflicts[0].actual_project == "another-project"


def test_conflict_conservative_for_unknown_owner(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", None, None, None, None
    )
    _add_observation(db_session, host_id, 8003, project_name=None, process_name="mystery.exe")

    conflicts = conflict_service.list_conflicts(db_session, host_id=host_id)
    assert len(conflicts) == 1
    assert "mystery.exe" in conflicts[0].reason
