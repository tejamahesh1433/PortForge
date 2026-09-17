"""Ingestion performance with synthetic snapshots of 100/500/1000
observations. Not a strict pass/fail benchmark (this is a local dev tool,
not a high-scale SaaS system) -- prints timing for the final report and
asserts a generous upper bound so a genuine performance regression still
fails the suite.
"""
import time
import uuid
from datetime import datetime, timezone

from app.models.host import Host
from app.services import ingestion_service


def _make_host(db) -> uuid.UUID:
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="perf-host", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def _synthetic_observations(count: int):
    from app.schemas.agent import ObservationIn

    now = datetime.now(timezone.utc)
    return [
        ObservationIn(
            port=10000 + i,
            protocol="tcp" if i % 2 == 0 else "udp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            process_name=f"process-{i}.exe",
            project_name=f"project-{i % 20}",
            purpose="generic",
            first_seen=now,
            last_seen=now,
        )
        for i in range(count)
    ]


def _time_ingest(db, count: int) -> float:
    host_id = _make_host(db)
    observations = _synthetic_observations(count)
    t0 = time.perf_counter()
    result = ingestion_service.ingest_snapshot(
        db, host_id, uuid.uuid4(), datetime.now(timezone.utc), observations, max_batch_size=10_000
    )
    elapsed = time.perf_counter() - t0
    assert result.appeared == count
    return elapsed


def test_ingest_100_observations(db_session, capsys):
    elapsed = _time_ingest(db_session, 100)
    print(f"\n[perf] 100 observations ingested in {elapsed:.3f}s")
    assert elapsed < 5.0


def test_ingest_500_observations(db_session, capsys):
    elapsed = _time_ingest(db_session, 500)
    print(f"\n[perf] 500 observations ingested in {elapsed:.3f}s")
    assert elapsed < 10.0


def test_ingest_1000_observations(db_session, capsys):
    elapsed = _time_ingest(db_session, 1000)
    print(f"\n[perf] 1000 observations ingested in {elapsed:.3f}s")
    assert elapsed < 20.0


def test_reingest_1000_observations_unchanged_is_fast(db_session, capsys):
    """The 'nothing changed' path (routine re-confirmation) should be at
    least as fast as initial ingestion, since it only updates existing
    rows and writes zero new history events.
    """
    host_id = _make_host(db_session)
    observations = _synthetic_observations(1000)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(db_session, host_id, uuid.uuid4(), t0, observations, max_batch_size=10_000)

    from datetime import timedelta

    t1 = t0 + timedelta(seconds=30)
    start = time.perf_counter()
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t1, _synthetic_observations(1000), max_batch_size=10_000
    )
    elapsed = time.perf_counter() - start
    print(f"\n[perf] re-ingest 1000 unchanged observations in {elapsed:.3f}s")
    assert result.changed == 0
    assert result.appeared == 0
    assert elapsed < 20.0
