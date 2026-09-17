# PortForge Agent — Phases 1-5

Cross-platform local port discovery engine. Discovers TCP/UDP ports
currently in use on this machine, identifies the owning process (Phase 1),
enriches Docker-published ports with authoritative container/Compose
metadata (Phase 2), infers which project/service/purpose each port belongs
to with an explainable confidence and evidence trail (Phase 3), manages
**reservations, conflict detection, and port recommendation** (Phase 4),
and can optionally sync to a **central registry** for multi-host
visibility (Phase 5) -- while remaining fully local-first throughout.

Discovery remains **read-only**: it never terminates a process, container,
starts, stops, or modifies anything running. Phase 4 adds local, explicit,
user-initiated state (reservations) but is equally conservative about other
processes: it never binds a real listening socket except a momentary,
immediately-closed probe (see "Bind probe" below), and it never overwrites
another project's reservation. **Phase 5's central server is entirely
optional and can never affect local behavior** -- every command above
still works identically with the central server stopped, unreachable, or
never configured at all; see "Central sync (Phase 5)" below.

## How it works

```
portforge_agent/
  platform.py          Dynamic OS detection (Windows / macOS / Linux)
  paths.py               Platform-aware local data directory (Phase 4)
  config.py               Recommendation ranges + exclusions + project config (Phase 4)
  models.py             Normalized DiscoveredPort data model
  discovery.py           Picks the right collector for this OS, dedupes results,
                           runs Docker discovery, merges native + Docker, enriches
  evaluate.py             Combines discovery + reservations into FREE/ACTIVE/
                            RESERVED/CONFLICT/SYSTEM (Phase 4)
  bindprobe.py             Real socket bind-availability probe (Phase 4)
  recommend.py              Three-layer port recommendation engine (Phase 4)
  reserve_ops.py             reserve()/release()/sync ownership rules (Phase 4)
  collectors/
    base.py               Collector interface + shared process-metadata helper
    windows.py             psutil-based collector (native WinAPI under the hood)
    macos.py                lsof-based collector
    linux.py                 ss-based collector
    docker.py                 docker inspect-based collector (isolated, optional)
  detection/            Project/purpose enrichment layer (Phase 3) -- interprets
                          raw facts, never gathers them; see below
    models.py              Confidence, DetectionInfo, DetectionFacts, result types
    evidence.py              safety-bounded manifest reading + bounded directory walk
    project.py                native (filesystem) project-name detection
    purpose.py                 rule-based purpose/category detection
    __init__.py               enrich_port()/enrich_ports(): combines the above
  reservations/          Local reservation model + storage + locking (Phase 4)
    models.py               Reservation dataclass
    storage.py                JSON file storage, atomic writes, schema versioning
    lock.py                    cross-platform (msvcrt/fcntl) advisory file lock
  cli.py                CLI: scan/docker/inspect (Phases 1-3) + check/next/reserve/
                          release/reservations/conflicts/sync-reservations (Phase 4)
```

Each OS collector returns the same normalized `DiscoveredPort` objects
regardless of OS, so nothing outside `collectors/` needs to know which
platform it's running on. Docker logic lives entirely in `collectors/docker.py`
— it is never mixed into the Windows/macOS/Linux collectors. Phase 4
follows the same separation: `evaluate.py`/`bindprobe.py`/`recommend.py`/
`reserve_ops.py` never touch collector code, and no collector or the
`detection/` package knows reservations exist.

| Platform | Underlying source                                         |
|----------|------------------------------------------------------------|
| Windows  | `psutil.net_connections()` (native `GetExtendedTcpTable`/`GetExtendedUdpTable`, no admin required) |
| macOS    | `lsof -iTCP -sTCP:LISTEN` and `lsof -iUDP`                 |
| Linux    | `ss -tulnp`                                                 |
| Docker   | `docker ps -q` + `docker inspect` (any OS, when Docker is available) |

Only **listening** TCP sockets and **bound** UDP sockets are reported — not
arbitrary outbound/ephemeral connections — matching "which ports are
currently being used by a service on this host."

Process executable path and working directory are enriched via `psutil`
after the OS-specific tool identifies the owning PID. If a process
disappears mid-scan or its metadata can't be read (permission denied), the
port is still reported — just with whatever fields could be resolved.

## Docker discovery (Phase 2)

Docker awareness is **entirely optional**. PortForge works normally — native
discovery is unaffected — whether or not:

- the Docker CLI is installed
- Docker Desktop / the daemon is running
- the current user has permission to talk to Docker
- a `docker` command times out or returns malformed output

Any of the above is logged and treated as "zero Docker ports this scan," not
an error.

**Authoritative metadata, not process-name guessing.** Rather than inferring
container ownership from process names like `com.docker.backend.exe`,
`com.docker.backend.exe`, or `docker-proxy`, the Docker collector asks Docker
directly:

1. `docker ps -q` — IDs of currently running containers.
2. `docker inspect <ids...>` — full container metadata in one batched call:
   `NetworkSettings.Ports` (actual published host bindings),
   `Config.Labels` (Compose project/service, when present), `Config.Image`,
   `State.Status`, `NetworkSettings.Networks`.

### Host ports vs. container ports

These are tracked as **distinct fields**, never collapsed:

- `host_port` — the port actually occupied on this host (e.g. `5444`)
- `container_port` — the port the process listens on *inside* the container (e.g. `5432`)
- `port` — kept for Phase 1 backward compatibility; always equals `host_port`

A container `EXPOSE 8000` in its Dockerfile does **not** mean host port 8000
is occupied — Docker's own `NetworkSettings.Ports` reports that as `null`
(exposed, not published), and PortForge correctly reports nothing for it.
Only an actual `-p`/`ports:` publication counts as host-port usage.

Bindings are handled per-entry, so a single container port with multiple
host bindings (Docker Desktop's typical dual-stack `0.0.0.0` + `::`
publication) becomes multiple `DiscoveredPort` records, one per binding —
and a container publishing multiple ports produces one record per binding
per port.

### Native + Docker merge

Windows in particular often attributes a Docker-published port to Docker
Desktop's backend process (`com.docker.backend.exe`) rather than the actual
application container, since Docker Desktop runs containers inside a
lightweight VM. PortForge merges the native observation and the Docker
observation of the *same host socket* into a single record instead of
showing two unrelated occupied ports:

- Docker's ownership metadata (container id/name, Compose project/service,
  image, status, networks, labels, `container_port`) **takes precedence**.
- The native process's `pid` / `process_name` / `process_path` /
  `working_directory` are **kept as secondary information**, not discarded —
  so `com.docker.backend.exe` still shows up as *who the OS thinks holds the
  socket*, next to *what Docker says actually owns it*.
- The native raw connection state (`LISTEN`) is preferred over the
  container's status string, since it is the more precise fact about the
  socket.

Matching is done by `(protocol, bind_address, host_port)` — **never by
PID**, since a container's host-visible PID (or a proxying process's PID)
has no reliable relationship to the process inside the container, especially
under Docker Desktop's VM backend on Windows/macOS. Every collector
(Windows/macOS/Linux and Docker) normalizes wildcard address notation
(`*`, `""`) down to canonical `0.0.0.0` / `::` before a record is created, so
the same real socket always arrives at the merge step with the same address
spelling on both sides, while genuinely distinct bindings (`127.0.0.1`, a
specific LAN IP, `::1`) stay distinct because they simply aren't string-equal.
A Docker port with no native counterpart (e.g. a permission gap in native
discovery) is still shown, with `pid: null`. A native port with no Docker
counterpart passes through unchanged. Full conflict detection across
observations is Phase 4 — this phase only avoids showing the same socket
twice.

### Docker Compose detection

`com.docker.compose.project` / `com.docker.compose.service` labels are read
when present. Not every container is a Compose container — a plain
`docker run` container simply has `docker_compose_project` /
`service_name: null`, which PortForge never assumes otherwise.

## Project, service & purpose detection (Phase 3)

