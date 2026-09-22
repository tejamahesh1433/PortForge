# Phase 8A — Agent Allocation Audit

Read-only audit of the existing recommendation/reservation/locking/host/project/CLI
architecture, done before writing any allocation code, per the Phase 8A instructions.

## 1. Recommendation engine (`backend/app/services/recommendation_service.py`)

- `suggest_port(db, host_id, service_type, protocol)` is the **entire** engine: a
  module-level `_DEFAULT_RANGES` dict (frontend 3000-3999, api 8000-8999, postgres
  5432-5499, mysql 3306-3399, redis 6379-6399, generic 10000-19999 — a deliberate,
  documented, independent copy of the agent's `DEFAULT_RANGES`, not an import, since
  Central has no runtime dependency on the agent package beyond shared enums), then a
  linear scan of that range excluding ports found in `CurrentPortObservation` (active
  bindings) and `CentralReservation` (existing reservations) for that host.
- **Critical, load-bearing semantic** (stated in the module's own docstring): Central
  can *never* claim a real socket bind probe, because it isn't running on the target
  host. `verification` is always `"central_suggestion"`, never verified availability.
  This is not a Phase 8A-optional detail — it is the reason recommendations exist as a
  separate, weaker concept from an agent-local `check`/`next`.
- **Reused as-is for allocation**: the range table and the exclusion logic (occupied
  ∪ reserved) are exactly what an allocation candidate search needs, per host, per
  purpose. Phase 8A's allocation service calls into a shared "find first free port in
  range for this host" helper factored out of `suggest_port`'s body (see below) —
  **no second range table, no second exclusion algorithm.**

## 2. Reservation service/model/repository

- `create_reservation()` (`services/reservation_service.py`) is a plain
  check-then-insert: `get_by_binding()` (a `SELECT`), then `repo.add()` (an `INSERT`),
  then a `RESERVATION_CREATED` `ActivityEvent`, then `db.commit()`. **No explicit
  application-level lock or transaction isolation bump around the check-then-insert.**
- **What actually prevents a duplicate binding under concurrency** is the DB-level
  constraint on `CentralReservation`: `UniqueConstraint(host_id, port, protocol,
  bind_address, name="uq_central_reservation_binding")`
  (`models/reservation.py`). Two concurrent transactions racing to insert the identical
  binding will have one succeed and one hit `IntegrityError` at `COMMIT`, not silently
  both succeed. The pre-check is a nicer-error-message optimization, not the actual
  safety net — Phase 8A's allocation logic can rely on the same constraint, but for a
  *multi-row* atomic bundle we additionally need our own guard against "candidate A and
  candidate B for the same request happened to differ across two racing allocation
  attempts" (see §5).
- `delete_reservation()` is a plain delete + `RESERVATION_RELEASED` activity event,
  idempotent in the sense that deleting a nonexistent/foreign-host reservation returns
  `False` rather than raising.
- `ReservationRepository` has no allocation-aware queries yet; `list()` supports
  `host_id`/`port`/`project` filters only. Phase 8A adds `allocation_id` as an optional
  filter/column (see §9), no other repository changes needed.

## 3. Transaction boundaries / PostgreSQL behavior

- `database.py`'s `get_db()` yields one `Session` per request, `autoflush=False`,
  `autocommit=False`, closed in a `finally`. A route handler's service-layer call is
  the natural transaction boundary — it calls `db.commit()` itself (see
  `reservation_service.create_reservation`) rather than FastAPI/`get_db()` doing it.
  Phase 8A's allocation service follows the same shape: one call, one commit (on full
  success) or one implicit rollback (on any exception — SQLAlchemy sessions roll back
  automatically if a `commit()` is never reached and the session is later closed/GC'd
  without one; the allocation service will additionally call `db.rollback()` explicitly
  before returning an error, for clarity and to release the advisory lock's transaction
  promptly rather than relying on `finally: db.close()` further up the stack).
- No use of `SELECT ... FOR UPDATE` anywhere in the codebase currently — the advisory
  lock pattern below is preferred project-wide over row locking, and Phase 8A continues
  that convention rather than introducing a second locking style.

## 4. Concurrency / locking mechanism — reused directly

