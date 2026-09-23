# Runbook: Agent upgrade

Safe upgrade procedure for a managed host. Adapt version labels to the release
you are installing — do not assume a single version string.

PortForge does **not** ship a `portforge upgrade` self-updater. Use explicit
package and service commands.

## Before upgrade

1. Record host UUID / config / runtime:
   - local identity under the PortForge config directory
   - `portforge agent service status`
   - current `portforge doctor --url <central>` output
2. Verify the host is healthy in Central (or document known offline causes).
3. Keep the previous install artifact available for rollback (previous wheel,
   tag, or editable checkout).
4. When the release provides a hash / checksum, verify the new artifact before
   installing.

## Upgrade steps

```bash
# Obtain the new release (git checkout / download / wheel)
pip install -e ./agent --upgrade   # or: pip install ./path/to/agent-<version>.whl

# Reinstall the native service definition in place (idempotent; preserves UUID
# and enrollment credential)
portforge agent service install
portforge agent service stop
portforge agent service start

portforge doctor --url http://<CENTRAL_ADDRESS>:<PORT>
```

## After upgrade

1. Confirm protocol / contract compatibility via doctor (advisory — mismatches
   should be reviewed, not ignored).
2. Observe **multiple** successful heartbeats in Central.
3. Optionally force a physical sync and confirm Central still shows the **same UUID** with **no duplicate** host row for that machine:

```bash
portforge agent sync
```

## Preserve on upgrade

- Host UUID
- Enrollment / agent credential files
- Existing service registration identity

Do not re-enroll as part of a routine package upgrade.

## Related

- `docs/runbooks/agent-rollback.md`
- `docs/v1.1/upgrade.md`
- `docs/v1.1/version-compatibility.md`
