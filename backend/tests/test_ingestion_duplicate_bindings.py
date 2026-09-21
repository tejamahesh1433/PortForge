"""Regression coverage for the Phase 6 physical failure: a real snapshot
from NTMKEYA (two distinct listeners both bound to UDP 5353/`::` for
mDNS, via SO_REUSEADDR -- entirely valid at the OS level) raised
`IntegrityError: duplicate key value violates unique constraint
"uq_current_port_binding"` and rejected the ENTIRE snapshot with HTTP
500, deterministically, on every single sync attempt (no concurrency
involved).

## Same-request root cause

`ingest_snapshot()` read `current_rows` from the DB once, then looped
over incoming observations deciding insert-vs-update against that one
static dict -- never updating it as new rows were added mid-loop. Two
incoming observations sharing one Central binding identity
(host+port+protocol+bind_address, `uq_current_port_binding`) both looked
"new," both got `db.add()`'d, and the batched insert collided with
itself at `commit()`. Fixed by `_canonicalize_observations()` /
`_merge_observation_group()` in ingestion_service.py, which collapses
each group of same-identity observations into one canonical observation
*before* the main loop ever runs -- see that module's "Duplicate-binding
canonicalization" docstring section for the full merge policy (source
authority, confidence, richness, then a fully content-derived tiebreak;
metadata backfilled in coherent groups, never field-by-field).

Scenarios A-E and H below exercise that fix directly via
`ingestion_service.ingest_snapshot()` (mirrors tests/test_ingestion_service.py's
existing conventions). F and G are direct regression checks that the
canonicalization fix didn't disturb the pre-existing physical-binding-
preservation / cross-host-isolation guarantees. The concurrent-request
test at the bottom exercises the SEPARATE, previously-flagged race (two
truly overlapping *requests* for the same host, each with its own
session/transaction) using two independent real Sessions -- `db_session`
(a single, savepoint-nested session) can't simulate that.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.host import Host
from app.models.port_observation import CurrentPortObservation, PortObservationEvent
from app.schemas.agent import ObservationIn
from app.services import ingestion_service

from .conftest import _admin_url, _test_db_url

_PROTECTED_DB_NAME = "portforge"


def _make_host(db, host_id=None) -> uuid.UUID:
    host_id = host_id or uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="test-host", operating_system="windows", first_seen=now, last_seen=now))
    db.commit()
    return host_id


def _obs(port=8000, protocol="udp", bind_address="::", state="ACTIVE", **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        state=state,
        source="process",
        first_seen=now,
        last_seen=now,
    )
    defaults.update(overrides)
    return ObservationIn(**defaults)


# ---------------------------------------------------------------------------
# A. Same-request exact duplicate
# ---------------------------------------------------------------------------


def test_exact_duplicate_observations_do_not_raise(db_session):
    host_id = _make_host(db_session)
    result = ingestion_service.ingest_snapshot(
        db_session,
        host_id,
        uuid.uuid4(),
        datetime.now(timezone.utc),
        [_obs(port=5353, process_name="svchost.exe"), _obs(port=5353, process_name="svchost.exe")],
        max_batch_size=100,
    )

    assert result.accepted
    assert result.appeared == 1
    assert result.duplicates_merged == 1

    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].port == 5353


# ---------------------------------------------------------------------------
# B. Same binding, different physical metadata -- the real NTMKEYA case
# ---------------------------------------------------------------------------


def test_same_binding_different_metadata_merges_deterministically(db_session):
    """Two distinct process observations for UDP 5353/:: (the exact
    physically reproduced case). Docker source authority > plain process
    (mirrors the agent's own merge_native_and_docker() precedent), so the
    docker-sourced observation's metadata should win.
    """
    host_id = _make_host(db_session)
    docker_obs = _obs(
        port=5353,
        source="docker",
        process_name="com.docker.backend.exe",
        container_id="abc123",
        container_name="mdns-responder",
    )
    process_obs = _obs(port=5353, source="process", process_name="svchost.exe", pid=4200)

    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), [process_obs, docker_obs], max_batch_size=100
    )

    assert result.accepted
    assert result.appeared == 1
    assert result.duplicates_merged == 1

    row = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalar_one()
    assert row.source == "docker"
    assert row.container_id == "abc123"
    assert row.process_name == "com.docker.backend.exe"


def test_same_binding_different_metadata_reversed_order_is_deterministic(db_session):
    """Reversing the incoming observation order must not change which
    metadata wins -- canonicalization ranks by content, never position.
    """
    host_id_a = _make_host(db_session)
    docker_obs = _obs(
        port=5353, source="docker", process_name="com.docker.backend.exe", container_id="abc123"
    )
    process_obs = _obs(port=5353, source="process", process_name="svchost.exe", pid=4200)

    forward = ingestion_service.ingest_snapshot(
        db_session, host_id_a, uuid.uuid4(), datetime.now(timezone.utc), [process_obs, docker_obs], max_batch_size=100
    )
    row_forward = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id_a)
    ).scalar_one()

    host_id_b = _make_host(db_session)
    reversed_result = ingestion_service.ingest_snapshot(
        db_session, host_id_b, uuid.uuid4(), datetime.now(timezone.utc), [docker_obs, process_obs], max_batch_size=100
    )
    row_reversed = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id_b)
    ).scalar_one()

    assert forward.accepted and reversed_result.accepted
    assert row_forward.source == row_reversed.source == "docker"
    assert row_forward.container_id == row_reversed.container_id == "abc123"
    assert row_forward.process_name == row_reversed.process_name == "com.docker.backend.exe"


# ---------------------------------------------------------------------------
# C. Three or more duplicates
# ---------------------------------------------------------------------------


def test_three_duplicates_create_exactly_one_current_row(db_session):
    host_id = _make_host(db_session)
    observations = [
        _obs(port=5353, source="process", process_name="a.exe"),
        _obs(port=5353, source="process", process_name="b.exe"),
        _obs(port=5353, source="system"),
    ]
    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), observations, max_batch_size=100
    )

    assert result.appeared == 1
    assert result.duplicates_merged == 2

    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# D. Existing DB row + duplicate incoming observations
# ---------------------------------------------------------------------------


def test_existing_row_plus_incoming_duplicates_updates_not_inserts(db_session):
    host_id = _make_host(db_session)
    t0 = datetime.now(timezone.utc)
    ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), t0, [_obs(port=5353, source="process", process_name="old.exe")],
        max_batch_size=100,
    )

    t1 = t0 + timedelta(seconds=30)
    result = ingestion_service.ingest_snapshot(
        db_session,
        host_id,
        uuid.uuid4(),
        t1,
        [
            _obs(port=5353, source="docker", process_name="new-docker.exe", container_id="c1"),
            _obs(port=5353, source="process", process_name="new-process.exe"),
        ],
        max_batch_size=100,
    )

    assert result.appeared == 0
    assert result.changed == 1
    assert result.duplicates_merged == 1

    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].process_name == "new-docker.exe"  # docker authority wins the merge


# ---------------------------------------------------------------------------
# E. Mixed snapshot
# ---------------------------------------------------------------------------


def test_mixed_snapshot_all_distinct_identities_survive(db_session):
    host_id = _make_host(db_session)
    observations = [
        _obs(port=8000, protocol="tcp", bind_address="0.0.0.0", source="process", process_name="app.exe"),
        _obs(port=8000, protocol="tcp", bind_address="::", source="process", process_name="app.exe"),  # IPv6, distinct
        _obs(port=53, protocol="udp", bind_address="0.0.0.0", source="system"),
        _obs(port=53, protocol="tcp", bind_address="0.0.0.0", source="system"),  # different protocol, distinct
        _obs(
            port=9000,
            protocol="tcp",
            bind_address="0.0.0.0",
            source="docker",
            container_id="xyz",
            container_name="web",
        ),
        # The duplicated identity: two observations for the same UDP 5353/::.
        _obs(port=5353, protocol="udp", bind_address="::", source="process", process_name="a.exe"),
        _obs(port=5353, protocol="udp", bind_address="::", source="docker", container_id="mdns1"),
    ]

    result = ingestion_service.ingest_snapshot(
        db_session, host_id, uuid.uuid4(), datetime.now(timezone.utc), observations, max_batch_size=100
    )

    assert result.observations_processed == 7
    assert result.duplicates_merged == 1
    assert result.appeared == 6  # 7 raw - 1 merged duplicate = 6 canonical identities

    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    keys = {(r.port, r.protocol.value, r.bind_address) for r in rows}
    assert keys == {
        (8000, "tcp", "0.0.0.0"),
        (8000, "tcp", "::"),
        (53, "udp", "0.0.0.0"),
        (53, "tcp", "0.0.0.0"),
        (9000, "tcp", "0.0.0.0"),
        (5353, "udp", "::"),
    }
    mdns_row = next(r for r in rows if r.port == 5353)
    assert mdns_row.source == "docker"  # docker authority wins over plain process


# ---------------------------------------------------------------------------
# F. Physical binding preservation must not regress
# ---------------------------------------------------------------------------


def test_same_port_protocol_different_bind_address_remain_distinct(db_session):
    host_id = _make_host(db_session)
    result = ingestion_service.ingest_snapshot(
        db_session,
        host_id,
        uuid.uuid4(),
        datetime.now(timezone.utc),
        [
            _obs(port=8000, protocol="tcp", bind_address="0.0.0.0"),
            _obs(port=8000, protocol="tcp", bind_address="::"),
            _obs(port=8000, protocol="tcp", bind_address="127.0.0.1"),
        ],
        max_batch_size=100,
    )

    assert result.appeared == 3
    assert result.duplicates_merged == 0
    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    assert {r.bind_address for r in rows} == {"0.0.0.0", "::", "127.0.0.1"}


# ---------------------------------------------------------------------------
# G. Cross-host isolation must not regress
# ---------------------------------------------------------------------------


def test_same_binding_on_different_hosts_stays_independent(db_session):
    host_a = _make_host(db_session)
    host_b = _make_host(db_session)

    ingestion_service.ingest_snapshot(
        db_session, host_a, uuid.uuid4(), datetime.now(timezone.utc),
        [_obs(port=5353, source="process", process_name="a.exe"), _obs(port=5353, source="docker", container_id="c1")],
        max_batch_size=100,
    )
    ingestion_service.ingest_snapshot(
        db_session, host_b, uuid.uuid4(), datetime.now(timezone.utc), [_obs(port=5353, source="process")],
        max_batch_size=100,
    )

    rows_a = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_a)
    ).scalars().all()
    rows_b = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_b)
    ).scalars().all()

    assert len(rows_a) == 1 and rows_a[0].source == "docker"  # host A merged its duplicate
    assert len(rows_b) == 1 and rows_b[0].source == "process"  # host B untouched by A's merge


# ---------------------------------------------------------------------------
# H. Transaction rollback with duplicates present
# ---------------------------------------------------------------------------


def test_rollback_with_duplicates_leaves_no_partial_state(db_session, monkeypatch):
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
            [
                _obs(port=8000, protocol="tcp", bind_address="0.0.0.0"),
                _obs(port=5353, source="process"),
                _obs(port=5353, source="docker", container_id="c1"),  # duplicate of the above
                _obs(port=9000, protocol="tcp", bind_address="0.0.0.0"),
            ],
            max_batch_size=100,
        )

    db_session.rollback()
    rows = db_session.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    assert rows == []  # nothing committed, including the merged duplicate


# ---------------------------------------------------------------------------
# Concurrent-request race (separate bug from the same-request one above)
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_db_sessionmaker():
    """Two genuinely independent Sessions/transactions against a
    disposable database -- db_session's single, savepoint-nested session
    can't simulate two truly overlapping requests racing each other.
    """
    db_name = f"portforge_test_ingest_concurrent_{uuid.uuid4().hex[:12]}"
    if db_name == _PROTECTED_DB_NAME:  # pragma: no cover - structurally impossible, kept as a hard guard
        raise AssertionError("refusing to target the protected database")

    try:
        admin_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin_engine.dispose()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    base = _test_db_url().rsplit("/", 1)[0]
    url = f"{base}/{db_name}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, future=True)

    yield SessionFactory

    engine.dispose()
    cleanup_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with cleanup_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
    cleanup_engine.dispose()


def test_concurrent_same_host_submissions_do_not_raise_integrity_error(monkeypatch, real_db_sessionmaker):
    """Two truly overlapping requests for the same host, with different
    (but each individually valid) full snapshots and strictly increasing
    observed_at times. Forces a known lock-acquisition order (thread A
    first) via a barrier so the expected outcome is fully deterministic:
    A commits, B's strictly-later observed_at is accepted afterward (not
    stale), and B's full snapshot -- being the last accepted -- is what
    current state reflects. Neither call may raise IntegrityError.
    """
    from sqlalchemy.exc import IntegrityError

    SessionFactory = real_db_sessionmaker
    host_id = uuid.uuid4()
    setup = SessionFactory()
    _make_host(setup, host_id)
    setup.close()

    t_a = datetime.now(timezone.utc)
    t_b = t_a + timedelta(seconds=10)

    a_holds_lock = threading.Event()
    let_a_proceed = threading.Event()
    original_lock = ingestion_service.acquire_host_lock

    def patched_lock(db, hid):
        original_lock(db, hid)
        if threading.current_thread().name == "ingest-a":
            a_holds_lock.set()
            let_a_proceed.wait(timeout=10)

    monkeypatch.setattr(ingestion_service, "acquire_host_lock", patched_lock)

    results: dict = {}
    errors: dict = {}

    def run_a():
        session = SessionFactory()
        try:
            results["a"] = ingestion_service.ingest_snapshot(
                session, host_id, uuid.uuid4(), t_a, [_obs(port=100, protocol="tcp", bind_address="0.0.0.0")],
                max_batch_size=100,
            )
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors["a"] = exc
        finally:
            session.close()

    def run_b():
        a_holds_lock.wait(timeout=10)
        session = SessionFactory()
        try:
            results["b"] = ingestion_service.ingest_snapshot(
                session, host_id, uuid.uuid4(), t_b, [_obs(port=200, protocol="tcp", bind_address="0.0.0.0")],
                max_batch_size=100,
            )
        except Exception as exc:  # noqa: BLE001
            errors["b"] = exc
        finally:
            session.close()

    thread_a = threading.Thread(target=run_a, name="ingest-a")
    thread_b = threading.Thread(target=run_b, name="ingest-b")

    thread_a.start()
    assert a_holds_lock.wait(timeout=10), "thread A never reported holding the lock"
    thread_b.start()
    time.sleep(0.3)  # give B's ingest_snapshot() time to genuinely start blocking on the real pg advisory lock
    let_a_proceed.set()

    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not isinstance(errors.get("a"), IntegrityError), errors.get("a")
    assert not isinstance(errors.get("b"), IntegrityError), errors.get("b")
    assert "a" not in errors, f"thread A raised unexpectedly: {errors.get('a')}"
    assert "b" not in errors, f"thread B raised unexpectedly: {errors.get('b')}"

    assert results["a"].accepted
    assert results["b"].accepted  # strictly later observed_at -- not stale

    verify = SessionFactory()
    rows = verify.execute(
        select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)
    ).scalars().all()
    verify.close()
    # Full-snapshot semantics: B's snapshot (port 200 only), being the
    # last one accepted, entirely replaces A's prior state (port 100).
    assert {r.port for r in rows} == {200}
