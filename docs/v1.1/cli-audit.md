# v1.1-E: Authoritative CLI Audit

Captured directly from the real CLI (`portforge --help` and every subcommand's own `--help`), not from prior
documentation. This supersedes `docs/v1.1/install-upgrade-audit.md`'s planning-stage findings by confirming
them against the exact current command surface and adding the columns needed to scope installation work.
Commands are grouped by installation relevance; every row marked "core" or "helper" below was physically
invoked during this audit (`python -m portforge_agent.cli <command> --help`, and several end-to-end against a
real running Central -- see `docs/v1.1/v1.1-e-implementation.md`).

There is exactly **one** installed console script: `portforge` (`agent/pyproject.toml`'s `[project.scripts]`).
No `portforge-agent` binary exists, and none should be invented merely to make old docs true --
`docs/installation.md` was fixed to describe the real command shape instead.

## Installation-critical commands

| Command | Exists? | Purpose | Install relevance | Platform |
|---|---|---|---|---|
| `portforge agent enroll --server URL --token TOKEN` | Yes | Writes the agent credential the always-on daemon reads (`credentials.json`) | **Core** -- the enrollment step every install must use | All |
| `portforge central enroll --url URL --enrollment-token TOKEN` | Yes | Older, separate one-off enrollment path for `central sync`/`status` only | Secondary -- works, but does not configure the daemon. As of this increment it also writes `credentials.json` (see dual-credential-store fix), so it now produces a working install too, just via a less direct route | All |
| `portforge central generate-token --url URL [--admin-token TOK]` | **New this increment** (was previously documented but not implemented -- see below) | Mints a host enrollment token; admin-only, requires `PORTFORGE_ADMIN_BOOTSTRAP_TOKEN` | **Core** -- without this, `security.md`'s documented flow had no real entry point | All (talks to Central over HTTP) |
| `portforge agent service install [--json]` | Yes | Installs/reinstalls the native OS startup definition | **Core** -- makes the agent survive logout/reboot | Windows (Task Scheduler)/macOS (LaunchAgent)/Linux (systemd --user, now also enables linger) |
| `portforge agent service start` / `stop` / `status` / `uninstall` | Yes | Native service lifecycle control | **Core** for status/verification; start/stop/uninstall are operational | All |
| `portforge doctor [--url URL] [--json]` | Yes | Read-only, never-mutating check of CLI/Central/agent/manifest/Docker state | **Core** -- the canonical post-install acceptance gate | All |
| `portforge agent test` | Yes | Diagnostic checks on agent configuration (older, narrower than `doctor`) | Helper -- superseded by `doctor` for install verification, still real | All |
| `portforge agent status` | Yes | Local runtime state (separate from `agent service status`, which asks the OS; this asks the running daemon) | Helper | All |
| `portforge agent run` | Yes | Foreground agent runtime (what the native service actually execs) | Helper -- useful for manual/debug runs outside the service manager | All |
| `portforge central status [--json]` | Yes | Central sync configuration/connectivity (reachability only) | Helper | All |
| `portforge central sync [--json]` | Yes | One-off push of a discovery snapshot + reservations | Helper, not install-relevant | All |
| `pip install -e ./agent` | Yes (verified: installs `portforge-agent` package, `portforge` script lands on PATH) | Source install | **Core** | All |

## Commands documented as broken before this increment (now fixed in docs, not code)

| Old doc claim | Reality |
|---|---|
| `portforge-agent install` / `portforge-agent start` | No such binary. Real command: `portforge agent service install` / `start`. |
| `portforge enroll --central "<url>" --token "<token>"` | No such command/flag shape. Real command: `portforge agent enroll --server <url> --token <token>`. |
| Backend on "port 8000" | Container-internal port is 8000; the published host port (what you actually connect to) defaults to 58000 (`PORTFORGE_API_HOST_PORT`). |
| `portforge central generate-token` (docs/security.md, docs/quickstart.md) | Did not exist as a CLI command before this increment -- only an unexposed backend endpoint (`POST /agent/enrollment-tokens`, admin-only) did. Implemented as a thin CLI wrapper this increment (see `central_client.py::generate_enrollment_token`, `cli.py::_cmd_central_generate_token`); no new auth mechanism, still requires the real admin bootstrap token, never logs it. |

## Operational commands (not installation-relevant; listed for completeness, not re-audited in depth this increment)

`scan`, `docker`, `inspect`, `check`, `next`, `reserve`, `release`, `reservations`, `conflicts`,
`sync-reservations`, `allocate`, `allocation get/release`, `project validate/plan/allocate/init`, `config
plan/apply/status/rollback`, `workflow prepare/apply/status`, `agent-contract` -- all confirmed to exist via
`--help`, unchanged by this increment, and already covered by `docs/cli.md`.

## Supported installation models (task Sec4)

1. **Central host** -- runs Postgres + the FastAPI backend + the dashboard (via `docker-compose up -d`), plus
   optionally its own agent if you want PortForge to manage ports on that same machine. Needs Docker.
2. **Agent-only host** (developer laptop, a server being managed but not running Central) -- runs only `pip
   install -e ./agent` + `portforge agent service install` + `portforge agent enroll`. Does **not** need
   Postgres, the dashboard, or Docker Compose at all -- Docker is optional and only used for the agent's own
   container-port discovery feature.
3. **Development** -- Central run outside Docker (`uvicorn` directly against a local/dev Postgres) for backend
   development; the agent run in the foreground (`portforge agent run`) instead of as a native service, for
   faster iteration.

No installer in this increment forces Postgres or the dashboard onto an agent-only host -- `docs/installation.md`
section 2 (Host Agent Setup) and the new install scripts (`scripts/install.ps1`/`install.sh`) only ever touch
the agent package and native service, never Central's stack.
