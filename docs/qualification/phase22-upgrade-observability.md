# Phase 22 — Upgrade Observability + Audit + Operator Controls

**Date:** 2026-09-24  
**Branch:** `feature/upgrade-observability-controls`  
**Base:** Phase 21 `b0a0a966517c0f53b12ec18d77d96276c8b7dcc7` (tree `61a68e21f9b9af0761e8de7865d2e905e99a7b82`)

Development / disposable qualification only. No production mutation. No public version bump. No DB migration.

---

## Status model

Derived read model `UpgradeStatusOut` from existing `Host` + `HostUpgrade` (no new columns).

| Field | Source |
|-------|--------|
| current_version / host_health / host_lifecycle | Host |
| upgrade_state / target_version | HostUpgrade |
| waiting_reason / progress_status / failure_code | Derived |
| reconciliation_status | Runtime vs upgrade history (never rewrites history) |
| operator_actions | VIEW / RETRY / CANCEL / ROLLBACK |
| attempt_index | Same host+artifact+target lineage |

### Waiting / failure codes

`WAITING_FOR_AGENT`, `WAITING_FOR_RESTART`, `WAITING_FOR_HEALTH`, `HOST_OFFLINE`, `HOST_DECOMMISSIONED`, `OPERATOR_CANCELLED`, `STUCK_TIMEOUT`, `CHECKSUM_MISMATCH`, `ARTIFACT_INVALID`, `ARTIFACT_DOWNLOAD_FAILED`, `UPGRADE_FAILED`, `ROLLOUT_STOPPED_ON_FAILURE`

### Runtime vs control-plane

Historical `FAILED` with runtime later on target → `reconciliation_status=RUNTIME_OK_HISTORY_FAILED`. Never shown as SUCCEEDED.

Mac/offline while `RESTARTING` stays `WAITING_FOR_RESTART` (not auto-FAILED).

---

## APIs

| Method | Path |
|--------|------|
| GET | `/api/upgrades/{id}/status` |
| GET | `/api/hosts/{id}/upgrade-status` |
| POST | `/api/upgrades/{id}/retry` (Phase 21) |
| POST | `/api/upgrades/{id}/cancel` (Phase 21) |
| GET | `/api/upgrade-rollouts/{request_id}` (+ `stop_reason`, `operator_summary`) |

---

## CLI

```
portforge upgrade status --upgrade-id|--host-id [--json]
portforge upgrade retry <id> --yes [--json]
portforge upgrade cancel <id> --yes [--json]
portforge upgrade rollout <request_id> [--json]
```

Admin bootstrap token via env/`--admin-token`. Mutations require `--yes`.

---

## Dashboard

- Fleet: active upgrade shows `progress_status` / `waiting_reason`
- Host upgrades: typed status panel; Retry/Cancel gated on `operator_actions` (uppercase)
- BFF proxies only; no browser admin secret

---

## Audit

Existing `activity_events`. Types: `UPGRADE_CREATED`, `CLAIMED`, `STATE`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `RETRY`, `ROLLBACK`, `STUCK`, `RECONCILED`.

SUCCEEDED/FAILED/CANCELLED/RETRY/ROLLBACK/STUCK are **idempotent** per upgrade_id (no duplicate SUCCEEDED on repeated heartbeat).

---

## MCP

**DEFERRED** — mutating fleet tools remain deferred; read-only MCP status also deferred (CLI/API/dashboard sufficient; avoids expanding MCP surface).

---

## Compatibility

Protocol **1** · Contract **1** · Machine schema **1** (additive JSON only) · DB head **d5e6f7a8b9c0** · Migration **NO**

---

## Limitations

1. Agent local download retry/backoff is not visible to Central until FAILED.
2. Rollout canary membership is not persisted; GET derives stop_reason from aggregate status.
3. `next_retry_at` not exposed (no durable field; agent backoff is local).

---

## Cumulative unreleased scope

| Phase | Scope |
|-------|-------|
| 20 | Restart handoff + heartbeat reconciliation |
| 21 | Stuck recovery, bounded retry, fleet canary/concurrency/stop-on-failure |
| 22 | Typed status, audit, CLI/dashboard operator controls |

**Ready for v1.5.2 Release Qualification:** YES (candidate only — do not release in this phase)

---

## Regression

| Suite | Result |
|-------|--------|
| Backend | **442 PASS** (Phase 21 baseline 431) |
| Agent | **1006 PASS / 5 skipped** (baseline 976/5) |
| Dashboard | **205 PASS** (baseline 168) |
| Service (gen+ops) | **79 PASS** |
| Lint | PASS |
| Typecheck | PASS |
| Build | PASS |
| Doctor RO | PASS |

### Disposable control-plane qual (`:58005`)

Harness: `.qual-temps/phase22/run_phase22_qual.py` — all checks PASS (typed status, checksum permanent, operator retry/cancel, canary stop-on-failure, production RO). Production used: **NO**.

---

## Sign-off

PHASE 22: PASS · READY TO FREEZE: YES · READY TO RELEASE: **NO** · NEXT: STOP
Ready for v1.5.2 Release Qualification: **YES** (do not create release branch/tag in this phase)
