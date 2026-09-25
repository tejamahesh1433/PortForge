# Phase 23C — Legacy macOS Bootstrap Bridge (Qualification)

**Verdict: PASS (development freeze only — not an RC, not a release)**

Failed v1.5.3 RC qualification remains **FAIL** for macOS exact published
1.5.2 → RC. This document does **not** rewrite that RC to PASS.

## Failed RC evidence (preserved)

| Platform | Exact published 1.5.2 → RC | Result |
|----------|----------------------------|--------|
| Linux    | PASS                       | First attempt Central `SUCCEEDED` |
| Windows  | PASS                       | First attempt Central `SUCCEEDED` |
| macOS    | **FAIL**                   | Pip reached 1.5.3; LaunchAgent stopped; Central stuck `RESTARTING` |

HIGH historical incident (same RC run): published 1.5.2 `agent service install`
hardcodes LaunchAgent label `com.portforge.agent` and briefly clobbered the
production Mac LaunchAgent. Production was restored afterward. Production was
**not** untouched during that RC run. Phase 23C disposable tests must never
address that production label.

## Root cause

See `docs/design/macos-legacy-bootstrap.md`.

Published 1.5.2 **does not** contain the Phase 23 detached helper. After pip
installs the target, 1.5.2 lazily imports `platform_restart` from the **new**
package and previously performed in-process `launchctl bootout` of its own job.
With plist `KeepAlive={SuccessfulExit:false, NetworkState:true}`, a clean exit
does not restart the job → no heartbeat → Central `RESTARTING`.

Helper-bearing baseline tests do **not** prove the legacy 1.5.2 → 1.5.3 hop.

## Bridge architecture

Smallest capability reachable from exact published 1.5.2 (no retrofit of 1.5.2):

1. 1.5.2 installs target wheel into the same venv (unchanged).
2. Lazy import loads **new** `restart_via_launchctl`.
3. New Darwin path schedules an **out-of-band** kickstarter (`Popen` +
   `start_new_session`) that waits for the calling PID to exit, then
   `launchctl kickstart -k gui/<uid>/<label>` (bootstrap known plist if needed).
4. **Never** self-bootout the agent job from inside the upgrade process.
5. New agent heartbeats → Phase 20 reconciliation → Central `SUCCEEDED`.

### Compatibility boundary

| Class | Versions | Restart ownership |
|-------|----------|-------------------|
| Legacy bootstrap source | exact published **1.5.2** | In-process install + lazy new kickstart bridge |
| Helper-capable source | **≥ development 1.5.3** with handoff/helper | Phase 23 detached helper (unchanged) |

### Security boundary (ABSENT)

Generic shell, remote command, arbitrary executable/args/service/URL/install
path/LaunchAgent label, generic package installer endpoint.

Allowed only: known PortForge label pattern `com.portforge.agent` or
`com.portforge.agent.<suffix>`, plist under `~/Library/LaunchAgents/`, local
env overrides `PORTFORGE_MACOS_PLIST_LABEL` / `_PATH`, validated upgrade
artifact.

### KeepAlive / plist semantics

**Unchanged.** Production KeepAlive remains
`{SuccessfulExit: false, NetworkState: true}`, `RunAtLoad: true`.
No casual LaunchAgent semantic change.

## Exact baseline + development target

| Artifact | Value |
|----------|-------|
| Baseline wheel | `portforge_agent-1.5.2-py3-none-any.whl` |
| Baseline SHA-256 | `19017e9b7f0207e4e0c4b7de3d1d02a47c3dd3080ad10b3418e7696cb1a46a48` |
| Baseline helper | **ABSENT** |
| Target wheel | `portforge_agent-1.5.3-py3-none-any.whl` (development, unpublished) |
| Target size | 245503 bytes |
| Target SHA-256 | `b5e50dbaf1bb1c58e931c7f9a1b5da9261a3c42fccc1c10b750cf458caade7cf` |

## Mac isolation proof

| Identity | Production | Disposable Phase23C |
|----------|------------|---------------------|
| Label | `com.portforge.agent` | `com.portforge.agent.phase23c` |
| Plist | `~/Library/LaunchAgents/com.portforge.agent.plist` | `~/Library/LaunchAgents/com.portforge.agent.phase23c.plist` |
| Data | `~/Library/Application Support/PortForge` | `~/PortForge-Phase23C/data` |
| Venv | `…/PortForge_v1.1.1_PD1/.venv` | `~/PortForge-Phase23C/venv` |
| UUID | `2a8d8b4c-ece0-4f62-8587-e7196b2d7688` | `7ebb8c37-7254-4e66-a4b9-7df39d564600` |
| Central | production `:58000` | disposable `:58005` (SSH reverse tunnel) |

