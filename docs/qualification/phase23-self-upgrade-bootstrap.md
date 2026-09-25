# Phase 23 — Cross-Platform Self-Upgrade Bootstrap Qualification

**Status:** PASS (frozen; not released)  
**Branch:** `feature/self-upgrade-bootstrap`  
**Base:** `v1.5.2` / `9c9ef97850c3c87ceb60aca071428f70e716cfed` / tree `b671a3b50758daa53361ce748ef60196a20273b2`  
**WIP checkpoint:** `071abf0ed9de2462a139254e5cef441d129fffb9` / tree `fa54e74fe6cff68eab2377d839c6bb2057af32da`  
**Production:** untouched (Central/Dashboard/agents remain 1.5.2)

---

## Production evidence that motivated the work

| Platform | Defect | Boundary |
|----------|--------|----------|
| Linux | First attempt `FAILED` with `systemctl … (rc=-15)` | Old agent sync-restarted itself |
| Windows | `pip` `WinError 32` locking `Scripts\portforge.exe` | In-process install while running |
| macOS | Wheel installed; LaunchAgent unloaded/stopped until operator recovery | Clean exit 0 under `KeepAlive={SuccessfulExit:false, NetworkState:true}` |

Authoritative published KeepAlive (tag `v1.5.2` / published wheel SHA `19017e9b…a46a48`):

```text
KeepAlive = { SuccessfulExit = false, NetworkState = true }
RunAtLoad = true
Label = com.portforge.agent
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
| macOS | One-shot LaunchAgent `com.portforge.agent.upgradehelper` |

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
- Evidence: `.qual-artifacts/phase23/win_phys.py`

### Linux (WSL2 Ubuntu systemd --user) — PASS

- Unit: `portforge-agent-phase23.service` with `KillMode=process`  
- First attempt: **SUCCEEDED**  
- `rc=-15` false failure: **ABSENT**  
- OOB / manual systemctl: **NO**  
- Identity preserved: **YES**  
- Evidence: `.qual-artifacts/phase23/linux_phys.py`

### macOS (isolated Phase23 LaunchAgent on LAN Mac) — PASS

- **Production Mac UUID `2a8d8b4c-…7688` was not used as the upgrade subject.**  
- Isolated label: `com.portforge.agent.phase23`  
- Data dir: `~/PortForge-Phase23` (production `com.portforge.agent` left running/intact)  
- Published KeepAlive source verified from wheel SHA `19017e9b…` before runtime install  
- Baseline runtime: Phase23-labeled `1.5.2` with helper (`7e7868b2…`)  
- Target: `1.5.3` (`04aab44b…`) via LAN HTTPS artifact server  
- Central: disposable `:58005` (SSH `-R` tunnel)  
- Disposable host id example: `e6288ab9-899d-48a1-8050-ec988d1e4e28`  
- First attempt: **SUCCEEDED** (upgrade `e500610b-…`, no retry, no OOB, no manual launchctl)  
- Helper survival (from `helper.log`):

```text
Upgrade helper started (pid=30710)
Waiting for old agent process (pid=30545) to exit...
Old agent process (pid=30545) has exited
Installing portforge_agent-1.5.3-py3-none-any.whl …
Upgrade helper finished: stage=done
```

- Post-upgrade LaunchAgent: `RunAtLoad=true`, `KeepAlive={SuccessfulExit:false, NetworkState:true}`  
- UUID/credential preserved; prod plist still intact  
- Evidence: `.qual-artifacts/phase23/mac_phys.py`, `mac-evidence-helper.log`, `mac_recovery.py`

#### macOS recovery

| Case | Result |
|------|--------|
| Crash after handoff / old-agent exit | PASS (physical helper.log) |
| Helper failure before install | PASS (missing artifact → rc≠0, no install) |
| Wrong artifact SHA | PASS (SHA mismatch → no install) |
| Stale / wrong host handoff | PASS |
| Idempotency (`stage=done` ×2) | PASS |
| Service stop/start lifecycle | PASS |
| Machine restart | SKIPPED (shared host; not required for bootstrap acceptance) |
| Sleep/wake / network transition | NOT OBSERVED |

---

## Lint / typecheck delta (v1.5.2 base vs Phase 23)

Commands: `ruff check agent backend`; `mypy portforge_agent --ignore-missing-imports` (agent).

| Gate | v1.5.2 (`9c9ef97`) | Phase 23 | New errors |
|------|--------------------|----------|------------|
| Ruff | Found 143 errors | Found 143 errors | **0** |
| Mypy (agent) | Found 118 errors in 29 files | Found 118 errors in 29 files (100 sources) | **0** |

Phase 23-introduced lint/type debt: **NONE** (pre-existing repository debt unchanged in count).

Dashboard: `npm run typecheck` PASS; `npm run lint` 0 errors (1 pre-existing warning).

---

## Regression

| Suite | Result |
|-------|--------|
| Backend pytest | 442 passed / 0 failed |
| Agent pytest | 1043 passed / 0 failed / 5 skipped |
| Dashboard vitest | 205 passed / 0 failed |
| Service (gen+ops) | 79 passed / 0 failed |
| Agent build | PASS |
| Doctor (prod Central read-only) | PASS / overall ok |

---

## Recovery / security

Covered in `agent/tests/test_upgrade_handoff.py` + `test_upgrade.py` and macOS physical recovery:

- Wrong SHA / host_id / schema → helper rejects, no install  
- Idempotent `done`/`restarted` stages  
- Spawn allowlist (no URL/command/service argv)  
- No generic shell / remote command / arbitrary executable / service / URL / install path  
- No secret leakage in helper argv  

---

## Compatibility gates

| Gate | Result |
|------|--------|
| DB head | `d5e6f7a8b9c0` (no migration added) |
| Protocol / Contract / Machine schema / MCP | unchanged (1) |
| Production modified | **NO** |

Production read-only check (Central `:58000`):

- Central/Dashboard **1.5.2**  
- Agents: 4 × **1.5.2** HEALTHY (including production Mac UUID)  
- Identities: 4 active credentials  
- Duplicates: 0  
- Alembic: `d5e6f7a8b9c0`

---

## Remaining limitations

1. Published production `1.5.2` agents **without** the helper still need one OOB path to absorb this code; subsequent upgrades use the helper. Qual used Phase23-labeled `1.5.2` wheels that include the helper.  
2. Artifact hosting for qual used local HTTPS + trust-anchor cert (`SSL_CERT_FILE` / qual-only venv `sitecustomize` on macOS LibreSSL) — not a production distribution channel.  
3. Machine restart at handoff boundary: SKIPPED on shared Mac.

---

## NEXT

Phase 23 **FROZEN**. Do **not** release. Do **not** start Phase 24 from this gate without an explicit new mission.
