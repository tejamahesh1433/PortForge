# PortForge MCP Guide

PortForge exposes a **provider-neutral MCP server** so coding agents can drive
structured port allocation and safe project configuration without shelling out
to ad-hoc scripts.

MCP is optional. The CLI/JSON contract remains fully supported.

Design reference: [`docs/design/mcp-integration.md`](../design/mcp-integration.md).

---

## Requirements

- PortForge agent ≥ 1.4.0 (with Phase 15 MCP)
- Local `portforge` on `PATH`, or an absolute path to the executable / module
- Central URL for mutation workflows (`PORTFORGE_CENTRAL_URL` or tool `central_url`)

Check:

```bash
portforge capabilities --json
# capabilities.mcp == true
# mcp.command == "portforge mcp serve"
```

---

## Start the server

```bash
portforge mcp serve
```

Optional:

```bash
portforge mcp serve --url http://127.0.0.1:58004 --log-level WARNING
```

| Stream | Content |
|--------|---------|
| **stdout** | MCP JSON-RPC messages only |
| **stderr** | Diagnostics / logging |

Do not redirect application logs to stdout — that corrupts the protocol.

---

## Transport

Local **stdio** JSON-RPC (MCP protocol version `2024-11-05` subset).

- No public HTTP listener in this phase
- One server binary for all coding-agent providers

MCP schema version: **1** (`mcp_schema_version`).

---

## Tools

| Tool | Class | Purpose |
|------|-------|---------|
| `portforge_capabilities` | READ | Contract, MCP schema, Central availability, mutation support matrix |
| `portforge_project_inspect` | READ | Manifest services, config targets, allocation status |
| `portforge_project_plan` | PLAN | Dry-run plan + `plan_id` / `plan_hash` (no project writes) |
| `portforge_project_provision` | MUTATE | Allocate + apply config via existing workflow |
| `portforge_project_verify` | READ | Workflow status and/or allocation verify |
| `portforge_project_rollback` | MUTATE | Config rollback only (does **not** release allocations) |
| `portforge_allocation_recommend` | READ | Advisory candidates |
| `portforge_allocation_create` | MUTATE | Allocation only (no config rewrite) |
| `portforge_allocation_release` | MUTATE | Release allocation only (no config rewrite) |

### Forbidden surface

There is **no** generic shell, terminal, HTTP, SQL, or arbitrary filesystem tool.

### Mutation acknowledgement

MUTATE tools require:

```json
"confirm_mutate": true
```

Otherwise the tool returns `MUTATION_NOT_APPROVED`.

### Kubernetes automatic mutations

| Allowed | Not allowed |
|---------|-------------|
| `hostPort` | `containerPort` |
| Service `nodePort` | Service `port` / `targetPort` |

Capabilities advertise this explicitly under `supported_mutation_types` /
`unsupported_automatic_mutations`.

---

## Recommended coding-agent workflow

1. `portforge_capabilities` — discover versions and tools
2. `portforge_project_inspect` — read-only project summary
3. `portforge_project_plan` — review proposed ports and files
4. Human / policy review of the structured plan
5. `portforge_project_provision` with `confirm_mutate: true`, stable `request_id`, and optional `plan_id`
6. `portforge_project_verify`
7. If needed: `portforge_project_rollback` then `portforge_allocation_release`

If project files change after a plan, provision with that `plan_id` fails with
`CONFIG_CHANGED_SINCE_PLAN`. Call plan again — PortForge will not silently
regenerate and apply.

---

## Provider setup (same server)

Use placeholders; do not hardcode personal paths.

### Claude Code

```bash
claude mcp add -s local portforge -- portforge mcp serve
claude mcp get portforge   # expect Connected
```

JSON form (example):

```json
{
  "mcpServers": {
    "portforge": {
      "command": "portforge",
      "args": ["mcp", "serve"],
      "env": {
        "PORTFORGE_CENTRAL_URL": "http://127.0.0.1:58004"
      }
    }
  }
}
```

Windows module fallback:

```json
{
  "mcpServers": {
    "portforge": {
      "command": "python",
      "args": ["-m", "portforge_agent", "mcp", "serve"]
    }
  }
}
```

### Codex

```bash
codex mcp add portforge -- portforge mcp serve
codex mcp get portforge
```

### Antigravity

Point Antigravity’s MCP entry at the same command (`portforge mcp serve`).
There is no Antigravity-specific PortForge binary.

### Provider qualification (Phase 16)

See [`docs/qualification/phase16-provider-validation.md`](../qualification/phase16-provider-validation.md).

| Provider | Registration | Actual tool call | Notes |
|----------|--------------|------------------|-------|
| Codex | PASS | PASS | `portforge_workspace_discover` completed |
| Claude Code | PASS (Connected) | EXTERNAL BLOCKED | OAuth session expired for `claude -p` |
| Antigravity | EXTERNAL BLOCKED | NO | Client not installed |

---

## Workspace discovery (Phase 16)

Additional READ tool:

- `portforge_workspace_discover`

`portforge_project_plan` accepts additive `workspace: true`.

Guide: [`workspace-discovery.md`](workspace-discovery.md).

---

## Platform notes

| Platform | Notes |
|----------|-------|
| **Linux / macOS** | Prefer `portforge` on `PATH` after `pip install` / editable install of `agent/` |
| **Windows** | Same; if console script shim is awkward in MCP hosts, use `python -m portforge_agent mcp serve` |
| **venv** | Point `command` at the venv’s `portforge` or `python` |

Discovery without personal paths:

```bash
# Unix
which portforge
python -c "import shutil; print(shutil.which('portforge'))"

# Windows PowerShell
Get-Command portforge
python -c "import shutil; print(shutil.which('portforge'))"
```

---

## Errors

Failures return MCP tool results with `isError: true` and JSON:

```json
{
  "error": {
    "code": "CONFIG_CHANGED_SINCE_PLAN",
    "message": "...",
    "details": [],
    "recovery": {}
  }
}
```

Decide on `error.code`. Examples: `CENTRAL_UNAVAILABLE`, `HOST_OFFLINE`,
`HOST_DECOMMISSIONED`, `ALLOCATION_UNAVAILABLE`, `CONFIG_CHANGED_SINCE_PLAN`,
`INVALID_PROJECT_MANIFEST`, `PATH_OUTSIDE_PROJECT`, `MUTATION_NOT_APPROVED`,
`VERIFICATION_FAILED`, `ROLLBACK_FAILED` / `CONFIG_ROLLBACK_FAILED`.

---

## Security model

- Project file writes confined by `resolve_within_root`
- Secrets scrubbed from MCP responses (tokens, passwords, Authorization)
- Allocation release does not rewrite project config
- Config rollback does not release allocations
- No admin bootstrap / enrollment tools on the MCP surface

---

## Limitations (Phase 15)

- stdio only (no public MCP HTTP)
- No MCP resources/prompts/sampling
- No automatic `containerPort` / Service `port` / `targetPort` mutation
- Development mutation testing must use an isolated Central — never production
  `:58000` / production Postgres

---

## CLI remains available

```bash
portforge capabilities --json
portforge project inspect --json
portforge project provision --request-id ID --dry-run --json
portforge project provision --request-id ID --json
```

You do not need MCP to use PortForge.