- `services/ingestion_service.py::_acquire_host_ingestion_lock(db, host_id)`:
  ```python
  db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:host_id))"), {"host_id": str(host_id)})
  ```
  A **transaction-scoped** PostgreSQL advisory lock keyed by `hashtext(host_id)`.
  Auto-released on `COMMIT`/`ROLLBACK`, no manual unlock, and scoped per-host so
  concurrent traffic for *different* hosts never blocks each other (verified: this is
  exactly what Phase 8A's own concurrency requirement — "host scoped, do not globally
  serialize unrelated hosts" — asks for, already built and already proven under real
  concurrent-ingestion load per the Phase 6 investigation referenced in that module's
  docstring).
  - Phase 8A reuses this **exact same lock key space** (`pg_advisory_xact_lock(hashtext(host_id))`)
    for allocation, not a second, differently-keyed lock. This is a deliberate choice:
    it means an allocation and a concurrent snapshot ingestion for the *same host*
    also serialize against each other (both are, semantically, "the thing that decides
    what's true about this host's ports right now"), which is strictly safer than two
    independent lock spaces that don't know about each other. A small risk-acceptance
    the audit should record: if the acceptance-test in §24 shows different behavior
    is needed, this key choice is the first place to revisit.
  - The helper is factored into a small shared module (`services/host_lock.py`) so both
    `ingestion_service.py` and the new `allocation_service.py` call the identical
    one-liner instead of each hand-rolling the SQL string — a genuine, minimal, safe
    dedup, not a "second recommendation engine."

## 5. Host identity / staleness

- `Host` model + `HostRepository.get(host_id)` — UUID primary key, no hostname lookup
  method exists yet. The dashboard/CLI resolve hostname → UUID by listing `/api/hosts`
  and filtering client-side (see `dashboard/app/hosts/page.tsx`'s search filter for
  precedent) — Phase 8A's CLI does the same rather than adding a
  `GET /api/hosts?hostname=` server-side filter (out of scope, not required by any
  Phase 8A section).
- `schemas/health_status.py::derive_health_state(last_seen, stale_after, offline_after)`
  is the single source of truth for HEALTHY/STALE/OFFLINE/DEGRADED, driven by
  `settings.host_stale_after_seconds` (120) / `settings.host_offline_after_seconds`
  (300). Already used identically in `api/hosts.py` and `services/host_service.py`.
  **Reused directly** for the allocation policy decision in §7 below — no new
  staleness logic.

## 6. Project semantics

- A "project" is *purely a free-text label* stored on `CurrentPortObservation.project_name`
  and `CentralReservation.project` — there is no `Project` table; `services/project_service.py`
  aggregates on the fly by grouping existing rows with a matching project string. This
  means: **an allocation's reservations need no new project plumbing** — writing
  `CentralReservation.project = <allocation's project string>` on each created row is
  sufficient for the existing project drill-down (`GET /api/projects/{name}`) to pick
  them up automatically, exactly as any other reservation would. Confirmed by reading
  `project_service.py`'s aggregation query, which has no allocation-awareness and needs
  none.

## 7. Bind probing — where it currently happens, and why Central cannot do it

- Real `bind()` socket probing exists in exactly one place: `agent/portforge_agent/bindprobe.py`,
  called from the CLI's `check`/`next --reserve` path (`cli.py::_cmd_check`,
  `recommend.py::recommend_and_reserve`) — **always executed on the machine being
  asked about**, never remotely. There is no RPC/socket-probe-on-demand mechanism from
  Central to a remote agent anywhere in the codebase (confirmed: `central_client.py`
  only has `health`/`enroll`/`heartbeat`/`submit_observations`/`sync_reservation` — no
  "probe this port right now" call in either direction).
- **This is the crux of Phase 8A §7/§8's instruction.** Central (this FastAPI process)
  happens, today, to run on the same physical machine as the `workstation` agent — but
  nothing in the code encodes or could safely rely on that coincidence (Central could
  be moved to any machine; there is no `host_id == "the machine I'm running on"` check
  anywhere, and adding one would be fragile, unverifiable from inside a request
  handler, and easily wrong after an infrastructure change). **Decision: Central's
  allocation validation NEVER performs a real socket bind() for any host, including
  workstation, uniformly.** The allocation response is honest about this via a
  `validation` block (see §8 of this doc / the API doc) reporting
  `"bind_probe": "not_remote_capable"` and the snapshot age used, exactly like the
  Phase 8A instructions' own example. This matches `recommendation_service.py`'s
  existing, already-shipped honesty discipline — Phase 8A's allocation is simply a
  *transactional, atomic, multi-port* version of the same guarantee level recommendations
  already give, not a stronger one.
- Practical consequence for §4 (atomicity): "ALL SUCCESS or NO RESERVATIONS" is
  guaranteed at the **Central-state** level (no two allocations can walk away with the
  same binding; a bundle either fully commits against everything Central currently
  knows or fully rolls back) — it is *not* a guarantee that the real OS-level socket is
  free on the remote host at the instant of allocation, exactly as today's
  recommendation already discloses. The freshness of "everything Central currently
  knows" is bounded by that host's last sync (`snapshot_age_seconds`, already computed
  by `derive_health_state`) — allocation refuses hosts past the STALE threshold by
  default (a policy decision, documented, not a hardcoded certainty claim).

