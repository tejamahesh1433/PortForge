"""Regression coverage for the Phase 6 physical failure: repeated Central
API requests eventually raised
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10
reached, connection timed out, timeout 30.00` from inside
`require_agent()` -> `get_active_credential_by_token_hash()`.

## Root cause (not a session leak)

Exhaustive investigation -- instrumenting get_db() to count generator
entries/exits per request (proved FastAPI's dependency caching correctly
shares one Session between `require_agent` and each route handler's own
`Depends(get_db)`, no duplication), then load-testing a real running
`uvicorn` subprocess (not just TestClient, which uses an in-process ASGI
transport and never touches real DNS/socket behavior) up to 4000
concurrent real HTTP requests with realistic ~200-observation payloads --
found **no session/connection leak anywhere in this codebase**. `get_db()`
already closes correctly on every path: success, auth rejection,
validation error, domain error, and unexpected exception.

The actual cause: `Settings.db_host` defaulted to `"localhost"`, and on
Docker Desktop + WSL2 (the actual physical environment), a brand-new
connection to `"localhost"` resolves IPv6-first and that resolution can
stall 100+ seconds (a pre-existing, previously-diagnosed networking quirk
-- see config.py's `db_host` comment). `create_engine()` is lazy, so the
first real request triggers the first physical connection attempt, and
under any real concurrent traffic, *multiple* requests each independently
try to create a new connection during that stall window -- each one
occupies a pool/overflow slot for the full ~100s it's stuck mid-handshake,
not because any code failed to release it. Once enough of those pile up to
fill all 15 slots (5 base + 10 overflow), a genuinely new request has to
wait in queue, and *that* wait is what `pool_timeout=30` bounds --
producing exactly the reported traceback. Fixed by changing `db_host`'s
default to `"127.0.0.1"` (see config.py), which skips that resolution
entirely.

## What these tests actually prove

`tests/conftest.py`'s shared `client` fixture overrides `get_db` to always
yield one fixed, transaction-bound session -- convenient for fast,
isolated tests, but it means **no existing test before this file ever
exercised the real engine/connection pool at all**, which is exactly why
this bug shipped without anything catching it. Every test below uses its
own `real_pool_client` fixture instead: a TestClient wired to the actual,
non-overridden `get_db()` and a real (disposable) database, so
`engine.pool.checkedout()` reflects genuine pool state. Each test sends
well more requests than `pool_size(5) + max_overflow(10) = 15` and asserts
`checkedout()` returns to the same baseline (0) it started at -- proving
connections are actually returned, not merely that the pool is big enough
to absorb the traffic.

`test_db_host_default_is_127_0_0_1_not_localhost` is the direct,
fast regression pin for the actual root-cause fix itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import app.database as dbmod
from app.config import Settings, get_settings
from app.main import create_app
from app.models import Base

from .conftest import _admin_url, _test_db_url

_PROTECTED_DB_NAME = "portforge"

# Well beyond pool_size(5) + max_overflow(10) = 15, several times over.
N_REQUESTS = 80


def _require_disposable_db_name(name: str) -> None:
    """Same safety discipline as test_migrations.py's helper of the same
    name: never let a fixture issue a destructive DROP/CREATE DATABASE
    against the real, protected development database.
    """
    if name == _PROTECTED_DB_NAME:
        raise AssertionError(
            f"Refusing to target the protected database {_PROTECTED_DB_NAME!r} -- "
            "pool lifecycle tests must only ever use disposable, uniquely-named databases."
        )


def _unique_db_name() -> str:
    return f"portforge_test_pool_{uuid.uuid4().hex[:12]}"


@pytest.fixture()
def real_pool_client():
    """A TestClient wired to the REAL get_db()/engine (never overridden),
    pointed at a fresh disposable database, with pool sizing matching
    production defaults (5 + 10) so a real regression here reproduces the
    genuine symptom rather than being masked by an oversized test pool.
    """
    db_name = _unique_db_name()
    _require_disposable_db_name(db_name)

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

    dbmod.reset_engine_for_testing()
    dbmod._engine = create_engine(
        url, pool_pre_ping=True, future=True, pool_size=5, max_overflow=10, pool_timeout=5
    )
    dbmod._SessionLocal = None
    Base.metadata.create_all(dbmod._engine)

    app = create_app()
    with TestClient(app) as client:
        yield client, dbmod._engine.pool

    dbmod.reset_engine_for_testing()

    _require_disposable_db_name(db_name)
    cleanup_engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with cleanup_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
    cleanup_engine.dispose()


def _enroll(client: TestClient) -> "tuple[str, str]":
    settings = get_settings()
    mint = client.post(
        "/api/agent/enrollment-tokens",
        headers={"Authorization": f"Bearer {settings.admin_bootstrap_token}"},
    )
    assert mint.status_code == 200, mint.text
    token = mint.json()["enrollment_token"]

    host_id = str(uuid.uuid4())
    enroll = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": token,
            "host_id": host_id,
            "hostname": "pool-test-host",
            "operating_system": "linux",
            "docker_available": False,
        },
    )
    assert enroll.status_code == 200, enroll.text
    return host_id, enroll.json()["agent_token"]


def _heartbeat(client: TestClient, headers: dict, host_id: str):
    return client.post(
        "/api/agent/heartbeat",
        headers=headers,
        json={
            "host_id": host_id,
            "hostname": "pool-test-host",
            "operating_system": "linux",
            "os_version": None,
            "architecture": None,
            "agent_version": "0.1.0",
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def _observation(client: TestClient, headers: dict, host_id: str, port: int):
    now = datetime.now(timezone.utc).isoformat()
    return client.post(
        "/api/agent/observations",
        headers=headers,
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": host_id,
            "observed_at": now,
            "observations": [
                {
                    "port": port,
                    "protocol": "tcp",
                    "bind_address": "0.0.0.0",
                    "state": "ACTIVE",
                    "source": "docker",
                    "project_name": "pool-test",
                    "first_seen": now,
                    "last_seen": now,
                }
            ],
        },
    )


# ---------------------------------------------------------------------------
# Direct regression pin for the actual root-cause fix
# ---------------------------------------------------------------------------


def test_db_host_default_is_127_0_0_1_not_localhost():
    """Inspects the field default directly (not `Settings()`, which would
    just reflect whatever env vars this process happens to have) -- fast,
    exact pin of the fix that actually resolved the physical failure.
    """
    assert Settings.model_fields["db_host"].default == "127.0.0.1"


# ---------------------------------------------------------------------------
# Session lifecycle invariant: connections always return to the pool
# ---------------------------------------------------------------------------


def test_repeated_health_checks_return_connections_to_pool(real_pool_client):
    client, pool = real_pool_client
    assert pool.checkedout() == 0

    for _ in range(N_REQUESTS):
        r = client.get("/api/health")
        assert r.status_code == 200

    assert pool.checkedout() == 0


def test_repeated_heartbeats_return_connections_to_pool(real_pool_client):
    client, pool = real_pool_client
    host_id, agent_token = _enroll(client)
    headers = {"Authorization": f"Bearer {agent_token}"}
    assert pool.checkedout() == 0

    for _ in range(N_REQUESTS):
        r = _heartbeat(client, headers, host_id)
        assert r.status_code == 200

    assert pool.checkedout() == 0


def test_repeated_observation_submissions_return_connections_to_pool(real_pool_client):
    client, pool = real_pool_client
    host_id, agent_token = _enroll(client)
    headers = {"Authorization": f"Bearer {agent_token}"}
    assert pool.checkedout() == 0

    for i in range(N_REQUESTS):
        r = _observation(client, headers, host_id, 30000 + i)
        assert r.status_code == 200

    assert pool.checkedout() == 0


def test_mixed_heartbeat_and_observation_traffic_returns_connections_to_pool(real_pool_client):
    client, pool = real_pool_client
    host_id, agent_token = _enroll(client)
    headers = {"Authorization": f"Bearer {agent_token}"}
    assert pool.checkedout() == 0

    for i in range(N_REQUESTS):
        r = _heartbeat(client, headers, host_id) if i % 2 == 0 else _observation(client, headers, host_id, 31000 + i)
        assert r.status_code == 200

    assert pool.checkedout() == 0


def test_repeated_authentication_failures_return_connections_to_pool(real_pool_client):
    """Auth rejection is raised INSIDE the `require_agent` dependency,
    before the route body ever runs -- proves get_db()'s cleanup still
    fires when the failure happens in a nested dependency, not the route.
    """
    client, pool = real_pool_client
    bad_headers = {"Authorization": "Bearer not-a-real-token"}
    assert pool.checkedout() == 0

    for _ in range(N_REQUESTS):
        r = _heartbeat(client, bad_headers, str(uuid.uuid4()))
        assert r.status_code == 401

    assert pool.checkedout() == 0


def test_repeated_stale_snapshot_rejections_return_connections_to_pool(real_pool_client):
    """A domain error raised deep inside a *service* call (not auth, not
    validation) -- ingestion_service.StaleSnapshotError -> HTTP 409 --
    proves get_db()'s `finally: db.close()` runs on that path too.
    """
    client, pool = real_pool_client
    host_id, agent_token = _enroll(client)
    headers = {"Authorization": f"Bearer {agent_token}"}

    future = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
    accepted = client.post(
        "/api/agent/observations",
        headers=headers,
        json={"scan_id": str(uuid.uuid4()), "host_id": host_id, "observed_at": future, "observations": []},
    )
    assert accepted.status_code == 200
    assert pool.checkedout() == 0

    stale = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
    for _ in range(N_REQUESTS):
        r = client.post(
            "/api/agent/observations",
            headers=headers,
            json={"scan_id": str(uuid.uuid4()), "host_id": host_id, "observed_at": stale, "observations": []},
        )
        assert r.status_code == 409

    assert pool.checkedout() == 0


def test_concurrent_burst_well_beyond_pool_capacity_returns_to_baseline(real_pool_client):
    """The same invariant under real concurrency (threads), not just
    sequential calls -- fires several multiples of pool_size+max_overflow
    at once and confirms checkedout() settles back to 0 afterward.

    Uses heartbeats only, not observations: concurrent *observation*
    submissions for the same host each carry their own single-port
    "full snapshot" (see ingestion_service.py's full-snapshot-semantics
    docstring), so two concurrent submissions with different ports race on
    each considering the other's port newly "disappeared" -- a real,
    pre-existing correctness issue in that diffing logic, but a distinct
    bug from (and out of scope for) this pool/session lifecycle fix.
    Heartbeats have no such shared-diff state and isolate the invariant
    this test actually targets: connection pool behavior under concurrency.
    """
    import threading

    client, pool = real_pool_client
    host_id, agent_token = _enroll(client)
    headers = {"Authorization": f"Bearer {agent_token}"}
    assert pool.checkedout() == 0

    results = []
    lock = threading.Lock()

    def worker() -> None:
        r = _heartbeat(client, headers, host_id)
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=worker) for _ in range(60)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(code == 200 for code in results), results
    assert pool.checkedout() == 0
