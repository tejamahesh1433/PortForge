# PortForge v1.1 Planning: Architecture Audit

Written against the tagged `v1.0.0` release (commit `1dabad2e`), from the
`planning/v1.1` branch. Read-only investigation — no code was changed to
produce this document. Baselines re-verified directly: agent 568 passed / 1
skipped, backend 176 passed, dashboard 52 passed (all match the claimed
v1.0.0 numbers after a transient local Postgres/Docker networking hiccup
was cleared by restarting the `portforge-postgres` container — a pure
environment flake, not a code defect; see "Observed fragility" below).

## 1. Repository shape

```
agent/     - portforge_agent Python package (CLI, local discovery, Central client, manifest/workflow/config engines)
backend/   - FastAPI "Central" server + PostgreSQL via SQLAlchemy/Alembic
dashboard/ - Next.js (App Router) operator UI
docs/      - phase-by-phase audits and external contracts (this file continues that convention)
```

Top-level also contains a number of files that are **not part of the
product** but ARE tracked in git: `diag.json`, `diag_lenovo.json`,
`dummy_pid.txt`, `health.json`, `hosts.json`, `insert_host.py`,
`lenovo_ports.json`, `simulate_agent.py`, `simulate_agent_cleanup.py`,
`simulate_recovery.py`, `status.txt`, `test_robustness.py`,
`write_remote_script.py`, `write_script.py`, plus stub directories
`cli/`, `frontend/`, `scripts/`, `tests/` (each holding only a `README.md`,
apparently pre-dating the real `agent/`/`dashboard/` layout) and
`robustness/portforge.yml` (a stray physical-test manifest). None of these
are referenced by any build, test, or CLI entry point — they read as
debugging/scratch artifacts from ad hoc physical validation that were
accidentally committed rather than gitignored. **Not touched in this
task** (analysis only), but flagged as a concrete v1.1 hygiene item — a
tagged v1.0.0 release shipping `dummy_pid.txt` and simulation scripts at
its root is worth cleaning up early, cheaply, with no behavior risk.

`venv_agent/` (≈244 MB) is present in the working tree but **not tracked**
by git and not explicitly gitignored either — harmless as-is, but worth
adding to `.gitignore` explicitly so it can never be accidentally added.

## 2. Backend (`backend/app/`)

### Layering
`api/` (FastAPI routers, one per resource) → `services/` (business logic)
→ `repositories/` (SQLAlchemy queries) → `models/` (ORM). `schemas/` holds
Pydantic request/response models throughout. This is consistent and
unbroken across every resource — no router talks to the ORM directly.

### Resources and their contracts

| Router | Auth | Notes |
|---|---|---|
| `health.py` | none | `GET /api/health` — service/db/version |
| `hosts.py` | none | paginated (`limit≤500`) |
| `ports.py` | none | paginated (`limit≤500`) |
| `reservations.py` | none for reads/dashboard writes; agent writes via `require_agent` | paginated (`limit≤500`); `POST /reservations/dashboard` is the unauthenticated UI path (Phase 7C.4 decision — see below) |
| `activity.py` | none | paginated (`limit≤1000`) |
| `conflicts.py` | none | |
| `recommendations.py` | none | `GET` only, always `verification: "central_suggestion"`, never `"locally_verified"` — the field exists specifically so a future live-probe path (see Kubernetes/remote-probe docs) doesn't need an API shape change |
| `projects.py` | none | free-text label, no separate project entity |
| `agents.py` | `require_agent` (bearer token per host) for enroll/heartbeat/observations; `require_admin` (bootstrap token) narrowly on `POST /enrollment-tokens` only | this is the **entire** authenticated surface in the system |
| `allocations.py` | none (Phase 8A explicit decision) | `POST/GET/DELETE`, atomic multi-port bundles, idempotency via `request_id` + payload hash |

