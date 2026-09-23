# Project Stack Configuration

Phase 12 design — end-to-end workflow for manifest-driven stacks reusing Phase 8B–8D building blocks. No new orchestration layer.

## Building blocks (by phase)

| Phase | Module / API | Role |
|-------|--------------|------|
| 8B | [`agent/portforge_agent/manifest.py`](../../agent/portforge_agent/manifest.py), `project validate/plan/allocate/status` | `portforge.yml` schema, host resolution, advisory plan |
| 8A | [`backend/app/services/allocation_service.py`](../../backend/app/services/allocation_service.py), `POST /api/allocations` | Atomic multi-port allocation |
| 8C | [`agent/portforge_agent/config_manager.py`](../../agent/portforge_agent/config_manager.py), `config plan/apply/status/rollback` | Safe dotenv / Compose / Kubernetes file mutation |
| 8D | [`agent/portforge_agent/workflow.py`](../../agent/portforge_agent/workflow.py), `workflow prepare/apply/status` | Sequences 8A then 8C with workflow idempotency |

See also [`docs/phase8b_project_manifest.md`](../phase8b_project_manifest.md), [`docs/phase8c_safe_config.md`](../phase8c_safe_config.md), [`docs/phase8d_agent_integration_audit.md`](../phase8d_agent_integration_audit.md).

## Recommended workflow

```
inspect  →  plan  →  allocate  →  mutation-plan  →  validate  →  apply  →  verify  →  rollback (optional)
```

CLI mapping:

| Step | Command | Mutates? |
|------|---------|----------|
| inspect | `portforge project inspect [--json]` | No — combines manifest status + config target summary |
| plan | `portforge project plan` or `portforge workflow prepare` | No — advisory candidates |
| allocate | `portforge project allocate` or inside `workflow apply` | Yes — Central reservations only |
| mutation-plan | `portforge config plan --allocation ID` | No — persists plan record under `.portforge/` |
| validate | `portforge project validate` | No |
| apply | `portforge config apply` or `portforge workflow apply` / `project provision` | Yes — project files (config apply only) |
| verify | `portforge allocation get ID`, `portforge config status MUTATION` | No |
| rollback | `portforge config rollback MUTATION` | Yes — restores original file bytes |

Dry-run provisioning: `portforge project provision --dry-run` calls `prepare_workflow()` only — no allocation, no file writes, no workflow record.

## CONFIG ROLLBACK ≠ ALLOCATION RELEASE

These are **independent lifecycle operations**:

- **Config rollback** ([`config_manager.rollback_mutation()`](../../agent/portforge_agent/config_manager.py)) restores exact pre-apply bytes for declared files. It never calls Central or releases reservations.
- **Allocation release** (`DELETE /api/allocations/{id}` / `portforge allocation release`) frees ports on the host. It never rewrites project files.

After a successful `workflow apply`, the JSON result's `recovery` block includes both commands when applicable. Roll back config **before** releasing the allocation if you need to undo file changes while keeping ports reserved temporarily.

Workflow compensation (config failure after a **new** allocation) may release the allocation — that is workflow-owned cleanup, not config rollback semantics.

## Manifest conventions

### `ports:` / services

Each key under `ports:` is a **service name** referenced by config mappings via `allocation: <name>` or dotenv value references.

```yaml
ports:
  frontend: {purpose: frontend, protocol: tcp, preferred: 3000}
  api:      {purpose: api, protocol: tcp}
```

### Config mappings

- **dotenv** — `values: ENV_VAR: <ports-key>` ([`config_manager`](../../agent/portforge_agent/config_manager.py) dotenv editor)
- **compose** — `services.<svc>.ports[].allocation` + `container` match port ([`compose_editor`](../../agent/portforge_agent/compose_editor.py))
- **kubernetes** — see k8s rule below ([`k8s_editor`](../../agent/portforge_agent/k8s_editor.py))

Reference fixture: [`fixtures/sample-stack/portforge.yml`](../../fixtures/sample-stack/portforge.yml).

## Operational rules

### Stale plan

`config apply` refuses when on-disk files changed since `config plan` (`CONFIG_CHANGED_SINCE_PLAN`). Re-plan against the current allocation, then apply.

### Multi-file rollback

One mutation ID covers every file touched in that apply. `config rollback` restores **all** backed-up files atomically from the mutation record under `.portforge/mutations/`.

### Kubernetes: hostPort / nodePort only

PortForge mutates only explicitly mapped fields:

| Field | Mutated? |
|-------|----------|
| `hostPort` (Deployment/DaemonSet, matched by `containerPort`) | Yes, when declared in manifest `hostPorts` |
| `nodePort` (Service type NodePort, matched by `servicePort`) | Yes, when declared in manifest `nodePorts` |
| `containerPort` | **No** — match key only |
| Service `port` | **No** |
| `targetPort` | **No** |

Enforced in [`k8s_editor.py`](../../agent/portforge_agent/k8s_editor.py); contract flags `kubernetes_containerPort_auto`, `kubernetes_service_port_auto`, `kubernetes_targetPort_auto` are **false**.

## Schema

**Migration: NONE** for workflow/config layers — state is project-local (`.portforge/workflows/`, `.portforge/mutations/`).
