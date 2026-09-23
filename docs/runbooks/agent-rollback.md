# Runbook: Agent rollback

Roll back the agent package when a new version misbehaves. Do **not** treat
re-enrollment as a routine rollback step.

## Prerequisites

- Previous wheel / tag / checkout still available
- Host UUID and credentials still present on disk (not deleted)

## Steps

```bash
# Restore previous package into the same environment
pip install -e ./agent==<previous>   # or install the previous wheel/path

portforge agent service install
portforge agent service stop
portforge agent service start

portforge doctor --url http://<CENTRAL_ADDRESS>:<PORT>
```

## Verify

1. Heartbeats resume against Central.
2. Manual sync succeeds (if used in your operations).
3. Central still shows the same host identity (same UUID, no duplicate).

## Preserve unless specifically invalidated

- Host UUID
- Local config
- Agent credential

Only re-enroll when Central has revoked the credential (for example after
**Remove record**) or the credential files were intentionally destroyed.

## Central / database note

Rolling back **agent** code is independent of Central database migrations.
Do not run `alembic downgrade` against a live fleet database as a casual
companion to agent rollback. See `docs/v1.1/upgrade.md`.
