# Deployment Schema Proposal (Phase 18)

**Status:** SCHEMA DECISION: **APPROVED WITH AMENDMENTS**  
**Implementation:** allowed after this document matches the approved design.

**Context:** [`deployment-orchestration.md`](deployment-orchestration.md).

Current Alembic head: `c3d4e5f6a1b2`.

---

## 0. Approved decision summary

| Item | Decision |
|------|----------|
| Migration required | **YES** |
| `host_deployments` | **REQUIRED** — immutable **attempt/history** rows |
| `deployment_revisions` | **REQUIRED** (not optional) — durable rollback targets |
| `PLANNED` in DB | **NO** — `/api/deployments/plan` is read-only; insert only on apply/approval |
| Package transport | **HTTPS URI + package_sha256** (chunked transfer deferred) |
| Protocol / contract / MCP | Remain **1** if additive + v1.4 agent ignore-test passes |
| Generic remote commands | **PROHIBITED** |
| Production migration | **PROHIBITED** during Phase 18 development |
| Host hard-delete | `ON DELETE CASCADE` — destroys deployment/revision history |
| Host decommission | **Retains** history |

### Relationship model

```text
Host
 │
 ├── Deployment Attempt A ─────→ Revision A (known-good)
 │
 ├── Deployment Attempt B ─────→ Revision B (known-good)
 │
 └── Deployment Attempt C (FAILED)
          │
          └── rollback attempt ──→ Revision B (via rollback_revision_id)
```

Attempts are **immutable history**. Rollback is a **new** attempt (or dedicated rollback transition that records `rollback_revision_id`), never an overwrite of A/B.

Active deployed content lives on `deployment_revisions` (`is_known_good` / current pointers), not by keeping a SUCCEEDED attempt non-terminal.

---

## 1. Table: `deployment_revisions`

Durable immutable package/revision identity — rollback points at these rows.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | Central revision UUID |
| `host_id` | UUID FK → `hosts.id` ON DELETE CASCADE | |
| `project` | String(256) NOT NULL | |
| `environment` | String(64) NOT NULL | |
| `revision_id` | String(64) NOT NULL | Agent-side revision key |
| `package_sha256` | String(64) NOT NULL | Archive integrity |
| `package_manifest_sha256` | String(64) NOT NULL | Internal file-list integrity |
| `source_deployment_id` | UUID FK → `host_deployments.id` ON DELETE SET NULL | Attempt that created it (nullable if needed for create order) |
| `is_known_good` | Boolean NOT NULL DEFAULT false | Eligible rollback target |
| `created_at` / `updated_at` | timestamptz | |

### Constraints

| Name | Definition |
|------|------------|
| `uq_deployment_revisions_host_revision` | UNIQUE (`host_id`, `revision_id`) |
| `ix_deployment_revisions_project_env_host` | INDEX (`project`, `environment`, `host_id`) |
| `ix_deployment_revisions_known_good` | INDEX (`host_id`, `project`, `environment`) WHERE `is_known_good` |

---

## 2. Table: `host_deployments`

One row per **durable execution attempt** (apply or rollback request).  
**Never** insert for read-only plan previews.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `host_id` | UUID FK → `hosts.id` ON DELETE CASCADE | Target |
| `project` | String(256) NOT NULL | |
| `environment` | String(64) NOT NULL | |
| `target_alias` | String(128) NULL | Display only |
| `request_id` | String(128) NOT NULL | Idempotency |
| `state` | String(32) NOT NULL | See §3 |
| `plan_hash` | String(64) NOT NULL | |
| `workspace_fingerprint` | String(64) NULL | |
| `package_uri` | String(2048) NOT NULL | Trusted HTTPS artifact (apply path) |
| `package_sha256` | String(64) NOT NULL | Downloaded archive checksum |
| `package_manifest_sha256` | String(64) NOT NULL | Inner manifest checksum |
| `result_revision_id` | UUID NULL FK → `deployment_revisions.id` | Revision produced on success |
| `rollback_revision_id` | UUID NULL FK → `deployment_revisions.id` | Revision restored by this attempt |
| `ports_json` | JSONB NULL | Allowlisted structure only |
| `ingress_json` | JSONB NULL | Allowlisted structure only |
| `health_json` | JSONB NULL | Allowlisted structure only |
| `failure_reason` | String(1024) NULL | |
| `failure_code` | String(64) NULL | |
| `approved_at` | timestamptz NULL | |
| `claimed_at` | timestamptz NULL | |
| `claim_token` | String(64) NULL | Lease / ownership generation |
| `claim_expires_at` | timestamptz NULL | |
| `last_agent_update_at` | timestamptz NULL | |
| `completed_at` | timestamptz NULL | |
| `created_by` | String(64) NULL | mcp / cli / … |
| `created_at` / `updated_at` | timestamptz | |

### Constraints / indexes

| Name | Definition |
|------|------------|
| `uq_host_deployments_host_request` | UNIQUE (`host_id`, `request_id`) |
| `ix_host_deployments_host_state` | INDEX (`host_id`, `state`) |
| `ix_host_deployments_project_env_host` | INDEX (`project`, `environment`, `host_id`) |
| `uq_host_deployments_active` | **Partial UNIQUE** (`project`, `environment`, `host_id`) WHERE `state` NOT IN (`SUCCEEDED`, `FAILED`, `ROLLED_BACK`) |

