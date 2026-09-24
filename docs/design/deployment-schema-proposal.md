# Deployment Schema Proposal (Phase 18 Gate)

**Status:** SCHEMA DECISION REQUIRED — do **not** implement until approved.

**Context:** Phase 18 deployment orchestration design
([`deployment-orchestration.md`](deployment-orchestration.md)).

**Why this exists:** Windows/Mac coding agents must orchestrate Compose deploy
on a remote PortForge agent (e.g. Lenovo) through Central. Every existing
Central→agent typed workstream (`host_probes`, `host_upgrades`) uses a
**durable table** + heartbeat pull + status API. There is no ephemeral job
channel. Agent-local revision trees are necessary but **not sufficient** for
offline claim, Central restart mid-flight, multi-client status, or concurrency
locks.

**Precedent:**

| Feature | Table | Migration |
|---------|-------|-----------|
| Remote probes | `host_probes` | `d6bcb4da5c4b` |
| Host upgrades | `host_upgrades` | `c3d4e5f6a1b2` |
| Deployments (proposed) | `host_deployments` (+ optional `deployment_revisions`) | **new** |

Current Alembic head: `c3d4e5f6a1b2`.

---

## 1. Decision summary

| Item | Proposal |
|------|----------|
| Migration required | **YES** |
| Abuse existing tables | **NO** |
| Protocol bump | **NO** (additive heartbeat field + new agent routes) |
| Package bytes in Postgres | **NO** (URLs / chunked transfer; Central stores metadata + checksums) |
| Agent-local blobs | **YES** (`PORTFORGE_DATA_DIR/deployments/...`) |
| v1.4 data | Additive empty tables; existing rows unchanged |

---

## 2. Table: `host_deployments`

One row per deployment **attempt** (apply request), analogous to `host_upgrades`.

### Columns

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `host_id` | UUID FK → `hosts.id` ON DELETE CASCADE | Target identity |
| `project` | String(256) NOT NULL | PortForge project name |
| `environment` | String(64) NOT NULL | e.g. production |
| `target_alias` | String(128) NULL | Display/alias only |
| `request_id` | String(128) NOT NULL | Idempotency; namespaced |
| `state` | String(32) NOT NULL | Lifecycle enum (string, like upgrades) |
| `plan_hash` | String(64) NOT NULL | Deployment plan fingerprint |
| `workspace_fingerprint` | String(64) NULL | Phase 16 fingerprint |
| `package_manifest_sha256` | String(64) NULL | Integrity of file list |
| `package_uri` | String(2048) NULL | Where agent fetches package (HTTPS) **or** null if chunked transfer API |
| `revision_id` | String(64) NULL | Agent-side revision id once prepared |
| `previous_revision_id` | String(64) NULL | Known-good before this attempt |
| `ports_json` | JSON/JSONB NULL | Summary INTERNAL/HOST mappings (no secrets) |
| `ingress_json` | JSON/JSONB NULL | Phase 17 ingress metadata / external requirements |
| `health_json` | JSON/JSONB NULL | Last health snapshot (normalized) |
| `failure_reason` | String(1024) NULL | Structured code + short message |
| `failure_code` | String(64) NULL | e.g. COMPOSE_VALIDATION_FAILED |
| `approved_at` | timestamptz NULL | |
| `claimed_at` | timestamptz NULL | Agent claimed via heartbeat |
| `completed_at` | timestamptz NULL | Terminal transition |
| `created_by` | String(64) NULL | e.g. mcp / cli / admin |
| `created_at` / `updated_at` | timestamptz | TimestampMixin |

### State values (string enum)

`PLANNED`, `APPROVED`, `PREPARING`, `TRANSFERRING`, `STARTING`, `VERIFYING`,
`SUCCEEDED`, `FAILED`, `ROLLING_BACK`, `ROLLED_BACK`

### Constraints / indexes

| Name | Definition |
|------|------------|
| `uq_host_deployments_host_request` | UNIQUE (`host_id`, `request_id`) |
| `ix_host_deployments_host_state` | INDEX (`host_id`, `state`) |
| `ix_host_deployments_project_env_host` | INDEX (`project`, `environment`, `host_id`) |
| Partial unique (recommended) | At most one **non-terminal** row per (`project`, `environment`, `host_id`) — enforce in service layer like upgrades if DB dialect partial unique is preferred |

