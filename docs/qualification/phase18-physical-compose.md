# Phase 18.5 — Physical Compose Deployment Qualification

**Date:** 2026-09-24  
**Branch:** `feature/deployment-orchestration`  
**Phase 18 base:** `ec4d3ad`  
**Harness:** `.qual-temps/phase18-physical/run_phase18_physical.py`  
**Evidence JSON:** `.qual-temps/phase18-physical/evidence/results.json`  
**Codex MCP:** `.qual-temps/phase18-physical/evidence/codex-mcp.txt`

## Topology

```text
Windows Client (CLI/MCP/Codex)
        │
        ▼
Disposable Central  http://127.0.0.1:58003  (portforge-qual)
        │  DB qual @ 127.0.0.1:55434  alembic d5e6f7a8b9c0
        ▼
Linux Agent container  portforge-phase18-agent
  (Docker Desktop Linux engine + /var/run/docker.sock)
        │
        ├── HTTPS package fetch  https://artifacts.pf18.test/  (nginx TLS)
        ▼
Docker Compose fixture  pf18-qual-app (nginx/postgres/redis)
```

Production (`:58000` / alembic `c3d4e5f6a1b2`) was never migrated or mutated.

## Versions

| Component | Value |
|-----------|--------|
| Disposable host UUID | `84738d7a-087b-4f65-bc7b-be9f0b8812be` |
| Hostname | `pf18-qual-linux` |
| Agent | 1.4.0 (source tree; image built from branch) |
| OS (agent container) | Linux (Docker Desktop Engine 29.6.1) |
| Compose | Docker Compose v5.1.4 |
| Qual Central | 1.4.0 protocol 1 |
| Qual DB head | `d5e6f7a8b9c0` |
| Prod DB head | `c3d4e5f6a1b2` (unchanged) |

## Core path evidence

| Step | Result | Notes |
|------|--------|-------|
| Discovery | PASS | 4 services |
| Deployment plan (read-only) | PASS | no DB insert |
| Approval → APPROVED create | PASS | not PLANNED |
| HTTPS package + dual SHA | PASS | `artifacts.pf18.test` |
| Agent claim lease | PASS | |
| Compose validate + apply | PASS | project `pf-pf18-qual-app-production-84738d7a087b` |
| Revision A SUCCEEDED | PASS | dep `12a4f172…` |
| Revision B SUCCEEDED | PASS | dep `3eb5fe9c…` |
| Manual rollback B→A | PASS | dep `cf06ad6f…` SUCCEEDED; `rollback_revision_id` set |
| Broken C FAILED | PASS | `DEPLOYMENT_UNHEALTHY` |
| Checksum mismatch | PASS | `DEPLOYMENT_CHECKSUM_MISMATCH` |
| Path `../` + absolute | PASS | |
| Idempotency | PASS | |
| Concurrency partial unique | PASS | one 201 + one 409 |
| Decommission reject | PASS | |
| Codex MCP plan | PASS | `portforge_capabilities`, `portforge_deployment_plan` |

Harness summary: **33 PASS / 0 FAIL**.

## Defects found and fixed during qualification

1. **SUCCEEDED without `revision_id`** — agent did not send Central-required `revision_id` on success.  
   Fix: `central_client.deployment_status` + handler pass `revision_id`.  
2. **Absolute archive paths accepted** — `/etc/passwd`-style entries were stripped to relative paths.  
   Fix: reject absolute POSIX/Windows paths in `_safe_member_path`.

## Known gaps (not blocking core physical path)

| Item | Status |
|------|--------|
| Pre-apply port recheck / stolen-port TOCTOU | **Not implemented** in product (HIGH for release qual) |
| Agent crash/restart lease recovery timed test | SKIPPED this run |
| Central restart mid-deploy | SKIPPED this run |
| Automatic rollback on C | Not configured; manual rollback proven |
| Claude / Antigravity MCP | EXTERNAL BLOCKED |
| Symlink-escape archive entry | Covered by existing package unit tests; not re-run as separate physical case |

## Regression after fixes

| Suite | Result |
|-------|--------|
| Agent | 944 passed, 5 skipped |
| Backend | 373 passed |
| Dashboard | 168 passed |
| Dashboard build | PASS |
| Doctor (qual + prod RO) | PASS |

## Cleanup

Disposable Compose fixture stacks and phase18 agent/artifacts containers may be removed after evidence capture. Qual Postgres volume retained unless wiped with `docker compose -p portforge-qual … down -v` (do not touch production).
