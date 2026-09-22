# Phase 8A: Agent Allocation API & CLI

This is the external contract for Phase 8A: how a coding agent or tool asks
PortForge Central "I need these ports on this host for this project" and
gets back an atomically-reserved bundle. It is provider-independent — no
specific AI provider is referenced or required anywhere in this design.

See [`phase8a_allocation_audit.md`](phase8a_allocation_audit.md) for the
implementation audit this contract was built against (why each mechanism is
reused rather than duplicated).

## Terminology

- **Recommendation** — a non-binding suggestion (`GET .../recommendations`).
  Nothing is reserved.
- **Reservation** — one binding: a single port claimed for a project on a
  host (`central_reservations` table, unchanged from Phase 4/5).
- **Allocation** — one atomic bundle of one or more reservations, created
  and released together. New in Phase 8A (`allocations` table).

## Concepts

### Atomicity

A bundle is **ALL SUCCESS or NO RESERVATIONS**. Every request in the bundle
is resolved to a candidate port and inserted inside a single database
transaction; the first unresolvable request rolls back the whole bundle
before anything commits. There is no partial allocation.

### Concurrency

Allocation reuses the same PostgreSQL transaction-scoped advisory lock
(`pg_advisory_xact_lock(hashtext(host_id))`) that snapshot ingestion has
used since Phase 6, via `services/host_lock.py`. This is deliberate: both
ingestion and allocation are "the thing that decides what's true about this
host's ports right now," so they serialize against each other for the same
host. Allocations against **different** hosts never block each other. The
lock is acquired before any port/reservation state is read, and host
freshness is re-checked immediately after acquiring it (not before), so the
freshest possible state is used.

### Host scoping and freshness policy

Allocation is refused if Central cannot currently vouch for the target host
as `HEALTHY` (using the same `derive_health_state` logic already powering
host status everywhere else in the dashboard):

- `HOST_STALE` (409) — last-seen age is past the stale threshold. Refused,
  not just logged, because a stale host means Central's own port/reservation
  picture for it may not reflect reality — exactly the state allocation's
  atomicity guarantee depends on being trustworthy.
- `HOST_OFFLINE` (409) — last-seen age is past the offline threshold.

### Remote-host validation limitations

**Central cannot remotely probe any host's sockets.** It never performs a
real `bind()` on behalf of a remote agent, and it does not pretend to —
including for hosts that happen to be co-located with Central itself. Every
allocation response includes an honest `validation` block:

```json
"validation": {
  "snapshot_age_seconds": 4,
  "host_health_state": "HEALTHY",
  "bind_probe": "not_remote_capable"
}
```

`bind_probe` is always `"not_remote_capable"`. Availability is derived from
Central's own reservation table and the host's most recent discovery
snapshot — not a live probe. This is the same honesty discipline the
recommendation engine has used since Phase 4.

### Port selection

Purpose ranges (`frontend`, `api`, `postgres`, `mysql`, `redis`, `generic`,
…) are the same ranges the recommendation engine already uses
(`services/recommendation_service.py::DEFAULT_RANGES`) — there is no second,
duplicated range table for allocation.

`preferred_port` is a **hint, not a demand**. If set and currently free, it
is used as-is. If set and occupied, allocation **falls back gracefully** to
the next available port in the purpose's range — it does **not** raise an
error. This is deliberate: forcing a hard failure here would contradict the
whole point of a hint ("give me this port if you can"). There is no
`PREFERRED_PORT_UNAVAILABLE` error code for this reason.

### Idempotency

An optional client-generated `request_id` makes allocation safe to retry.
It is persisted (`allocations.request_id`, unique-indexed) alongside a
SHA-256 hash of the canonicalized request payload
(`allocations.request_payload_hash`), so idempotency survives a Central
restart:

- Same `request_id` + identical payload → returns the existing allocation
  untouched (no new reservations, no duplicate work).
- Same `request_id` + a **different** payload → `IDEMPOTENCY_CONFLICT` (409).
  Nothing is mutated.

### Release semantics

`DELETE /api/allocations/{id}` releases every still-active reservation that
belongs to the allocation and emits one `RESERVATION_RELEASED` activity
event per reservation (the existing activity event type — not a new one).
Release is **idempotent**: releasing an already-released allocation is a
no-op success, not an error, because a client retrying a release after a
dropped response must not see a confusing failure for work that already
happened.

