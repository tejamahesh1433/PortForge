# PortForge Post-v1.1 Security & Repository Hygiene Audit

## Release Baseline Immutability
- **Current Branch/HEAD:** `815de61faccaa682e1da624c9a7723438edb334f`
- **v1.1.0 Tag:** `815de61faccaa682e1da624c9a7723438edb334f`
- **v1.0.0 Tag:** `1dabad2e50beebbf126bd15cba8a62609bf1db19`
- **Integrity Result:** PASS (The Git release graph is stable and immutable).

## Current Tracked-Tree Findings
- **Scan Method:** `gitleaks detect --no-git` on a clean `v1.1.0` worktree.
- **Findings:** `0` leaks found.
- **Result:** The actual tracked v1.1.0 tree does NOT contain secrets.

## deploy_remote.py Assessment
- **Path:** `scripts/deploy_remote.py`
- **Tracked:** Yes.
- **Classification:** Deployment helper script (Dead/Legacy).
- **Findings:** Contains hardcoded host configurations:
  - `host` IP addresses (Normal configuration)
  - `user` names (Sensitive but non-secret)
  - `pass` field (Secret password)
- **Reachable from product execution:** No.

## Full Git-History Secret Inventory
- **Overview:** Historical commits contain hardcoded SSH passwords. 
- **Affected Artifacts:**
  - `scripts/deploy_remote.py`
  - `backend/mac_ssh.py`
  - `scratch/ssh_hp.py`
  - `scratch/ssh_hp_validate.py`
  - `scratch/ssh_lenovo.py`
  - `scratch/ssh_lenovo_validate.py`
- **Recommended Action:** All passwords inside historical scratch/helper files must be purged via `git filter-repo`.

## Credential Rotation Inventory
1. **Windows -> Mac SSH**: macOS user credentials (SSH/SFTP/System login).
   - **Rotation Mechanism:** Rotate password using macOS System Preferences.
2. **Windows -> Linux SSH (Lenovo)**: Linux user credentials.
   - **Rotation Mechanism:** Run `passwd` on the target machine.
3. **Windows -> Linux SSH (HP/TejaServer)**: Linux user credentials.
   - **Rotation Mechanism:** Run `passwd` on the target machine.
- **Validity:** The validity cannot be tested safely. All 3 must be rotated.

## Scratch & Debug Artifact Audit
The following tracked files act as one-off scratch scripts or temporary test artifacts:
- `insert_host.py` (Classification: REMOVE)
- `scripts/deploy_remote.py` (Classification: REMOVE)
- `simulate_agent.py` (Classification: REMOVE)
- `simulate_agent_cleanup.py` (Classification: REMOVE)
- `simulate_recovery.py` (Classification: REMOVE)
- `test_robustness.py` (Classification: MOVE TO TESTS / REMOVE)
- `write_remote_script.py` (Classification: REMOVE)
- `write_script.py` (Classification: REMOVE)

## .gitignore Gaps
The `.gitignore` has some omissions that may result in tracked secrets or noise:
- `venv_agent/`
- `*.egg-info/`
- `.next/`
- `node_modules/`
- `build/`
- `dist/`
- IDE directories (`.vscode/`, `.idea/`)
- `.env.*` (e.g. `.env.local`, `.env.production`)

## Generated File / Lockfile Policy
- **`uv.lock`**: Tracked. Required to define and resolve deterministic python package trees for `backend` and `agent`.
- **`*.egg-info`**: Do NOT track. These are local cache files generated dynamically during `pip install -e .` and should be ignored.

## History-Rewrite Impact Analysis
Using `git filter-repo` to rewrite history and purge SSH secrets will have the following consequences:
- **Branches affected:** `main` and any open local/remote feature branches.
- **Tags affected:** `v1.0.0`, `v1.1.0` (Tags must be recreated since commit SHAs will be regenerated).
- **Remote force-push:** REQUIRED.
- **Local clones:** Any other user clones MUST be hard-reset or re-cloned completely.
- **GitHub Implications:** Pull Requests referencing old commits will point to dangling objects. 

## Security Boundary Review
- There is no direct path for leaking credentials out to unauthorized consumers.
- `doctor`, `agent-contract`, `workflow prepare/apply`, the backend APIs, and Dashboard correctly expose only necessary runtime properties. No environment variables or credentials are leaked in diagnostics.

## Public Readiness Decision
- **SAFE TO REMAIN PRIVATE?** YES.
- **SAFE TO SHARE WITH A TRUSTED COLLABORATOR?** YES (Since it's on a trusted network, though historical credentials exist).
- **SAFE TO MAKE PUBLIC?** NO.
  - **Blockers:** The historical Git commits contain active system credentials that would be immediately exposed and compromised. History MUST be rewritten first, and the credentials MUST be rotated before going public.

## Recommended Remediation Sequence
1. **Preserve/backup repository** (Critical first step before destructive actions).
2. **Rotate compromised credentials** (Ensure live systems are safe before publishing anything).
3. **Remove hardcoded current-tree secrets and scratch files** (Delete legacy `scripts/deploy_remote.py` and other scratch files).
4. **Add prevention/.gitignore improvements**.
5. **Sanitize historical Git objects** (Run `git filter-repo` to strip the scratch/deployment scripts entirely).
6. **Force-update private remote refs** (Overwrite origin with clean history, including re-tagging v1.0.0 and v1.1.0).
7. **Verify historical secrets no longer reachable** (Perform a full deep gitleaks scan on the rewritten tree).
8. **Reclone/reset other working copies**.
9. **Rerun PortForge regression** (Verify the rewrite didn't damage application files).
10. **Decide whether repository can ever become public**.
