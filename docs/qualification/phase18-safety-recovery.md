# Phase 18.6 — Deployment Safety + Interruption/Recovery

**Date:** 2026-09-24  
**Base:** `064f87c`  
**Branch:** `feature/deployment-orchestration`  
**Harness:** `.qual-temps/phase18-physical/run_phase18_6_safety.py`  
**Evidence:** `.qual-temps/phase18-physical/evidence/phase18_6_results.json`

## Port recheck algorithm

Immediately after Compose `config` validation and **before** `compose up`:

1. Read declared **host** ports from heartbeat `ports_json.services[].host_port` (+ protocol).
2. Never treat `internal_port` as a host binding.
3. Discover Docker published bindings and native listeners on the target.
4. Classify each required `(protocol, host_port)`:

| Verdict | Meaning |
|---------|---------|
| `FREE` | No listener |
| `EXPECTED_EXISTING_DEPLOYMENT` | Docker publish owned by this deployment’s Compose project (`com.docker.compose.project` == `compose_project_name(...)`) |
| `UNEXPECTED_OCCUPANT` | Native listener or Docker project mismatch |
| `UNKNOWN` | Discovery failed — **fail closed** |

5. `UNEXPECTED` → `DEPLOYMENT_PORT_CONFLICT` (no apply, no silent reallocation).  
6. `UNKNOWN` → `DEPLOYMENT_PORT_STATE_UNKNOWN`.

### Residual TOCTOU

Recheck narrows the race between final check and Docker bind; it does **not** provide OS-level atomic port reservation. Documented limitation — no socket hold across the apply boundary in this phase.

## Claim lease semantics

| Field | Behavior |
|-------|----------|
| `claim_token` | `secrets.token_urlsafe(32)` on first claim or after expiry |
| `claim_expires_at` | `now + CLAIM_LEASE_SECONDS` (300) |
| Renewal | Agent calls `claim` again before apply; non-expired claim returns same token |
| Stale token | Status/health with wrong/expired token → `409 DEPLOYMENT_CLAIM_MISMATCH` |
| Terminal | Claim/status on SUCCEEDED/FAILED/ROLLED_BACK → 409 |
| Heartbeat reclaim | Non-terminal jobs with **expired** lease (or APPROVED) are re-delivered on `pending_deployment` |

## Physical results (48 PASS)

| Case | Result |
|------|--------|
| Stolen port (unrelated bind) | PASS — `DEPLOYMENT_PORT_CONFLICT`, no Up containers |
| Expected owner A→B | PASS — no false conflict |
| Wrong Compose project owner | PASS — conflict |
| Agent interrupt before apply + expiry reclaim | PASS — SUCCEEDED, no duplicate stack |
| Central restart before claim | PASS |
| Central restart after success | PASS — durable SUCCEEDED |
| Critical path A→B→rollback | PASS |
| Broken C | PASS — `DEPLOYMENT_UNHEALTHY` |
| During-apply interrupt | SKIPPED — non-deterministic Docker midpoint; covered by `PORTFORGE_DEPLOYMENT_TEST_INTERRUPT` unit hook |
| During-verify interrupt | SKIPPED physically; unit hook `during_verify` present |

## Defects fixed in 18.6

1. Pre-apply host port recheck missing → implemented (`port_recheck.py` + handler).  
2. Interrupted `STARTING` jobs never re-delivered after lease expiry → `get_pending_for_host` includes expired in-progress.  
3. Absolute/UNC archive paths → regression tests strengthened.  
4. SUCCEEDED without `revision_id` → explicit 422 regression test.

## Security

No generic shell / remote command / arbitrary Docker or Compose argv. Port recheck uses fixed discovery collectors only. Claim tokens are not surfaced via MCP.
