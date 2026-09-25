# Self-Upgrade Bootstrap Hardening (Phase 23)

Extends Phase 10 (safe upgrade management) and Phase 20 (Central heartbeat
reconciliation) with a **detached, typed upgrade helper** so an agent can
upgrade itself from the production v1.5.2 baseline without OOB installation,
SSH, local console recovery, or first-attempt false FAILED rows.

---

## Production evidence that motivated this design

Real v1.5.2 production rollout (Central/Dashboard/agents already on 1.5.2 after
recovery) exposed three **bootstrap** defects when a **running** agent of the
prior generation performed install+restart in-process:

| Platform | Symptom | Stage | Root mechanism |
|----------|---------|-------|----------------|
| Linux | First attempt `FAILED` with `systemctl … restart … (rc=-15)` while runtime often already healthy on target | Restart handoff from **1.5.1** agent (reports FAILED); even 1.5.2 `--no-block` can race SIGTERM | Old process owns sync service-manager call that terminates itself |
| Windows | `pip install` `WinError 32` locking `…\Scripts\portforge.exe` | `INSTALLING` | Running process holds files pip must replace |
| macOS | Wheel installed; Central stuck `RESTARTING`; host OFFLINE until operator `service install`/`kickstart` | Post-restart | Clean exit 0 under `KeepAlive={SuccessfulExit:false, NetworkState:true}`; launchd may leave job unloaded/stopped |

These are **upgrade-from-running-agent** gaps. They are not Phase 20
reconciliation bugs (reconciliation worked once a 1.5.2 process heartbeated).

**Authoritative published v1.5.2 LaunchAgent semantics** (exact tag `v1.5.2` /
commit `9c9ef97` / tree `b671a3b5`):

```text
KeepAlive = { SuccessfulExit = false, NetworkState = true }
RunAtLoad = true
Label = com.portforge.agent
```

Do not assume `KeepAlive = true` for the published baseline (later RC-branch
commits exist; they are not the production wheel).

---

## Lifecycle ownership (before Phase 23)

```
Central approval
  → old agent claim (status API)
  → download / verify SHA          [OLD AGENT]
  → pip install                    [OLD AGENT — unsafe while running]
  → report RESTARTING
  → systemctl / schtasks / kickstart [OLD AGENT — self-terminating]
  → old process exit
  → service manager relaunch       [may fail / no-op on Mac]
  → new process heartbeat          [NEW AGENT]
  → Central reconcile → SUCCEEDED  [CENTRAL Phase 20]
```

---

## Fundamental design decision

**An agent process must not:**

1. Replace its own currently-locked package/executable, or  
2. Synchronously drive the service-manager operation that kills itself as the
   sole install/restart owner.

**Selected model: narrowly-scoped detached upgrade helper.**

```
RUNNING AGENT
-------------
- claims typed upgrade
- downloads artifact (https, size-bounded)
- verifies SHA-256
- copies wheel into PortForge-controlled upgrade area
- writes durable typed handoff record
- reports RESTARTING
- starts detached helper (allowlisted entrypoint only)
- exits cleanly (runtime loop stop / process exit 0)

UPGRADE HELPER
--------------
- reads ONLY the local handoff record (no arbitrary argv payload)
- revalidates SHA / upgrade_id / host_id / versions
- waits for old PID to exit (bounded)
- installs exact verified local wheel via known sys.executable / recorded python
- restarts/bootstraps the known PortForge service identity
- records stage completion; exits

NEW AGENT
---------
- starts via service manager
- same host UUID + credential
- heartbeat with target version
- Central Phase 20 reconcile → VERIFYING_HEALTH → SUCCEEDED
```

Central remains the **only** authority for SUCCEEDED. The helper does not
post SUCCEEDED and does not invent a second success path.

---

## Helper security model

The helper **MUST NOT** accept:

| Forbidden | Rationale |
|-----------|-----------|
| arbitrary executable | no RCE surface |
| arbitrary shell / args | injection |
| arbitrary service name | only PortForge constants / existing local env overrides |
| arbitrary URL | download already completed by agent under https rules |
| arbitrary filesystem destination | confined under `data_dir()/upgrade/` |

Allowed handoff fields (schema versioned, additionalProperties false):

| Field | Purpose |
|-------|---------|
| `schema_version` | handoff format (1) |
| `upgrade_id` | Central upgrade UUID |
| `host_id` | expected host UUID (must match `host.json`) |
| `expected_current_version` | version at handoff prepare time |
| `target_version` | must match Central target |
| `artifact_filename` | basename only under upgrade artifacts dir |
| `artifact_sha256` | 64 hex; revalidated before install |
| `python_executable` | absolute path = agent’s `sys.executable` at prepare |
| `old_pid` | PID to wait for |
| `created_at` | ISO timestamp |
| `nonce` | unique handoff id (stale protection) |
| `stage` | `prepared` → `running` → `installed` → `restarted` → `done` / `failed` |

CLI entrypoint (internal):

```text
python -m portforge_agent.upgrade.helper
```

No URL, no package path, no service name flags. Optional `--data-dir` only for
qualification (absolute path), never from Central.

---

## Artifact trust

1. Agent downloads under existing Phase 10 rules (https, size cap, SHA).  
2. Wheel is copied to `data_dir()/upgrade/artifacts/<sha256>/<pip-compatible-basename>`.  
   Bare `{sha256}.whl` names are rejected — pip requires a PEP 427 filename.  
3. Helper refuses any path outside that directory and any basename with
   path separators.  
4. Helper re-hashes before `pip install`.  
5. `pip` uses the recorded `python_executable` only (never Central-supplied).

---

## Handoff durability (filesystem-only)

