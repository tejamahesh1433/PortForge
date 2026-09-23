# Host Decommission Lifecycle

Phase 8 design for permanent host retirement. Distinct from v1.3 **Remove Record**.

Baseline: PortForge v1.3.0 @ `c337886`. Protocol **1**, agent contract **1** unchanged.

## Current behavior (v1.3)

| Operation | Effect |
|-----------|--------|
| **Remove Record** (`DELETE /api/hosts/{id}`) | Hard-purges Central host row + dependents; revokes credential by deletion; remote agent is **not** stopped; same UUID may re-enroll with a new enrollment token |
| Health (`HEALTHY` / `STALE` / `OFFLINE`) | Derived from `last_seen` only |
| Enrollment | Upserts host by agent-supplied UUID; reissues credential |

There is no persisted lifecycle state. Offline ≠ retired.

## Desired behavior

Two independent axes:

1. **Health** — connectivity freshness (`HEALTHY` / `STALE` / `OFFLINE`)
2. **Lifecycle** — operator intent (`ACTIVE` / `DECOMMISSIONED`)

Valid combination examples:

- `ACTIVE` + `OFFLINE` — machine asleep / network down
- `DECOMMISSIONED` + any health — tombstone retained; health may still age from last_seen but UI must not treat it as “just offline”

### Decommission (ACTIVE → DECOMMISSIONED)

- Host **row retained** (UUID, hostname, historical facts)
- `lifecycle_state = DECOMMISSIONED`
- `decommissioned_at` set; optional `decommission_reason`
- Active agent credential **revoked** (`revoked_at`); old Bearer → **401** on heartbeat/sync
- Host must **not** be treated as an operable active identity for new enrollments
- Central does **not** stop, uninstall, or wake the remote agent
- Historical observations / activity **retained**
- **Active** allocations (and their live reservations) are **released** via existing allocation release semantics so ports are not held forever for a retired identity; released history remains
- New allocations / agent reservations against a DECOMMISSIONED host are rejected

### Resurrection protection

Ordinary enrollment (`POST /api/agent/enroll`) for a UUID whose host row is `DECOMMISSIONED` must **REJECT** (HTTP **409**) with a safe message:

> Host identity is decommissioned.

No silent reactivation. No new credential. Enrollment token is **not** consumed on this failure (operator can reuse after Reactivate).

### Reactivate (DECOMMISSIONED → ACTIVE)

Admin-only. Clears decommission markers; sets `lifecycle_state = ACTIVE`.

Does **not** mint an agent credential. Preferred follow-up:

1. Admin Reactivate
2. Operator mints enrollment token (Add Host / CLI)
3. Agent enrolls with same UUID → new credential
4. Old revoked credential remains invalid

### Remove Record interaction

| Host state | Remove Record |
|------------|---------------|
| `ACTIVE` | Unchanged v1.3 hard purge; re-enroll allowed |
| `DECOMMISSIONED` | Allowed (admin); **removes tombstone**; UI must warn that resurrection protection is lost and the UUID may enroll again later |

Decommission must never be implemented as DELETE.

## Schema

Migration revising `038acf539985`.

Columns on `hosts`:

| Column | Type | Notes |
|--------|------|-------|
| `lifecycle_state` | `String(32)` NOT NULL | `ACTIVE` \| `DECOMMISSIONED`; `server_default='ACTIVE'` for existing rows |
| `decommissioned_at` | `DateTime(tz)` NULL | Set on decommission; cleared on reactivate |
| `decommission_reason` | `String(1024)` NULL | Optional; cleared on reactivate |

No Postgres ENUM (match `Allocation.status` / `Host.status` string style).

Index: `ix_hosts_lifecycle_state` for filtering.

### Migration safety

- Upgrade: add columns with default `ACTIVE`; backfill implicit via server default
- Downgrade: drop the three columns (tombstone semantics lost — acceptable for reverse migration)

## API

All mutating lifecycle routes use `require_admin` (bootstrap bearer). Agent / enrollment tokens must fail.