**Known limitation:** `GET`/release responses reflect the allocation's
*current* reservations (a live join, not a historical snapshot). After
release, `allocations: []` is expected — the response does not retain a
record of which ports the bundle used to hold. This matches the task's own
"current state" framing; a historical snapshot is a possible future
addition, not a Phase 8A requirement.

## API

All endpoints are **unauthenticated**, matching the posture already
established for dashboard write endpoints in Phase 7C.5 (PortForge is a
trusted private/LAN control plane; authentication for coding-agent
allocation is explicitly out of scope for Phase 8A).

### `POST /api/allocations`

```json
{
  "project": "jarvis",
  "host_id": "f90db087-f7b4-4647-958c-e8e13051ddc3",
  "requests": [
    {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
    {"name": "api", "purpose": "api", "protocol": "tcp", "preferred_port": 8080}
  ],
  "request_id": "task-123"
}
```

- `requests`: 1–20 items, unique `name` per bundle (bundle size is capped at
  20 because the whole bundle runs under one host-scoped lock — a larger
  bundle would block other work against that host for too long).
- `protocol`: `tcp` or `udp`, default `tcp`.
- `preferred_port`: optional, 1–65535, a hint (see above).
- `request_id`: optional idempotency key.

Response `201`:

```json
{
  "allocation_id": "b286a2ce-a1d2-4895-b6ce-52dcd0cf0b22",
  "project": "jarvis",
  "host": {"id": "f90db087-f7b4-4647-958c-e8e13051ddc3", "hostname": "workstation"},
  "status": "active",
  "allocations": [
    {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3001, "reservation_id": "..."},
    {"name": "api", "purpose": "api", "protocol": "tcp", "port": 8080, "reservation_id": "..."}
  ],
  "validation": {"snapshot_age_seconds": 4, "host_health_state": "HEALTHY", "bind_probe": "not_remote_capable"},
  "created_at": "2026-09-21T14:13:42.300872Z",
  "released_at": null
}
```

### `GET /api/allocations/{allocation_id}`

Returns the allocation's current state (see "Known limitation" above).
`404 ALLOCATION_NOT_FOUND` if unknown.

### `DELETE /api/allocations/{allocation_id}`

Releases the allocation (idempotent, see above). Returns the released
state (`status: "released"`, `allocations: []`).

## Error contract

Allocation errors never return FastAPI's default `{"detail": "..."}` shape
(that shape is reserved for standard Pydantic schema-validation errors, e.g.
malformed JSON, an out-of-range `preferred_port`, a duplicate request name —
those remain ordinary `422`s). Every allocation-specific failure is:

```json
{"error": {"code": "...", "message": "...", "details": [...]}}
```

Never a stack trace.

| Code | HTTP | Meaning |
|---|---|---|
| `HOST_NOT_FOUND` | 404 | `host_id` does not exist |
| `HOST_STALE` | 409 | Host's last-seen age is past the stale threshold |
| `HOST_OFFLINE` | 409 | Host's last-seen age is past the offline threshold |
| `INVALID_REQUEST` | 422 | Unknown purpose (schema-level errors like bad protocol/port/empty list surface as standard `422`s instead) |
| `ALLOCATION_UNAVAILABLE` | 409 | At least one request in the bundle could not be resolved to a free port; no reservations were created |
| `IDEMPOTENCY_CONFLICT` | 409 | `request_id` reused with a different payload |
| `ALLOCATION_NOT_FOUND` | 404 | Unknown `allocation_id` on GET/DELETE |

## CLI

Provider-independent. No AI-provider-specific flags, env vars, or output
shapes exist anywhere in this surface.

```
portforge allocate --host <hostname-or-uuid> --project <name>
                    --request NAME:PURPOSE[:PROTOCOL[:PREFERRED_PORT]] [repeatable]
                    [--request-id ID] [--url URL] [--json] [--format text|env]

portforge allocate --file portforge.request.json [--url URL] [--json] [--format text|env]
portforge allocate --stdin [--url URL] [--json] [--format text|env]

portforge allocation get ALLOCATION_ID [--url URL] [--json] [--format text|env]
portforge allocation release ALLOCATION_ID [--url URL] [--json]
```

`--host` accepts either a hostname (resolved via `GET /api/hosts`, matched
case-insensitively) or a raw UUID.

