# MCP Integration (Phase 15)

Provider-neutral Model Context Protocol (MCP) surface for coding agents.
MCP is an **additional** interface. PortForge continues to work fully via
CLI/JSON without MCP.

Baseline: PortForge **v1.4.0** (`3c4038d` / tree `8ba5125`).
Protocol / agent contract / machine schema remain **1**. MCP introduces its
own interface schema version (`mcp_schema_version`) and does **not** bump
those contracts. Flipping `capabilities.mcp` from `false` → `true` is an
additive capability activation already reserved in the Phase 13 contract.

Related: [`coding-agent-integration.md`](coding-agent-integration.md),
[`agent_contract.py`](../../agent/portforge_agent/agent_contract.py),
[`workflow.py`](../../agent/portforge_agent/workflow.py).

---

## 1. Goals and non-goals

### Goals

- Expose structured PortForge operations to coding agents over **stdio MCP**.
- Reuse the same application layer as the CLI (`build_contract`,
  `prepare_workflow` / `apply_workflow`, `config_manager`, `CentralClient`,
  `resolve_within_root`).
- Preserve project-root confinement, stale-plan protection, K8s hostPort /
  nodePort-only mutation, allocation/release separation, and secret boundaries.
- One MCP server for all providers (Antigravity, Codex, Claude Code, …).

### Non-goals

- Publicly listening HTTP/SSE MCP server.
- Browser authentication, admin bootstrap secrets, or agent enrollment tokens
  in the MCP surface.
- Generic shell / subprocess / arbitrary HTTP / SQL / filesystem / Docker /
  Kubernetes command tools.
- Provider-specific PortForge forks or SDKs.
- Database migrations.
- Protocol / contract / machine-schema bumps.

---

## 2. Architecture

```
Coding agent (Antigravity / Codex / Claude Code / …)
        │  MCP JSON-RPC over stdio
        ▼
portforge mcp serve
        │
        ▼
portforge_agent.mcp.server   (transport + JSON-RPC framing)
        │
        ▼
portforge_agent.mcp.tools    (thin handlers; approval gate; secret scrub)
        │
        ├── agent_contract.build_contract
        ├── manifest / project inspect helpers
        ├── workflow.prepare_workflow / apply_workflow / get_workflow_status
        ├── config_manager.build_plan / apply / rollback / get_status
        ├── project_adapter (recommend / allocate body)
        ├── central_client.CentralClient
        └── config_files.resolve_within_root
```

**Rule:** MCP must never reimplement allocation, mutation, verification, or
rollback. It only sequences existing APIs and maps results/errors.

CLI remains the primary human/script interface. MCP and CLI share the same
modules; neither shells out to the other.

---

## 3. Transport

| Choice | Value |
|--------|-------|
| Transport | **stdio** only (Phase 15) |
| Framing | Newline-delimited JSON-RPC 2.0 (MCP `2024-11-05` subset) |
| stdout | Protocol messages **only** |
| stderr | Diagnostics / logging |
| Network listen | **Forbidden** in this phase |

Supported server methods:

| Method | Role |
|--------|------|
| `initialize` | Handshake; return `serverInfo` + tool capability |
| `notifications/initialized` | Ack (no response) |
| `tools/list` | Enumerate tools + JSON Schema inputs |
| `tools/call` | Invoke one tool |
| `ping` | Liveness |

Unsupported in Phase 15: resources, prompts, sampling, HTTP/SSE transports.

Entry point:

```text
portforge mcp serve
```

Optional flags (non-protocol):

- `--url URL` — default Central base URL for tools that need Central
- `--log-level LEVEL` — stderr only

---

## 4. MCP schema version

```text
MCP_SCHEMA_VERSION = 1
MCP_SERVER_NAME = "portforge"
```

Returned by `portforge_capabilities` and in `initialize.serverInfo`.
Independent of `CONTRACT_VERSION`, `PROTOCOL_VERSION`, and package semver.

---

## 5. Tool surface

Provider-neutral names (`portforge_*`). No generic `execute` / `run` / `shell`.

| Tool | Class | Underlying API |
|------|-------|----------------|
| `portforge_capabilities` | READ | `build_contract()` + MCP metadata + Central health probe |
| `portforge_project_inspect` | READ | CLI inspect logic (manifest + optional `list_allocations`) |
| `portforge_project_plan` | PLAN | `prepare_workflow` + config impact + plan fingerprint |
| `portforge_project_provision` | MUTATE | `apply_workflow` (inspect→allocate→plan→apply→verify path) |
| `portforge_project_verify` | READ | workflow status + allocation verify |
| `portforge_project_rollback` | MUTATE | `config_manager.rollback_mutation` |
| `portforge_allocation_recommend` | READ | `CentralClient.get_recommendation` (+ preview) |
| `portforge_allocation_create` | MUTATE | `create_allocation` via adapter |
| `portforge_allocation_release` | MUTATE | `release_allocation` (**no** project config rewrite) |

