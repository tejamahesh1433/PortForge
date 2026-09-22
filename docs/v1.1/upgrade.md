# Upgrading PortForge

This is the supported upgrade path for both the agent (on every managed host) and Central. It intentionally
does **not** include a `portforge upgrade` self-updater command -- a boring, explicit sequence of
already-existing, already-tested commands is preferred over a magical updater that could rewrite git history,
pull an arbitrary branch, silently change your package source, or destroy host identity. See
`docs/v1.1/install-upgrade-audit.md` for why this was the deliberate choice for this increment.

## Agent upgrade (every managed host)

```bash
git pull                       # or: download/checkout the new release
pip install -e ./agent --upgrade
portforge agent service install   # reinstalls the native service definition in place
portforge agent service start
portforge doctor                  # verify the upgrade landed correctly
```

`agent service install` is idempotent by design (Phase 6, re-verified this increment): re-running it never
creates a duplicate Task Scheduler task / LaunchAgent / systemd unit, and it never touches your host identity
(`~/.portforge/host_id` or platform equivalent -- see `identity.py`) or your existing enrollment credential
(`credentials.json`/`central.json`). Both were confirmed unchanged across a real v1.0.0-schema-to-v1.1 upgrade
performed as part of this increment's mandatory upgrade test (see
`docs/v1.1/v1.1-e-implementation.md`): same host UUID, same reservation, same enrollment credential, before
and after.

An **editable install** (`pip install -e`) picks up new agent code immediately without a service restart for
most changes, but the native service still runs the process that was live at daemon start -- restart it
(`portforge agent service stop` then `start`, or just re-run `install`) after every upgrade to be certain the
new code is actually running. A **non-editable install** (`pip install ./agent`, no `-e`) always needs at
least a service restart, and a reinstall if the entry point itself changed.

## Central upgrade

```bash
git pull
docker-compose build portforge-api portforge-dashboard
docker-compose up -d
cd backend && alembic upgrade head   # or run migrations from inside the container -- see below
```

Central does **not** auto-migrate on startup (`main.py` does not call `alembic upgrade head` itself) --
migrations are a deliberate, separate step. Confirmed this increment: `alembic upgrade head` run against a
disposable Postgres seeded with real v1.0.0-schema data (host, reservation) via v1.0.0's own backend code
applied the two v1.1 migrations (`add_host_probes`, `add_host_protocol_version`) cleanly, and the current
backend then read back the exact same host UUID, `first_seen` timestamp, and reservation -- the new
`protocol_version` column came back correctly `null`/"unknown" for the pre-upgrade host rather than a
fabricated value.

## Version compatibility during upgrade

An older agent talking to a newer Central, or a newer agent talking to an older Central, is expected to keep
working for existing functionality -- `agent_version`/`protocol_version` are informational, never enforced as
a hard block (see `docs/v1.1/version-compatibility.md`). `portforge doctor`'s `protocol_compatibility` check is
advisory: it reports `compatible`/`unknown`/a mismatch note, but never fails the doctor run outright over a
version difference. Upgrade your fleet at your own pace; there is no forced simultaneous upgrade requirement.

## Rollback

**Application rollback and database rollback are not the same operation, and only one of them is generally
safe.**

- **Rolling back the application code** (`git checkout <previous-tag>` + `pip install -e ./agent`, or the
  equivalent for Central's Docker images) is safe and reversible on its own, as long as you do **not** also
  roll back the database schema underneath a newer schema-dependent binary.
- **Rolling back the database schema** (`alembic downgrade <revision>`) is much riskier and, for this
  increment's migrations, is **not a recommended path**: `alembic downgrade` would drop the `host_probes`
  table and the `hosts.protocol_version` column outright, permanently discarding any probe history and
  protocol-version data recorded since the upgrade. There is no migration-level mechanism that reconstructs
  that data afterward.
- **Always back up the Postgres volume before upgrading** (`docker run --rm -v
  portforge_portforge-postgres-data:/data -v $(pwd):/backup alpine tar czf /backup/portforge-db-backup.tar.gz
  /data`, or your own `pg_dump`) if you want a real rollback option. Restoring that backup, then running the
  matching older application code, is the only rollback path this project actually recommends -- not
  `alembic downgrade` against live data.
- Agent-side state (host identity, enrollment credential, local reservations) is plain JSON on disk and is
  never touched by a Central rollback either direction; it is safe to leave alone.

## What this upgrade path deliberately does not attempt

Per this increment's explicit scope: no self-updating `portforge upgrade` command, no automatic Central
schema migration on startup, no forced fleet-wide simultaneous upgrade, and no public package repository
publishing (PyPI/Homebrew/winget) -- see `docs/v1.1/v1.1-e-implementation.md` for the full list of exclusions.
