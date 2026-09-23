# Runbook: Remove Record

**Remove record** deletes a host from PortForge Central. It is not a remote
decommission or uninstall tool.

## What it does

- Removes the Central host record
- Removes Central-side dependent records (ports / reservations / allocations /
  related history as implemented by Central)
- Removes / revokes that host's agent credential on Central

## What it does NOT do

- Stop the remote PortForge agent
- Uninstall PortForge from the machine
- Power off the machine
- Permanently tombstone the identity (see limitation below)

## Operator checklist (before confirming)

1. Inspect host freshness (HEALTHY / STALE / OFFLINE).
2. Note active allocations and reservations for that host.
3. Prefer releasing allocations/reservations first when practical.
4. Understand that a still-running agent will continue running, but its existing
   credential will stop working against Central.

## Dashboard confirmation

On the host detail page:

1. Choose **Remove record**.
2. Read the remote-agent warning.
3. Type the **exact hostname** (case-sensitive; leading/trailing spaces are trimmed).
4. Confirm only when the destructive action enables.

Cancel makes no API request.

Architecture:

```text
Browser
  → Dashboard DELETE /api/hosts/{host_id}
      → (server attaches admin/bootstrap credential)
          → Central DELETE /api/hosts/{host_id}
```

## After success

Expected feedback: **Host record removed.**

Central no longer tracks the host. If the agent is still running, it will begin
receiving authentication failures until re-enrolled.

## Permanent decommissioning limitation (v1.3)

Remove Record is **not** a permanent tombstone. A machine that retains its
existing host UUID may be **explicitly re-enrolled** and reclaim the same
identity with a new credential.

A future **Decommission** lifecycle state may add permanent blocking. Do not
advertise that behavior in v1.3.

## Related

- `docs/runbooks/re-enrollment.md`
- `docs/runbooks/recheck-status.md`