### Explicitly ABSENT tools

- Any shell / terminal / PowerShell / bash / cmd
- Arbitrary HTTP, SQL, filesystem write, Docker, kubectl
- Admin bootstrap, enrollment, or agent credential management

### Mutation types reported in capabilities

**Supported automatic mutations:** `dotenv`, `compose`, `kubernetes-hostPort`,
`kubernetes-nodePort`.

**Unsupported automatic mutations (explicit):** `containerPort`, Service
`port`, Service `targetPort` — matches
`kubernetes_containerPort_auto` / `kubernetes_service_port_auto` /
`kubernetes_targetPort_auto` = `false` in the agent contract.

---

## 6. Approval model

| Class | Tools | Gate |
|-------|-------|------|
| READ | capabilities, inspect, verify, recommend | None |
| PLAN | project_plan | None (non-mutating) |
| MUTATE | provision, rollback, allocation_create, allocation_release | `confirm_mutate: true` required |

Missing or false `confirm_mutate` on a MUTATE tool → structured error
`MUTATION_NOT_APPROVED`.

This is an **explicit acknowledgement** by the calling coding agent / user
policy layer. It is **not** browser auth and does **not** involve Central
admin secrets. Local Central URL/token resolution reuses existing agent
environment semantics (`PORTFORGE_CENTRAL_URL`, agent credential store) —
MCP never returns those secrets in responses.

---

## 7. Project root confinement

All project-relative paths go through `config_files.resolve_within_root`.

Reject (structured `PATH_OUTSIDE_PROJECT` / `CONFIG_PATH_OUTSIDE_PROJECT`):

- `../` traversal
- Absolute paths that escape the project root
- Symlink escapes
- Alternate encoding escapes already covered by `resolve_within_root`

MCP must not implement a weaker parallel validator.

---

## 8. Plan / provision / stale protection

### Plan (`portforge_project_plan`)

1. Load + validate manifest (read-only).
2. Resolve host; call `prepare_workflow` (no allocation, no file writes of
   project config).
3. Fingerprint: SHA-256 of canonical manifest hash + per-config-target
   content hashes (via existing file helpers).
4. Persist plan record under
   `<project_root>/.portforge/mcp/plans/<plan_id>.json`
   (MCP metadata only — not a config mutation).
5. Return: requested allocations, proposed candidate ports, files/fields
   affected, before/after **intent**, verification plan, rollback guidance,
   `plan_id` / `plan_hash`.

### Provision (`portforge_project_provision`)

Required workflow (existing layer):

```text
inspect → allocate → plan → validate plan → apply → verify
```

Implemented by calling `apply_workflow` (same as `project provision`).

If `plan_id` is supplied:

1. Load MCP plan record.
2. Recompute fingerprint; on mismatch → **`CONFIG_CHANGED_SINCE_PLAN`**.
3. Do **not** silently regenerate and apply.

Agent must call `portforge_project_plan` again after edits.

### Verify / rollback / release separation (v1.4)

- **Verify:** read-only status checks.
- **Rollback:** restores project config only (`rollback_mutation`).
- **Allocation release:** releases Central allocation only — **never**
  rewrites project configuration.
- Config rollback does **not** release allocations.

---

## 9. Error model

Every tool failure returns MCP `tools/call` result with `isError: true` and
JSON text payload:

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

Map existing exceptions (`WorkflowError`, `ConfigError`, `ManifestError`,
`CentralResult`) without inventing parallel enums. MCP-specific additions:

| Code | When |
|------|------|
| `MUTATION_NOT_APPROVED` | MUTATE tool without `confirm_mutate: true` |
| `PATH_OUTSIDE_PROJECT` | Alias / map for confinement failures at MCP boundary |
| `INVALID_TOOL` | Unknown tool name (JSON-RPC / tools/call) |
| `INVALID_PARAMS` | Schema / required-arg failures |

