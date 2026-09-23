# Coding-Agent Integration

Phase 13 design — provider-neutral machine interface reusing Phase 8D contract and CLI. MCP deferred.

Baseline: `machine_interface.contract_version = 1`, `protocol_version = 1`. Additive capability fields only — no version bump unless shape breaks v1 promises.

See [`docs/phase8d_coding_agent_integration.md`](../phase8d_coding_agent_integration.md), [`agent/portforge_agent/agent_contract.py`](../../agent/portforge_agent/agent_contract.py), [`agent/portforge_agent/workflow.py`](../../agent/portforge_agent/workflow.py).

## Provider-neutral CLI/API core

PortForge exposes **no model APIs, no provider SDKs, no paid inference**. Any coding agent (Antigravity, Codex, Claude Code, custom scripts) integrates by:

1. Reading the machine contract (`portforge capabilities --json` or `portforge agent-contract --json`)
2. Driving subprocess CLI commands with structured JSON on stdout
3. Parsing structured errors (`error.code`, `error.details`, optional `recovery`)

Central HTTP (`POST /api/allocations`, etc.) is available for non-CLI integrators; the agent CLI is a thin client over the same APIs ([`central_client.py`](../../agent/portforge_agent/central_client.py)).

## Machine-readable commands

| Command | Alias | Purpose |
|---------|-------|---------|
| `portforge capabilities [--json]` | `agent-contract` | Full capability contract via `build_contract()` |
| `portforge project inspect [--json]` | — | Read-only: manifest services + allocation status + config targets |
| `portforge project provision --request-id ID [--dry-run] [--json]` | — | Dry-run → `prepare_workflow`; apply → `apply_workflow` |
| `portforge workflow prepare/apply/status` | — | Lower-level orchestration (unchanged) |
| `portforge project validate/plan/allocate/status` | — | Phase 8B commands (unchanged) |
| `portforge config plan/apply/status/rollback` | — | Phase 8C commands (unchanged) |

### Dry-run semantics

`project provision --dry-run`:

- Calls [`prepare_workflow()`](../../agent/portforge_agent/workflow.py) only
- Does **not** call `create_allocation`
- Does **not** write project files or workflow records
- Returns advisory candidates + config file list

Without `--dry-run`, delegates to [`apply_workflow()`](../../agent/portforge_agent/workflow.py) (allocate then optional config apply).

## Structured errors

Reuse existing codes — do not invent parallel enums:

| Layer | Examples |
|-------|----------|
| Manifest | `MANIFEST_NOT_FOUND`, `UNSUPPORTED_MANIFEST_VERSION`, `HOST_NOT_FOUND` |
| Allocation (8A) | `HOST_DECOMMISSIONED`, `HOST_STALE`, `ALLOCATION_UNAVAILABLE`, `IDEMPOTENCY_CONFLICT` |
| Config (8C) | `CONFIG_PATH_OUTSIDE_PROJECT`, `CONFIG_CHANGED_SINCE_PLAN`, `KUBERNETES_CONTAINER_NOT_FOUND` |
| Workflow (8D) | `WORKFLOW_IDEMPOTENCY_CONFLICT`, `WORKFLOW_NOT_FOUND`, `WORKFLOW_COMPENSATION_FAILED` |

CLI prints one JSON object to stdout on `--json` failure paths; exit codes follow [`agent/README.md`](../../agent/README.md).

## Contract shape (additive)

`build_contract()` returns:

```json
{
  "contract_version": 1,
  "protocol_version": 1,
  "machine_interface": {"contract_version": 1},
  "capabilities": {
    "batch_allocation": true,
    "dry_run": true,
    "project_inspect": true,
    "project_provision": true,
    "kubernetes_hostPort": true,
    "kubernetes_nodePort": true,
    "kubernetes_containerPort_auto": false,
    "kubernetes_service_port_auto": false,
    "kubernetes_targetPort_auto": false,
    "mcp": false
  }
}
```

Existing boolean capabilities (`workflow_apply`, `config_rollback`, …) remain unchanged.

## MCP: DEFERRED

An MCP server is **not** implemented in this phase.

**Why defer:** The CLI/API core already gives coding agents a stable, testable, provider-neutral integration surface without running another long-lived server or duplicating contract logic. MCP would mostly re-wrap the same commands; it adds deployment and auth surface area before a concrete consumer requires it.

When added later, MCP tools should delegate to the same `build_contract()` and CLI entry points — not fork allocation or config logic.

## External agent validation (no paid APIs)

Antigravity, Codex, and Claude integrations are validated by driving the **same JSON contract** via subprocess/`main([...])` in tests ([`agent/tests/test_coding_agent_external_workflow.py`](../../agent/tests/test_coding_agent_external_workflow.py)) — no provider API keys, no imports of `allocation_service` or DB from the test harness.

## Intentionally deferred

| Item | Reason |
|------|--------|
| MCP server | See above; CLI/API core sufficient for v1 |
| Physical host upgrade automation | Phase 8E / separate design ([`docs/design/agent-upgrade-management.md`](agent-upgrade-management.md)) |
| Provider-specific prompt adapters | Thin wrappers only when a real environment needs them ([`docs/phase8d_coding_agent_integration.md`](../phase8d_coding_agent_integration.md)) |