Published 1.5.2 `service install` was **not** used (would hardcode production
label). Disposable LaunchAgent was constructed manually with env overrides.

## Physical evidence — first attempt

Evidence: `.qual-artifacts/phase23c/mac_phys.log` (gitignored).

| Check | Result |
|-------|--------|
| First attempt | **PASS** |
| OOB / retry / manual launchctl / manual service reinstall / manual pip | **NO** |
| New agent automatic | **PASS** |
| Central final | **SUCCEEDED** |
| UUID preserved | **YES** (`7ebb8c37-…`) |
| Credential preserved | **YES** |
| Runtime after | `1.5.3` HEALTHY |
| Production label/plist/venv/UUID | **UNTOUCHED** |

Log excerpt: `FINAL_STATE=SUCCEEDED` … `MACOS_FIRST_ATTEMPT=PASS` …
`OOB=NO RETRY=NO MANUAL_LAUNCHCTL=NO MANUAL_PIP=NO`.

## Service lifecycle evidence

Evidence: `.qual-artifacts/phase23c/mac_lifecycle.sh` run → `LIFECYCLE_ALL=PASS`.

| Check | Result |
|-------|--------|
| Normal stop left stopped | PASS |
| Normal start once | PASS |
| Crash (`kill -9`) restarts | PASS |
| Restart loop | ABSENT |
| Duplicate Phase23C process | ABSENT |
| Production untouched | PASS |

## Recovery / idempotency

Evidence: `.qual-artifacts/phase23c/mac_recovery.log` → `RECOVERY_ALL=PASS`.

| Scenario | Result |
|----------|--------|
| Wrong SHA / corrupt artifact | **PASS** — Central `FAILED` (`SHA-256 mismatch`); venv stayed `1.5.3`; service running; single process/plist |
| Install failure (unit) | **PASS** — `test_run_upgrade_returns_false_on_handoff_prepare_failure` / spawn failure report `FAILED`, no false `SUCCEEDED` |
| Post-install failure (bounded) | Kickstarter waits ≤120s for PID exit then kickstarts known label only; Central success still requires heartbeat reconciliation |
| Idempotency (double schedule) | **PASS** — still 1 plist, 1 process |
| Helper-capable modules present on target | **PASS** (`helper` + `write_handoff` / `spawn_upgrade_helper`) |

## Helper-capable path

Bridge is transitional compatibility for legacy 1.5.2 only. Phase 23 helper
modules remain in the development 1.5.3 package. Darwin `restart_via_launchctl`
change is also safe when a helper calls `restart_service()` after the old agent
PID is gone (kickstarter waits for the helper PID). Automated agent upgrade
suite: 49 passed (focused) / full agent 1045 passed, 5 skipped.

## Windows / Linux

Implementation change is **Darwin-only** (`restart_via_launchctl`). Windows
scheduler and Linux systemd adapters unchanged. Prior RC physical PASS on
Linux/Windows for exact 1.5.2 → RC remains applicable for those platforms;
this phase does not re-run them.

## Regression

| Suite | Result |
|-------|--------|
| Backend | 442 passed |
| Agent | 1045 passed / 5 skipped |
| Dashboard | 205 passed |
| Service | 79 passed |
| Focused upgrade tests | 49 passed |
| New Ruff (changed production file) | 0 (`platform_restart.py` clean; pre-existing unused imports in `test_upgrade.py` not cleaned) |
| New mypy (`platform_restart.py`) | 0 |
| Doctor (production Central) | Overall ok; protocol 1 |
| Build | PASS (development wheel produced; unpublished) |

## Compatibility

| Surface | Value |
|---------|-------|
| Protocol | 1 |
| Contract | 1 |
| Machine schema | 1 |
| MCP | 1 |
| DB | `d5e6f7a8b9c0` |
| Migration | **NO** |

## Production read-only verification (after Phase 23C)

| Check | Result |
|-------|--------|
| Central | **1.5.2** |
| Dashboard | **1.5.2** |
| Agents | 4 × **1.5.2** HEALTHY |
| Identities | 4 |
| Duplicates | 0 |
| Production Mac plist / venv / UUID / version | **CORRECT** (`com.portforge.agent`, PD1 venv, `2a8d8b4c-…`, 1.5.2) |
| Production modified during Phase 23C | **NO** |

## Freeze

Branch: `feature/macos-legacy-bootstrap`  
Base HEAD: `8aef672194037bd30537f205c061e863fb0d0623`  
Base tree: `a5963598029710a22e45b813fc0a74ed80dbca94`

Committed only if exact published 1.5.2 → development 1.5.3 macOS first
attempt PASS without OOB/manual recovery (satisfied).

**Do not publish. Do not deploy production. Do not resume RC automatically.**
