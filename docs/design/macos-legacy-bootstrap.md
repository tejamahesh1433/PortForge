# macOS Legacy Bootstrap Bridge (Phase 23C)

## Contradiction discovered by failed v1.5.3 RC

Phase 23’s detached helper assumes the **running** agent can write a typed
handoff and spawn `python -m portforge_agent.upgrade.helper`.

**Exact published** `portforge_agent` **1.5.2** (SHA-256
`19017e9b7f0207e4e0c4b7de3d1d02a47c3dd3080ad10b3418e7696cb1a46a48`)
**predates that helper**. The 1.5.2 process cannot execute helper code that
only exists in newer source before the new package is installed and the old
process has already committed to its restart/exit path.

Therefore helper-bearing-baseline tests (Phase 23 physical) do **not** prove
the real production transition:

```text
exact published 1.5.2  →  1.5.3
```

Failed RC evidence (preserved; do not rewrite to PASS):

| Platform | Exact 1.5.2 → RC | Result |
|----------|------------------|--------|
| Linux    | PASS             | First attempt SUCCEEDED |
| Windows  | PASS             | First attempt SUCCEEDED |
| macOS    | **FAIL**         | Install reached 1.5.3; LaunchAgent stopped; Central stuck RESTARTING |

HIGH historical incident: published 1.5.2 `agent service install` hardcodes
`com.portforge.agent` and briefly clobbered the production Mac LaunchAgent
during RC enroll. Restored afterward. Phase 23C must never address that label
for disposable tests.

## Observed macOS failure sequence (published 1.5.2)

1. Old 1.5.2 agent running under LaunchAgent  
   `KeepAlive={SuccessfulExit:false, NetworkState:true}`, `RunAtLoad=true`
2. Artifact downloaded and SHA-verified
3. In-process `pip install` upgrades the venv to 1.5.3
4. 1.5.2 reports `RESTARTING`, then lazily imports
   `portforge_agent.upgrade.platform_restart` (**from the newly installed
   package**) and calls `restart_service()` → `restart_via_launchctl`
5. **Legacy** `restart_via_launchctl` did `launchctl bootout` + `bootstrap`
   of the **same** job from **inside** that job
6. Bootout SIGTERMs/unloads the running agent mid-sequence; clean exit 0
   follows
7. With `SuccessfulExit=false`, launchd does **not** revive a clean stop
8. No Phase 23 helper was ever started (absent from 1.5.2)
9. New agent never heartbeats → Central remains `RESTARTING` / host `STALE`

## Why a helper-only architecture cannot close this gap alone

The N→N+1 bootstrap problem is: the **first** hop onto helper-capable code
must succeed when the **old** process still lacks the helper. Phase 23 correctly
owns upgrades **after** an agent already has the helper (1.5.3→later). It does
not, by itself, make published 1.5.2 spawn a helper it does not contain.

## Bridge (smallest capability reachable from exact 1.5.2)

Published 1.5.2 already:

1. Pip-installs the target into the **same** venv
2. Lazily imports `platform_restart` **after** that install (not imported
   earlier on the upgrade path)
3. Calls `restart_service()` then exits 0 when the runtime loop ends

Therefore the **new** package’s Darwin `restart_via_launchctl` can change
behavior for that lazy import **without** retrofitting 1.5.2:

| Step | Actor | Action |
|------|--------|--------|
| A | Old 1.5.2 | Installs 1.5.3 wheel (unchanged) |
| B | New module (1.5.3+) | `restart_via_launchctl` schedules an **out-of-band** kickstarter; **never** bootouts the agent job from inside itself |
| C | Kickstarter | Waits for the calling PID to exit, then `launchctl kickstart -k gui/<uid>/<label>` (bootstrap plist first if needed) |
| D | Old process | Returns from restart, exits 0 |
| E | launchd | Starts the job; ProgramArguments load **new** code from the updated venv |
| F | New agent | Normal heartbeat → Phase 20 reconciliation → Central `SUCCEEDED` |

Phase 23 detached helper remains the path for **helper-capable** sources
(≥ development 1.5.3 with handoff). The Darwin kickstart change is also safe
when the helper calls `restart_service()` after the old PID is already gone:
the kickstarter waits for the **helper** PID, then kickstarts the agent.

### Explicitly unchanged

- Production KeepAlive semantics: `{SuccessfulExit: false, NetworkState: true}`
- `RunAtLoad: true`
- Normal CLI stop (`launchctl kill SIGTERM`) still leaves the job stopped
- No generic shell, remote command, arbitrary executable/URL/service/label
- Label/path only from `PORTFORGE_MACOS_PLIST_*` or PortForge defaults
- Protocol / Contract / Machine / MCP / DB unchanged

### Legacy vs helper-capable

| Class | Example | Upgrade restart ownership |
|-------|---------|---------------------------|
| Legacy bootstrap source | exact published **1.5.2** | In-process install + lazy new `platform_restart` kickstart bridge |
| Helper-capable source | **≥ 1.5.3** with handoff/helper | Detached helper installs + `restart_service` after old PID exits |

## Isolation rule (mandatory)

Disposable Mac tests must **never** mutate `com.portforge.agent` when that
label is production. Prefer isolated label/plist/data/venv/UUID. Published
1.5.2 `service_gen` hardcodes the production label for `service install` —
do **not** call that on a machine hosting production; construct the disposable
LaunchAgent manually with `PORTFORGE_MACOS_PLIST_LABEL` / `_PATH` (honored by
`platform_restart` even on 1.5.2).
