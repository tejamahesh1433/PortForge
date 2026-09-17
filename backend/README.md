# PortForge Central (Phase 5)

FastAPI + PostgreSQL central registry: aggregates port observations,
reservations, and Compose-derived project info reported by PortForge
agents across multiple hosts, and offers advisory port suggestions.

**The central server is entirely optional.** Every local `portforge`
command (`scan`, `check`, `next`, `reserve`, `release`, `reservations`,
`conflicts`) works fully offline and never depends on this service being
up, reachable, or even installed — see `agent/README.md` "Offline
behavior". Nothing here can weaken that guarantee; central sync is opt-in,
explicit, and lives entirely in `portforge central *` commands that the
rest of the agent's CLI never imports.

## Architecture

```
backend/app/
  main.py           FastAPI app factory -- wires routers together, nothing else
  config.py          Settings (env vars, PORTFORGE_-prefixed)
  database.py         SQLAlchemy engine/session + get_db() dependency
  models/            SQLAlchemy ORM (one table = one bounded concern)
  schemas/            Pydantic request/response contracts -- separate from models/
  repositories/        The only place that issues a query (routes/services never do)
  services/             Business logic: enrollment, ingestion, reservation sync, ...
  security/              Token generation/hashing + FastAPI auth dependencies
  api/                    Routers, one file per resource
alembic/              Migrations (the only way schema changes are applied)
tests/                Backend test suite (a real PostgreSQL database, see below)
```

Routes are thin (parse → call one service function → map to a response
schema); services hold the actual rules; repositories hold the actual
queries. See each package's `__init__.py` docstring for the exact
division of responsibility.

### Shared domain types

`Protocol`, `PortState`, and `Confidence` are imported directly from the
already-installed `portforge_agent` package (`app/models/base.py`) rather
than redefined here — a narrow, one-directional dependency (the central
server depends on the agent library for these pure value types; the agent
never imports anything from `backend`). A third shared package was
considered and deliberately not created: it would mean refactoring
already-working, already-tested Phase 1-4 agent code purely to extract
three enum classes, for no benefit `backend` depending on the existing,
stable `portforge_agent` package doesn't already provide. `DiscoveredPort`
and the agent's local `Reservation` are **not** shared as literal classes
with their central counterparts — a local, ephemeral, JSON-backed
dataclass and a persisted, relational ORM row serve genuinely different
purposes; sharing field *vocabulary* (the same names, the same meaning)
without forcing one class to serve two bounded contexts is the intended
design, not an oversight.

## Persistent host identity

The central server never mints its own host identifier — `Host.id` *is*
the agent's own persistent UUID (see `agent/portforge_agent/identity.py`),
generated once locally and sent during enrollment. `hostname` is
deliberately **not** a unique/lookup key: two different machines can
legitimately share a hostname (a common default, a VM template, a renamed
clone), and identity must never be inferred from it — see
`tests/test_multihost_simulation.py::test_duplicate_hostname_different_uuids_are_independent_hosts`.

## Database model

Seven tables (`alembic/versions/..._initial_schema.py`):

- `hosts` — one row per enrolled machine (keyed by its agent-generated UUID).
- `enrollment_tokens` / `agent_credentials` — see "Authentication" below.
- `current_port_observations` — exactly one row per (host, port, protocol,
  bind_address) currently believed active.
- `port_observation_events` — append-only history of *meaningful changes*
  only (see "Current state vs. history").
- `central_reservations` — one row per synchronized reservation; the same
  (port, protocol) may appear for many different hosts (never a conflict
  — see "Reservation synchronization").
- `scans` — one row per accepted snapshot submission, used for
  idempotent-replay detection.

### Current state vs. history

Two tables, not one row-per-scan log growing forever:

- Every accepted snapshot **upserts** `current_port_observations` to match
  exactly what the agent reported this time (full-snapshot semantics — see
  `services/ingestion_service.py`'s module docstring for the complete
  write-up).
