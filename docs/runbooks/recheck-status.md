# Runbook: Recheck status

**Recheck status** (UI may still say **Try again** on frozen v1.2.0 dashboards)
refreshes host health from PortForge Central only.

## What it does

- Fetches the latest host record from Central
- Invalidates / refreshes dashboard host queries
- Reports whether Central currently considers the host healthy, stale, or offline

Tooltip guidance: *Refresh the latest host status from PortForge Central.*

## What it does NOT do

- Wake a sleeping machine
- Restart the remote agent
- Force a heartbeat from the agent
- Execute a remote command
- Repair network connectivity

## Interpreting results

| Result | Meaning |
|--------|---------|
| Healthy | Central has a fresh heartbeat |
| Still stale / offline | Central has not received a recent heartbeat |

If status remains offline, verify on the host that the agent service is running
and can reach Central. Laptop sleep is a common cause of temporary offline —
see `docs/runbooks/mac-sleep.md`.
