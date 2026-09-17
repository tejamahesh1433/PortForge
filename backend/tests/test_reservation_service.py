"""Tests for central reservation create/list/delete and the same-port-on-
different-hosts non-conflict rule.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.host import Host
from app.services import reservation_service


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def test_create_reservation(db_session):
    host_id = _make_host(db_session)
    r = reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", "api", "api", None, None
    )
    assert r.port == 8003
    assert r.project == "deeptrace"


def test_same_port_on_different_hosts_is_not_a_conflict(db_session):
    host_a = _make_host(db_session)
    host_b = _make_host(db_session)

    r1 = reservation_service.create_reservation(
        db_session, host_a, 8001, "tcp", None, "project-a", None, None, None, None
    )
    r2 = reservation_service.create_reservation(
        db_session, host_b, 8001, "tcp", None, "project-b", None, None, None, None
    )
    assert r1.host_id != r2.host_id
    assert r1.port == r2.port == 8001

    rows, total = reservation_service.list_reservations(db_session, port=8001)
    assert total == 2


def test_sync_by_local_reservation_id_is_idempotent_upsert(db_session):
    host_id = _make_host(db_session)
    local_id = "local-uuid-123"

    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", "api", "api", None, local_id
    )
    updated = reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", "api-v2", "api", None, local_id
    )

    rows, total = reservation_service.list_reservations(db_session, host_id=host_id)
    assert total == 1  # upserted, not duplicated
    assert updated.service == "api-v2"


def test_sync_refuses_different_local_reservation_on_same_binding(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", None, None, None, "local-id-1"
    )
    with pytest.raises(reservation_service.ReservationConflictError):
        reservation_service.create_reservation(
            db_session, host_id, 8003, "tcp", None, "other-project", None, None, None, "local-id-2"
        )


def test_delete_reservation(db_session):
    host_id = _make_host(db_session)
    r = reservation_service.create_reservation(
        db_session, host_id, 8003, "tcp", None, "deeptrace", None, None, None, None
    )
    deleted = reservation_service.delete_reservation(db_session, host_id, r.id)
    assert deleted is True

    rows, total = reservation_service.list_reservations(db_session, host_id=host_id)
    assert total == 0


def test_delete_reservation_wrong_host_returns_false(db_session):
    host_a = _make_host(db_session)
    host_b = _make_host(db_session)
    r = reservation_service.create_reservation(
        db_session, host_a, 8003, "tcp", None, "deeptrace", None, None, None, None
    )
    deleted = reservation_service.delete_reservation(db_session, host_b, r.id)
    assert deleted is False

    rows, total = reservation_service.list_reservations(db_session, host_id=host_a)
    assert total == 1  # untouched


def test_delete_nonexistent_reservation_returns_false(db_session):
    host_id = _make_host(db_session)
    assert reservation_service.delete_reservation(db_session, host_id, uuid.uuid4()) is False


def test_list_filters_by_project(db_session):
    host_id = _make_host(db_session)
    reservation_service.create_reservation(
        db_session, host_id, 1, "tcp", None, "ocrforge", None, None, None, None
    )
    reservation_service.create_reservation(
        db_session, host_id, 2, "tcp", None, "job-trailers-resume", None, None, None, None
    )
    rows, total = reservation_service.list_reservations(db_session, project="ocrforge")
    assert total == 1
    assert rows[0].project == "ocrforge"
