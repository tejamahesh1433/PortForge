# Workspace Discovery + Coordinated Port Planning (Phase 16)

Deterministic, static workspace discovery that feeds the **existing**
PortForge planning/provisioning pipeline. No AI/LLM inside discovery.
No code execution. No parallel allocation/mutation engines.

Baseline: Phase 15 freeze `b2285af` (PortForge **v1.4.0** source).
Protocol / contract / machine schema / MCP schema remain **1** (additive only).
No database migration.

Related: [`mcp-integration.md`](mcp-integration.md),
[`coding-agent-integration.md`](coding-agent-integration.md),
[`project-stack-configuration.md`](project-stack-configuration.md).

---

## 1. Goals and non-goals

### Goals

- Enter an unfamiliar development workspace and safely discover:
  - services
  - externally exposed / host-relevant ports
  - configuration sources
  - conflicts (project / local / Central / reservation / config)
- Produce **one coordinated plan** that reuses existing
  `prepare_workflow` / `apply_workflow` / batch allocation / config mutation.
- Expose discovery via CLI and MCP with **semantic parity**.
- Physically validate MCP with Antigravity, Codex, and Claude Code
  (or record `EXTERNAL BLOCKED` with exact limitation).

### Non-goals

- Executing project code, npm scripts, Compose, Helm template, kubectl, Terraform
- Automatic mutation of `containerPort`, Service `port`, `targetPort`
- Automatic Helm chart rewriting
- Provider-specific PortForge forks
- Protocol/contract/MCP schema bumps
- Production mutation

---

## 2. Architecture

```
portforge project discover [--json]
portforge_workspace_discover (MCP READ)
        │
        ▼
workspace.discover(project_root)
        │  static walk + parsers
        ▼
WorkspaceModel (services, ports, evidence, conflicts, fingerprint inputs)
        │
        ├── (optional) enrich with Central list_allocations
        ├── (optional) enrich with local discover_all_ports / reservations
        │
        ▼
workspace.plan(model) → CoordinatedPlan
        │  minimal-change + conflict resolution
        │  builds / updates portforge.yml intent OR feeds NormalizedRequest
        ▼
existing pipeline:
  prepare_workflow / apply_workflow / config_manager / CentralClient
```

**Rule:** Discovery and workspace planning are **new orchestration** only.
Allocation, mutation, verification, rollback stay in existing modules.

---

## 3. CLI / MCP surface

| Surface | Name | Class |
|---------|------|-------|
| CLI | `portforge project discover [path] [--json] [--url]` | READ |
| CLI | `portforge project plan` (existing) + workspace-aware when no manifest / `--workspace` | PLAN |
| MCP | `portforge_workspace_discover` | READ |
| MCP | `portforge_project_plan` | PLAN — additive optional `mode: workspace` / auto when no manifest |

Additive contract fields (no version bump):

```json
"capabilities": {
  "workspace_discover": true,
  "workspace_plan": true
}
```

Existing nine Phase 15 MCP tools remain unchanged in name/semantics.

---

## 4. Static discovery targets

| Target | Action |
|--------|--------|
| `.env`, `.env.example`, `.env.local` | Port-key extraction only |
| `docker-compose.yml/.yaml`, `compose.yml/.yaml` | Static Compose parse |
| `Dockerfile`, `Dockerfile.*` | `EXPOSE` as evidence only |
| `package.json` | Safe structured port patterns; scripts → AMBIGUOUS if shell-like |
| Kubernetes YAML | Report hostPort/nodePort/containerPort/port/targetPort; mutate only hostPort/nodePort |
| Helm `values*.yaml` | Read-only obvious port keys; **no** `helm template` |
| `portforge.yml` / `portforge.yaml` | Authoritative PortForge intent; conflicts reported, never silently overridden |

### Ignore / bounds

Downward walk under project root. Skip directories:

`.git`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `.next`, `coverage`,
`target`, `vendor`, `__pycache__`, `.tox`, `.mypy_cache`, `.pytest_cache`,
`.portforge` (except reading existing plans when needed)

Bounds: max depth (default 8), max files considered (default 2000),
max file size for parse (reuse existing size limits where present).

All relative reads via `resolve_within_root`.

---

## 5. Normalized workspace model

```text
WorkspaceModel
  project_root
  central_available / central_error
  services[]:
    name, type?, source_paths[], port_requirements[], dependencies[], confidence, evidence[]
  port_requirements[]:
    service?, port?, protocol, role (host|container|unknown)
    classification: EXPLICIT | INFERRED | AMBIGUOUS | UNSUPPORTED
    mutable: bool   # discovery support ≠ mutation support
    evidence[]
  conflicts[]:
    kind: INTERNAL_PROJECT_CONFLICT | LOCAL_RUNTIME_CONFLICT |
          CENTRAL_ALLOCATION_CONFLICT | RESERVATION_CONFLICT | CONFIGURATION_CONFLICT
    severity, parties[], port?, message, details
  files_considered / files_parsed / ignored_directories / duration_ms
  fingerprint_inputs[]  # relative paths that feed workspace fingerprint
  existing_manifest?    # summary if portforge.yml present
```

Service types (optional): `frontend`, `backend`, `database`, `cache`, `queue`,
`worker`, `proxy`, `other`. Unknown is valid.

---

## 6. Evidence and confidence