Every collector (and Docker) only ever gathers **RAW FACTS**: process name,
executable path, working directory, command line, parent process, container
image/labels/networks/resolved command. `portforge_agent/detection/`
**interprets** those facts into `project_name`, `purpose`, `category`, and a
`detection` block explaining *why* — and never writes back into a raw fact
to do so. An unrecognized process/container legitimately stays unknown
rather than getting an invented project name or purpose (see "Be
conservative" below).

### Evidence hierarchy (project naming)

Highest-priority evidence wins, evaluated across the *whole* bounded
ancestor chain before falling through to the next tier (so an explicit
override two levels up always outranks a manifest name one level up —
depth only breaks ties *within* a tier):

1. **Docker Compose project label** (`com.docker.compose.project`) — always
   authoritative when present; native filesystem detection is never
   consulted for a Compose container. A non-Compose container's *name* is
   deliberately **not** used as a project name (a container is not
   inherently "a project").
2. **An explicit `.portforge.json` / `.portforge.yml`** file with a
   `project` key — lets a user force/override detection for their own repo.
   (The YAML variant recognizes only a minimal top-level `project: <name>`
   key, not full YAML — this avoids adding a YAML dependency for one field.)
3. **A project manifest's own declared name** — `package.json`/`composer.json`
   `"name"`, `pyproject.toml` `[project].name`/`[tool.poetry].name`,
   `Cargo.toml` `[package].name`, `go.mod`'s `module` line, or `pom.xml`'s
   `<artifactId>` (with `<parent>` stripped first so a parent POM's
   artifactId isn't picked by mistake).
4. **The name of the nearest ancestor directory containing a `.git` folder.**
5. **The name of the nearest ancestor directory containing any other
   recognized project marker** (`pnpm-workspace.yaml`, `yarn.lock`,
   `package-lock.json`, `requirements.txt`, `Pipfile`, `poetry.lock`,
   `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle(.kts)`,
   `settings.gradle`, `composer.json`, `Gemfile`, `docker-compose.y(a)ml`,
   `compose.y(a)ml`).
6. **Unknown** — no marker found within the bounded traversal.

Traversal starts at the process's working directory (falling back to its
parent process's working directory if unavailable) and walks upward at
most `PORTFORGE_MAX_TRAVERSAL_DEPTH` parent levels (default 6, configurable
via that environment variable). The walk **never reaches or crosses the
user's home directory** — see "Known limitations" for why this matters in
practice, not just in theory.

### Purpose / category detection

Rule-based and centralized in `detection/purpose.py`. Four independent
evidence sources are checked, always in this fixed order:

1. **process name / container image identity** — an exact match against a
   curated list of known infrastructure software (`postgres`, `mysqld`,
   `redis-server`, `memurai`, `nginx`, `traefik`, `caddy`, `grafana`,
   `prometheus`, `ollama`, `minio`, `rabbitmq-server`, `kafka`, ...).
2. **a dependency actually declared in a project manifest** — e.g.
   `fastapi`/`flask`/`django` in `pyproject.toml`/`requirements.txt`, or
   `next`/`vite`/`express`/`react-scripts` in `package.json`.
3. **a distinctive tool name in the command line** — `uvicorn`, `gunicorn`,
   `vite`, `next dev`/`next start`, `react-scripts`, `manage.py`, etc.
   (generic wrapper phrases like `npm run dev` are deliberately **not**
   matched — too common to carry real information).
4. **an unambiguous Docker Compose service-name keyword** — e.g. a service
   named `frontend-ui` or `backend-api`.

**Be conservative.** A bare interpreter never implies a purpose on its own:
`python.exe` does not imply `api`, `node.exe` does not imply `frontend`,
`java` does not imply `backend`. `uvicorn` in a command line alone yields
the generic category `api` — only a *manifest-declared* `fastapi`
dependency earns the specific `purpose: fastapi`.

**Conflict resolution is deterministic, never "whichever rule ran first":**
if every signal that fired agrees on the same category, confidence is HIGH
(a single signal is already enough on its own — e.g. a process literally
named `mysqld.exe` is decisive). If signals disagree, the category with the
most supporting signals wins at a *downgraded* MEDIUM confidence, with all
evidence (including the disagreeing signal) kept visible. An exact tie
between categories is left `unknown` at LOW confidence rather than
guessing.

### Confidence meanings

Not a numeric score — there's no documented model that would make e.g.
0.73 vs 0.68 meaningful here — so four understandable buckets instead:

| Confidence | Meaning |
|---|---|
| `high` | A single strong, specific piece of evidence (or several agreeing) — safe to trust. |
| `medium` | Evidence exists but something about it disagreed with another source. |
| `low` | Only weak/generic evidence (e.g. a bare project-marker file with no declared name). |
| `unknown` | No usable evidence at all. |

A record's overall `detection.confidence` is the **weaker of** its project
and purpose confidences, **excluding `unknown`** — an explicitly-undetected
field (e.g. no project found) is an honest absence, not a "weak claim" that
should drag down a purpose detection you're actually confident about. This
is why a record can show `purpose: mysql` at `confidence: high` with no
project at all (matching the project brief's own worked example).

### Safety limits

- **Never executes, imports, or evaluates** anything it reads — only
  `json.loads` / a read-only TOML parser (`tomllib`, stdlib on Python
  3.11+) / plain-text regex.
- **Never scans the whole disk** — only a process's own working directory
  and a bounded number of parent directories.
- **Every manifest read is size-capped** (512 KiB) *before* parsing, so a
  huge or hostile file is skipped, not slow-parsed.
- **Every read degrades gracefully** — permission errors, missing files,
  malformed JSON/TOML, and encoding errors all become "no evidence here,"
  never an exception.
- **Never runs `npm`, `pip`, `git`, or any other tool** — markers are
  recognized purely by filename.
- **Caches within a scan**: one `DetectionCache` per `discover_all_ports()`
  call, shared across every port, so a directory listing or manifest parse
  is never repeated for the same path twice in one scan.

### Filtering

`scan` supports `--port`, `--project`, `--source`, `--purpose`, and any
combination of them, applied to the already-enriched records (never by
re-running a specialized scanner):

- `--port` — exact numeric match.
- `--source` — case-insensitive exact match (`process`/`docker`/`system`).
- `--project` — case-insensitive **substring** match.
- `--purpose` — case-insensitive **substring** match against *both*
  `purpose` and `category` (since the default table's PURPOSE column shows
  `category` — see below — typing what you see should just work).

### `inspect` command

`python -m portforge_agent inspect <port>` shows full discovery + detection
detail for every record matching that host port — ownership, Docker detail,
native process detail, and the full evidence trail. If several records
match (different bind addresses — IPv4 vs IPv6, multiple interfaces), **all
of them are shown**, never collapsed or hidden.

## Reservations, conflicts & recommendation (Phase 4)

### Reservation storage

Reservations are stored locally, per host, at a platform-appropriate
user-level data directory (never a hardcoded path or username):

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\PortForge\reservations.json` |
| macOS | `~/Library/Application Support/PortForge/reservations.json` |
| Linux | `$XDG_DATA_HOME/portforge/reservations.json`, else `~/.local/share/portforge/reservations.json` |

Plain JSON, not SQLite/PostgreSQL: at this scale (a local list of reservations
under an explicit lock) a real database adds a dependency and complexity
without solving anything JSON + atomic-replace doesn't already solve. A
central multi-host server (a later phase) is where a real database earns
its keep.

**Atomic writes**: every save writes a temp file in the same directory,
flushes it, `fsync`s it, then `os.replace()`s it over the real file --
atomic on both POSIX (`rename(2)`) and Windows (`MoveFileExW` +
`MOVEFILE_REPLACE_EXISTING`). A crash mid-write leaves the previous file
(or nothing, on the very first save) intact, never a half-written file.

**Never silently destroys data**: a missing file is treated as "no
reservations yet" (not an error); an *empty* file is the same. But a
malformed JSON file, an unsupported schema version, or a single malformed
entry inside an otherwise well-formed list all raise a clear
`ReservationStorageError` instead of being silently dropped or
overwritten -- because a `save()` always rewrites the whole file, silently
dropping one bad entry on load would permanently delete it the next time
anything wrote a reservation. The file is left exactly as it was until a
human fixes or removes it.

### Reservation model

```
reservation_id   uuid4 hex
host_id          which host this reservation belongs to (see "Host identity" below)
port             int
protocol         tcp | udp -- 8000/tcp and 8000/udp are DISTINCT reservations
bind_address     optional; None = "any address" (the common case)
project          required
service          optional
purpose          optional
notes            optional
created_at       ISO 8601 UTC
updated_at       ISO 8601 UTC
```

**Host identity**: every reservation carries `host_id` (still the Phase 1
placeholder -- the hostname) specifically so a future central server can
aggregate reservations from multiple hosts without a schema change. This
phase only ever manages reservations for the *current* host; a persisted,
stable host UUID remains an explicitly later-phase concern, not redesigned
here.

### State evaluation

| Condition | State |
|---|---|
| No listener, no reservation | `FREE` |
| A listener, no reservation | `ACTIVE` |
| No listener, a reservation exists | `RESERVED` |
| A listener whose detected project matches the reservation's project | `ACTIVE` (reservation kept as metadata, never lost) |
| A listener whose detected project does NOT match | `CONFLICT` |
| A listener whose project is **unknown** (Phase 3 couldn't identify it) | `CONFLICT` -- never assumed safe |
| The listener itself is OS/system-owned (`DiscoveredPort.source == SYSTEM`) | `SYSTEM`, regardless of any reservation |

**The conservative rule that matters most**: unknown ownership is *never*
treated as "probably the reserved project." Phase 3's own project
detection already refuses to guess when evidence is weak; the same
discipline applies here, for a stronger reason -- guessing wrong would hide
a real conflict. `SYSTEM` is only ever assigned to what discovery itself
already classified as system-owned (Phase 1/2's `Source.SYSTEM`) -- never
inferred from a port merely being low-numbered.

Reservations/conflicts/`check`/`next` operate at `(protocol, port)`
granularity (not per-bind-address) -- a reservation for "port 8003" is
evaluated against whichever address(es) something is actually listening on;
`scan`/`inspect` remain address-precise as in Phase 1-3.

### Conflict detection

`portforge conflicts` lists every `(protocol, port)` in `CONFLICT` state,
with reservation owner, actual detected owner, process/container, and the
evidence explaining the mismatch. Exit code `1` if any conflicts exist (`0`
if none) -- useful for scripting/CI. **PortForge remains read-only regarding
running processes/containers**: it never terminates anything, stops a
container, or rewrites a configuration to "fix" a conflict -- purely
advisory.

### Bind probe

Before ever calling a port "available," PortForge attempts a real,
temporary socket bind (`bindprobe.py`): create a socket, `bind()`, close
immediately. No `listen()` call (unnecessary -- `bind()` alone triggers
`EADDRINUSE`, and UDP has no `listen()` concept at all).

**Deliberately never sets `SO_REUSEADDR` or `SO_REUSEPORT`**, on any
platform. Their semantics differ enough between Windows and POSIX that
using them here would undermine the probe's whole purpose: on Windows in
particular, `SO_REUSEADDR` can let a new socket bind over an address that
already has an active listener, which would make an occupied port look
free. Accepted tradeoff: a genuinely free port very recently in TIME_WAIT
may occasionally probe as unavailable on POSIX. That bias is intentional --
PortForge would rather under-recommend than hand out a port still in use.
IPv6 addresses (containing `:`) select `AF_INET6` automatically; no extra
options are forced for dual-stack behavior, matching what a normal
application binding that address would get from the OS's own defaults. A
permission error (a privileged port without the right OS privileges) is
reported distinctly from "occupied," never conflated with it.

### Three-layer validation & recommendation algorithm

Every recommendation must pass, in this order:

1. **Fresh discovery** (native + Docker) -- the candidate must be `FREE`.
2. **Reservation / exclusion evaluation** -- no reservation, not excluded.
3. **Real socket bind probe** -- must actually be bindable right now.

A port is only ever recommended if it clears all three. Candidate order
is pluggable (`RecommendationStrategy`) so future strategies
(project-affinity, an explicit preferred list, ...) can be added without
touching the validation pipeline. Phase 4 implements one strategy,
`sequential`: walk the configured range from `start` to `end` in order --
which already satisfies "try common defaults first" for the built-in
ranges (3000 before 3001 for frontend, 8000 before 8001 for api, ...)
without a second, separately-maintained "preferred ports" list that would
just duplicate the range's own starting point.

### Recommendation ranges & exclusions

One place defines the ranges -- `config.py`'s `DEFAULT_RANGES` -- never
scattered numeric literals:

```
frontend:  3000-3999      mysql:    3306-3399
api:       8000-8999      redis:    6379-6399
postgres:  5432-5499      generic: 10000-19999
```

Override or add ranges, and configure exclusions, via a user-level config
file (`config.yml`/`.yaml`/`.json` in the same data directory as
reservations -- first one found wins):

```yaml
ranges:
  internal-api:
    start: 8500
    end: 8599
exclude:
  - 22
  - 3389
  - 50000-50100
```

A recommendation **never** returns an excluded port; `check` reports
exclusion explicitly.

### Configuration precedence

```
CLI arguments  >  project config (.portforge.yml/.json)  >  user config  >  built-in defaults
```

- **Built-in defaults**: `DEFAULT_RANGES`, no exclusions.
- **User config**: the file above; a malformed entry is logged and skipped
  (falls back to the default for that key) rather than failing outright --
  unlike reservation storage, a bad config file isn't irreplaceable user
  data, so the friendlier degrade-to-defaults behavior is appropriate.
- **Project config** (`.portforge.yml`/`.json`): found via the *same*
  bounded, home-directory-safe ancestor traversal Phase 3 project detection
  uses (see `detection/evidence.py`), starting from the current directory
  -- so a `.portforge.yml` belonging to an unrelated project, or one
  sitting loose in `$HOME`, is never picked up by accident. Currently used
  for `sync-reservations` and to supply default project/service context;
  it does not (yet) override ranges/exclusions, which are inherently
  machine-wide settings, not per-project ones.
- **CLI arguments** always win when given explicitly (`--protocol`,
  `--project`, etc.).

### Importing a project's reservations

```yaml
# .portforge.yml
project: deeptrace
ports:
  - port: 8003
    service: api
    purpose: api
  - port: 3002
    service: frontend
  - port: 5436
    service: postgres
```

`python -m portforge_agent sync-reservations` reads this and creates/refreshes
local reservations for each entry -- **only when explicitly invoked**.
Nothing about `scan`/discovery ever creates a reservation as a side effect
of merely encountering a `.portforge.yml`.

### Concurrency / locking

Two PortForge processes could race to reserve the same port
(`portforge next api --reserve ...` run twice at once). A cross-platform
advisory file lock (`reservations/lock.py`) guards every reservation
read-modify-write cycle -- deliberately **stdlib-only** (`msvcrt` on
Windows, `fcntl` on POSIX), not a third-party dependency: both are always
available wherever Python itself runs, so a `filelock`/`portalocker`
dependency would add nothing a small platform branch doesn't already solve.

The pattern (`recommend_and_reserve()`, `reserve_ops.reserve()`): run the
expensive part (native + Docker discovery) *before* acquiring the lock --
live listeners can't change because of a reservation race, so there's no
correctness reason to hold the lock during it. Everything that depends on
*other processes'* reservations happens *inside* the lock: reservations
are reloaded fresh from disk, and the cheap parts (reservation lookup, bind
probe) are redone from scratch for the candidate. A second process that
loses the race simply sees the first process's freshly-written reservation
and correctly moves on to the next candidate -- no special-case retry
logic needed. Proven with a real concurrent-threads test hitting the actual
file lock (`tests/test_recommend_concurrency.py`).

### CLI commands (Phase 4)

```bash
portforge check 8000 [--protocol tcp|udp] [--address ADDR] [--json]
portforge next api [--project P] [--service S] [--purpose P]
                    [--protocol tcp|udp] [--address ADDR] [--reserve] [--json]
portforge reserve 8003 --project deeptrace [--service S] [--purpose P]
                        [--protocol tcp|udp] [--address ADDR] [--notes N] [--json]
portforge release 8003 --project deeptrace [--protocol tcp|udp] [--json]
portforge release --id <reservation-id> [--json]
portforge reservations [--json]
portforge conflicts [--json]
portforge sync-reservations [--path DIR] [--json]
```

Also installed as a console script (`[project.scripts]` in `pyproject.toml`):
`portforge <command> ...` behaves identically to
`python -m portforge_agent <command> ...`.

`next api --reserve` requires `--project` and performs recommendation +
reservation as one atomic, locked operation (see "Concurrency" above);
without `--reserve`, `next` never creates a reservation.

`reserve`/`release` ownership rules: a reservation for the *same* project
is idempotent (refreshes service/purpose/notes, doesn't error); a
reservation held by a *different* project is refused outright -- PortForge
never silently overwrites another project's reservation. Reserving a port
that's actively in use is refused *unless* the active listener's detected
project confidently matches the requested project (adopting a port the
project already legitimately occupies) -- unknown or different ownership
always refuses. `release` is idempotent (releasing an already-absent
reservation succeeds as a no-op) and also accepts `--id <reservation-id>`
instead of a port.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success: available / no conflicts / operation succeeded |
| `1` | A normal, expected negative outcome: unavailable, refused, conflicts found, range exhausted |
| `2` | An operational or configuration error: bad arguments, unreadable reservation storage, unknown service type |

Exact per-command semantics: `check` -- 0 available, 1 unavailable, 2 error
(e.g. corrupt reservation file). `next` -- 0 recommended, 1 range exhausted,
2 unknown service type or bad arguments. `reserve`/`release` -- 0 success
(including idempotent no-ops), 1 refused (ownership conflict), 2 error.
`conflicts` -- 0 no conflicts, 1 conflicts found. A normal negative result
never looks like a crash: PortForge only ever prints a Python traceback for
a genuine internal bug.

## Persistent host identity (Phase 5)

`get_host_id()` (used throughout discovery, reservations, and
recommendations) now returns a **persisted UUID**, not the hostname --
generated once and stored at the same platform-appropriate location as
`reservations.json`:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\PortForge\host.json` |
| macOS | `~/Library/Application Support/PortForge/host.json` |
| Linux | `$XDG_DATA_HOME/portforge/host.json`, else `~/.local/share/portforge/host.json` |

It survives process restarts and **hostname changes** (renaming the
machine never changes its `host_id`) -- this is what makes it safe for a
central server to use as a durable primary key. Written atomically, same
discipline as reservation storage; a malformed `host.json` is **never**
silently replaced with a fresh UUID (see `identity.py`) -- doing so could
make one physical machine appear as two different hosts to a central
server, so a malformed file fails loudly instead, exactly like a malformed
`reservations.json` does.

### Legacy reservation migration

Phase 4 reservations were written with `host_id` = the hostname at the
time. The first time any `portforge` command runs after upgrading, an
idempotent, one-time migration (`reservations/migration.py`, invoked from
`cli.py`'s `main()`, **outside** any lock a command itself will acquire)
rewrites any reservation whose `host_id` matches the current hostname to
the new persistent UUID -- preserving `reservation_id`, timestamps, and
ownership exactly. After the first successful run there's nothing left to
migrate, so every later invocation is a fast no-op; nothing is ever lost
(a failed migration simply leaves the file as Phase-4-style, to retry next
time -- see `reservations/storage.py`'s atomic-write guarantee).

## Central sync (Phase 5, entirely optional)

A separate, explicit layer (`central_config.py`, `central_client.py`,
`central_sync.py`, and the `portforge central *` commands) that **no other
command imports or is aware of** -- `scan`/`check`/`next`/`reserve`/
`release`/`reservations`/`conflicts` behave identically whether central
sync has never been configured, is disabled, or the server is completely
unreachable. This isn't a runtime feature-flag check sprinkled through
already-working code; it's architectural (the dependency simply doesn't
exist in that direction).

```bash
portforge central enroll --url http://localhost:58000 --enrollment-token <token>
portforge central status              # connectivity + config, never prints the token
portforge central sync                # fresh discovery -> submit snapshot + push reservations
```

Configuration lives in its own protected file (`central.json`, same data
directory as `reservations.json`/`host.json`) -- **never** in a project's
`.portforge.yml`, which is meant to be committed to source control and is
exactly where a bearer token must never end up. `central status`/`central
sync`/every log line this layer emits deliberately never print the raw
token.

`central sync` performs, in order: a health check, a heartbeat, a full
`discover_all_ports()` snapshot submission, and a push of every local
reservation belonging to this host -- all best-effort: any failure (server
down, network unreachable, auth rejected) is reported clearly and exits
non-zero, but never raises past the CLI or corrupts local state. See
`backend/README.md` for what the central server does with this data, and
why it only ever offers a **suggestion**, never a "verified available"
answer, for `GET /api/recommendations`.

## Install

```bash
cd agent
pip install -r requirements.txt
# or, for the `portforge` console script too:
pip install -e .
```

## Usage

```bash
python -m portforge_agent scan          # native + Docker, enriched
python -m portforge_agent scan --json
python -m portforge_agent scan --port 8000
python -m portforge_agent scan --project ocrforge
python -m portforge_agent scan --source docker
python -m portforge_agent scan --purpose api
python -m portforge_agent scan --project ocrforge --purpose api   # combinable

python -m portforge_agent docker        # Docker-published ports only
python -m portforge_agent docker --json

python -m portforge_agent inspect 8000  # full detail for a specific port
python -m portforge_agent inspect 8000 --json

python -m portforge_agent check 8000            # is this port available right now?
python -m portforge_agent next api              # recommend a port for a service type
python -m portforge_agent next api --project deeptrace --reserve   # recommend + reserve atomically
python -m portforge_agent reserve 8003 --project deeptrace --service api
python -m portforge_agent release 8003 --project deeptrace
python -m portforge_agent reservations
python -m portforge_agent conflicts
python -m portforge_agent sync-reservations     # import .portforge.yml's ports list

# `portforge` (console script) works identically to `python -m portforge_agent`:
portforge check 8000
```

Example `scan` output (mixed native + Docker, real machine):

```
PORT      PROTOCOL  STATUS    PROJECT              PURPOSE         OWNER                  SOURCE    ADDRESS
3306      TCP       ACTIVE    -                    database        mysqld.exe             Process   0.0.0.0
5173      TCP       ACTIVE    job-trailers-resume  frontend        job-trailerd-frontend  Docker    0.0.0.0
8000      TCP       ACTIVE    job-trailers-resume  api             job-trailerd-backend   Docker    0.0.0.0
8090      TCP       ACTIVE    ocrforge             reverse-proxy   ocrforge-nginx         Docker    0.0.0.0
9000      TCP       ACTIVE    ocrforge             object-storage  ocrforge-minio         Docker    0.0.0.0
11434     TCP       ACTIVE    -                    ai              ollama.exe             Process   127.0.0.1
```

The PURPOSE column shows `category` (the broad bucket, e.g. `database`,
`ai`) — the more specific identity (e.g. `mysql`, `ollama`) is always in
`--json` and `inspect`. ADDRESS is kept in the default table beyond the
project brief's literal column list, deliberately: without it, dual-stack
bindings (very common in practice — Docker Desktop and Windows both
routinely publish/listen on both `0.0.0.0` and `::` for the same port)
render as confusing duplicate-looking rows.

`--json` output includes full detail for every record (pid, command line,
parent process, raw connection state, container image/status/networks/
labels, and the complete `detection` block) regardless of which columns
the table shows.

## Tests

```bash
cd agent
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests for the collector that matches the current OS exercise real parsing
logic against mocked OS-tool output for determinism (actual open ports vary
run to run). Collectors for other operating systems, and the Docker
collector, are also tested against mocked command output, per project
convention: PortForge never pretends to execute another OS's (or Docker's)
commands during tests. The native+Docker merge logic in `discovery.py` is
tested directly with controlled `DiscoveredPort` lists, not through mocked
collectors, so the actual identity/merge behavior is what's under test.

Detection tests (`test_detection_*.py`) use real temporary directories
(pytest's `tmp_path`) with real manifest files, so project-root traversal,
manifest parsing, and the safety limits (size caps, malformed input,
inaccessible directories) are exercised against real filesystem behavior,
not mocks.

Phase 4 tests (`test_reservations_*.py`, `test_evaluate.py`,
`test_bindprobe.py`, `test_recommend*.py`, `test_reserve_ops.py`,
`test_cli_reservations.py`, `test_config.py`, `test_paths.py`) follow the
same "test the real thing, not a mock of it" convention: `test_bindprobe.py`
uses real sockets (a real free port from the OS, a real occupied one held
by another socket in the test), `test_reservations_lock.py` and
`test_recommend_concurrency.py` use real threads racing for the real
file-based lock, and reservation storage tests use real temp files on disk
including a genuinely malformed JSON file and a genuinely too-large
manifest.

## Known limitations

- No persistence: every `scan` is a one-off snapshot; `first_seen`/`last_seen`
  are both set to the scan time. Historical tracking arrives with the
  backend/database phase.
- No reservation, conflict, or recommendation logic yet — `state` is always
  `ACTIVE` for anything discovered. `FREE`/`RESERVED`/`CONFLICT` require
  comparing a scan against a reservation store, which doesn't exist yet.
  Two different containers/processes publishing the exact same
  `(protocol, bind_address, host_port)` are both reported rather than
  flagged — actual conflict detection is Phase 4.
- `host_id` is currently just the hostname. It will become a persisted,
  stable identifier once multi-host sync is implemented.
- macOS and Linux OS-level collectors are implemented against the documented
  output formats of `lsof`/`ss` and unit-tested with mocks, but have not been
  run against a live macOS/Linux machine (this development environment is
  Windows). The Docker collector's parsing has been both mock-tested and
  validated against a real Docker Desktop instance (see the Phase 2
  completion report for the validation table); it should behave identically
  on macOS/Linux since it only shells out to `docker`, which presents the
  same JSON shape on every platform. Windows native collector output has
  been verified against `Get-NetTCPConnection` / `Get-NetUDPEndpoint` /
  `Get-Process`.
- Docker Swarm services, `docker-compose` v1 (`docker-compose.yml` label
  variants for very old Compose CLI versions), and rootless Docker are not
  specifically tested against, though the same `docker inspect` shape should
  apply.
- `docker inspect` output larger than a shell can pass as argv (an extremely
  large number of simultaneously running containers) is not chunked; this is
  a theoretical limit not expected to matter for a local dev machine.
- **Project detection never traverses to or above the user's home
  directory** (see "Evidence hierarchy" above). This was a real bug found
  during Phase 3 validation on the development machine: a stray
  `package.json`/`requirements.txt` sitting directly in `$HOME` (left by
  unrelated tooling) caused *every* process whose working directory fell
  anywhere under `$HOME` — a very common fallback cwd for GUI-launched
  processes and services — to be misattributed to a bogus "project" named
  after the home directory. The accepted cost: a real project living
  directly in `$HOME` with no subdirectory won't be detected.
- Project detection only reads the current user's own process tree; a
  process owned by another user (permission denied on `cwd()`/`cmdline()`)
  falls back to its parent's working directory if available, else stays
  unknown — it never guesses.
- Full dependency-name extraction (for purpose-detection evidence) is only
  implemented for `package.json`, `pyproject.toml`, and `requirements.txt`.
  `Cargo.toml`/`go.mod`/`composer.json`/`pom.xml` are recognized project
  markers and their declared *name* is read, but their dependencies aren't
  parsed for purpose evidence (Rust/Go/PHP/Java framework detection is out
  of scope for this phase).
- Purpose detection's known-software and framework lists are a curated,
  reasonably broad starting set, not exhaustive — an unrecognized process
  or image correctly stays `unknown` rather than guessing (see the Phase 3
  completion report for real unclassified processes encountered on the
  validation machine).
- No caching persists *across* scans (only within one `discover_all_ports()`
  call) — each `scan` re-walks and re-reads manifests from scratch. This is
  intentional for now (Phase 1 discovery is meant to be read-only and
  stateless); a persistent cache would need the storage layer later phases
  introduce.
- **Reservations are per-host only** — `host_id` is carried on every
  reservation, but this phase never synchronizes or reconciles reservations
  across machines; that's explicitly the central server's job (a later
  phase). Two different hosts can happily "reserve" the same port number
  with no conflict reported, by design.
- **`host_id` is still the Phase 1 placeholder** (the hostname), not a
  persisted stable UUID — a rename of the machine changes its identity as
  far as reservations are concerned. Not redesigned in this phase, per the
  project brief.
- The reservation lock (`reservations/lock.py`) has a default 10s timeout;
  a `LockTimeoutError` surfaces as an unhandled exception today rather than
  a clean CLI error message with its own exit code -- acceptable for local,
  single-user use where sustained multi-second lock contention would be
  unusual, but worth revisiting if PortForge grows automated/scripted
  callers that could pile up.
- Project-level config (`.portforge.yml`/`.json`) does not currently
  override recommendation ranges or exclusions -- only user-level config
  does. Project config is used for `sync-reservations` and default
  project/service context; extending it to ranges/exclusions is
  straightforward if a real use case shows up.
- The bind probe (see "Bind probe" above) checks one address at a time; it
  does not itself probe both `0.0.0.0` and `::` for a "would this be free
  on all interfaces" guarantee — callers wanting that would need to probe
  both explicitly (`--address`).
- `reserve`'s "adopt an actively-used port" rule trusts Phase 3's project
  detection as-is; if Phase 3 mis-detects a project (rare, and always at
  reduced confidence when evidence is weak — see Phase 3 docs above), that
  same trust carries into the adoption decision. This is the same
  conservative design already used for conflict detection, not a new risk
  introduced by Phase 4.
- **Central reservation sync is one-directional** (local → central) — the
  local reservation file is never updated from central data. Two hosts
  that both locally reserve the same port for two different projects will
  both sync successfully and show up side by side centrally (correct,
  expected behavior — see backend/README.md), but PortForge never warns a
  user locally that another of their own machines made a different choice
  for the same port; that cross-host awareness is exactly what `portforge
  central status`/a future dashboard is for, not a local safety check.
- `central sync` is entirely manual/on-demand in Phase 5 — there is no
  background scheduler or OS service yet (explicitly out of scope: "full
  persistent background agent loop" is later-phase work).
- The central credential (`central.json`'s `token`) is stored in plain
  text locally, protected only by the same OS-level file permissions as
  `reservations.json`/`host.json` — no OS keychain/credential-manager
  integration yet.