Service layer still checks for clean errors; the partial unique index is the race-safe invariant.

### Create order (circular FKs)

1. Create `host_deployments` **without** `result_revision_id` / `rollback_revision_id` FKs  
2. Create `deployment_revisions` with `source_deployment_id` FK → `host_deployments`  
3. Add `result_revision_id` / `rollback_revision_id` FKs → `deployment_revisions`  

---

## 3. States

### Durable attempt states (no `PLANNED`)

Non-terminal (hold concurrency lock):

`APPROVED`, `PREPARING`, `TRANSFERRING`, `STARTING`, `VERIFYING`, `ROLLING_BACK`

Terminal (release concurrency lock):

`SUCCEEDED`, `FAILED`, `ROLLED_BACK`

`SUCCEEDED` is terminal for the **attempt**. The live revision remains active via
`deployment_revisions`, not by leaving the attempt open.

Planning returns a deterministic plan object **without** inserting a row.

---

## 4. Claim lease

| Field | Role |
|-------|------|
| `claim_token` | Opaque ownership generation issued on claim |
| `claim_expires_at` | Lease expiry; stale claims reclaimable |
| `last_agent_update_at` | Progress heartbeat from agent |
| `claimed_at` | First claim time |

Status updates must present the current `claim_token`. Mismatched/expired token → reject (`DEPLOYMENT_CLAIM_MISMATCH` / reclaim path). Prevents crashed and restarted agents from dual-reporting without a generation bump.

---

## 5. JSON allowlists (application-validated)

Persisted JSON must pass schema validation before write. Reject unknown keys.

**ports_json (example allowlist):**  
`services[]`: `name`, `internal_port`, `host_port`, `protocol`, `reason_code`

**ingress_json:**  
`name`, `scheme`, `hostname`, `public_port`, `service`, `status`, `bound_host_port`

**health_json:**  
`overall` (`HEALTHY`\|`UNHEALTHY`\|`HEALTH_UNKNOWN`\|`RUNTIME_STARTED`),  
`checks[]`: `id`, `kind` (`compose`\|`container`\|`http`\|`tcp`), `status`, `detail` (short, non-secret)

No passwords, tokens, env dumps, Authorization headers.

---

## 6. Package transport (Option A approved)

```text
Central ──typed job──► Agent
                         ├── fetch package_uri (trusted policy)
                         ├── verify package_sha256
                         ├── safe extract + verify package_manifest_sha256
                         ├── Compose validate / apply
                         └── health verify
```

**Trusted-artifact policy (required):**

- HTTPS only (or explicitly approved schemes matching upgrade artifacts)
- Approved host/source allowlist (same class of policy as upgrades)
- Max size limit
- Checksum before extract
- Archive entry path validation (no `../`, no absolute paths)
- Redirects only to equally trusted destinations (or redirects disallowed)

Chunked transfer: **deferred**.

---

## 7. Host deletion vs decommission

| Action | Deployment / revision history |
|--------|-------------------------------|
| **Remove Record** (hard delete host) | CASCADE deletes `host_deployments` + `deployment_revisions` |
| **Decommission** | Host retained; history **retained** |

Document in APIs and guides so operators are not surprised.

---

## 8. API / heartbeat (additive, protocol 1)

### Heartbeat

`HeartbeatResponse.pending_deployment: Optional[PendingDeploymentOut]`

Include: `deployment_id`, `request_id`, `project`, `environment`, `state`,
`package_uri`, `package_sha256`, `package_manifest_sha256`, `plan_hash`,
`claim_token`, `claim_expires_at`.

### Agent routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/agent/deployments/{id}/claim` | Obtain/renew lease + token |
| POST | `/api/agent/deployments/{id}/status` | State + failure_code (token required) |
| POST | `/api/agent/deployments/{id}/health` | Allowlisted health snapshot |

### Client routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/deployments/plan` | **Read-only** — no DB insert |
| POST | `/api/deployments` | Create **APPROVED** attempt (idempotent `request_id`) |
| GET | `/api/deployments/{id}` | Status |
| POST | `/api/deployments/{id}/rollback` | New attempt or transition targeting `rollback_revision_id` |

No admin bootstrap tokens to coding agents.

### Compatibility qualification (required)

```text
v1.4 agent → new development Central
```

Old agent continues heartbeat/sync; never claims deployment work; ignores unknown field.

---

## 9. Alembic upgrade / downgrade

### Upgrade

1. Create `host_deployments` (without revision FKs)  
2. Create partial unique index `uq_host_deployments_active`  
3. Create `deployment_revisions`  
4. Add FKs `result_revision_id`, `rollback_revision_id`  
5. No backfill  

### Downgrade

Drop FKs → drop `deployment_revisions` → drop `host_deployments`.  
No changes to `hosts`, `allocations`, `host_upgrades`, `host_probes`.

---

## 10. Allocation / config separation

Deployment FAILED / ROLLED_BACK **must not** automatically release PortForge
allocations or rewrite unrelated config. Preserve Phase 17 separation.

---

## 11. Implementation gate fields

| Field | Value |
|-------|-------|
| Migration required | **YES** |
| Schema proposal | this document (amended) |
| Implementation allowed | **YES** (after amended doc committed) |
| Phase 18 classification | **IMPLEMENTATION IN PROGRESS** |
