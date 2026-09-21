# v1.1 Planning: Installation / Upgrade Audit

**Headline finding: `docs/installation.md` as shipped in v1.0.0 does not
work if followed literally.** This was verified directly against the real
CLI (`portforge --help` and its subcommand `--help` output) and the real
`docker-compose.yml`, not inferred.

## Concrete defects found in `docs/installation.md`

1. **`docker-compose up -d` will fail outright as documented.**
   `docker-compose.yml`'s `portforge-api` service requires
   `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` (`${PORTFORGE_ADMIN_BOOTSTRAP_TOKEN:?set
   PORTFORGE_ADMIN_BOOTSTRAP_TOKEN before starting}` — Compose's own
   "fail if unset" syntax). The install doc never mentions setting it.
   Following the doc exactly produces an immediate Compose error, not a
   running stack.

2. **The doc's claimed backend port is wrong.** It says "the FastAPI
   backend on port 8000"; the real default (confirmed in
   `docker-compose.yml`) is `PORTFORGE_API_HOST_PORT:-58000`.

3. **`portforge-agent install` / `portforge-agent start` do not exist.**
   There is exactly one installed console script, `portforge`
   (`agent/pyproject.toml`'s `[project.scripts]`) — no `portforge-agent`
   binary at all. The real commands are `portforge agent service install`
   / `start` / `status` / `stop` / `uninstall` (confirmed via
   `portforge agent service --help`).

4. **`portforge enroll --central "<url>" --token "<token>"` does not
   exist as written.** There are two real, different enrollment paths and
   the doc's syntax matches neither:
   - `portforge agent enroll --server <url> --token <token>` (Phase 6's
     always-on agent daemon enrollment)
   - `portforge central enroll --url <url> --enrollment-token <token>`
     (Phase 5's older push-sync path, still present and still real)
   The doc's flag name (`--central`) and command shape match neither.

Taken together: a new user following `docs/installation.md` top to bottom
cannot actually stand up PortForge. This is the single highest-value,
lowest-risk v1.1 item in this entire audit — it's a **documentation fix**,
zero code risk, and it's currently blocking. Recommend it ship first,
ungated by any other v1.1 work (see roadmap, v1.1-A).

## What actually works (verified)

- `pip install -e ./agent` — real, works, confirmed (`portforge-agent`
  package installs correctly, `portforge` script lands on `PATH`).
- `docker-compose up -d` — works once `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN`
  (and, implicitly, `PORTFORGE_DB_PASSWORD` if the default isn't
  acceptable) are set.
- `portforge agent service install` then `portforge agent service start`
  — the real native-service path, per-OS: Windows Task Scheduler, macOS
  LaunchAgent, Linux systemd user service (`service_gen.py`/
  `service_ops.py`, Phase 6, already has real regression coverage
  including idempotent reinstall and elevation-denial detection on
  Windows).
- `portforge agent enroll --server <url> --token <token>` — real,
  confirmed by CLI help output; this is the command an install guide
  should actually document.

## Version compatibility today: none

No install/upgrade step anywhere checks that an agent's version is
compatible with Central's. `agent_version` is captured at enrollment and
heartbeat and simply stored/displayed — never compared against anything
(confirmed: no reference to `agent_version` outside storage/display code
in `backend/app/`). An operator upgrading Central without upgrading
agents (or vice versa) gets no warning today. See
`docs/v1.1/version-compatibility.md`.

## Self-check / doctor gap

There is no `portforge doctor` (or equivalent) today — the closest thing
is `portforge central status` (reachability only) and `portforge agent
service status` (is the native service installed/running). Neither checks
Central connectivity + agent identity + Docker availability + manifest
validity + filesystem permissions together in one command, which is
exactly the kind of thing a new user (or a coding agent's own setup step)
needs. See `docs/v1.1/doctor-design.md`.

## Upgrade path: doesn't exist yet

There is no `portforge upgrade` or documented upgrade procedure at all —
v1.0.0 is PortForge's first tagged release, so this has never been
exercised. Concretely unresolved questions for v1.1:
- Does upgrading Central require a manual `alembic upgrade head`, or
  should that be automatic on startup? (Today: manual — `main.py` doesn't
  auto-migrate.)
- Does the native agent service definition need to be reinstalled after a
  `pip install --upgrade`, or does it pick up the new code automatically?
  (Today: depends on whether the service was installed with `pip install
  -e` — an editable install picks up code changes without reinstalling
  the service; a non-editable install would need a service restart at
  minimum, a reinstall if the entry point changed.)
- No `portforge agent service update` command exists to reinstall a
  service definition in place without a full uninstall/install cycle.

## Recommended v1.1 scope (free options only, no paid publishing assumed)

- Fix `docs/installation.md` to match reality (v1.1-A, no code change
  needed, ships independently of everything else).
- A `portforge doctor` command (design in `doctor-design.md`, v1.1-A).
- A `portforge agent service update` (or extend `install` to be safely
  re-runnable — it already claims to be idempotent per Phase 6's own
  tests, worth confirming that covers the upgrade case specifically, not
  just repeated fresh installs).
- Do **not** assume PyPI/Homebrew/winget publishing for v1.1 — nothing in
  the current repository suggests packaging infrastructure for any of
  these exists yet (no `MANIFEST.in`/`twine` config, no Homebrew formula,
  no winget manifest), and setting that up is a distinct, separately
  scoped effort with its own maintenance burden (credential rotation for
  publishing, versioning discipline, etc.) — worth a deliberate future
  decision, not a default assumption for v1.1.
- A plain install script (PowerShell for Windows, POSIX shell for
  macOS/Linux) that does `git clone` + `pip install -e ./agent` + prompts
  for `portforge agent enroll` is a reasonable, low-cost v1.1-E candidate
  and does not require any publishing infrastructure.
