# Upgrade Observability + Operator Controls (Phase 22)

Builds on Phase 20 reconciliation and Phase 21 recovery/rollout.

**No DB migration. Protocol 1. Contract 1. Machine schema 1 (additive).**

---

## Operator questions → fields

See `UpgradeStatusOut` / `GET /api/upgrades/{id}/status`.

Runtime truth (`current_version`, `host_health`) is separate from control-plane
history (`upgrade_state`). A FAILED row is never rewritten to SUCCEEDED when
runtime later matches target.

---

## Safe actions

| Action | When |
|--------|------|
| VIEW | Always |
| CANCEL | APPROVED, WAITING_FOR_AGENT, DOWNLOADING, VERIFYING |
| RETRY | FAILED and no other non-terminal upgrade; host ACTIVE |
| ROLLBACK | Terminal with previous artifact metadata; no non-terminal |

Backend remains authoritative; UI/CLI only surface actions Central will accept.

---

## Audit

Append-only `activity_events` with `source=upgrade`. Idempotent terminal events
prevent heartbeat storms from duplicating SUCCEEDED.

---

## MCP

Mutating fleet MCP: **DEFERRED** (Phase 21).  
Read-only upgrade status MCP: **DEFERRED** (Phase 22) — API/CLI/dashboard cover the need.

---

## Surfaces

- Central typed status + existing retry/cancel/rollout
- CLI `portforge upgrade …`
- Dashboard fleet column + host upgrade status panel + BFF