- `port_observation_events` only grows when something **meaningfully
  changes**: a binding appearing, disappearing, or its `state`/
  `process_name`/`container_id`/`project_name`/`purpose` changing. An
  agent re-confirming the exact same state 30 seconds later updates
  `last_seen`/`observed_at` on the current row and writes **zero** new
  history rows — this is what keeps history bounded while still answering
  "when was port 8000 last used" (the most recent `disappeared` event's
  `occurred_at`, or the current row's `last_seen` if still active).

### Snapshot identity & staleness

Each submission carries an agent-generated `scan_id` (UUID) and
`observed_at`. A **duplicate** `scan_id` (a network-retry resubmission) is
accepted idempotently without reprocessing. A submission whose
`observed_at` is **not newer** than the host's last accepted scan is
rejected outright (HTTP 409) — protects against a delayed, out-of-order
submission overwriting newer state with stale data. A numeric `sequence`
counter was considered and intentionally not added: it would require new
persistent agent-side state purely for ordering, when every scan already
carries a timestamp sufficient for this purpose (the project brief
explicitly allows this: "sequence number if appropriate").

## Reservation synchronization

The agent's **local** reservation file remains authoritative for local
allocation safety — `portforge reserve`/`next --reserve` never consult the
central server, and never will by design. The central registry is a
synchronized *mirror*. A sync upsert is keyed by the agent's own
`local_reservation_id`: re-syncing the same local reservation updates the
matching central row in place; a *different* local reservation attempting
to claim a binding another local reservation already occupies centrally is
refused (HTTP 409), mirroring the agent's own "never silently overwrite
another project's reservation" rule. The same (port, protocol) reserved on
two different hosts is explicitly valid and never flagged — see
`services/reservation_service.py`'s module docstring.

## Central suggestion vs. locally verified recommendation

**The central server can never claim a port is "verified available."** It
only has cached observations and reservations on file for a host — it
cannot run fresh discovery, check the agent's local reservation file, or
attempt a real socket bind on that machine. `GET /api/recommendations`
therefore always returns `verification: "central_suggestion"`, with a
`basis` string explaining exactly that, and the schema already has a
`"locally_verified"` value reserved for a future phase where the central
server can ask a specific *online* agent to perform a live check and
report back — no API-shape change will be needed for that. Until then,
the correct client behavior is: use a central suggestion to pick a
*candidate*, then run `portforge check <port>` **on the target host**
before actually using it.

## Authentication / enrollment

A practical private-deployment scheme — not OAuth (unnecessary complexity
for a small number of machines on a network the admin controls):

1. An admin mints a one-time enrollment token
   (`POST /api/agent/enrollment-tokens`, requires
   `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` as a Bearer credential). Only a
   SHA-256 hash is stored; the raw token is printed once and never
   retrievable again.
2. A new agent calls `POST /api/agent/enroll` with that token plus its own
   persistent host UUID and basic host facts.
3. The server verifies the token is unexpired and unconsumed, registers
   the host, issues a brand-new per-host agent credential (revoking any
   prior one for that host), and marks the enrollment token consumed — it
   can never be replayed to enroll a second host.
4. The agent stores its raw per-host token in a protected local file
   (`central.json` in the PortForge data directory — never in a project's
   `.portforge.yml`) and sends it as `Authorization: Bearer <token>` on
   every future call.

Every subsequent authenticated route cross-checks the `host_id` a request
body claims against the identity the bearer token actually proved, and
rejects a mismatch (403) — one host's credential can never write another
host's data. No token is ever logged, echoed in a response, or included in
an exception message.

## API overview

```
GET  /api/health                              -- non-sensitive status
GET  /api/hosts                                 (paginated)
GET  /api/hosts/{host_id}
GET  /api/hosts/{host_id}/ports
GET  /api/ports?port=&project=&purpose=&source=  (paginated) -- "where is port X used"
GET  /api/projects                               -- aggregated by project_name (see below)
GET  /api/reservations?host_id=&port=&project=   (paginated, public read)
POST /api/reservations                            (agent-authenticated; acts on the caller's own host)
DELETE /api/reservations/{id}                      (agent-authenticated; own reservations only)
GET  /api/recommendations?host_id=&service_type=   -- always a central_suggestion, see above
GET  /api/conflicts?host_id=                        -- same-host reservation-vs-active mismatches only
POST /api/agent/enrollment-tokens                    (admin-authenticated)
POST /api/agent/enroll
POST /api/agent/heartbeat                             (agent-authenticated)
POST /api/agent/observations                           (agent-authenticated)
```

Interactive docs at `/docs` (Swagger UI) once the server is running.

### Projects and services

A conservative identity strategy on purpose (per the project brief): a
"project" is simply a distinct `project_name` string seen across current
observations, aggregated with which hosts/ports/services currently report
it. Two hosts using the same name are treated as the same project for
display — there is no separate cross-host identity-resolution system.

## Configuration

Every setting is an environment variable, `PORTFORGE_`-prefixed (see
`app/config.py`), optionally from a `.env` file:

| Variable | Default | Purpose |
|---|---|---|
| `PORTFORGE_DATABASE_URL` | *(unset)* | Full URL; wins over the pieces below if set |
| `PORTFORGE_DB_HOST` | `localhost` | |
| `PORTFORGE_DB_HOST_PORT` | `55432` | The **host-published** Postgres port — deliberately not 5432 |
| `PORTFORGE_DB_NAME` / `_USER` / `_PASSWORD` | `portforge` | |
| `PORTFORGE_API_HOST_PORT` | `58000` | The API's own published port |
| `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` | *(required)* | Needed to mint enrollment tokens; never defaulted |
| `PORTFORGE_MAX_OBSERVATIONS_PER_SNAPSHOT` | `5000` | Ingestion batch cap |

## Choosing ports (never a blind guess)

Per the project brief, host ports here were **not** hardcoded to a common
default. On the real development machine: `portforge next postgres` and
`portforge next api` were run first to confirm a genuinely free port
existed; the docker-compose defaults (`55432`, `58000`) were then chosen
deliberately *higher* than those live recommendations specifically to stay
clear of the standard `5432`/`8000`-ish ranges most local dev tools
default to (lower collision risk over time, not just right now) — verified
free with `portforge check` before use, and reserved locally with
`portforge reserve 55432 --project portforge-backend --service postgres`
(and the same for `58000`) so PortForge itself tracks its own central
server's ports. Override either at any time:

```bash
PORTFORGE_DB_HOST_PORT=<port> PORTFORGE_API_HOST_PORT=<port> docker compose up
```

## Running locally

### Docker Compose (Postgres + API)

```bash
cp .env.example .env   # set PORTFORGE_ADMIN_BOOTSTRAP_TOKEN
docker compose up -d
```

### Or: Postgres via Compose, API directly (faster local iteration)

```bash
docker compose up -d portforge-postgres
cd backend
pip install -r requirements-dev.txt   # includes `-e ../agent`
alembic upgrade head
PORTFORGE_DB_HOST=localhost PORTFORGE_DB_HOST_PORT=55432 \
PORTFORGE_ADMIN_BOOTSTRAP_TOKEN=<your-token> \
uvicorn app.main:app --port 58000
```

## Alembic workflow

```bash
alembic revision --autogenerate -m "description"   # after changing models/
alembic upgrade head                                 # apply
alembic downgrade -1                                   # roll back one step
alembic current                                          # show applied revision
```

`alembic/env.py` builds the database URL from the same `Settings` the app
itself uses — migrations and the running app can never point at two
different databases by accident. `create_all()` is never used as a
substitute for a real migration in production code (it's used only inside
the test suite's fixtures, purely for speed — migration correctness itself
has its own dedicated test, `tests/test_migrations.py`, which runs the
real Alembic upgrade path).

## Tests

Require a reachable PostgreSQL (a dedicated `portforge_test` database,
created/dropped automatically — never the development database):

```bash
docker compose up -d portforge-postgres
cd backend
pip install -r requirements-dev.txt
PORTFORGE_DB_HOST=localhost PORTFORGE_DB_HOST_PORT=55432 \
PORTFORGE_ADMIN_BOOTSTRAP_TOKEN=test-admin-bootstrap-token \
python -m pytest tests/ -v
```

If PostgreSQL isn't reachable, the whole backend test session is skipped
with a clear reason rather than failing confusingly.

## Known limitations

See the Phase 5 completion report and `docs/architecture.md` for the full
list — highlights: no real-time push (agents must poll/sync on their own
schedule); `/api/recommendations` is always advisory; central reservation
sync is one-directional (local → central; the local file is never updated
from central data); no frontend/dashboard yet (Phase 6+); no automatic OS
service installation or persistent background agent loop yet.