**Security posture, stated plainly**: PortForge v1.0 is an intentionally
unauthenticated, trusted-LAN control plane for everything except (a)
minting new agent enrollment tokens and (b) an enrolled agent's own
heartbeat/observation submission. This was a deliberate, repeatedly
reaffirmed decision across Phase 7C.4 through 8D, not an oversight — any
v1.1 proposal that touches auth must treat this as the explicit baseline
to preserve or consciously change, not silently assume needs fixing.

### Allocation system (`services/allocation_service.py`, Phase 8A)
Atomic multi-port bundles under one `pg_advisory_xact_lock(hashtext(host_id))`
(same lock key space as ingestion — deliberate, so allocation and a
concurrent snapshot sync for the same host serialize against each other).
`AllocationError` → a scoped FastAPI exception handler → structured
`{"error": {"code","message","details"}}`, never FastAPI's default
`{"detail": ...}` shape. Idempotency: `request_id` + SHA-256 of the
canonicalized request, persisted on the `allocations` row, so replay
safety survives a Central restart. **Known limitation, unchanged since
Phase 8A**: `GET`/release responses reflect an allocation's *current*
reservations (a live join), not a historical snapshot — after release,
`allocations: []`.

### Recommendation system (`services/recommendation_service.py`)
`DEFAULT_RANGES` (frontend/api/postgres/mysql/redis/generic) is the single
source of truth for port ranges — reused by both `suggest_port()`
(Phase 4/5's advisory endpoint) and `find_available_port()` (extracted in
Phase 8A specifically so allocation never duplicates this logic). Always
honest about verification level (`"central_suggestion"` only in v1.0 — see
`docs/v1.1/remote-probe-design.md`).

### Reservation system (`services/reservation_service.py`, `models/reservation.py`)
`uq_central_reservation_binding` (host_id, port, protocol, bind_address)
is the **authoritative** concurrency safety net — the advisory lock is
belt-and-suspenders on top of it, not instead of it. `allocation_id`
(nullable FK) and `request_name` (nullable) are Phase 8A additions
letting a reservation optionally point back at the allocation bundle that
created it; both are `None` for reservations created directly (agent sync,
dashboard manual reserve, `sync-reservations`).