## 8. Conflict detection (`services/conflict_service.py`)

- Strictly same-host: for each reservation, looks up `PortRepository.get_current(host_id,
  port, protocol, bind_address or "0.0.0.0")` and flags a conflict only if that exact
  binding is *currently active* under a different project. Confirmed via the Phase
  7C.5 regression pass that this genuinely never fires cross-host, and that a
  reservation's `bind_address` defaults to `"0.0.0.0"` for matching purposes when unset
  — Phase 8A's allocation-created reservations will (like all dashboard reservations
  today) leave `bind_address` unset unless a caller explicitly narrows it, so the same,
  already-understood matching behavior applies uniformly; no new conflict-service code
  needed.

## 9. Existing Central APIs — pattern to follow

- One router module per resource under `api/`, `APIRouter(prefix="/x", tags=["x"])`,
  included in `main.py::create_app()`. Pydantic schemas in `schemas/`, business logic in
  `services/`, queries in `repositories/`. Phase 8A adds `api/allocations.py`,
  `schemas/allocation.py`, `services/allocation_service.py`,
  `repositories/allocation_repository.py`, `models/allocation.py` — same shape as every
  existing resource, nothing structurally new.
- Phase 7C.5 already established that dashboard/tool-facing write endpoints are
  **unauthenticated by design** (PortForge is a trusted private/LAN control plane, no
  login/session/token architecture in scope) with CORS opened for `POST`/`DELETE` in
  addition to `GET`/`HEAD`/`OPTIONS`. Phase 8A's `/api/allocations` endpoints follow the
  exact same posture — consistent with "Authentication for coding-agent allocation is
  OUT OF SCOPE for Phase 8A," this is not a new decision, it is applying an
  already-made one.

## 10. CLI architecture

- `agent/portforge_agent/cli.py::build_parser()` — one `argparse` subparser per
  command, `--json` flag on every command, exit codes 0/success, 1/expected-negative,
  2/operational-error (documented in the module docstring and `agent/README.md`).
  `central_client.py::CentralClient` is a stdlib-only (`urllib.request`) HTTP client
  already supporting unauthenticated calls (`authenticated=False`, used today for
  `/api/health` and `/api/agent/enroll`) — Phase 8A's new `allocate`/`allocation`
  commands reuse this client unauthenticated (consistent with §9 above), adding
  `allocate`/`get_allocation`/`release_allocation` methods to it rather than building
  a second HTTP layer.
- `central_config.py::CentralConfig`/`load_central_config()` resolves the configured
  Central URL from `~/.portforge/central.json`, but `is_usable()` requires
  `enabled AND url AND token` — too strict for allocation, which needs no token. Phase
  8A's allocation CLI resolves the base URL itself, in order: `--url` flag →
  `PORTFORGE_CENTRAL_URL` env var (already the convention used in this project's own
  physical-validation scripts) → `central.json`'s bare `url` field (ignoring
  `enabled`/`token`) → a clear CLI error if none is set. This does not modify
  `central_config.py`'s existing frozen behavior for `central enroll/status/sync`.

## 11. What must actually change to support bundle allocation

1. **New model + migration**: `Allocation` entity (§9 of the main task) +
   `CentralReservation.allocation_id` nullable FK, indexed. One Alembic revision.
2. **New shared lock helper**: `services/host_lock.py` (factored out of
   `ingestion_service.py`'s inline SQL, reused by both).
3. **New shared candidate-search helper**: factor `suggest_port`'s range-scan-with-exclusions
   loop into a small reusable function both `recommendation_service.py` and the new
   `allocation_service.py` call, so there is genuinely one implementation.
4. **New service**: `allocation_service.py` — the atomic multi-request bundle logic
   (§4/§5), idempotency (persisted, keyed by `request_id` + payload hash), release.
5. **New API router**: `api/allocations.py` — `POST/GET/DELETE /api/allocations[/...]`.
6. **New CLI commands + `CentralClient` methods**: `allocate`, `allocation get`,
   `allocation release`, `--file`, `--stdin`, `--format env`.
7. **No changes** to `recommendation_service.py`'s public signature, `reservation_service.py`'s
   existing functions, `conflict_service.py`, `project_service.py`, or any dashboard
   page beyond the minimal, explicitly-scoped addition in §23 of the task (allocation
   context on a reservation).

Nothing here requires touching frozen Phase 1-7 behavior. All additions are net-new
files or additive, backward-compatible columns/methods.