Preserve (non-exhaustive): `CENTRAL_UNAVAILABLE`, `HOST_OFFLINE`,
`HOST_DECOMMISSIONED`, `ALLOCATION_UNAVAILABLE`, `CONFIG_CHANGED_SINCE_PLAN`,
`INVALID_PROJECT_MANIFEST` (mapped from `MANIFEST_*`), `VERIFICATION_FAILED`,
`ROLLBACK_FAILED` / `CONFIG_ROLLBACK_FAILED`, `WORKFLOW_*`, K8s codes.

Coding agents must decide on `error.code`, not prose.

---

## 10. Secret boundary

MCP responses must never include:

- Admin bootstrap token
- Agent credential / enrollment token
- Database password
- Authorization headers
- Internal secrets from env or agent store

Implementation: response scrubber applied to all serialized tool results
(deny-list patterns + known key names). Tests search serialized MCP frames
for secret fixtures.

Central URL may appear as a non-secret endpoint string; tokens must not.

---

## 11. Central and local agent interaction

| Concern | Behavior |
|---------|----------|
| Central | Tools that need network use `CentralClient` with existing URL resolution |
| Unavailable Central | Structured `CENTRAL_UNAVAILABLE` (or pass-through Central error) |
| Local agent | Same process as MCP server; discovery uses existing local modules where relevant |
| Offline / decommissioned hosts | Existing Central error codes pass through |
| Idempotency | `request_id` required on mutate allocate/provision; Central + workflow semantics unchanged |
| MCP process death | No Central-side leak beyond what CLI already guarantees (idempotent request_id; compensation in workflow) |

Development Central only for mutation tests — never production `:58000` /
`127.0.0.1:55432`.

---

## 12. Compatibility with CLI / JSON

| Surface | Change |
|---------|--------|
| `portforge capabilities --json` | `capabilities.mcp` → `true`; optional `mcp` block additive |
| Existing project / workflow / allocation commands | Unchanged |
| Machine schema / protocol / contract version | Remain `1` |
| Database | No migration |

Users who never enable MCP see only the capability flag flip.

---

## 13. Provider configuration

One server binary/module. Provider-specific **config examples only**:

- Antigravity MCP config
- Codex MCP config
- Claude Code MCP config

Placeholders: `/path/to/portforge` or `portforge` on `PATH`. Document
Windows / macOS / Linux differences in `docs/guides/mcp.md`.

---

## 14. Testing strategy

| Layer | Coverage |
|-------|----------|
| Unit / protocol | initialize, tools/list, tools/call framing; stderr vs stdout |
| Security | No shell tools; secret scrub; path / symlink / absolute escape |
| Workflow | capabilities → inspect → plan → provision → verify → rollback → release via MCP only |
| Failure | Central down, offline/decommissioned, no ports, bad manifest, stale plan, write/verify/rollback fail, malformed JSON-RPC, disconnect, restart |
| Concurrency | Concurrent MCP clients; no duplicate ports; idempotency; no leaks |
| Regression | Agent / backend / dashboard baselines; lint / typecheck / build / doctor |
| External agent style | Simulated coding-agent workflow over MCP (no paid provider APIs) |

Black-box E2E must speak real MCP frames — not call internal Python tools
directly as the primary proof.

---

## 15. Implementation layout

```text
agent/portforge_agent/mcp/
  __init__.py       # MCP_SCHEMA_VERSION, run_stdio_server
  server.py         # stdio JSON-RPC loop
  protocol.py       # message encode/decode helpers
  tools.py          # tool registry + handlers
  context.py        # Central client, manifest, project_root resolution
  errors.py         # exception → structured error
  scrub.py          # secret scrubber
  approval.py       # MUTATE gate
  plans.py          # MCP plan fingerprint persistence
```

CLI: `mcp` subparser → `serve`.

---

## 16. Freeze criteria

Phase 15 freezes when:

1. Design and guide docs are present and consistent with code.
2. MCP tools above are implemented and tested.
3. Security absences proven.
4. Regression suites meet or exceed v1.4.0 baselines.
5. Logical commits land on `feature/native-agent-mcp`.
6. No version bump, tag, release, or production deploy.

**STOP** after freeze. Next phase is separate.

---

## Decisions locked

| Decision | Choice |
|----------|--------|
| Transport | stdio JSON-RPC MCP subset |
| SDK dependency | None — minimal protocol in-tree (CLI works without MCP code path) |
| Approval | `confirm_mutate: true` on MUTATE tools |
| Plan stale check | MCP plan fingerprint + existing `CONFIG_CHANGED_SINCE_PLAN` on config apply |
| Release vs rollback | Preserved v1.4 separation |
| K8s auto-mutate | hostPort + Service nodePort only |
| Contract bump | No — additive `mcp: true` only |
| DB migration | No |