### Config-mutation system — **lives entirely in the agent, not the backend**
`config_manager.py`/`dotenv_editor.py`/`compose_editor.py`/`config_files.py`
(Phase 8C) never touch Central's database. They operate purely on a
project's local files plus a locally-persisted mutation record
(`<project_root>/.portforge/mutations/<id>/record.json`). This is an
important architectural fact for v1.1 planning: the backend has **zero
knowledge** that a config mutation ever happened — Central sees only the
reservation that resulted from the allocation. A Kubernetes extension
(§4) inherits this same shape by default: agent-side file editing, no new
backend surface required, unless v1.1 explicitly wants Central-side
visibility into mutations (a real, separate design question — see that
doc's IN/OUT SCOPE split).

### Migrations
Three Alembic revisions: `6277151d1f2d_initial_schema` (hosts, ports,
reservations, agent credentials), `757214bd00e6_add_activity_events`,
`e53bf191ea97_add_allocations`. All three have real up/down tests
(`tests/test_migrations.py`) including a fresh-DB upgrade and a full
downgrade-to-base. No migration has ever needed correction after landing
(the one bug found during Phase 8A — an unnamed FK constraint breaking
`downgrade()` — was caught and fixed before merge, not after).

## 3. Agent (`agent/portforge_agent/`)

### Discovery/detection (Phases 1-3, unchanged since)
`discovery.py` (native + Docker), `detection/` (project/purpose inference
from `PROJECT_MARKERS`, bounded upward directory walk, size-capped file
reads). `collectors/` has one module per OS (`collector_windows.py`,
`collector_macos.py`, `collector_linux.py`) plus a shared Docker collector
— this is the one area with real per-OS branching, and it's isolated
behind a single `discover_all_ports()` entry point.

### Reservation/recommendation (Phase 4)
Local-only: `reservations/storage.py` (atomic write-temp-then-`os.replace()`,
the pattern every later phase's own atomic-write code was built by
generalizing), `reservations/lock.py` (cross-platform advisory file lock,
`msvcrt`/`fcntl` branch, no third-party dependency), `recommend.py`
(local recommendation, calls the SAME `config.py::DEFAULT_RANGES` a user
can extend via `~/.../config.yml` — note this is a **separate,
locally-configurable range table** from the backend's own
`recommendation_service.DEFAULT_RANGES`; they happen to share default
values today but there is no code-level link between them, a genuine,
documented-since-Phase-8B limitation of the two-deployable split).

### Central sync (Phase 5/6)
`central_client.py` (stdlib `urllib.request` only, deliberately no
`requests` dependency), `central_sync.py` (enroll/heartbeat/observations),
`runtime/` (the foreground/service agent loop), `service_gen.py`/`service_ops.py`
(native Windows Task Scheduler / macOS LaunchAgent / Linux systemd-user
service generation and install).

### Manifest / project / workflow / config (Phase 8A-8D)
`manifest.py` → `project_adapter.py` (the one host resolver,
`resolve_host_ref`, and the one candidate-preview function,
`build_candidate_preview`, shared by both `project plan` and `workflow
prepare`) → `workflow.py` (orchestrates allocate-then-config-apply with
its own persisted idempotency record, `.portforge/workflows/<hash of
request_id>/record.json` — the directory name is a SHA-256 of the raw
`request_id`, not the raw string, specifically closing a path-traversal
vector a `--request-id "../../escape"` would otherwise open) →
`config_manager.py` (Phase 8C, reused unchanged by workflow.py) →
`agent_contract.py` (the versioned machine contract, `contract_version: 1`)
→ `project_init.py` (manifest scaffolding, refuses to overwrite an
existing file).

**`AGENT_VERSION` in `central_sync.py` is a separate hardcoded string
literal (`"1.0.0"`), not derived from `pyproject.toml` / `importlib.metadata`**
the way `agent_contract.py`'s `portforge_version` field is. Two sources of
truth for the same number, today coincidentally in sync — a concrete,
low-risk-to-fix v1.1 item (see `version-compatibility.md`).

### CLI (`cli.py`, 1621 lines)
One `argparse` subparser tree:
`scan/docker/inspect/check/next/reserve/release/reservations/conflicts/
sync-reservations/central/allocate/allocation/project/config/agent-contract/
workflow/agent`. Convention held consistently end-to-end since Phase 4:
`--json` → stdout is JSON-only, stderr carries diagnostics only; exit 0
success / 1 expected-negative / 2 operational error; every structured
error is `{"error": {"code","message","details"}}`. There is no `doctor`
command (§8's proposal is genuinely new, not already covered).

## 4. Dashboard (`dashboard/app/`)

Next.js App Router, one route segment per resource: `activity/`,
`conflicts/`, `diagnostics/`, `hosts/` (+ `[hostId]/`), `ports/`,
`projects/` (+ `[projectId]/`), `recommendations/`, `reservations/`,
`settings/`, plus the overview at `app/page.tsx`. TanStack Query hooks
(`hooks/use-*.ts`) wrap `lib/api/client.ts`. The reservations page performs
a **client-side join**: it fetches reservations (paginated, ≤100) and
separately fetches up to 500 hosts on every page load just to resolve
`host_id` → hostname for display — a real, already-known pattern (see
`docs/v1.1/dashboard-audit.md` and the performance audit for its cost).

**No dashboard surface exists for**: allocations (list/detail/release),
workflow status, or config-mutation status. Phase 8A/8B/8C/8D's data is
only visible indirectly — an allocation-created reservation shows a small
"allocated" badge (added in Phase 8A) with the allocation ID in a tooltip,
but there is no page to browse allocations, workflows, or mutations
directly. This is the dashboard audit's single biggest finding.

## 5. Existing test coverage by layer

| Layer | Count | What's covered | What's thin |
|---|---|---|---|
| Backend | 176 | Every router, every service, real-DB concurrency (`test_allocation_concurrency.py`, `test_ingestion_duplicate_bindings.py`), full migration up/down | No load/scale tests; no test exercises >20 hosts or >1000 reservations |
| Agent | 568 (+1 platform-skipped) | Discovery/detection per collector, manifest/workflow/config engines exhaustively (unit + integration), CLI wiring | Only 1 test is platform-conditional (symlink escape, skipped on this Windows dev box without Developer Mode) — genuine coverage gap on Windows specifically, not just a skip artifact |
| Dashboard | 52 | Components, hooks, a few page-level tests | No allocation/workflow/config UI exists to test, so there's no gap here yet — it's simply unbuilt |

## 6. Extension points relevant to v1.1

- **New CLI subcommand**: `build_parser()` is a flat, additive list of
  `subparsers.add_parser(...)` calls — a new top-level command (e.g.
  `doctor`) or a new `config` sub-subcommand (e.g. `config k8s-plan`) is a
  pure addition, zero risk to existing wiring, proven repeatedly across
  Phases 8A-8D.
- **New manifest top-level key**: `manifest.py`'s `_TOP_LEVEL_KEYS`
  whitelist already demonstrated the additive pattern for `config:` in
  Phase 8C — a `kubernetes:` key (or an extension of `config:`) follows
  the identical shape.
- **New Compose-like editor**: `compose_editor.py`'s `ruamel.yaml`
  round-trip pattern (load → mutate specific nodes in place → dump,
  preserving comments/anchors/style) is directly reusable for Kubernetes
  YAML — see `kubernetes-design.md` for why multi-document YAML changes
  the loading step specifically, not the mutation philosophy.
- **New Central capability without a new authenticated surface**: Phase
  8A/8B/8C/8D all avoided adding new authenticated endpoints by keeping
  mutation logic client-side (config) or reusing the existing
  unauthenticated allocation surface. A remote-probe design (§5) is the
  first v1.1 candidate that would genuinely need NEW backend
  request/response plumbing between Central and a remote agent — worth
  flagging as the one area where "just add a CLI command" isn't enough.

## 7. Observed fragility (encountered during this audit, not a code defect)

The local `portforge-postgres` Docker container reported `healthy` and
accepted `pg_isready` from inside itself, but connections from the host
through the published `55432` port were refused
("server closed the connection unexpectedly") until the container was
restarted. This is standard Docker-Desktop-on-Windows port-forwarding
flakiness (this machine runs a large number of unrelated Postgres
containers for other projects simultaneously), not a PortForge defect —
recorded here only because it produced a false "158 tests skipped" signal
during this audit's own baseline re-verification, and a future contributor
hitting the same thing should know to restart the container before
assuming the backend test suite is broken.

## 8. Risks if modified (summary, expanded per-subsystem in the other v1.1 docs)

- **Allocation/reservation schema**: any change here is a migration, and
  the `allocation_id`/`request_name` reservation columns are already
  load-bearing for the dashboard's badge and for `config_manager.py`'s
  ownership checks — treat as high-risk, additive-only.
- **`agent-contract` shape**: now a public, versioned contract with a
  real (if thin) doc at `docs/agent/`. `contract_version` exists
  specifically so this can change without ambiguity — use it.
- **`portforge.yml` schema**: `_TOP_LEVEL_KEYS`/`_PORT_ENTRY_KEYS`/etc.
  whitelists reject unknown fields by design ("typos fail loudly") — this
  means adding a new top-level key is safe and additive, but it also
  means any v1.1 manifest field MUST be added to the relevant whitelist
  or it will be rejected, not silently ignored (a good property to
  preserve, not a bug to work around).
- **CLI exit-code/JSON conventions**: entirely undocumented as a formal
  spec anywhere except by precedent across `docs/phase8*.md` — worth
  consolidating into one authoritative reference in v1.1-A (see roadmap).
