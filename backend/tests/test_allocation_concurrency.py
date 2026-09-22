"""Phase 8A §24/§25: real concurrency and rollback regression coverage.

Mirrors tests/test_ingestion_duplicate_bindings.py's established pattern
for testing genuine cross-transaction races: `db_session`'s single,
savepoint-nested session cannot simulate two truly overlapping requests,
so these tests use two independent real Sessions against a disposable
database, with threads.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.host import Host
from app.models.reservation import CentralReservation
from app.schemas.allocation import AllocationIn
from app.services import allocation_service

from .conftest import _admin_url, _test_db_url

_PROTECTED_DB_NAME = "portforge"


def _make_host(db, host_id=None) -> uuid.UUID:
    host_id = host_id or uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.add(Host(id=host_id, hostname="test-host", operating_system="linux", first_seen=now, last_seen=now))
    db.commit()
    return host_id


@pytest.fixture()
def real_db_sessionmaker():
    """Two genuinely independent Sessions/transactions against a
    disposable database -- see module docstring.
    """
    db_name = f"portforge_test_alloc_concurrent_{uuid.uuid4().hex[:12]}"
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


def test_concurrent_allocations_same_host_same_purpose_never_collide(real_db_sessionmaker):
    """Phase 8A §24/§30: two agents requesting the same purpose on the
    SAME host simultaneously must never receive the same binding.
    """
    SessionFactory = real_db_sessionmaker
    setup = SessionFactory()
    host_id = _make_host(setup)
    setup.close()

    results: dict = {}
    errors: dict = {}

    def worker(name: str, project: str):
        db = SessionFactory()
        try:
            payload = AllocationIn(
                project=project,
                host_id=host_id,
                requests=[{"name": "api", "purpose": "api", "protocol": "tcp"}],
            )
            out = allocation_service.create_allocation(db, payload)
            results[name] = out.allocations[0].port
        except Exception as exc:  # pragma: no cover - failure path surfaced via assertion below
            errors[name] = exc
        finally:
            db.close()

    t_a = threading.Thread(target=worker, args=("a", "project-a"), name="alloc-a")
    t_b = threading.Thread(target=worker, args=("b", "project-b"), name="alloc-b")
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)

    assert not errors, f"unexpected errors: {errors}"
    assert results["a"] != results["b"], f"both allocations got the same port: {results}"

    verify = SessionFactory()
    rows = verify.execute(
        select(CentralReservation).where(CentralReservation.host_id == host_id)
    ).scalars().all()
    verify.close()
    ports = [r.port for r in rows]
    assert len(ports) == 2
    assert len(set(ports)) == 2  # no duplicate binding


def test_concurrent_allocations_different_hosts_proceed_independently(real_db_sessionmaker):
    """Phase 8A §5: concurrency protection must be host-scoped -- two
    allocations against DIFFERENT hosts must not block/interfere with
    each other, and both must succeed.
    """
    SessionFactory = real_db_sessionmaker
    setup = SessionFactory()
    host_a = _make_host(setup)
    host_b = _make_host(setup)
    setup.close()

    results: dict = {}
    errors: dict = {}

    def worker(name: str, host_id: uuid.UUID):
        db = SessionFactory()
        try:
            payload = AllocationIn(
                project=f"project-{name}",
                host_id=host_id,
                requests=[{"name": "api", "purpose": "api", "protocol": "tcp"}],
            )
            out = allocation_service.create_allocation(db, payload)
            results[name] = out.allocations[0].port
        except Exception as exc:  # pragma: no cover
            errors[name] = exc
        finally:
            db.close()

    t_a = threading.Thread(target=worker, args=("a", host_a), name="alloc-host-a")
    t_b = threading.Thread(target=worker, args=("b", host_b), name="alloc-host-b")
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)

    assert not errors, f"unexpected errors: {errors}"
    # Same numeric port on different hosts is fine -- host-scoped, not a conflict.
    assert results["a"] == 8000
    assert results["b"] == 8000


def test_rollback_on_partial_unavailability_leaves_zero_reservations(real_db_sessionmaker):
    """Phase 8A §4/§25: if any request in the bundle cannot be satisfied,
    NO reservations from that bundle may remain -- verified against actual
    database state, not just the HTTP/service response.
    """
    SessionFactory = real_db_sessionmaker
    db = SessionFactory()
    host_id = _make_host(db)

    # Exhaust the entire redis range (6379-6399 inclusive -- 21 ports) with
    # pre-existing reservations for an unrelated project, so a bundle
    # requesting "cache"/redis alongside other, easily-satisfiable
    # purposes is guaranteed to fail on the redis leg specifically.
    for port in range(6379, 6400):
        db.add(CentralReservation(host_id=host_id, port=port, protocol="tcp", project="unrelated-project"))
    db.commit()

    payload = AllocationIn(
        project="portforge-phase8a-rollback-test",
        host_id=host_id,
        requests=[
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
            {"name": "api", "purpose": "api", "protocol": "tcp"},
            {"name": "cache", "purpose": "redis", "protocol": "tcp"},  # exhausted -- must fail
        ],
    )

    with pytest.raises(allocation_service.AllocationError) as exc_info:
        allocation_service.create_allocation(db, payload)
    assert exc_info.value.code == "ALLOCATION_UNAVAILABLE"
    db.close()

    # Fresh session/connection -- confirms committed database state, not
    # merely the failing session's own (already-rolled-back) view.
    verify = SessionFactory()
    rows = verify.execute(
        select(CentralReservation).where(
            CentralReservation.host_id == host_id, CentralReservation.project == "portforge-phase8a-rollback-test"
        )
    ).scalars().all()
    verify.close()
    assert rows == []  # frontend and api must NOT remain, even though both had a free candidate


# --- v1.1-B: probe-aware concurrency (task §20/§22) -------------------------


def test_concurrent_allocations_same_host_unaffected_by_probe_queries(real_db_sessionmaker):
    """Probe evidence lookups/queues happen INSIDE the same host-locked
    transaction as ordinary candidate resolution -- this proves that
    doesn't weaken the existing host-scoped locking guarantee (this is
    also a regression test for a real bug found during implementation:
    queue_probe used to commit internally, which prematurely released the
    transaction-scoped advisory lock and let two concurrent allocations
    both win the same port).
    """
    from app.services import probe_service

    SessionFactory = real_db_sessionmaker
    setup = SessionFactory()
    host_id = _make_host(setup)
    setup.close()

    results: dict = {}
    errors: dict = {}

    def worker(name: str, project: str):
        db = SessionFactory()
        try:
            payload = AllocationIn(
                project=project,
                host_id=host_id,
                requests=[{"name": "api", "purpose": "api", "protocol": "tcp"}],
            )
            out = allocation_service.create_allocation(db, payload)
            results[name] = out.allocations[0].port
        except Exception as exc:  # pragma: no cover
            errors[name] = exc
        finally:
            db.close()

    t_a = threading.Thread(target=worker, args=("a", "probe-conc-a"), name="alloc-probe-a")
    t_b = threading.Thread(target=worker, args=("b", "probe-conc-b"), name="alloc-probe-b")
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)

    assert not errors, f"unexpected errors: {errors}"
    assert results["a"] != results["b"], f"both allocations got the same port: {results}"

    verify = SessionFactory()
    rows = verify.execute(select(CentralReservation).where(CentralReservation.host_id == host_id)).scalars().all()
    verify.close()
    ports = [r.port for r in rows]
    assert len(ports) == 2
    assert len(set(ports)) == 2


def test_same_numeric_port_different_hosts_with_probe_evidence_no_false_conflict(real_db_sessionmaker):
    """Task §20: a numeric port may be allocated on two different hosts
    simultaneously if independently free -- probe evidence for host A must
    never affect host B's own candidate resolution.
    """
    from app.services import probe_service

    SessionFactory = real_db_sessionmaker
    setup = SessionFactory()
    host_a = _make_host(setup)
    host_b = _make_host(setup)
    # Mark port 8000 as VERIFIED FREE for host A specifically via a real
    # probe -- host B has no probe evidence at all and must resolve
    # independently.
    probe = probe_service.queue_probe(setup, host_a, 8000, "tcp")
    setup.commit()
    probe_service.submit_result(setup, host_a, probe.id, True, None)
    setup.close()

    results: dict = {}
    errors: dict = {}

    def worker(name: str, host_id: uuid.UUID):
        db = SessionFactory()
        try:
            payload = AllocationIn(
                project=f"probe-diffhost-{name}",
                host_id=host_id,
                requests=[{"name": "api", "purpose": "api", "protocol": "tcp"}],
            )
            out = allocation_service.create_allocation(db, payload)
            results[name] = (out.allocations[0].port, out.validation.bind_probe)
        except Exception as exc:  # pragma: no cover
            errors[name] = exc
        finally:
            db.close()

    t_a = threading.Thread(target=worker, args=("a", host_a), name="alloc-diffhost-a")
    t_b = threading.Thread(target=worker, args=("b", host_b), name="alloc-diffhost-b")
    t_a.start()
    t_b.start()
    t_a.join(timeout=15)
    t_b.join(timeout=15)

    assert not errors, f"unexpected errors: {errors}"
    assert results["a"] == (8000, "verified_free")  # host A: real probe evidence used
    assert results["b"] == (8000, "not_remote_capable")  # host B: same port, no false cross-host effect
