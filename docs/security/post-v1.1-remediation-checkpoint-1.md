# PortForge Security Remediation Checkpoint 1

## Preservation & Backup
- **Backup created:** A full private `git clone --mirror` was created before remediation.
- **Unrelated User Work:** `dashboard/lib/api/config.ts` and `docker-compose.yml` were safely backed up and left unmodified in the working tree.

## Credential Rotation
- **Mac SSH Credential:** ROTATED
- **Lenovo SSH Credential:** ROTATED
- **server-b SSH Credential:** ROTATED
- **New Authentication Mechanism:** per-device SSH key authentication (ed25519).
- **Fleet Identity & Enrollment:** Preserved. Agents continue to run and report `agent_identity: ok` without requiring enrollment changes. Heartbeat and sync function normally over the new SSH key mechanism where applicable.


- **Shared key**: shared temporary key removed
- **Isolation**: cross-host isolation verified
- **Note**: The earlier shared-key configuration was transitional and was replaced before history sanitation.
## Current-Tree Hardcoded Secret Removal
- **deploy_remote.py Decision:** REMOVED (File was an obsolete helper script. Hardcoded credentials eliminated).

## Scratch & Debug Cleanup
The following artifacts were deleted:
- `insert_host.py`
- `scripts/deploy_remote.py`
- `simulate_agent.py`
- `simulate_agent_cleanup.py`
- `simulate_recovery.py`
- `test_robustness.py`
- `write_remote_script.py`
- `write_script.py`

## Repository Ignore Rules & Generated Artifacts
- **.gitignore Changes:** Added patterns for `.next/`, `node_modules/`, `*.egg-info/`, `build/`, `dist/`, `venv_agent/`, `.vscode/`, `.idea/`, and `!.env.example`.
- **egg-info Decision:** Untracked. Local generated package metadata `agent/portforge_agent.egg-info` and `backend/portforge_backend.egg-info` were removed from Git tracking and will now be ignored.
- **uv.lock Decision:** Preserved. Both `agent/uv.lock` and `backend/uv.lock` are retained as intentional, deterministic lockfiles.

## Prevention Mechanisms
- **Secret Scanning Guard:** Added `scripts/check_secrets.sh` and `scripts/check_secrets.ps1` to run local `gitleaks detect` scans. A pre-commit hook template `gitleaks protect --staged` was implemented.

## Current-Tree Security Scan
- **Scan Method:** `gitleaks detect --no-git` against a fresh clean worktree at HEAD.
- **Meaningful Findings:** 0

## Regression & Health
- Agent Regression: PASS
- Backend Regression: PASS
- Dashboard Regression: PASS
- Lint/Typecheck/Build: PASS
- Fleet Health: PASS (PortForge version 1.1.0, protocol 1, components healthy)

## History Rewrite Plan Preparation
A plan has been formulated to purge historical credentials using `git filter-repo`. The exact paths to be completely removed from all historical commits are:
- `backend/mac_ssh.py`
- `scratch/ssh_hp.py`
- `scratch/ssh_hp_validate.py`
- `scratch/ssh_lenovo.py`
- `scratch/ssh_lenovo_validate.py`
- `scripts/deploy_remote.py`

This will be executed pending final authorization.

