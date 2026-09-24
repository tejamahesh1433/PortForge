# Environment + Deployment Target + Ingress (Phase 17)

Target-aware port management so the same project can develop on Windows/Mac
and deploy to Lenovo/HP with **stable internal ports** and **independent host
ports**, plus a **read/plan-only ingress model**.

Baseline: Phase 16 freeze `e55c56a` (PortForge **v1.4.0** source).
Protocol / contract / machine schema / MCP schema remain **1** (additive).
**Database migration: NO** (see §15).

Related: [`workspace-discovery.md`](workspace-discovery.md),
[`mcp-integration.md`](mcp-integration.md),
[`project-stack-configuration.md`](project-stack-configuration.md).

---

## 1. Goals and non-goals

### Goals

- Named **environments** (development / staging / production + custom)
- **Deployment targets** bound to immutable host UUID
- Explicit port **scopes**: INTERNAL / HOST / INGRESS
- Target-aware plan/allocate/provision from a coding agent on any machine
- Same project on multiple targets without overwriting allocations
- Environment-specific config overrides (minimal mutation)
- Ingress modeled for plan/validation — not proxy/DNS/TLS management

### Non-goals

- Automatic reverse-proxy install/configure/restart
- DNS, TLS issuance, firewall, UPnP, security groups
- Hidden “best production host” selection
- Automatic `containerPort` / Service `port` / `targetPort` mutation
- Large dashboard surface
- DB migration / protocol bumps

---

## 2. Conceptual hierarchy

```text
Project
 ├─ Environment (development | staging | production | custom)
 │    └─ Deployment Target (host_id UUID + optional alias)
 │         ├─ Service mappings
 │         │    INTERNAL port (app/container intent — stable)
 │         │    HOST port (allocated on that host)
 │         └─ optional Ingress bindings (public → target host port)
```

| Concept | Meaning |
|---------|---------|
| **Project** | Logical application (`portforge.yml` `project`) |
| **Environment** | Named stage; does **not** imply a host |
| **Deployment Target** | A PortForge host identity (`host_id`) for that environment |
| **Service** | Named port requirement (frontend, api, …) |
| **Internal Port** | Application/container listen intent |
| **Host Port** | Port reserved/allocated on the target host |
| **Ingress Port** | External listener mapping (model only) |
| **Allocation** | Central allocation for `(project, host_id)` with namespaced `request_id` |
| **Configuration Binding** | Env/target-specific dotenv/compose/k8s file mappings |

---

## 3. Port scopes

| Scope | Example | Mutate? |
|-------|---------|---------|
| `INTERNAL` | FastAPI `8000` | Prefer **never** change for target conflicts |
| `HOST` | Lenovo `18000` → container `8000` | Target-specific allocation + Compose published / k8s hostPort|nodePort |
| `INGRESS` | `443` → Lenovo `18000` | Plan/discover only this phase |

Never treat scopes as equivalent.

Compose proof pattern:

```text
Windows:  8000:8000
Lenovo:  18000:8000
HP:      28000:8000
```

Container side stays `8000`.

---

## 4. Manifest extension (additive)

Optional sections (unknown keys remain rejected except these additions):

```yaml
version: 1
project: myapp
target:
  host: windows-dev   # default / legacy single-host

environments:
  development:
    targets:
      windows:
        host_id: "<uuid>"   # authoritative
        # host: "NTMKEYA"   # optional display / resolve aid
      macbook:
        host_id: "<uuid>"
  production:
    targets:
      lenovo-prod:
        host_id: "<uuid>"
      hp-prod:
        host_id: "<uuid>"

ports:
  api:
    purpose: api
    protocol: tcp
    preferred: 8000          # INTERNAL preferred; may also be HOST preferred on a target
    internal: 8000           # optional explicit INTERNAL (defaults to preferred)

config:
  dotenv:
    - file: .env
      values: { API_PORT: api }
  # Environment overrides via alternate files (preferred pattern):
  # dotenv: [{ file: .env.production, values: { API_HOST_PORT: api } }]

ingress:                     # optional READ/PLAN model
  - name: api-public
    scheme: https
    hostname: example.test
    public_port: 443
    service: api
    environment: production
    target: lenovo-prod
    # status derived: INGRESS_PLANNED | TARGET_BOUND | EXTERNAL_NETWORK_UNVERIFIED
```

**Alias rule:** `lenovo-prod` is a friendly name; **`host_id` is identity**.

Legacy manifests without `environments` keep working (`target.host` only).

---

## 5. Allocation / Central (no migration)

Central already stores allocations as `(project, host_id, request_id, …)`.

Phase 17 agent conventions:

1. **Target host** = `host_id` passed to `create_allocation` (caller host ≠ target).
2. **Namespaced `request_id`:**  
   `{environment}:{target_alias_or_host}:{logical_request_id}`  
   so the same logical id on Windows vs Lenovo never collides under global `UNIQUE(request_id)`.
3. Environment name lives in agent plan records + namespaced request_id — **not** a Central column.

Release of `production/Lenovo` uses that allocation id only — does not touch Windows/HP.

### Migration decision: **NO**

