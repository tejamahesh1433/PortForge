# Safe Agent Upgrade Management (Phase 10)

**PortForge upgrade management is NOT arbitrary remote shell execution.**

Only a narrowly defined, structured upgrade operation is permitted. Central never
accepts shell/PowerShell/bash commands, arbitrary executables, or script bodies.

## Control model

1. Admin creates an **approved** structured upgrade request (admin bootstrap auth).
2. Agent receives the request on its normal authenticated **heartbeat** (outbound).
3. Agent validates identity, platform, version, artifact metadata.
4. Agent downloads **only** the configured trusted PortForge wheel URL.
5. Agent verifies **exact SHA-256**; mismatch → fail closed.
6. Agent installs into existing runtime (non-editable), preserves UUID/config/credential/service.
7. Agent restarts via known platform adapter (Scheduled Task / systemd user / launchd).
8. Agent reconnects, reports version + result.
9. Central marks **SUCCEEDED** only after reconnect with expected version + same UUID.
10. On failure, agent/Central may follow controlled **rollback** using stored previous artifact metadata.

No Central→agent inbound connection is required.

## Request fields (only)

- `target_version`
- `artifact_url`
- `artifact_sha256`
- `artifact_filename` (optional)
- `request_id` (idempotency)

Never: command, script, executable path override, arbitrary URL outside policy.

Trusted URL policy: must match configured artifact URL **or** same host as
`PORTFORGE_UPDATE_ARTIFACT_URL` / official GitHub release host for this product
when equal to the configured target metadata. Default: request must use the
exact configured URL + SHA for the target version.

## States

```
APPROVED
WAITING_FOR_AGENT   # approved while host offline / not yet claimed
DOWNLOADING
VERIFYING
INSTALLING
RESTARTING
VERIFYING_HEALTH
SUCCEEDED
FAILED
ROLLED_BACK
```

Persistence: `host_upgrades` table (survives Central/agent restarts).

## Authorization

| Actor | Create/approve | Claim/status own upgrade | Cross-host |
|-------|----------------|--------------------------|------------|
| Admin bootstrap | Yes | Yes (read) | Yes |
| Agent credential | No | Own host only | No |
| Enrollment token | No | No | No |
| Browser | Via BFF only | N/A | N/A |

## Rules

- **DECOMMISSIONED** hosts: reject create/approve.
- **ACTIVE** only.
- One active upgrade per host (not terminal). Duplicate create with same `request_id` → idempotent return.
- Target < current: reject (use distinct rollback path for downgrade).
- Offline approval → `WAITING_FOR_AGENT` until heartbeat claims it.
- Decommission during active upgrade: cancel to `FAILED` with reason `host_decommissioned`; clear pending delivery.
- Reactivate: does not revive cancelled upgrades.
- Remove Record: purge upgrade rows with host (FK cascade / explicit delete in Remove Record map).

## Agent adapters

| Platform | Restart | Preserve |
|----------|---------|----------|
| Windows | Scheduled Task `PortForge Agent` | UUID, config, credential, task |
| Linux | `systemctl --user restart portforge-agent.service` | UUID, config, credential, unit |
| macOS | launchctl bootout/bootstrap existing plist | UUID, config, credential, plist |

No re-enroll. No new UUID.

## Rollback

Store on upgrade row: `previous_version`, `previous_artifact_url`, `previous_artifact_sha256`.

Admin `POST /api/upgrades/{id}/rollback` creates a new controlled upgrade request
targeting previous metadata (explicit downgrade path).

## APIs

Admin:

- `POST /api/hosts/{host_id}/upgrades`
- `GET /api/hosts/{host_id}/upgrades`
- `GET /api/upgrades/{upgrade_id}`
- `POST /api/upgrades/{upgrade_id}/rollback`

Agent:

- Heartbeat response may include `pending_upgrade` (additive)
- `POST /api/agent/upgrades/{upgrade_id}/status` — progress/result for own host only

## Dashboard

Fleet/host: Upgrade Agent confirmation (host, current, target, SHA, restart expectation — not machine reboot).

BFF with server-side admin token. No command fields.

## Out of scope

Upgrade-all, remote shell, browser auth, production rollout of this feature.
