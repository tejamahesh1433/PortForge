# Deployment Orchestration (Phase 18)

Target-aware **Compose** deployment planning, constrained transfer, apply,
health verification, and rollback — driven from a coding agent that may run
on a **different** machine than the deployment target.

Baseline: Phase 17 freeze `5c1863a` (PortForge **v1.4.0** source).
Protocol / contract / machine / MCP schema remain **1** if additive.

**SCHEMA GATE:** **APPROVED WITH AMENDMENTS** — see
[`deployment-schema-proposal.md`](deployment-schema-proposal.md).
Implementation may proceed on development Central only; production migration
is prohibited during Phase 18.

Related: [`environment-targets.md`](environment-targets.md),
[`workspace-discovery.md`](workspace-discovery.md),
[`mcp-integration.md`](mcp-integration.md),
[`agent-upgrade-management.md`](agent-upgrade-management.md).

---

## 1. Goals and non-goals

### Goals

- Turn Phase 17 target plans into an operational **deployment plan**
- Explicit approval before any mutate/start
- Constrained Docker **Compose** adapter only (no K8s orchestration yet)
- Typed Central↔agent operations (upgrade/probe pattern) — **no generic shell**
- Package + checksum transfer into PortForge-owned deployment root
- Revision model with known-good rollback
- Health model that does not equate “started” with “healthy”
- MCP/CLI parity for plan / apply / status / rollback

### Non-goals (this phase / forever for security)

- Generic `run_command` / SSH / PowerShell / arbitrary Docker/Compose args
- Automatic reverse-proxy / DNS / TLS / firewall mutation
- Kubernetes deploy orchestration
- Deploying Phase 18 to PortForge production / using prod hosts for destructive E2E
- Version bump / release / GHCR publish

---

## 2. Concepts

| Concept | Meaning |
|---------|---------|
| **Deployment** | One apply attempt for `(project, environment, target host_id)` |
| **Deployment Plan** | Read-only preview: ports, files, health checks, rollback info, fingerprints |
| **Deployment Target** | Phase 17 target — immutable `host_id` |
| **Deployment Package** | Deterministic artifact set + manifest + checksums (no secrets) |
| **Deployment Revision** | Immutable package tree on the agent (`rev-<id>`) |
| **Runtime Adapter** | Compose-only; fixed verb set |
| **Health Verification** | Constrained checks → HEALTHY / UNHEALTHY / HEALTH_UNKNOWN |
| **Rollback Point** | Previous known-good revision id |
| **Deployment State** | Lifecycle enum below |

---

## 3. Lifecycle (deterministic)

```text
PLANNED
  → APPROVED          # explicit confirm_mutate / CLI approval
  → PREPARING         # package materialize + preflight
  → TRANSFERRING      # constrained package transfer to target agent
  → STARTING          # Compose validate + apply declared project only
  → VERIFYING
  → SUCCEEDED

Any non-terminal failure → FAILED

If previous known-good exists and policy says auto-rollback after failed activate:
  → ROLLING_BACK → ROLLED_BACK

Manual rollback from SUCCEEDED:
  → ROLLING_BACK → ROLLED_BACK
```

**No deployment begins without explicit approval.**

First deployment with no prior known-good and failed activate:
report `NO_ROLLBACK_REVISION` — do not pretend rollback succeeded.

---

## 4. Security: typed operations only

Central→agent protocol (additive on protocol 1) must expose **only** typed ops,
mirroring upgrades/probes:

| Op (conceptual) | Allowed effect |
|-----------------|----------------|
| `DEPLOYMENT_PREPARE` | Create staging revision dir under PortForge data root |
| `DEPLOYMENT_TRANSFER` | Accept package files with path/size/checksum validation |
| `COMPOSE_VALIDATE` | Validate exact generated Compose for this revision |
| `COMPOSE_APPLY` | Up/start **declared** Compose project identity only |
| `COMPOSE_STOP` / rollback apply | Stop/restore **declared** project only |
| `DEPLOYMENT_STATUS` | Report normalized state |
| `DEPLOYMENT_ROLLBACK` | Activate previous known-good revision |

**ABSENT forever in this surface:** shell, SSH, arbitrary subprocess, arbitrary
`docker`/`compose` argv from Central/MCP/user.

The agent maps each op to a fixed local implementation.

---

## 5. Deployment plan (read-only)

Built from Phase 17 `plan_for_target` + workspace fingerprint:

- project, environment, target UUID + display hostname
- services: INTERNAL ports + allocated HOST ports
- configuration mutations / override files to include
- package file list + checksums (after package build)
- health check declarations
- rollback info (previous revision if any)
- workspace fingerprint, target identity, plan hash
- ingress metadata (Phase 17) as **external requirements**, not auto-apply

---

## 6. Preflight

Before APPROVED→active work:

- target exists, ACTIVE, not decommissioned
- heartbeat/sync freshness per existing allocation semantics
- agent compatible; Docker + Compose available on target
- allocations present and owned by this project/target
- workspace fingerprint + plan target still match
- required package files exist; no AMBIGUOUS unresolved config for deploy set
- secrets: if required and not safely available → `DEPLOYMENT_SECRET_REQUIRED`

---

## 7. Package

Deterministic contents only:

- Compose base + **generated target override** (host side only; container stable)
- non-secret env-specific config explicitly listed
- deployment manifest + checksums
- optional health declaration file

**Never** package: `.git`, `node_modules`, `.venv`, caches, unrelated trees,
arbitrary `.env` secrets.

Override pattern (Lenovo):

```yaml
# override — host:container
services:
  api:
    ports:
      - "18000:8000"
```

Do not rewrite base development Compose in place when an override suffices.

Compose **project name** must incorporate stable
`project` + `environment` + `host_id` (not bare directory basename).

---

## 8. Transfer and confinement

- Destination: `<PORTFORGE_DATA_DIR>/deployments/<project>/<environment>/<deployment_id>/`
- Stage into `revisions/<revision_id>/` then activate
- Manifest lists relative paths, sizes, sha256
- Reject: absolute paths, `../`, symlink escape, unexpected files, checksum mismatch, oversized packages
- Partial transfer must never become current

---

## 9. Apply, health, rollback

- Re-check target ports immediately before apply (TOCTOU); on conflict → fail;
  **no silent reallocation** — new plan required
- Validate Compose before start → `COMPOSE_VALIDATION_FAILED`
- Normalize Docker/Compose outcomes; do not dump raw CLI as contract
- Health: container state / Compose health / declared HTTP or TCP checks only
  - `RUNTIME_STARTED` ≠ `HEALTHY`
  - Prefer `HEALTH_UNKNOWN` over false HEALTHY
- Rollback restores previous revision files + Compose project; **does not**
  auto-release PortForge allocations (preserve Phase 17 separation)
- Concurrent apply same `(project, environment, host_id)` → lock / reject
- Idempotent same `request_id` / revision → no duplicate activation

---

## 10. Observability

Structured events (no secrets):  
`deployment.planned`, `.approved`, `.transfer_*`, `.starting`, `.verifying`,
`.succeeded`, `.failed`, `.rollback_*`.

---

## 11. MCP / CLI

| CLI | MCP |
|-----|-----|
| `portforge deployment plan` | `portforge_deployment_plan` |
| `portforge deployment apply` | `portforge_deployment_apply` |
| `portforge deployment status` | `portforge_deployment_status` |
| `portforge deployment rollback` | `portforge_deployment_rollback` |

Share one service layer. Approval = MUTATE gate (`confirm_mutate` on MCP).

---

## 12. Schema gate decision

| Question | Answer |
|----------|--------|
| Migration required? | **YES** |
| Tables | `host_deployments` + `deployment_revisions` (**both required**) |
| Implementation allowed? | **YES** (amended proposal committed) |
| Production migration during Phase 18? | **NO** |

Details: [`deployment-schema-proposal.md`](deployment-schema-proposal.md).

---

## 13. Implementation status (development Central only)

1. Alembic migration for `host_deployments` + `deployment_revisions` — done (dev only)
2. Heartbeat `pending_deployment` + agent claim/status/health APIs — done (protocol 1 additive)
3. Agent Compose adapter + local revision store + trusted HTTPS package fetch — done
4. MCP/CLI tools + unit/service tests + Codex discovery evidence — done
5. Dashboard read-only — deferred (not required for Phase 18 freeze)
6. Physical disposable Linux Compose E2E — best-effort / deferred if environment unavailable

---

## 14. Phase 18 freeze criteria

- Amended schema proposal + migration + Central/agent/MCP implementation committed
- Clean tree on `feature/deployment-orchestration`
- Protocol / contract / machine / MCP schema remain **1**
- No version bump, no release, no production migration/deploy
- Qualification: suites green; v1.4 agent ignores `pending_deployment` without claiming
- Report: **PHASE 18 COMPLETE (development)** — not production-ready