### Foreign keys

- `host_id` → `hosts.id` CASCADE

---

## 3. Optional table: `deployment_revisions`

Use if revision metadata must outlive a single attempt row or be listed independently.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `deployment_id` | UUID FK → `host_deployments.id` ON DELETE CASCADE | Originating attempt |
| `host_id` | UUID FK → `hosts.id` | Denormalized for queries |
| `revision_id` | String(64) NOT NULL | Agent revision key |
| `package_manifest_sha256` | String(64) NOT NULL | |
| `is_known_good` | Boolean NOT NULL DEFAULT false | |
| `created_at` | timestamptz | |

**UNIQUE** (`host_id`, `revision_id`).

**v1 recommendation:** start with columns on `host_deployments` only; add
`deployment_revisions` only if listing/history UX requires it.

---

## 4. Package storage (not a migration table)

| Option | Pros | Cons |
|--------|------|------|
| A. Agent pulls HTTPS artifact URL (like upgrades) | Reuses upgrade pattern | Needs hosting for packages |
| B. Chunked `POST /api/agent/deployments/{id}/files` | No external blob store | More API surface |

Proposal: **support A first** (Central stores `package_uri` + sha256 of
tarball/zip of the deployment package). Agent verifies checksum before unpack.
Transfer confinement still applies on unpack paths.

---

## 5. API / heartbeat (additive, protocol 1)

### Heartbeat response

```text
HeartbeatResponse.pending_deployment: Optional[PendingDeploymentOut]
```

Fields: `deployment_id`, `request_id`, `project`, `environment`, `state`,
`package_uri`, `package_sha256`, `plan_hash`, `revision_id` (if any).

### Agent routes (mirror upgrades)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/agent/deployments/{id}/status` | State transitions + failure_code |
| POST | `/api/agent/deployments/{id}/health` | Normalized health snapshot |
| (optional) | file chunk upload/download | If not using package_uri |

### Client routes (CLI/MCP via Central)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/deployments/plan` | Read-only plan materialization |
| POST | `/api/deployments` | Create APPROVED/PLANNED row (idempotent request_id) |
| POST | `/api/deployments/{id}/approve` | Explicit approval if two-step |
| GET | `/api/deployments/{id}` | Status |
| POST | `/api/deployments/{id}/rollback` | Request ROLLING_BACK |

Auth: existing agent credential for agent routes; existing client patterns for
plan/apply (no admin bootstrap to coding agents).

---

## 6. Upgrade / downgrade

### Upgrade (Alembic)

1. `op.create_table("host_deployments", ...)`
2. Create indexes/constraints above
3. Optional `deployment_revisions`
4. No backfill — empty table is correct for v1.4 fleets

### Downgrade

1. Drop new tables
2. No changes to `hosts`, `allocations`, `host_upgrades`, `host_probes`

### v1.4 compatibility

- Old agents ignore unknown heartbeat fields
- New Central without new agent: deployments stay WAITING / unclaimed — safe
- Allocations/config semantics unchanged

---

## 7. Concurrency / idempotency (service rules)

- UNIQUE (`host_id`, `request_id`) → replay returns same row
- At most one non-terminal deployment per (`project`, `environment`, `host_id`)
- Different targets (different `host_id`) may proceed concurrently
- Never release allocations solely because a deployment FAILED/ROLLED_BACK

---

## 8. What must NOT be done

- Store secrets in `ports_json` / `ingress_json` / logs
- Reuse `host_upgrades` rows for Compose deploy
- Put arbitrary command strings in the deployment row
- Claim Internet/PUBLIC_READY from deployment success alone

---

## 9. Approval ask

Please approve or amend:

1. Create `host_deployments` (and optionally `deployment_revisions`)
2. Additive heartbeat + agent/client APIs as above
3. Package via URI + checksum (upgrade-like) first
4. Then implement Phase 18 runtime (Compose adapter, MCP/CLI, E2E)

Until approved: **no Alembic revision, no deployment code, no production touch.**

---

## 10. Gate report fields

| Field | Value |
|-------|-------|
| Migration required | **YES** |
| Schema proposal | `docs/design/deployment-schema-proposal.md` |
| Implementation allowed | **NO** |
| Phase 18 classification | **SCHEMA DECISION REQUIRED** |