```text
{data_dir}/upgrade/
  handoff.json          # atomic write (temp + replace)
  helper.log            # helper diagnostics (no secrets)
  run_helper.cmd        # Windows one-shot wrapper (allowlisted argv only)
  artifacts/<sha256>/<wheel-basename>
```

---

## Platform spawn (process isolation)

The helper must outlive the agent process **and** escape the service manager
job/cgroup that owns the agent:

| Platform | Spawn mechanism | Why |
|----------|-----------------|-----|
| Windows | One-shot Scheduled Task `{task} UpgradeHelper` | Task Scheduler Job Objects kill DETACHED children when the agent task ends |
| Linux | `systemd-run --user --no-block` transient oneshot (double-fork fallback); agent unit `KillMode=process` | Default cgroup teardown SIGKILLs helper when MainPID exits |
| macOS | One-shot LaunchAgent `com.portforge.agent.upgradehelper` via `bootstrap`+`kickstart` | Same class of job teardown; main agent KeepAlive unchanged |

Always pass absolute `--data-dir` (and platform service env) — Scheduled
Tasks / transient units do **not** inherit the agent's environment.
**No Central DB migration.** Head remains `d5e6f7a8b9c0`.

If the machine reboots mid-helper:

- On next agent start, if a `prepared`/`running` handoff exists with matching
  host_id and incomplete stage, the **new** agent does **not** auto-SUCCEEDED;
  operator/typed retry remains Phase 21. Optionally a future agent boot can
  re-spawn helper for `prepared`/`installed` stages only when nonce still
  current — Phase 23 implements: helper is idempotent; agent prepare will not
  overwrite a newer nonce; stale handoff with mismatched upgrade_id is refused.

---

## Handoff idempotency

| Case | Behavior |
|------|----------|
| Helper starts twice | Second instance acquires exclusive lock or sees `stage>=running` and exits 0 if same nonce |
| Service manager retries helper | Idempotent stages; skip pip if already installed hash matches |
| Agent crashes after spawning helper | Helper continues (detached); Central stays RESTARTING until heartbeat or stuck recovery |
| Helper crashes after install before restart | Stage `installed`; re-run helper completes restart only |
| New agent starts before helper cleanup | Identity untouched; reconcile on version match |

---

## Platform-specific target behavior

### Linux

- Agent does **not** call `systemctl restart` on itself after install.  
- Helper waits for old PID exit, installs wheel, then
  `systemctl --user restart --no-block <unit>`.  
- No interpretation of SIGTERM/`rc=-15` as install failure.  
- First attempt must reach Central `SUCCEEDED` without typed retry.

### Windows

- Agent does **not** `pip install` while holding `portforge.exe`.  
- Helper waits for old PID exit, then pip, then `schtasks /End` + `/Run`
  (known task name only).  
- No operator OOB pip / manual schtasks.

### macOS

- Preserve published KeepAlive dict + RunAtLoad.  
- Helper after install: ensure LaunchAgent loaded (`bootstrap` if needed) then
  `kickstart -k` (or `start` via service_ops equivalents).  
- No SSH / console / manual plist edits.

---

## Crash / fault matrix

| Scenario | Expected |
|----------|----------|
| Crash before handoff file committed | No helper; Central non-terminal or FAILED only if agent reported FAILED; safe retry |
| Crash after handoff + helper spawned | Helper owns install/restart; reconcile |
| Helper crash before install | Stage `running`/`prepared`; retry helper or Phase 21 retry |
| Helper crash after install before restart | Stage `installed`; helper re-entry restarts service |
| Wrong SHA / version / upgrade_id / host_id | Reject; no install |
| Stale handoff (superseded upgrade_id/nonce) | Reject |
| Terminal FAILED/SUCCEEDED/ROLLED_BACK | Helper never mutates Central terminals improperly; Phase 20 rules unchanged |

---

## Observability

Prefer existing operator statuses:

- Agent still reports `DOWNLOADING` → `VERIFYING` → (`INSTALLING` only if
  legacy path) → **`RESTARTING`** once handoff prepared and helper launched.  
- Map helper wait to existing `WAITING_FOR_RESTART` / progress explanations
  where Phase 22 already surfaces them.  
- Avoid new public status codes unless tests prove necessity.

Audit (Central already emits upgrade events): helper logs locally
`handoff prepared`, `helper launched`, `install completed`,
`service restart requested`, `failed` with typed codes — no credentials.

---

## Explicit non-goals

- No automatic package rollback beyond existing admin rollback semantics.  
- No protocol / contract / machine / MCP bump.  
- No generic remote execution.  
- No production experiments (disposable hosts only).  
- No KeepAlive=true change unless physical Mac evidence demands it (baseline
  remains SuccessfulExit=false / NetworkState=true).

---

## Compatibility gates

| Gate | Target |
|------|--------|
| Protocol | 1 |
| Contract | 1 |
| Machine schema | 1 |
| MCP | 1 |
| DB head | `d5e6f7a8b9c0` |
| Migration | NONE |

---

## Qualification gates (must all pass for PHASE 23 PASS)

Physical disposable matrix, each **first attempt** from installed **1.5.2**
wheel to synthetic development target:

- Linux systemd-user: SUCCEEDED, no OOB, no manual systemctl, no rc=-15 false FAILED  
- Windows: SUCCEEDED, no WinError 32, no OOB pip, no operator schtasks  
- macOS: SUCCEEDED, no SSH/console/manual launchctl  

Plus unit tests for security rejects, idempotency, stale handoff, and full
repo regression (backend/agent/dashboard/service + lint/typecheck/build/doctor).

Phase 23B completed macOS physical first-attempt SUCCEEDED on an isolated
LaunchAgent (`com.portforge.agent.phase23`) without touching the production
Mac agent UUID. See `docs/qualification/phase23-self-upgrade-bootstrap.md`.
