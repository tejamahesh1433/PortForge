# Full-System Qualification (Phase 14)

Phase 14 qualifies Phases 11–13 on branch `qualification/full-system` using a
**disposable Docker Compose stack** (`portforge-qual`). No feature development
occurs during qualification — evidence collection and matrix sign-off only.

## Freeze record

| Item | Value |
|------|-------|
| Branch | `qualification/full-system` |
| Frozen HEAD | `46d3887d3c18be3e9152f799ad88ba45d901fbeb` |
| Phase 11 commit | `64c10fe` — batch allocation alias and lifecycle guards |
| Phase 12 commit | `8eda554` — provider-neutral coding-agent project interface |
| Phase 13 commit | `46d3887` — project and coding-agent integration designs |

Phases 11–13 are frozen at the commits above. Qualification runs against images
built from this branch HEAD unless pre-built `PORTFORGE_QUAL_*_IMAGE` overrides
are set in `.env.qual`.

## Evidence classification legend

| Class | Meaning |
|-------|---------|
| **UNIT** | Automated tests (pytest, Jest) with mocks/fixtures; no live stack required. |
| **INTEGRATION** | Multi-component tests against in-process or test DB; may use fixtures. |
| **DISPOSABLE REAL** | Live qualification stack (`portforge-qual`) and/or real host processes; safe to mutate and wipe. |
| **PRODUCTION READ-ONLY** | Observe frozen production stack only — no writes, no `--build`, no schema changes. |

## Qualification matrix