### `POST /api/hosts/{host_id}/decommission`

Request body (optional):

```json
{ "reason": "retired lab box" }
```

`reason` optional, max 1024 chars.

Responses:

- **200** + `HostOut` — transitioned (or already `DECOMMISSIONED`: idempotent success, no second activity stamp unless reason updated — prefer **no-op** if already decommissioned: return current `HostOut`, do not rewrite `decommissioned_at`)
- **404** — unknown host
- **401/503** — admin auth

### `POST /api/hosts/{host_id}/reactivate`

No body required.

Responses:

- **200** + `HostOut` — now `ACTIVE`
- **404** — unknown host
- **409** — host is already `ACTIVE` (clear operator signal)
- **401/503** — admin auth

### `DELETE /api/hosts/{host_id}`

Unchanged Remove Record.

### Enrollment

On decommissioned UUID: **409** `Host identity is decommissioned.` after valid token checks (token remains unconsumed). Invalid token still **401** as today.

### `HostOut` additions

```text
lifecycle_state: str          # ACTIVE | DECOMMISSIONED
decommissioned_at: datetime | null
decommission_reason: str | null
```

Health fields unchanged and independently derived.

## Credential behavior

- Decommission: revoke active credential (`revoked_at = now`); do not delete credential rows (auditability)
- Heartbeat / observations: existing `require_agent` → no active credential → **401**
- Defense in depth: `record_heartbeat` / enrollment upsert refuse DECOMMISSIONED hosts even if a credential somehow existed
- Reactivate: no automatic credential; enroll after reactivate issues a new one via `replace_credential_for_host`

## Dashboard

### Display

- Lifecycle badge separate from health badge
- Decommissioned hosts: show `DECOMMISSIONED`, timestamp, reason; never label solely as Offline

### Decommission Host

- Confirmation dialog + **typed hostname** confirmation
- Copy: credential invalidated; identity retained as tombstone; remote process **not** stopped; ordinary re-enrollment **rejected**
- Stronger warning if host is HEALTHY / recently seen
- BFF: `POST /api/hosts/[hostId]/decommission` with server-side bootstrap token (never browser)

### Reactivate Host

- Shown only when `DECOMMISSIONED`
- Confirmation: allows enrollment again; old credential stays invalid; new enrollment still required
- BFF: `POST /api/hosts/[hostId]/reactivate`

### Remove Record on DECOMMISSIONED

- Existing dialog gains explicit tombstone warning when `lifecycle_state === DECOMMISSIONED`

### Add Host / enrollment

- If Central rejects enroll with decommissioned identity, surface the 409 message; do not auto-reactivate

### Recheck Status

- Unchanged: Central GET refresh only; no lifecycle mutation

## Audit / activity

Reuse `ActivityEvent` strings (max 32 chars):

| Event | When |
|-------|------|
| `HOST_DECOMMISSIONED` | Successful ACTIVE → DECOMMISSIONED transition |
| `HOST_REACTIVATED` | Successful DECOMMISSIONED → ACTIVE transition |

Payload may include reason text truncated to fit existing payload conventions.

Idempotent decommission (already DECOMMISSIONED): **no** duplicate activity event.

## Compatibility

- Protocol **1**, contract **1** — no bump
- v1.3 agents against Phase 8 Central: heartbeats continue while `ACTIVE`; after decommission they receive **401** (same as revoked credential) — no agent upgrade required
- Product version remains **1.3.0** on this development branch (no release bump in this phase)

## Rollback / recovery

| Scenario | Path |
|----------|------|
| Accidental decommission | Admin Reactivate → re-enroll |
| Want UUID gone entirely | Remove Record (destroys tombstone) |
| Want protection restored after Remove Record | Re-enroll creates new ACTIVE identity (no automatic tombstone) |

## Out of scope

Remote uninstall/shutdown, auto-update, browser auth, RBAC redesign, protocol/contract changes, version bump / tag / deploy.
