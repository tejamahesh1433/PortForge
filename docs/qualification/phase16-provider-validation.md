# Phase 16 Provider Validation Evidence

Date: 2026-09-24  
Branch: `feature/workspace-discovery`  
MCP command: `portforge mcp serve` (editable install of Phase 16 tree)

Disposable workspace: `.qual-temps/phase16-provider-workspace` (copy of fixture A)

## Transport proof (shared server)

Script: `.qual-temps/phase16_mcp_evidence_run.py`  
Result: `.qual-temps/phase16-mcp-stdio-evidence.json`

- `tools/list` returned 10 tools including `portforge_workspace_discover`
- `portforge_workspace_discover` → services `api`, `app`
- `portforge_project_plan` workspace mode → `plan_id` issued
- Secret fixtures **not** present in MCP payloads

## Claude Code

| Item | Result |
|------|--------|
| Registration | `claude mcp add -s local portforge -- portforge mcp serve` |
| Health | `claude mcp get portforge` → **√ Connected** |
| Non-interactive agent turn | `claude -p ...` → **EXTERNAL BLOCKED**: `Failed to authenticate: OAuth session expired and could not be refreshed` |
| Actual MCP tool invocation via Claude agent | **NO** (auth blocker) |
| PortForge defect | **NO** — server Connected; client session expired |

## Codex

| Item | Result |
|------|--------|
| Registration | `codex mcp add portforge -- portforge mcp serve` |
| Status | enabled, transport stdio |
| Agent turn | `codex exec --approve-for-me` |
| Actual MCP invocation | **YES** — `mcp: portforge/portforge_workspace_discover (completed)` |
| Result | `{"services":[{"name":"api","ports":[4000]},{"name":"app","ports":[3000]}]}` |
| Evidence file | `.qual-temps/phase16-codex-mcp-evidence.txt` |

## Antigravity

| Item | Result |
|------|--------|
| Client present | **NO** (`where antigravity` empty; no config dirs found) |
| Classification | **EXTERNAL BLOCKED** — client not installed in this environment |
| PortForge defect | **NO** |

## Consistency

Same PortForge binary/command for all providers. No provider-specific PortForge implementation.
