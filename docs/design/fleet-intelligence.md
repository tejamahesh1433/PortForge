# Fleet Intelligence (Phase 9)

Development base: Phase 8 HEAD (`feature/fleet-upgrade-management`). Protocol **1**, contract **1**.

## Field classification

| Field | Class | Source |
|-------|-------|--------|
| Host ID / machine UUID | ALREADY AVAILABLE | `hosts.id` (agent UUID is PK) |
| Hostname | ALREADY AVAILABLE | `hosts.hostname` |
| Lifecycle | ALREADY AVAILABLE | `lifecycle_state` |
| Health / freshness | ALREADY AVAILABLE / DERIVABLE | `last_seen` → `health_state`, `age_seconds` |
| OS / OS version / arch | ALREADY AVAILABLE | enroll/heartbeat |
| Agent version | ALREADY AVAILABLE | enroll/heartbeat |
| Protocol version | ALREADY AVAILABLE | enroll/heartbeat |
| Contract version | AGENT REPORTING + DATABASE | optional heartbeat/enroll field; column `contract_version` |
| Last heartbeat | ALREADY AVAILABLE | `last_seen` |
| Last successful sync | DERIVABLE | `last_scan_observed_at` exposed on fleet view |
| Update availability | DERIVABLE | compare `agent_version` vs configured target |
| Update execution state | DATABASE | `host_upgrades` (Phase 10) |
| Diagnostic state | DERIVABLE | health + probe_capability on detail; optional `last_error` |
| Python/runtime version | AGENT REPORTING + DATABASE | optional `python_version` |
| Service/runtime info | NOT WORTH COLLECTING (this phase) | remains local doctor |
| Last error | DATABASE (nullable) | agent may report structured upgrade/runtime error string |

Separate hardware/SMBIOS UUID: **NOT WORTH COLLECTING** — PortForge identity is already the host UUID.

## Schema (migration after `a1b2c3d4e5f6`)

Additive nullable columns on `hosts`:

- `contract_version` (Integer, nullable)
- `python_version` (String(64), nullable)
- `last_error` (String(1024), nullable)

Existing Phase 8 columns untouched.

## Wire compatibility

Optional additive fields on enroll/heartbeat request:

- `contract_version`
- `python_version`

Optional additive heartbeat response:

- `pending_upgrade` (Phase 10; null/absent for legacy agents)

**No protocol bump.** Legacy agents omit new fields; Central leaves columns null → fleet shows `UNKNOWN` where needed.

## Fleet API

Prefer dedicated read surface for operator UX without overloading list_hosts filters:

- `GET /api/fleet` — `Page[FleetHostOut]`
- `GET /api/fleet/{host_id}` — single `FleetHostOut`

`FleetHostOut` extends host facts with:

- `last_heartbeat` (= `last_seen`)
- `last_sync` (= `last_scan_observed_at`)
- `update_availability`: `CURRENT` | `UPDATE_AVAILABLE` | `UNKNOWN` | `UNSUPPORTED`
- `target_version` (from settings, if configured)
- `active_upgrade` summary (id/state/target) if any
- `contract_version`, `python_version`, `last_error`
- existing health + lifecycle fields

Query params: `lifecycle_state`, `health_state`, `update_availability`, `q` (hostname search).

## Update availability

Configured trusted target (settings / env):

- `PORTFORGE_UPDATE_TARGET_VERSION`
- `PORTFORGE_UPDATE_ARTIFACT_URL`
- `PORTFORGE_UPDATE_ARTIFACT_SHA256`
- `PORTFORGE_UPDATE_ARTIFACT_FILENAME` (optional)

Comparison: packaging-style semantic versions (`packaging.version.Version`), never lexicographic strings.

| Condition | Result |
|-----------|--------|
| No target configured or agent_version missing | `UNKNOWN` |
| agent == target | `CURRENT` |
| agent < target | `UPDATE_AVAILABLE` |
| agent > target | `UNSUPPORTED` (accidental downgrade not offered) |
| Unparseable version | `UNKNOWN` |

Availability ≠ approval. Execution is Phase 10.

## Dashboard

New `/fleet` page: table with hostname, lifecycle, health, OS/arch, agent/target version, update status, last heartbeat, last sync, diagnostics indicator.

Filters: offline, decommissioned, outdated, diagnostics problems.

Host detail: version status + update availability; preserve Remove Record / Decommission / Reactivate / Recheck.

## Out of scope

Bulk auto-upgrade, generic remote shell, protocol/contract bump, production deploy.
