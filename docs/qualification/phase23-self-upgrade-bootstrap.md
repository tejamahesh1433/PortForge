# Phase 23 — Cross-Platform Self-Upgrade Bootstrap Qualification

**Status:** PARTIAL / BLOCKED (macOS physical unavailable)  
**Branch:** `feature/self-upgrade-bootstrap`  
**Base:** `v1.5.2` / `9c9ef97850c3c87ceb60aca071428f70e716cfed` / tree `b671a3b50758daa53361ce748ef60196a20273b2`  
**Production:** untouched (Central/Dashboard/agents remain 1.5.2)

---

## Production evidence that motivated the work

| Platform | Defect | Boundary |
|----------|--------|----------|
| Linux | First attempt `FAILED` with `systemctl … (rc=-15)` | Old agent sync-restarted itself |
| Windows | `pip` `WinError 32` locking `Scripts\portforge.exe` | In-process install while running |
| macOS | Wheel installed; LaunchAgent unloaded/stopped until operator recovery | Clean exit 0 under `KeepAlive={SuccessfulExit:false, NetworkState:true}` |

Authoritative published KeepAlive (tag `v1.5.2`):

```text
KeepAlive = { SuccessfulExit = false, NetworkState = true }
RunAtLoad = true
```

---

## Architecture under test

Detached typed upgrade helper (`python -m portforge_agent.upgrade.helper`):

- Agent: claim → download → SHA verify → handoff → spawn helper → exit  
- Helper: wait PID → re-SHA → pip install → platform restart  
- New agent: heartbeat → Phase 20 reconcile → `SUCCEEDED`  
- No DB migration, no protocol bump, no generic remote execution

Platform spawn isolation (required for first-attempt success):

| Platform | Mechanism |
|----------|-----------|
| Windows | One-shot Scheduled Task `{task} UpgradeHelper` + absolute `--data-dir` |
| Linux | `systemd-run --user --no-block` + `KillMode=process` on agent unit |
| macOS | One-shot LaunchAgent `com.portforge.agent.upgradehelper` (code present; physical pending) |

Artifact layout: `upgrade/artifacts/<sha256>/<pep427-wheel-name>`.

---

## Physical matrix

### Windows (disposable Scheduled Task) — PASS

- Baseline: Phase23-labeled `1.5.2` wheel with helper  
- Target: `1.5.3`  
- Central: disposable `:58005`  
- First attempt: **SUCCEEDED**  
- WinError 32: **ABSENT**  
- OOB / manual schtasks: **NO**  
- Identity preserved: **YES**  
- Evidence: `.qual-artifacts/phase23/win_phys.py` run; helper.log stage=done

### Linux (WSL2 Ubuntu systemd --user) — PASS

- Unit: `portforge-agent-phase23.service` with `KillMode=process`  
- First attempt: **SUCCEEDED**  
- `rc=-15` false failure: **ABSENT**  
- OOB / manual systemctl: **NO**  
- Identity preserved: **YES**  
- Evidence: `.qual-artifacts/phase23/linux_phys.py` run

### macOS — SKIPPED / BLOCKED

- No disposable macOS host available for Phase 23 experiments  
- Production Mac (`192.168.4.170`) must not be used per mission constraints  
- Code path for helper LaunchAgent spawn + existing `bootout`/`bootstrap` restart is implemented  
- KeepAlive/RunAtLoad generators unchanged from published semantics  
- **Phase 23 cannot PASS until disposable macOS first-attempt SUCCEEDED is recorded**

---

## Recovery / security (unit-covered)

Covered in `agent/tests/test_upgrade_handoff.py` + `test_upgrade.py`:

- Wrong SHA / host_id / schema → helper rejects, no install  
- Idempotent `done`/`restarted` stages  
- Spawn allowlist (no URL/command/service argv)  
- Windows schtasks / Linux systemd-run / macOS LaunchAgent spawn paths  

Physical crash-before/after-handoff and machine-restart cases: exercised indirectly by helper PID-wait + service restart on Win/Linux first-attempt path; dedicated crash harness not re-run this cycle.

---

## Compatibility gates

| Gate | Result |
|------|--------|
| DB head | `d5e6f7a8b9c0` (no migration added) |
| Protocol / Contract / Machine schema / MCP | unchanged (1) |
| Production modified | **NO** |

---

## Remaining limitations

1. **macOS physical first-attempt** not executed → Phase 23 PARTIAL/BLOCKED.  
2. Published production `1.5.2` agents **without** the helper still need one OOB path to absorb this code; subsequent upgrades use the helper. Qual used Phase23-labeled `1.5.2` wheels that include the helper.  
3. Artifact hosting for qual used local HTTPS + trust-anchor cert via `SSL_CERT_FILE` (not a production distribution channel).

---

## NEXT

STOP until disposable macOS qualification is available. Do not release.
