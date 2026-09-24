# Phase 20 — Upgrade-State Reconciliation + Self-Restart Hardening

**Date:** 2026-09-24  
**Branch:** `feature/upgrade-reconciliation`  
**Base:** `v1.5.1` (`45b6639199b65a2022fc6449e7ce02d8a4e9f5ce`, tree `47b804c96d445acd42794cfe6d728662fa997082`)  
**Scope:** Development / disposable qualification only. No production mutation. No public version bump.

---

## Defects reproduced

### Linux (Defect A)

**YES** (structural + historical production observation; disposable fix path qualified).

Root cause: `systemctl --user restart <unit>` is synchronous by default. The calling agent process is the unit being restarted, so the subprocess can be interrupted with SIGTERM (`returncode = -15`). Pre-Phase-20 agent code treated restart exceptions as `FAILED` via the status API even though the package install succeeded and the new process came up healthy.

### Windows (Defect B)

**YES** (structural + disposable physical).

Root cause: after reporting `RESTARTING`, the old process exits. The new process has no in-memory upgrade context and never posts `VERIFYING_HEALTH` / `SUCCEEDED`. Central had no heartbeat reconciliation path, so upgrades remained `RESTARTING` indefinitely.

---

## Architecture

### Restart handoff

1. Agent completes download → verify → install.
2. Agent reports `RESTARTING`.
3. Agent calls allowlisted platform restart (`systemctl --user restart --no-block`, Windows helper / schtasks, launchctl).
4. On restart-call exception after `RESTARTING` was reported: **do not** report `FAILED`; leave `RESTARTING` for Central reconciliation.
5. New process reconnects with the same host UUID and credential; reports `agent_version` = installed version.

### Heartbeat reconciliation (Central)

`reconcile_upgrade_after_heartbeat` runs after an authenticated heartbeat:

- Eligible states only: `RESTARTING`, `VERIFYING_HEALTH`
- Exact `reported_version == target_version`
- Same host via bearer credential (`host_id` from token)
- `claimed_at` must be set
- Host not `DECOMMISSIONED`
- `SELECT … FOR UPDATE` against the eligible row
- Advances `RESTARTING → VERIFYING_HEALTH → SUCCEEDED` in one reconciliation when the heartbeat itself proves the target version
- Terminal rows (`SUCCEEDED` / `FAILED` / `ROLLED_BACK`) are never touched

### Linux `--no-block`

`restart_via_systemd` now uses:

```text
systemctl --user restart --no-block <literal-unit>
```

Unit name from env override or default literal — never from Central payload. `shell=False`. Success is **not** inferred from return code alone; Central reconciliation is authoritative.

---

## Reconciliation invariants (tested)

| Invariant | Coverage |
|-----------|----------|
| Matching target heartbeat → SUCCEEDED | unit + physical |
| Wrong version | unit |
| Wrong host | unit |
| Wrong credential | unit |
| Stale attempt (FAILED A + RESTARTING B) | unit |
| FAILED immutable | unit |
| ROLLED_BACK immutable | unit |
| APPROVED not skipped to SUCCEEDED | unit |
| Install-stage FAILED not completed by later matching version | unit |
| Decommissioned host | unit |
| Idempotent after SUCCEEDED | unit |
| Restart exception → stay RESTARTING | agent unit |
| `--no-block` in systemd argv | agent unit |

---

## Schema / protocol

| Gate | Result |
|------|--------|
| New DB migration | **NO** |
| Protocol / contract / machine schema / MCP | **unchanged** (1) |
| Package public version bump | **NO** (dev phase) |

---

## Physical qualification (disposable)

### Linux (WSL2 systemd `--user`)

| Check | Result |
|-------|--------|
| Disposable unit `portforge-agent-p20.service` | PASS |
| Source → target (`1.5.20` → `1.5.21`) | PASS |
| Self-restart via `--no-block` | PASS |
| Central final state | **SUCCEEDED** |
| UUID preserved | YES |
| Restart loop | ABSENT |

Evidence: `.qual-temps/phase20/evidence/linux_upgrade.json`, `linux_journal.txt`

### Windows (Scheduled Task)

| Check | Result |
|-------|--------|
| Disposable task `PortForge Agent P20 Qual` | PASS |
| Source → target (`1.5.20` → `1.5.21`) | PASS |
| Restart helper + heartbeat reconcile | PASS |
| Central final state | **SUCCEEDED** |
| UUID preserved | YES |

Evidence: `.qual-temps/phase20/evidence/windows_upgrade.json`

Production task name `PortForge Agent` and production Linux unit were **not** used.

### Mac

No Mac-specific code changes. v1.5.1 LaunchAgent KeepAlive (`SuccessfulExit=false`, `NetworkState=true`) unchanged. Service suite 79 PASS.

---

## Regression

| Suite | Result |
|-------|--------|
| Backend | **393 PASS** (was 381; +12 Phase 20) |
| Agent | **972 PASS / 5 skipped** |
| Dashboard | **168 PASS** |
| Service | **79 PASS** |
| Typecheck (dashboard) | PASS |
| Doctor (RO) | PASS |

---

## Security boundaries

| Check | Result |
|-------|--------|
| Generic shell / `shell=True` in restart path | ABSENT |
| Generic remote command | ABSENT |
| Arbitrary executable / args from Central | ABSENT |
| Artifact HTTPS + SHA-256 | unchanged |

---

## Production (read-only observation at freeze)

| Item | Value |
|------|-------|
| Central | 1.5.0 |
| Dashboard | 1.5.0 (unchanged this phase) |
| DB head | `d5e6f7a8b9c0` |
| Agents | 4 identities on 1.5.1 (see freeze report for live health mix) |
| Historical upgrade rows | **not modified** |

---

## Remaining limitations

1. Upgrades that reach `FAILED` (including historical Linux `-15` rows) remain `FAILED` forever by design — operators must create a new upgrade attempt.
2. Stuck `RESTARTING` without a matching target-version heartbeat stays `RESTARTING` until timeout/operator action (no false SUCCEEDED).
3. Public version number for this work is intentionally undecided (candidate for v1.5.2 or a later grouping).

---

## Sign-off

| Item | Value |
|------|-------|
| PHASE 20 | PASS |
| READY TO FREEZE | YES |
| READY TO RELEASE | **NO** |
| NEXT | STOP |
