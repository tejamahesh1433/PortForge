# v1.1 Design: `portforge doctor`

**Design only — not implemented in this task.**

## Motivation

No single command today answers "is my PortForge setup actually working?"
Today's closest pieces are `portforge central status` (Central
reachability only) and `portforge agent service status` (native service
installed/running only) — an operator (or a coding agent's own setup
step, per `docs/agent/PORTFORGE_AGENT.md`) has to run several commands and
mentally combine the results. `docs/v1.1/install-upgrade-audit.md`
documents concretely why this matters: v1.0.0's own install instructions
don't work as written, and a `doctor` command is the natural thing a
confused new user (or an agent-contract-reading coding agent) reaches for
first.

## Proposed checks

| Check | How | Failure meaning |
|---|---|---|
| CLI version | `importlib.metadata.version("portforge-agent")` (same call `agent_contract.py` already makes) | Never fails — informational only |
| Central connectivity | `GET /api/health` via `central_client.py` (same call `central status` already makes) | Central unreachable/down |
| Central compatibility | Compare Central's reported `version` (already in the health response, `settings.version`) against this agent's own version, once `docs/v1.1/version-compatibility.md`'s rules exist | Version mismatch outside compatible range |
| Host enrollment / identity | `pf.get_host_id()` resolves + `~/.portforge/central.json` (or equivalent) has a token | Not enrolled, or credential missing |
| Agent service status | Reuse `agent service status`'s existing per-OS check | Native service not installed, or installed but not running |
| Docker availability | Reuse `discovery.py`'s existing Docker-detection path (already handles "Docker not installed" gracefully, not a new capability) | Docker unavailable (not fatal — PortForge already runs fine without it, so this is a warning, not an error) |
| Collector health | Whether the platform-specific collector (`collectors/collector_{windows,macos,linux}.py`) can actually enumerate ports right now (a real, cheap discovery call) | A collector-level permission or environment problem |
| Manifest validity | If `portforge.yml`/`.yaml` exists in cwd, run it through `manifest.py::load_and_validate_manifest` (already exists, zero new logic) | Manifest present but invalid |
| Filesystem permissions | Can write to `paths.data_dir()` and (if a manifest was found) the project root's `.portforge/` directory | Permission denied — would silently break reservations/config mutation later |
| Config-mutation prerequisites | If a manifest with `config:` exists, confirm every declared `file:` resolves inside the project root (reuses `config_files.resolve_within_root` — a **read-only** dry run, never an actual plan/apply) | A declared config file path already looks unsafe before any real command tries it |

Every check reuses an existing function — `doctor` is explicitly a
**read-only aggregator**, not new probing logic. This mirrors how
`workflow.py` was built in Phase 8D (pure orchestration over
already-proven pieces) and should be held to the same "don't duplicate
logic" standard.

## Output shape

```
$ portforge doctor --json
{
  "contract_version": 1,
  "overall": "ok",            // "ok" | "degraded" | "error"
  "checks": [
    {"name": "central_connectivity", "status": "ok", "detail": "..."},
    {"name": "agent_service", "status": "ok", "detail": "..."},
    {"name": "docker", "status": "warn", "detail": "Docker not installed -- native-process discovery only"},
    {"name": "manifest", "status": "skip", "detail": "no portforge.yml in current directory"}
  ]
}
```

- `status` per check: `ok` / `warn` (non-fatal, e.g. Docker missing) /
  `error` (fatal to that capability) / `skip` (not applicable in this
  context, e.g. no manifest present).
- `overall`: `error` if any check is `error`, else `degraded` if any is
  `warn`, else `ok`.
- Human mode (`portforge doctor`, no `--json`) prints one line per check
  with a plain-ASCII `OK`/`WARN`/`FAIL`/`SKIP` marker, matching the exact
  convention `cli.py::_cmd_next`'s validation-step rendering already uses
  ("Plain ASCII, not a Unicode check/cross mark: some Windows console
  code pages can't render those").

## Exit codes

Follows the existing convention exactly, reinterpreted for a
diagnostic command:
- `0` — `overall: "ok"`.
- `1` — `overall: "degraded"` (a normal, expected negative signal — e.g.
  Docker missing — not a crash).
- `2` — `overall: "error"`, or `doctor` itself couldn't run a check due to
  an operational problem (e.g. can't read `paths.data_dir()` at all).

## Non-goals

- `doctor` never mutates anything — no config apply, no manifest write,
  no service install. Every check is read-only.
- `doctor` does not replace `central status` or `agent service status`
  (both remain useful on their own for a narrower question) — it's an
  aggregator, not a deprecation.
