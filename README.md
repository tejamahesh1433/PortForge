# PortForge

PortForge is a cross-platform port discovery, tracking, reservation, conflict
detection, and recommendation platform for developers running many local
projects, containers, databases, and services across multiple computers.

It answers:

- Which host ports are currently in use, and by what?
- Which ports are reserved for a project that isn't currently running?
- Do any of my ports conflict?
- Which port should I assign to a new service?

## Architecture

| Component | Path        | Role                                                        |
|-----------|-------------|--------------------------------------------------------------|
| Agent     | `agent/`    | Cross-platform local port discovery (Windows / macOS / Linux) + Docker/Compose awareness + project/purpose detection + reservations/conflicts/recommendation + optional central sync |
| Backend   | `backend/`  | FastAPI + PostgreSQL central registry: multi-host observations, reservations, advisory recommendations — entirely optional |
| Frontend  | `frontend/` | Next.js + TypeScript dashboard (not yet implemented)          |
| CLI       | `cli/`      | `portctl` command-line client (not yet implemented)           |
| Tests     | `tests/`    | Cross-component integration tests (not yet implemented)       |
| Docs      | `docs/`     | Architecture and design documentation                         |
| Scripts   | `scripts/`  | Dev/setup scripts (not yet implemented)                       |

Availability is always evaluated **per host** — two machines using the same
port number is normal and not a conflict. Docker host ports and container
ports are tracked separately: a mapping like `5435 -> 5432` means host port
5435 is occupied, but container port 5432 does not occupy the host.

## Status

**Phase 1 — Cross-Platform Local Port Discovery Engine: implemented.**
**Phase 2 — Docker and Docker Compose Intelligence: implemented.**
**Phase 3 — Automatic Project, Service, and Purpose Detection: implemented.**
**Phase 4 — Port Reservations, Conflict Detection, and Smart Port Recommendation: implemented.**
**Phase 5 — Central Registry, Persistent Host Identity, and Multi-Host API: implemented.**

See [`agent/README.md`](agent/README.md) and [`backend/README.md`](backend/README.md) for how to run them.

PortForge now has an optional central registry (`backend/`, FastAPI +
PostgreSQL) that agents can sync to for multi-host visibility — "where is
port 8000 used across all my machines" (`GET /api/ports?port=8000`),
central reservation mirroring, and advisory port suggestions. **The
central server can never affect local behavior**: every agent command
(`scan`/`check`/`next`/`reserve`/`conflicts`) works identically whether
the central server is running, stopped, or was never configured at all —
central sync is a separate, explicit, opt-in layer
(`portforge central enroll` / `sync` / `status`) that the rest of the
agent's CLI has no dependency on, architecturally, not just by
convention. Every host now has a persistent UUID identity (surviving
restarts and hostname changes) instead of the Phase 1-4 hostname-based
one, with existing local reservations migrated forward automatically and
losslessly. See `agent/README.md` "Persistent host identity" / "Central
sync" and `backend/README.md` for the full design, including why a
central recommendation is always labeled a "suggestion" and never
"verified available."

PortForge can now reserve ports for a project even while stopped
(`portforge reserve 8003 --project deeptrace --service api`), detect when a
reservation and an active listener disagree on ownership
(`portforge conflicts`), and recommend a port for a service type after
passing three independent checks — fresh discovery, reservation/exclusion
evaluation, and a real socket bind probe
(`portforge next api --project deeptrace --reserve`). Reservations are
local and per-host (stored under a platform-appropriate user data
directory, e.g. `%LOCALAPPDATA%\PortForge` on Windows), protected by a
cross-platform file lock so two concurrent PortForge invocations can never
allocate the same port. See `agent/README.md` for the full state model
(FREE/ACTIVE/RESERVED/CONFLICT/SYSTEM), the conservative
unknown-ownership rule, and exit code semantics.

Docker discovery is optional and additive: `python -m portforge_agent scan`
merges native OS discovery with published Docker container ports
(preferring authoritative Docker metadata — container/Compose
project/service — while keeping native process info as secondary detail),
and `python -m portforge_agent docker` shows the Docker-published view on
its own. Absence of Docker never affects native discovery.

Every discovered port is now enriched with a best-effort `project_name`,
`purpose`, and `category`, each backed by an explainable
confidence/method/evidence trail — never an invented guess for something
genuinely unrecognized. `scan` supports `--port`/`--project`/`--source`/
`--purpose` filtering, and `inspect <port>` shows the full evidence for a
specific port. See `agent/README.md` for the evidence hierarchy, confidence
model, and safety limits (bounded, size-capped, never executes anything it
reads).

All later phases (frontend dashboard, `portctl` packaging, automatic OS
service installation, a persistent background agent loop, remote
process/container control) are not yet implemented. Their directories
exist as placeholders so the repository layout matches the target
architecture from the start.

## Running the central server (optional)

```bash
cp .env.example .env         # set PORTFORGE_ADMIN_BOOTSTRAP_TOKEN
docker compose up -d portforge-postgres
cd backend && pip install -r requirements-dev.txt && alembic upgrade head
uvicorn app.main:app --port 58000   # or docker compose up -d for both services
```

See [`backend/README.md`](backend/README.md) for full setup, the API
reference, and why host ports here (`55432`/`58000` by default) were
chosen deliberately rather than guessed.

## Development rules

This project is built incrementally, one phase at a time. Each phase is
implemented for real (no stubs pretending to be functional), tested, and
documented before the next phase begins. See each subdirectory's README for
phase-specific status.


claude --resume 27cc0b6d-73c3-4511-8586-6b9174596e74