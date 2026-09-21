# PortForge Architecture

## Components

1. **Agent** (`agent/`) — cross-platform local port discovery, Docker/Compose
   awareness, project/purpose detection, local reservations/conflict
   detection/recommendation, and optional central sync. Runs on each
   machine. Implemented (Phases 1-5).
2. **Backend** (`backend/`) — FastAPI + PostgreSQL central registry.
   Aggregates observations and reservations reported by agents across
   hosts, so reservations and port usage become visible across a user's
   machines. Entirely optional -- every local agent command works fully
   offline (see agent/README.md "Central sync"). Implemented (Phase 5):
   host registry, authenticated ingestion, reservation sync, advisory
   recommendations, and activity history.
3. **Dashboard** (`dashboard/`) — Next.js + TypeScript SPA dashboard
   (React Query, Tailwind CSS, shadcn/ui). Implemented (Phases 6-7). Consumes the
   backend API to provide cross-host visibility into port usage, offline host
   detection, project conflict analysis, and global port search.
4. **CLI** (`cli/`) — `portctl`, a local command-line client that talks to
   the agent and/or backend. Not yet implemented (the agent's own `cli.py`
   already covers this ground locally, including the new `portforge
   central *` commands -- a separate `portctl` package remains a later
   packaging decision, not a capability gap).

## Core port model

Every discovered port is normalized into a single shape regardless of which
OS or source produced it. Fields split into two conceptual groups (see
"Detection architecture" below for why this distinction is structural, not
just documentation):

**Raw facts** — what a collector actually observed:
- `hostname`, `host_id`, `operating_system`
- `port` (= `host_port`, kept for backward compatibility), `protocol` (tcp/udp), `bind_address`
- `pid`, `process_name`, `process_path`, `working_directory`, `command_line`,
  `parent_pid`, `parent_process_name`, `parent_working_directory`
- `source` (process / docker / system / reservation)
- `container_id`, `container_name`, `docker_compose_project`,
  `container_image`, `container_status`, `docker_networks`, `docker_labels`,
  `container_command`
- `host_port`, `container_port`
- `state` (ACTIVE / FREE / RESERVED / CONFLICT / SYSTEM), `first_seen`, `last_seen`

**Inferred** — a conclusion, always paired with an explanation:
- `project_name`, `service_name`, `purpose`, `category`
- `detection` — `{confidence, method, evidence[]}`, explaining how/why

Availability is always evaluated **per host** — the same port number in use
on two different machines is not a conflict.

### Host ports vs. container ports (Phase 2)

Docker host ports and container ports are tracked as **distinct fields**,
never collapsed into one ambiguous number:

```
0.0.0.0:5444 -> container 5432/tcp
    host_port = 5444        (occupied on the host)
    container_port = 5432   (NOT occupied on the host merely because the
                              container listens on it internally)
```

A Dockerfile `EXPOSE 8000` does not, by itself, occupy host port 8000 —
Docker only reports host-port usage for ports actually **published**
(`-p`/`ports:`), which is exactly what `NetworkSettings.Ports` from
`docker inspect` distinguishes (`null` = exposed only, a binding list =
published). See [`agent/README.md`](../agent/README.md) for the full
Docker discovery design, including how native OS-level observations and
Docker observations of the same host socket are merged into a single
record (Docker metadata authoritative, native process info kept as
secondary detail) rather than shown as two unrelated occupied ports.

## Detection architecture (Phase 3)

A dedicated `agent/portforge_agent/detection/` package interprets raw facts
into `project_name`/`purpose`/`category`. It is intentionally isolated from
every collector: **collectors collect facts, detection interprets them** —
no OS collector, the Docker collector, or the CLI contains project/purpose
logic. `discovery.py` calls `detection.enrich_ports()` as the very last step
of `discover_all_ports()`, after native+Docker merge, so enrichment can
never influence discovery or merge correctness (and `discover_ports()`, the
Phase 1 native-only entry point, stays completely unenriched and untouched).

Detection is evidence-based and explainable: every inferred field is paired
with a `confidence` (`high`/`medium`/`low`/`unknown` — deliberately not a
numeric score, since there's no documented model that would make a number
meaningful here), a `method`, and an `evidence` list of plain-English
strings. Project naming follows a strict, documented priority order (Docker
Compose labels first, then an explicit `.portforge.json` override, then a
manifest's declared name, then a Git repo root, then a generic directory
marker, else unknown); purpose/category detection resolves multiple
evidence sources deterministically (agreement -> high confidence, conflict
-> majority vote at reduced confidence, true tie -> unknown) — never
"whichever rule happened to run first." Full detail, including the exact
evidence-hierarchy and conflict-resolution rules, is in
[`agent/README.md`](../agent/README.md).

Detection never executes, imports, or evaluates anything it reads (only
`json.loads`/`tomllib`/regex on size-capped file reads), never scans the
whole disk (bounded ancestor traversal from a process's working directory,
configurable depth, and never reaching the user's home directory — a real
false-positive source found and fixed during Phase 3 validation), and
caches within a scan so repeated manifest reads are never redone.