Forced only if we later need Central-side env/target columns or composite
`request_id` uniqueness. Not required for Phase 17 semantics.

---

## 6. Target-aware planning

Inputs: project root / manifest, `environment`, `target` (alias or host UUID).

Pipeline:

1. Resolve target → `host_id` (reject unknown / invalid UUID / decommissioned).
2. Discover workspace (Phase 16) for INTERNAL intents.
3. Build HOST requirements: prefer INTERNAL as published host port when free
   **on the target**; else recommend replacement on **that host only**.
4. Conflict keys: `(host_id, port, protocol, scope=HOST)`.
5. Persist plan with `environment`, `target`, `host_id`, fingerprint.
6. Apply → existing `apply_workflow` / batch allocation against **target** client host.

### Errors (additive)

| Code | When |
|------|------|
| `PLAN_TARGET_MISMATCH` | Apply plan_id recorded for different host/env |
| `UNKNOWN_TARGET` | Alias/UUID not resolvable |
| `INVALID_HOST_ID` | Malformed UUID |
| `HOST_DECOMMISSIONED` | Existing Central semantics |
| `HOST_OFFLINE` / stale | Existing safe allocation semantics |
| `CONFIG_CHANGED_SINCE_PLAN` | Fingerprint mismatch (includes env/target inputs) |

---

## 7. Minimal mutation

Prefer overrides:

```text
API_INTERNAL_PORT=8000          # stable
API_HOST_PORT=18000             # production override file
```

Compose: change **published** only.  
Do **not** rewrite internal/container port to the host port.

K8s: hostPort / nodePort only.

---

## 8. Ingress model (READ/PLAN)

Structured ingress object:

- scheme, hostname, public_port
- environment, target, service
- bound_host_port (from plan/allocation when known)
- status: `INGRESS_PLANNED` | `TARGET_BOUND` | `EXTERNAL_NETWORK_UNVERIFIED`

**Never** claim `PUBLIC_READY` / Internet reachability without external evidence
(DNS/TLS/firewall) — those remain external requirements.

Optional static discovery of Nginx/Caddy/Traefik configs — parse only; **no**
proxy mutation, install, or restart.

---

## 9. CLI / MCP (additive)

### CLI

```text
portforge project plan --environment production --target lenovo-prod [--json]
portforge project provision --environment production --target lenovo-prod --request-id ID [--json]
portforge project discover --json   # unchanged Phase 16
```

Also accept `--target-host-id <uuid>`.

### MCP

Extend existing tools additively:

- `portforge_project_plan`: `environment`, `target`, `target_host_id`
- `portforge_project_provision`: same + `confirm_mutate`
- `portforge_workspace_discover`: unchanged
- Optional READ `portforge_ingress_plan` **or** include `ingress` block in plan result (prefer embed to avoid tool sprawl)

Capabilities flags: `environment_targets: true`, `ingress_plan: true`.

Phase 15/16 tools keep working without these args.

---

## 10. Coding-agent workflow

1. Discover workspace → INTERNAL ports  
2. Plan `development` / Windows target → HOST allocations  
3. Develop  
4. Plan `production` / Lenovo → independent HOST allocations  
5. Review / approve  
6. Provision supported config  
7. Verify config (not Internet)

Agent must not guess remote availability — Central + target host evidence only.

---

## 11. Stale / mismatch / idempotency

Plan record includes: `environment`, `target`, `host_id`, workspace fingerprint,
intent summary.

Apply checks:

- fingerprint fresh
- `PLAN_TARGET_MISMATCH` if env/host differs

Idempotent: same `{environment, target, logical request_id}` → same namespaced
`request_id` → Central replay.

---

## 12. Module layout

```text
agent/portforge_agent/targets/
  __init__.py
  models.py          # Environment, TargetRef, PortScope, IngressBinding
  resolve.py         # alias → host_id, validation
  plan.py            # target-aware coordinated plan
  request_id.py      # namespacing
  ingress.py         # ingress model + optional static proxy parse
  config_overrides.py # select env-specific file mappings
```

Reuse: `workspace.discover`, `workflow.*`, `compose_editor` (host side only),
`mcp/plans` fingerprint extension.

---

## 13. Fixtures / E2E

Disposable fictional hosts (UUIDs), not production:

- Windows development
- Mac development  
- Lenovo production
- HP production

Same INTERNAL ports; forced different HOST conflicts per target.

Ingress fixture: `example.test:443` → Lenovo host port → api INTERNAL 8000
(structured only).

---

## 14. Security / freeze

Preserve MCP absences (shell/HTTP/SQL/arbitrary FS). No admin/agent/enrollment
secrets. No privileged proxy changes.

Freeze: logical commits, full regression, clean tree. No version/tag/release/deploy.

---

## 15. Decisions locked

| Item | Choice |
|------|--------|
| DB migration | **NO** |
| Host identity | UUID |
| Environment → host | Explicit target; env alone insufficient |
| Internal vs host | Scopes separated; compose published-only |
| Ingress | Plan/discover only |
| Contract/MCP schema | Stay **1**, additive |
| Auto host selection | **Forbidden** this phase |
