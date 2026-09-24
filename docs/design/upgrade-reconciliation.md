# Upgrade-State Reconciliation (Phase 20)

Extends Phase 10 (safe agent upgrade management) with Central-side heartbeat
reconciliation for the process-boundary gap that occurs between RESTARTING and
VERIFYING_HEALTH/SUCCEEDED.  Also hardens the Linux systemd restart adapter.

---

## State machine

```
APPROVED          ─ admin creates upgrade for an online host
WAITING_FOR_AGENT ─ admin creates upgrade while host offline
DOWNLOADING       ─╮
VERIFYING         │ agent-reportable via status API
INSTALLING        │
RESTARTING        ─╯
VERIFYING_HEALTH  ─╮ Central-managed via heartbeat reconciliation
SUCCEEDED         ─╯
FAILED            ─ terminal; set by agent or Central on error / decommission
ROLLED_BACK       ─ terminal; set by admin rollback endpoint
```

### Ownership per transition

| From → To | Who owns the transition | Mechanism |
|-----------|------------------------|-----------|
| (create) → APPROVED | Admin | `POST /api/hosts/{id}/upgrades` |
| (create) → WAITING_FOR_AGENT | Admin | Same, host offline at creation time |
| WAITING_FOR_AGENT → APPROVED | (automatic, same create-time selection) | |
| APPROVED → DOWNLOADING | Agent | `POST /api/agent/upgrades/{id}/status` |
| DOWNLOADING → VERIFYING | Agent | status API |
| VERIFYING → INSTALLING | Agent | status API |
| INSTALLING → RESTARTING | Agent | status API |
| RESTARTING → VERIFYING_HEALTH | **Central** | heartbeat reconciliation |
| VERIFYING_HEALTH → SUCCEEDED | **Central** | heartbeat reconciliation |
| Any non-terminal → FAILED | Agent or Central (decommission) | status API / lifecycle |
| Any → ROLLED_BACK | Admin | rollback endpoint |

---

## Process-boundary restart handoff

After the agent reports RESTARTING and calls `restart_service()`, the **old
process exits**.  The old process can never report VERIFYING_HEALTH or SUCCEEDED
because it is no longer running.  The **new process** starts fresh; it has no
upgrade context in memory and must not call `report_upgrade_status` (it does not
know the upgrade ID).

Central bridges this gap via heartbeat reconciliation:

1. New process connects, sends a heartbeat carrying `agent_version` = the newly
   installed target version and the same `host_id` (UUID is never changed by an
   upgrade).
2. Central's heartbeat handler calls `reconcile_upgrade_after_heartbeat` after
   `record_heartbeat` completes.
3. If there is a RESTARTING (or VERIFYING_HEALTH) upgrade for this host *and*
   the reported version matches the target exactly, Central advances through
   VERIFYING_HEALTH into SUCCEEDED in that reconciliation and clears
   `host.last_error`. The authenticated heartbeat itself is the health proof —
   no separate probe round-trip is required.

---

## Reconciliation invariants

All of the following must hold before any state is mutated:

1. **Same host UUID via auth** — the bearer token is validated by `require_agent`
   before the heartbeat handler is entered; `host_id` is always taken from the
   token, never from the request body alone.
2. **Exact target version match** — `reported_version == upgrade.target_version`
   as plain string equality.  No semver relaxation.
3. **State in {RESTARTING, VERIFYING_HEALTH}** — only these states are eligible;
   all others are skipped.
4. **`claimed_at` set** — the upgrade must have been claimed (i.e. the agent
   reported at least DOWNLOADING before restarting).  An unclaimed upgrade in
   RESTARTING would be a data anomaly; we skip rather than guess.
5. **ACTIVE host** — `lifecycle_state != DECOMMISSIONED`; decommissioned hosts
   receive no reconciliation.
6. **Terminal states are immutable** — SUCCEEDED, FAILED, and ROLLED_BACK are
   never touched by reconciliation.  This invariant is enforced by the state
   guard (`state.in_(["RESTARTING", "VERIFYING_HEALTH"])`) in the repository
   query, and by never calling the function for terminal rows.
7. **`SELECT ... FOR UPDATE`** — the eligibility query uses a row-level lock to
   prevent duplicate transitions if two heartbeat requests race (e.g. after a
   network retry).

---

## Linux `--no-block` rationale

`systemctl --user restart <unit>` is *synchronous by default*: it waits for the
unit to reach the active state (or time out) before returning.  For a unit that
restarts the current process this is self-defeating — the unit cannot reach
`active` while the old process occupies the PID and has not yet returned from
the subprocess call.

Adding `--no-block` (`systemctl --user restart --no-block <unit>`) makes systemd
enqueue the restart asynchronously and return immediately (exit code 0).
The kernel then delivers SIGTERM to the old process, which exits, and systemd
starts the new one.

This is **not** interpreted as a success signal.  The restart is considered
merely *requested*; Central's heartbeat reconciliation (above) is the actual
success criterion — VERIFYING_HEALTH and SUCCEEDED are only reached once the
new process sends a heartbeat carrying the expected `agent_version`.

A return code of -15 (SIGTERM during the subprocess call) is **not** treated
as success.  The `--no-block` flag eliminates that scenario entirely: because
systemd returns before the restart happens, the calling process (the old agent)
has already received and logged the result before SIGTERM is delivered.

---

## Data model fields used by reconciliation

All fields already exist in the `host_upgrades` table; no migration is needed.

| Field | Role in reconciliation |
|-------|----------------------|
| `state` | Checked and mutated |
| `host_id` | FK used to scope the lookup |
| `target_version` | Compared against `reported_version` from heartbeat |
| `claimed_at` | Safety guard — must not be NULL |
| `completed_at` | Set when SUCCEEDED |
| `host.last_error` | Cleared when SUCCEEDED |