### Central URL resolution order

1. `--url` flag
2. `PORTFORGE_CENTRAL_URL` environment variable
3. The bare `url` field of `~/.portforge/central.json` (not gated on that
   file's `enabled`/`token` — allocation needs no token)
4. Otherwise: an operational error (exit code 2)

### JSON request file schema (`--file`)

```json
{
  "project": "jarvis",
  "host": "workstation",
  "requests": [
    {"name": "frontend", "purpose": "frontend", "protocol": "tcp"},
    {"name": "api", "purpose": "api", "protocol": "tcp", "preferred_port": 8080}
  ],
  "request_id": "task-123"
}
```

`host` accepts a hostname or UUID, same as `--host`. `--stdin` reads this
exact same JSON shape from standard input.

### `--json` output

When `--json` is passed, **stdout contains only the JSON response body** —
no human-readable log lines, progress text, or banners are mixed in. This
is verified by `agent/tests/test_cli_allocation.py`
(`test_allocate_via_flags_json_output_is_pure_json` asserts `captured.err
== ""` and that `stdout` parses cleanly as JSON).

### `--format env`

Transforms a successful allocation/get response into `NAME_PORT=value`
lines, one per entry, deriving each variable name from the request's
`name` field: uppercased, any run of non-alphanumeric characters collapsed
to a single underscore, suffixed with `_PORT`. For example `name: "db-1"` →
`DB_1_PORT`. **This name is never executed or evaluated** — it is pure
string transformation (see `cli.py::_env_var_name`). Phase 8A intentionally
only *outputs* these values; it does not write a `.env` file.

```
$ portforge allocate --host workstation --project jarvis \
    --request frontend:frontend:tcp --request api:api:tcp --format env
FRONTEND_PORT=3001
API_PORT=8001
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | A normal, expected negative outcome (allocation refused/unavailable, not found) |
| 2 | An operational or configuration error (bad arguments, unreachable Central, malformed `--file`/`--stdin` input) |

## Recovery after interruption

If a client's connection drops after `POST /api/allocations` was sent but
before the response was received, the safe recovery path is: retry the
**exact same request with the same `request_id`**. If the original request
committed, the retry returns the existing allocation unchanged. If it did
not commit (the request never reached Central, or failed before its single
`db.commit()`), the retry proceeds as a fresh allocation. There is no
"half-allocated" state to reconcile — atomicity guarantees this.

## Dashboard

Reservations created via allocation carry `allocation_id` and
`request_name` (both nullable, additive fields on `ReservationOut` and the
`/reservations` table). The dashboard's Reservations page shows a small
"allocated" badge next to the project name for such rows, with a tooltip
naming the allocation ID and request name. No other dashboard surface was
changed — this was kept deliberately minimal per the Phase 8A scope (no new
dashboard redesign).

## Security/safety review

- **No accidental exposure**: allocation endpoints are unauthenticated by
  design, matching the already-shipped, already-reviewed posture of every
  other dashboard write endpoint since Phase 7C.5 — PortForge is explicitly
  a trusted private/LAN control plane, not a public service.
- **Bounded inputs**: bundle size capped at 20; `name`/`purpose` length-
  bounded; `protocol` restricted to a fixed pattern (`^(tcp|udp)$`);
  `preferred_port` bounded 1–65535; all enforced by Pydantic schema
  validation before any service code runs.
- **No command execution from request strings**: nothing in the allocation
  path (API or CLI) passes any part of a request payload to a shell,
  subprocess, or `eval`. The CLI's `_env_var_name()` sanitizes a request's
  `name` into a safe identifier purely as a string transform for *display*
  output — it is never executed or written to a file automatically.
- **No arbitrary environment-variable names take effect**: `--format env`
  only prints lines to stdout; Phase 8A does not export, set, or persist
  any environment variable, and does not write a `.env` file.
- **No secrets in responses or logs**: allocation responses contain only
  host/project/port/purpose metadata already exposed elsewhere in the API
  (reservations, hosts). No tokens, credentials, or internal paths appear
  in any allocation response or error detail.
- **Errors never leak internals**: `AllocationError` always carries a
  bounded `code`/`message`/`details` triple; unhandled exceptions are not
  given a bespoke allocation-shaped response and fall through to FastAPI's
  standard error handling (no stack traces reach the client).
