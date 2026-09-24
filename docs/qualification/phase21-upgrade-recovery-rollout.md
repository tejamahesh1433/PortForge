# Phase 21 — Upgrade Recovery + Retry + Fleet Rollout Safety

**Date:** 2026-09-24  
**Branch:** `feature/upgrade-recovery-rollout`  
**Base:** Phase 20 freeze `114b8daebd85d8cbbc162b9e95656adaa2eb3dce` (tree `4168b87c0fe4e770fe05f1b16a2a6ce095cf2167`)

Development / disposable qualification only. No production mutation. No public version bump. No DB migration.

---

## Architecture summary

Zero-migration design on existing `host_upgrades` + shared `request_id` for fleet identity.

| Capability | Mechanism |
|------------|-----------|
| Stuck detection | `POST /api/upgrades/recover-stuck` — state-bucket thresholds vs `updated_at` → `FAILED` `stuck_timeout:<STATE>` |
| Cancel | SAFE: APPROVED/WAITING/DOWNLOADING/VERIFYING → `operator_cancelled`; REJECT: INSTALLING/RESTARTING/VERIFYING_HEALTH |
| Operator retry | `POST /api/upgrades/{id}/retry` — **new** row; FAILED immutable |
| Agent transient download retry | Typed `UpgradeTransientDownloadError`; max 3; exp backoff; SHA/install never retried |
| Fleet | `POST /api/upgrade-rollouts` + `…/advance` + `GET …/{request_id}`; canary, concurrency, stop_on_failure, offline policy, skip_if_current |
| Phase 20 reconcile | Unchanged |

MCP mutating fleet tools: **DEFERRED** (security surface).

---

## Physical / disposable evidence

### Control-plane + fleet (qual Central `:58005`)

Harness: `.qual-temps/phase21/run_phase21_qual.py`  
Results: `.qual-temps/phase21/evidence/phase21_qual_results.json`

| Check | Result |
|-------|--------|
| Stuck RESTARTING → FAILED `stuck_timeout:RESTARTING` | PASS |
| Cancel APPROVED | PASS |
| Cancel RESTARTING rejected | PASS |
| Retry new row; FAILED immutable | PASS |
| Phase 20 heartbeat reconcile → SUCCEEDED | PASS |
| Fleet canary (3 hosts, canary=1) | PASS |
| Advance after canary | PASS |
| Stop-on-failure | PASS |
| Skip already-current | PASS |
| Production used | **NO** |

### Linux / Windows self-restart physical

Phase 20 disposable physical (WSL systemd-user + Windows Scheduled Task) already proved restart handoff → SUCCEEDED. Phase 21 does not change restart adapters (`--no-block` / Windows helper). Phase 21 requalified reconciliation on disposable Central (above).

---

## Regression

| Suite | Result |
|-------|--------|
| Backend | **431 PASS** (Phase 20 baseline 393) |
| Agent | **976 PASS / 5 skipped** (baseline 972/5) |
| Dashboard | **168 PASS** |
| Service (gen+ops) | **79 PASS** |
| Typecheck | PASS |
| Doctor RO | PASS |

---

## Compatibility

Protocol **1** · Contract **1** · Machine schema **1** · MCP **unchanged** (mutating fleet deferred) · DB head **d5e6f7a8b9c0** · Migration **NO**

---

## Security

Generic shell / remote command / arbitrary executable/args: **ABSENT**  
Artifact HTTPS + SHA unchanged. Restart adapters allowlisted.

---

## Limitations

1. Rollout policy is re-supplied on `advance` (not persisted) — by design, no migration.
2. Skipped hosts appear on create/advance responses; `GET` rollout is DB-row aggregate only.
3. INSTALLING is not auto-replayed after crash — stuck timeout → FAILED; operator retry creates a new attempt.
4. MCP fleet mutation deferred.

---

## Sign-off

PHASE 21: PASS · READY TO FREEZE: YES · READY TO RELEASE: **NO** · NEXT: STOP
