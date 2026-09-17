"""Tests for snapshot ingestion: full-snapshot semantics, staleness
rejection, duplicate scan_id idempotency, current-state/history strategy,
and transactional rollback.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.host import Host
from app.models.port_observation import CurrentPortObservation, PortObservationEvent
from app.schemas.agent import ObservationIn
from app.services import ingestion_service


def _make_host(db, host_id=None) -> uuid.UUID:
    host_id = host_id or uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(
        Host(
            id=host_id,
            hostname="test-host",
            operating_system="windows",
            first_seen=now,
            last_seen=now,
        )
    )
    db.commit()
    return host_id


def _obs(port=8000, protocol="tcp", bind_address="0.0.0.0", state="ACTIVE", process_name="app.exe", **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        state=state,
        source="process",
        process_name=process_name,
        first_seen=now,
        last_seen=now,
    )
    defaults.update(overrides)
    return ObservationIn(**defaults)


def test_empty_snapshot_accepted(db_session):
    host_id = _make_host(db_session)
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), [], max_batch_size=100
    )
    assert result.accepted
    assert result.observations_processed == 0


def test_normal_snapshot_creates_current_rows(db_session):
    host_id = _make_host(db_session)
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), [_obs(port=8000), _obs(port=3000)],
        max_batch_size=100,
    )
    assert result.appeared == 2
    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert {r.port for r in rows} == {8000, 3000}


def test_second_identical_snapshot_updates_without_new_event(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000)], max_batch_size=100)

    t1 = t0 + timedelta(seconds=30)
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t1, [_obs(port=8000)], max_batch_size=100
    )

    assert result.appeared == 0
    assert result.changed == 0  # identical re-confirmation -- no history event

    events = db_session.execute(select(PortObservationEvent).where(PortObservationEvent.host_id == host_id)).scalars().all()
    assert len(events) == 1  # only the original "appeared" event


def test_changed_owner_creates_change_event(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000, process_name="old.exe")], max_batch_size=100
    )

    t1 = t0 + timedelta(seconds=30)
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t1, [_obs(port=8000, process_name="new.exe")], max_batch_size=100
    )

    assert result.changed == 1
    current = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalar_one()
    assert current.process_name == "new.exe"


def test_disappeared_port_removed_from_current_with_event(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000), _obs(port=3000)], max_batch_size=100
    )

    t1 = t0 + timedelta(seconds=30)
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t1, [_obs(port=8000)], max_batch_size=100  # 3000 gone
    )

    assert result.disappeared == 1
    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert {r.port for r in rows} == {8000}

    events = db_session.execute(
        select(PortObservationEvent).where(PortObservationEvent.host_id == host_id, PortObservationEvent.event_type == "disappeared")
    ).scalars().all()
    assert len(events) == 1
    assert events[0].port == 3000


def test_full_snapshot_semantics_empty_submission_clears_all_current(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000), _obs(port=3000)], max_batch_size=100
    )

    t1 = t0 + timedelta(seconds=30)
    result = ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t1, [], max_batch_size=100)

    assert result.disappeared == 2
    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert rows == []


def test_duplicate_scan_id_is_idempotent(db_session):
    host_id = _make_host(db_session)
    scan_id = uuid.uuid4()
    t0 = datetime.now(timezone.utc)

    first = ingestion_service.ingest_snapshot(db_session, host_id, scan_id, t0, [_obs(port=8000)], max_batch_size=100)
    second = ingestion_service.ingest_snapshot(db_session, host_id, scan_id, t0, [_obs(port=8000)], max_batch_size=100)

    assert first.appeared == 1
    assert second.accepted
    assert second.appeared == 0  # not reprocessed
    assert "duplicate" in (second.reason or "").lower()

    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert len(rows) == 1  # no duplication


def test_stale_snapshot_rejected(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000)], max_batch_size=100)

    stale_time = t0 - timedelta(minutes=5)
    with pytest.raises(ingestion_service.StaleSnapshotError):
        ingestion_service.ingest_snapshot(
            db_session, host_id, uuid.uuid4(), stale_time, [_obs(port=9000)], max_batch_size=100
        )

    # The stale submission's data must never have been applied.
    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert {r.port for r in rows} == {8000}


def test_equal_observed_at_is_also_rejected_as_stale(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t0, [_obs(port=8000)], max_batch_size=100)

    with pytest.raises(ingestion_service.StaleSnapshotError):
        ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t0, [_obs(port=9000)], max_batch_size=100)


def test_batch_too_large_rejected(db_session):
    host_id = _make_host(db_session)
    observations = [_obs(port=8000 + i) for i in range(10)]
    with pytest.raises(ingestion_service.BatchTooLargeError):
        ingestion_service.ingest_snapshot(
            db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), observations, max_batch_size=5
        )


def test_unknown_host_rejected(db_session):
    with pytest.raises(ingestion_service.SnapshotRejectedError):
        ingestion_service.ingest_snapshot(
            db_session, uuid.uuid4(), uuid.uuid4(), datetime.now(timezone.utc), [_obs()], max_batch_size=100
        )


def test_naive_datetime_is_treated_as_utc(db_session):
    host_id = _make_host(db_session)
    naive_time = datetime.now()  # no tzinfo
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), naive_time, [_obs(port=8000)], max_batch_size=100
    )
    assert result.accepted


def test_transactional_rollback_on_failure_leaves_no_partial_state(db_session, monkeypatch):
    """Simulate a failure partway through processing a multi-observation
    snapshot and confirm nothing was partially applied.
    """
    host_id = _make_host(db_session)

    from app.repositories.port_repository import PortRepository

    original_upsert = PortRepository.upsert_current
    call_count = {"n": 0}

    def _failing_upsert(self, observation):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure mid-ingestion")
        return original_upsert(self, observation)

    monkeypatch.setattr(PortRepository, "upsert_current", _failing_upsert)

    with pytest.raises(RuntimeError):
        ingestion_service.ingest_snapshot(
            db_session,
            host_id,
            uuid.uuid4(),
            datetime.now(timezone.utc),
            [_obs(port=8000), _obs(port=3000), _obs(port=9000)],
            max_batch_size=100,
        )

    db_session.rollback()
    rows = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert rows == []  # nothing committed -- the whole batch was rolled back


def test_tcp_and_udp_on_same_port_are_distinct_current_rows(db_session):
    host_id = _make_host(db_session)
    result = ingestion_service.ingest_snapshot(
        db_session,
        host_id,
        uuid.uuid4(),
        datetime.now(timezone.utc),
        [_obs(port=53, protocol="tcp"), _obs(port=53, protocol="udp")],
        max_batch_size=100,
    )
    assert result.appeared == 2


def test_malformed_observation_rejected_by_schema_validation():
    with pytest.raises(Exception):
        ObservationIn(
            port=70000,  # out of range
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        )