## Reservations, conflict detection & recommendation (Phase 4)

Phase 4 adds a local, per-host allocation layer on top of discovery,
following the same separation-of-concerns discipline as Phases 2-3: new
modules (`evaluate.py`, `bindprobe.py`, `recommend.py`, `reserve_ops.py`,
`reservations/`, `config.py`, `paths.py`) never touch collector or
detection code, and nothing in discovery/detection knows reservations
exist.

**State model**: combining a live discovery record with a reservation
produces one of `FREE` / `ACTIVE` / `RESERVED` / `CONFLICT` / `SYSTEM`.
The rule that matters most: a listener whose project Phase 3 could not
identify is **never** assumed to match a reservation — unknown ownership
is conservatively treated as a conflict, the same discipline Phase 3
already applies to project/purpose detection itself, now with a stronger
consequence attached to getting it wrong.

**Three-layer validation** governs every recommendation: fresh discovery
must show the candidate `FREE`, no reservation/exclusion may apply, and a
real, temporary socket bind must actually succeed (never `SO_REUSEADDR`/
`SO_REUSEPORT`, since their cross-platform semantics could make an
occupied port look free — the opposite of what the probe exists to
guarantee). All three, or the candidate is skipped.

**Storage**: reservations live in a small local JSON file (a platform-
appropriate user data directory — never PostgreSQL/SQLite at this scale),
written atomically (temp file + `fsync` + `os.replace()`), and never
silently discarded when malformed — a bad file raises a clear error
instead of being dropped or overwritten. A cross-platform advisory file
lock (stdlib `msvcrt`/`fcntl`, no new dependency) serializes reservation
read-modify-write cycles so two concurrent PortForge invocations can never
allocate the same port — proven with a real concurrent-threads test
against the actual lock file.

Full detail — the evidence/confidence-style conservative rules, the exit
code table, configuration precedence, and known limitations — is in
[`agent/README.md`](../agent/README.md).

## Central registry, persistent identity & multi-host API (Phase 5)

A new, entirely optional component (`backend/`), and one change to the
agent that everything else in Phase 5 depends on:

**Persistent host identity.** `agent/portforge_agent/identity.py` gives
every installation a UUID, generated once and stored locally (same
directory as reservations.json), surviving restarts and hostname changes.
`platform.get_host_id()` now returns this UUID instead of the Phase 1-4
hostname -- every caller already treated it as an opaque string (nothing
compared it against a literal hostname), so this is safe for existing
behavior *given* the one-time, idempotent migration
(`reservations/migration.py`) that rewrites any Phase 4 reservation's
`host_id` from the old hostname value to the new UUID, preserving
`reservation_id`/timestamps/ownership exactly.

**The central server never mints its own host ID** -- `Host.id` in
PostgreSQL literally *is* the agent's UUID, sent during enrollment.
`hostname` is deliberately not a unique/lookup key (two machines can
legitimately share one).

**Full separation, not a feature flag.** Every local command
(`scan`/`check`/`next`/`reserve`/`release`/`reservations`/`conflicts`)
is completely unaware the central server exists -- there is no runtime
"is central enabled" check inside any of that already-working Phase 1-4
code. Central sync lives entirely in `central_config.py`/
`central_client.py`/`central_sync.py` and the separate `portforge central
*` commands. This is what makes "local operation never depends on the
central server" true architecturally, not just true by convention.

**Current state vs. history**, and **central suggestion vs. locally
verified recommendation**, are both load-bearing design decisions with
their own full write-ups in [`backend/README.md`](../backend/README.md) --
notably: the central server can *never* claim a port is verified
available, since it only has cached data and cannot run fresh discovery,
check a local reservation file, or attempt a real bind on a remote
machine. Every `GET /api/recommendations` response is explicitly labeled
`"central_suggestion"`.

**Authentication** is a practical private-deployment bearer-token scheme
(enrollment token → per-host credential, hashed at rest, never logged) --
full flow in `backend/README.md` "Authentication / enrollment".

## Phase status

| Phase | Scope                                             | Status      |
|-------|----------------------------------------------------|-------------|
| 1     | Cross-platform local port discovery engine (agent) | Implemented |
| 2     | Docker and Docker Compose intelligence (agent)     | Implemented |
| 3     | Automatic project, service, and purpose detection (agent) | Implemented |
| 4     | Port reservations, conflict detection, and recommendation (agent) | Implemented |
| 5     | Central registry, persistent host identity, multi-host API (agent + backend) | Implemented |
| 6-7   | Frontend dashboard UI, cross-host search, activity history, health monitoring (dashboard + backend) | Implemented |
| 8+    | `portctl`, automatic OS service installation, persistent background agent loop, remote process/container control | Not started |

See [`agent/README.md`](../agent/README.md) and
[`backend/README.md`](../backend/README.md) for full Phase 1-5 details.
