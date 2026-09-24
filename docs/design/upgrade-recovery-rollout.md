# Upgrade Recovery + Fleet Rollout (Phase 21)

Builds on Phase 20 heartbeat reconciliation. **No DB migration. Protocol 1.**

---

## Current architecture (as implemented)

### State machine

```
APPROVED | WAITING_FOR_AGENT
        ↓ (agent status)
DOWNLOADING → VERIFYING → INSTALLING → RESTARTING
                                            ↓ (Central heartbeat reconcile)
                                      VERIFYING_HEALTH → SUCCEEDED

Any non-terminal → FAILED   (agent error, stuck timeout, cancel, decommission)
Any / SUCCEEDED → ROLLED_BACK  (admin rollback creates a *new* upgrade row)
```

Terminal (immutable): `SUCCEEDED`, `FAILED`, `ROLLED_BACK`.

### Claim / lease / generation

- Soft claim: `claimed_at` set on first agent status report.
- **No** claim token, lease TTL, or execution generation on upgrades.
- Heartbeat re-delivers `pending_upgrade` while state ∈ `{APPROVED, WAITING_FOR_AGENT}`.

### Phase 20 restart handoff

Agent reports `RESTARTING` → allowlisted restart → new process heartbeats target
version → Central: `RESTARTING → VERIFYING_HEALTH → SUCCEEDED`.

### Concurrent protection

At most one non-terminal upgrade per host (application-level 409).

### Idempotency

`(host_id, request_id)` unique. Same pair returns existing row (any state).
Same `request_id` **across hosts** is allowed → fleet batch key.

### Surfaces today

| Surface | Capability |
|---------|------------|
| Admin API | create / list / get / rollback |
| Agent API | heartbeat pending + status |
| CLI | none for Central upgrades |
| Dashboard | single-host upgrade + rollback |
| MCP | no upgrade mutation tools |

---

## Recoverable vs terminal

| State | Recoverable? | Notes |
|-------|--------------|-------|
| APPROVED / WAITING_FOR_AGENT | Yes | Re-delivered on heartbeat; cancel SAFE |
| DOWNLOADING / VERIFYING | Yes (agent crash) | Re-offer only if still non-terminal; agent must re-claim from APPROVED path — if already claimed mid-flight, stuck timeout or operator retry |
| INSTALLING | **Not auto-replayed** | Package side effects uncertain → stuck → FAILED; operator creates new attempt |
| RESTARTING / VERIFYING_HEALTH | Yes (Phase 20) | Wait for matching heartbeat; stuck timeout if never returns |
| SUCCEEDED / FAILED / ROLLED_BACK | Terminal | Never auto-converted |

---

## Stuck detection (no new columns)

Uses existing `updated_at` (and state) plus host heartbeat context.

Configurable thresholds (`PORTFORGE_UPGRADE_*`):

| Bucket | Default | States |
|--------|---------|--------|
| Pending delivery | 3600s | APPROVED, WAITING_FOR_AGENT |
| In-flight pre-restart | 1800s | DOWNLOADING, VERIFYING, INSTALLING |
| Post-restart | 900s | RESTARTING, VERIFYING_HEALTH |

Admin: `POST /api/upgrades/recover-stuck`  
Also invoked opportunistically from rollout advance / status reads.

Outcome: `FAILED` with `failure_reason=stuck_timeout:<STATE>` — **not** SUCCEEDED.

---

## Bounded transient retry (agent)

Typed errors — not string matching:

| Error class | Retry? |
|-------------|--------|
| `UpgradeTransientDownloadError` (timeout, connection reset, 5xx) | Yes, bounded |
| `UpgradeSHA256Error` | No |
| `UpgradeValidationError` | No |
| Install / pip failure | No |
| Restart after RESTARTING reported | Handoff (Phase 20), not FAILED |

Defaults: max 3 download attempts, exponential backoff base 2s (cap 30s).
Partial files deleted before each attempt. SHA still mandatory.

Operator retry: `POST /api/upgrades/{id}/retry` creates a **new** row (never mutates FAILED).
Uses new `request_id` derived as `{original}:retry:{n}` or client-supplied.

---

## Cancel

| State | Policy |
|-------|--------|
| APPROVED, WAITING_FOR_AGENT | **SAFE** → FAILED `operator_cancelled` |
| DOWNLOADING, VERIFYING | **SAFE** (best-effort; agent may still finish one status) |
| INSTALLING, RESTARTING, VERIFYING_HEALTH | **REJECTED** (409) |
| Terminal | **REJECTED** |

---

## Fleet rollout (zero new tables)

Logical rollout id = shared `request_id` (UUID string).

### Create `POST /api/upgrade-rollouts`

Body: host_ids, artifact coords, target_version, canary_size, concurrency,
stop_on_failure, offline_policy (`WAIT`|`SKIP`|`FAIL`), skip_if_current.

Behavior:

1. Validate hosts (skip DECOMMISSIONED; offline per policy; already-current → SKIPPED entry, no row).
2. Create HostUpgrade rows for **canary** hosts only first (or all if canary_size=0 meaning all).
3. Return aggregate status.

### Advance `POST /api/upgrade-rollouts/{request_id}/advance`

1. Run stuck recovery on rollout members.
2. If stop_on_failure and any FAILED → do not create more hosts; return paused.
3. If canary incomplete → wait.
4. Create next batch of pending hosts up to `concurrency` minus in-flight count.
5. Return aggregate.

### Status `GET /api/upgrade-rollouts/{request_id}`

Truthful counts: succeeded / failed / in_flight / waiting / skipped / cancelled.
Never flattens partial to PASS.

### Idempotency

Same `request_id` + host returns existing upgrade row. Re-submit of rollout create is safe.

---

## MCP decision

**DEFER** mutating fleet-upgrade MCP tools. Approval/safety model would broaden
attack surface. Read-only status may be added later; not required for Phase 21.

---

## Security

Typed APIs only. Restart adapters remain allowlisted. Artifact HTTPS + SHA unchanged.
No generic shell / remote command / arbitrary args.