| Classification | Meaning | Auto-manage? |
|----------------|---------|--------------|
| EXPLICIT | Clear host-facing port declaration | Eligible if mutation type supported |
| INFERRED | Strong framework/convention pattern | Eligible only if mapping is unambiguous |
| AMBIGUOUS | Likely port; unsafe to decide | **Never** auto-mutate |
| UNSUPPORTED | Seen but outside mutation policy (e.g. containerPort-only, Helm-only) | **Never** auto-mutate |

Deterministic confidence: derived from evidence class + parser certainty
(e.g. `high` / `medium` / `low`). No LLM.

### Compose nuance

- `ports: ["8000:8000"]` → host mapping EXPLICIT
- `expose: ["8000"]` → container intent only; **not** host allocation

### Dockerfile

- `EXPOSE 8000` → evidence only; not host allocation

### Kubernetes

- Automatic mutation still only: **hostPort**, Service **nodePort**
- Discovery may report containerPort / Service port / targetPort as
  UNSUPPORTED for mutation

---

## 7. Secret handling

`.env` scan extracts **port-related keys only** (name matches
`/(^|_)PORT(_|$)/i`, `HOST_PORT`, known compose-style `*_PORT`, or value that
is solely an integer 1–65535 **and** key looks port-like).

Never serialize:

- `DATABASE_PASSWORD`, `API_KEY`, `TOKEN`, `SECRET`, …
- Entire `.env` contents
- Unrelated values adjacent to port keys

MCP scrubber remains in place.

---

## 8. Conflict graph

| Kind | Source |
|------|--------|
| `INTERNAL_PROJECT_CONFLICT` | Two workspace services claim same host port |
| `LOCAL_RUNTIME_CONFLICT` | Preferred port held by local listener (`discover_all_ports` / evaluate) |
| `CENTRAL_ALLOCATION_CONFLICT` | Preferred port held by active Central allocation (other project) |
| `RESERVATION_CONFLICT` | Preferred port reserved locally for another project |
| `CONFIGURATION_CONFLICT` | Same logical service declares inconsistent ports across files, or conflicts with existing `portforge.yml` |

Discovery works offline: Central unavailable → `central_available: false`;
conflict kinds that need Central are omitted or marked unavailable.
Authoritative fleet planning still fails with `CENTRAL_UNAVAILABLE` when
allocation is required.

---

## 9. Coordinated plan

### Minimal change

If preferred host port is free and allowed → **preserve**.
Only recommend replacement on conflict / policy / explicit request.

### Atomicity

Multi-service allocation uses existing batch allocation (`request_id` +
multi-port body). All or nothing.

### Plan contents

For each service/port:

- `current_port` / `proposed_port`
- `reason.code` (e.g. `LOCAL_RUNTIME_CONFLICT`) + safe metadata
- mutation targets (dotenv / compose / k8s hostPort|nodePort) if applicable
- `plan_id` / `workspace_fingerprint`

### Workspace fingerprint

SHA-256 of canonical JSON over:

- relative paths of fingerprint_inputs
- content hash of each (or MISSING)
- normalized discovered host-port intents (sorted)

Does **not** hash `node_modules` / vendor trees.

Extend MCP plan persistence: if workspace mode, fingerprint includes all
planned input files; any change → `CONFIG_CHANGED_SINCE_PLAN` with
`details[].path` of changed inputs. No silent regenerate.

### Approval

Discovery = READ. Workspace plan = PLAN. Provision = MUTATE (`confirm_mutate`).

---

## 10. Verify / rollback

After apply:

- Expected files/fields changed only
- Allocated ports match plan
- Config parses
- Distinctions: `CONFIG_VERIFIED` vs `RUNTIME_NOT_EXECUTED`

Rollback: existing multi-mutation rollback semantics; config rollback does
**not** release allocation; release does **not** rewrite config.

---

## 11. Module layout

```text
agent/portforge_agent/workspace/
  __init__.py
  models.py
  ignore.py
  walk.py
  discover.py          # orchestrator
  extract/
    dotenv.py
    compose.py
    dockerfile.py
    package_json.py
    kubernetes.py
    helm.py
    manifest.py
  conflicts.py
  plan.py              # coordinated plan builder → feeds existing pipeline
  fingerprint.py
```

CLI: `_cmd_project_discover` → `workspace.discover`.
MCP: thin handler → same function.

---

## 12. Fixtures (disposable)

Under `fixtures/workspace/`:

| ID | Scenario |
|----|----------|
| A | Simple frontend + API |
| B | Frontend + API + Postgres + Redis |
| C | Compose stack |
| D | dotenv-driven |
| E | Kubernetes |
| F | Mixed Compose + dotenv |
| G | Existing PortForge manifest |
| H | Contradictory configuration |
| I | Malformed configuration |
| J | Nested monorepo |

---

## 13. Provider validation

Same `portforge mcp serve` for Antigravity, Codex, Claude Code.

Evidence required: actual tool invocations on a disposable project.
If client cannot register MCP: `EXTERNAL BLOCKED` with exact reason —
not a PortForge defect.

---

## 14. Compatibility decisions (locked)

| Item | Decision |
|------|----------|
| Protocol / contract / machine / MCP schema | Stay **1**; additive capabilities/tools only |
| DB migration | **NO** |
| Mutation types | Unchanged (dotenv, compose, k8s hostPort/nodePort) |
| Discovery vs mutation | Discovered ≠ automatically mutable |
| Static only | No project code execution |

---

## 15. Freeze criteria

Implementation + fixtures + tests + docs + provider evidence (or EXTERNAL BLOCKED)
+ full regression green + logical commits + clean tree.
No version / tag / release / deploy / production touch.