Planned evidence types and notes per subsystem. Results are filled in during
qualification (see [Results](#results) below).

| Subsystem | Planned evidence | Notes |
|-----------|------------------|-------|
| Central (API) | INTEGRATION, DISPOSABLE REAL | Health, diagnostics, reservations, host CRUD via `127.0.0.1:58003`. |
| Dashboard | INTEGRATION, DISPOSABLE REAL | UI against qual API; BFF admin routes with bootstrap token. |
| Agent | UNIT, DISPOSABLE REAL | Agent pytest suite; enroll/heartbeat against qual central. |
| PostgreSQL | INTEGRATION, DISPOSABLE REAL | Persistence, restart survival on `portforge-qual-postgres`. |
| CLI | UNIT, DISPOSABLE REAL | `portforge doctor`, `capabilities`, workflow commands on qual stack. |
| Project workflow | INTEGRATION, DISPOSABLE REAL | Phase 12 manifest → provision → status → cleanup. |
| Allocation engine | UNIT, INTEGRATION, DISPOSABLE REAL | Phase 11 batch alias, lifecycle guards, concurrent allocation. |
| Fleet management | INTEGRATION, DISPOSABLE REAL | Multi-host identity, doctor, remote probe semantics. |
| Host lifecycle | INTEGRATION, DISPOSABLE REAL | Add host, remove record, decommission runbooks. |
| Agent upgrades | DISPOSABLE REAL | Upgrade/rollback runbooks; **physical multi-OS proof is a qualification gap** (resolve platform-by-platform). |
| Windows | DISPOSABLE REAL | Agent install, service, port discovery on Windows host. |
| Linux | DISPOSABLE REAL | Agent install, systemd, port discovery on Linux host. |
| macOS | DISPOSABLE REAL | Agent install, launchd, sleep/resume runbook. |
| Docker | INTEGRATION, DISPOSABLE REAL | Container port discovery via agent. |
| Docker Compose | INTEGRATION, DISPOSABLE REAL | Compose file parsing and published-port mapping. |
| Kubernetes | UNIT, INTEGRATION | hostPort / nodePort config lifecycle (no live cluster required for unit tier). |
| Coding-agent interface | UNIT, INTEGRATION, DISPOSABLE REAL | Phase 13 contract, `project inspect/provision`, structured JSON errors. |

### Deferred / non-blocking

- **MCP server integration** is deferred (Phase 13 design). Absence of MCP is
  **not a qualification blocker** — the provider-neutral CLI/API contract is the
  acceptance surface.
- **Physical multi-OS agent upgrade proof** remains a gap until exercised on each
  target platform (Windows, Linux, macOS). Track per-platform rows in
  [Platform results](#platform-results).

## Disposable qualification stack

Isolated from production and development. Fixed compose project name
`portforge-qual`.

| Resource | Value |
|----------|-------|
| Compose file | `docker-compose.qual.yml` |
| Env template | `.env.qual.example` → copy to `.env.qual` |
| Central API | `127.0.0.1:58003` |
| Dashboard | `127.0.0.1:3003` |
| PostgreSQL | `127.0.0.1:55434` |
| Database name / user | `qual` / `qual` |
| Volume | `portforge-qual-postgres-data` |
| API container | `portforge-qual-api` |
| Dashboard container | `portforge-qual-dashboard` |
| Postgres container | `portforge-qual-postgres` |
| Default image tags | `portforge-api:qual`, `portforge-dashboard:qual` (built from current tree) |

**Collision avoidance** — do not bind qualification ports on stacks already using:

| Stack | API | Dashboard | Postgres |
|-------|-----|-----------|----------|
| Production (`portforge`) | 58000 | 3000 | 55432 |
| Development (`portforge-dev`) | 58001 | 3001 | 55433 |
| Smoke / informal | 58002 | 3002 | — |
| **Qualification (`portforge-qual`)** | **58003** | **3003** | **55434** |

The qualification API hard-wires `PORTFORGE_DB_HOST=portforge-qual-postgres` and
database name `qual`. It cannot be pointed at production or dev Postgres through
`.env.qual` overrides.

### Start

```bash
cp .env.qual.example .env.qual
# Edit .env.qual — set PORTFORGE_QUAL_ADMIN_BOOTSTRAP_TOKEN to a fresh disposable secret.

docker compose -p portforge-qual -f docker-compose.qual.yml --env-file .env.qual up -d --build
```

Verify:

```bash
curl -sS http://127.0.0.1:58003/api/health
curl -sS http://127.0.0.1:58003/api/diagnostics
```

Dashboard: http://127.0.0.1:3003

Optional pre-built image overrides (skip rebuild):

```bash
# In .env.qual:
# PORTFORGE_QUAL_API_IMAGE=portforge-api:qual
# PORTFORGE_QUAL_DASHBOARD_IMAGE=portforge-dashboard:qual
```

### Stop

```bash
docker compose -p portforge-qual -f docker-compose.qual.yml --env-file .env.qual down
```

### Wipe (disposable data)

```bash
docker compose -p portforge-qual -f docker-compose.qual.yml --env-file .env.qual down -v
```

Removes volume `portforge-qual-postgres-data`. Production and dev volumes are
untouched.

## Results

Filled during qualification. Leave as TBD until evidence is collected.

### Automated regression

| Suite | Result | Commit / run | Notes |
|-------|--------|--------------|-------|
| Backend pytest | TBD | | |
| Agent pytest | TBD | | |
| Dashboard tests | TBD | | |
| Lint / typecheck / build | TBD | | |

### Disposable stack (portforge-qual)

| Check | Result | Notes |
|-------|--------|-------|
| Stack start / health | TBD | |
| Central diagnostics | TBD | |
| Agent enroll + heartbeat | TBD | |
| Allocation E2E | TBD | |
| Project workflow E2E | TBD | |
| Dashboard visibility | TBD | |
| Postgres restart persistence | TBD | |
| Coding-agent contract CLI | TBD | |

### Platform results

| Platform | Agent install | Discovery | Upgrade / rollback | Result | Notes |
|----------|---------------|-----------|-------------------|--------|-------|
| Windows | TBD | TBD | TBD | TBD | |
| Linux | TBD | TBD | TBD | TBD | |
| macOS | TBD | TBD | TBD | TBD | |

### Production read-only (optional observation)

| Check | Result | Notes |
|-------|--------|-------|
| Frozen stack unchanged | TBD | No `--build`, no writes |
| Port collision with qual | TBD | All three stacks may coexist |

## Sign-off

| Role | Name | Date | SHA qualified |
|------|------|------|---------------|
| Operator | TBD | | |
| Reviewer | TBD | | |
