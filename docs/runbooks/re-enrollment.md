# Runbook: Re-enrollment after Remove Record

Use this when a host record was removed from Central but the machine (and usually
its local UUID) still exists.

## What happens after Remove Record

1. Central deletes the host record and dependent Central data.
2. The previous agent credential is no longer accepted.
3. Heartbeat / sync from the old credential receive authentication failure
   (typically HTTP 401).
4. The remote agent process is **not** stopped by Remove Record.

## Reconnect the same machine

1. Mint a new enrollment token (Dashboard **Add host**, or an admin mint against
   development/production Central as appropriate).
2. On the machine, enroll again:

```bash
portforge agent enroll \
  --server http://<CENTRAL_ADDRESS>:<PORT> \
  --token "<NEW_ENROLLMENT_TOKEN>"
```

3. Confirm service is running:

```bash
portforge agent service status
# if needed:
portforge agent service start
portforge doctor --url http://<CENTRAL_ADDRESS>:<PORT>
```

4. Observe heartbeats in Central / Dashboard until the host is HEALTHY.

## Identity rules

- Prefer keeping the existing host UUID. PortForge allows the **same UUID** to
  re-enroll and reclaim the identity.
- Do **not** delete or regenerate the UUID merely to reconnect.
- The new credential replaces the old one. The old credential remains invalid.

## Related

- `docs/runbooks/add-host.md`
- `docs/runbooks/remove-record.md`
