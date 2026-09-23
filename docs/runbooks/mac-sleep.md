# Operational note: Laptop sleep (macOS and others)

Laptop agents may appear **STALE** or **OFFLINE** in Central when the operating
system suspends networking during sleep.

## How to read the status

- Central freshness is based on recent successful communication.
- Offline / stale does **not** automatically mean the agent crashed.
- Do not infer power-off, crash, or network failure unless you have additional
  evidence on the machine.

## Expected recovery

Status should recover when:

1. The machine wakes
2. The network becomes reachable again
3. The agent resumes successful communication with Central

Use **Recheck status** / **Try again** only to refresh Central's view — it does
not wake the machine or restart the agent.

## Operator guidance

- Prefer confirming `portforge agent service status` on the host after wake.
- Do **not** recommend disabling normal power management globally just to keep
  PortForge green.
