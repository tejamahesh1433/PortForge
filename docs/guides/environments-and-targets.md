# Environments and Deployment Targets

PortForge separates **where you develop** from **where you deploy**.

Same application ports (INTERNAL) can map to different HOST ports on Windows,
Mac, Lenovo, and HP — without rewriting container/application listen ports.

Design: [`docs/design/environment-targets.md`](../design/environment-targets.md).

---

## Core ideas

| Term | Meaning |
|------|---------|
| **Environment** | `development`, `staging`, `production`, or custom. Does **not** pick a host by itself. |
| **Deployment target** | A PortForge host identified by **UUID** (`host_id`). Aliases are labels only. |
| **INTERNAL port** | App/container intent (e.g. FastAPI `8000`). Prefer stable across targets. |
| **HOST port** | Port allocated on that specific host (e.g. Lenovo `18000`). |
| **INGRESS port** | Public mapping model (e.g. `443` → Lenovo `18000`). Plan only — not Internet proof. |

```text
Windows/Mac  →  development target  →  HOST ports often == INTERNAL
Lenovo/HP    →  production target   →  HOST ports may differ; INTERNAL stays
Public       →  ingress (planned)   →  reverse proxy / DNS / TLS are external
```

---

## Manifest sketch

```yaml
version: 1
project: myapp
target:
  host: windows-dev

environments:
  development:
    targets:
      windows:
        host_id: "11111111-1111-1111-1111-111111111111"
  production:
    targets:
      lenovo-prod:
        host_id: "33333333-3333-3333-3333-333333333333"
      hp-prod:
        host_id: "44444444-4444-4444-4444-444444444444"

ports:
  api:
    purpose: api
    preferred: 8000
    internal: 8000

ingress:
  - name: api-public
    scheme: https
    hostname: example.test
    public_port: 443
    service: api
    environment: production
    target: lenovo-prod
```

---

## CLI

```bash
# Discover INTERNAL intents (Phase 16)
portforge project discover --json

# Windows development
portforge project plan --environment development --target windows --json

# Lenovo production (caller may be on Windows — PortForge checks Lenovo)
portforge project plan --environment production --target lenovo-prod --json

# HP production
portforge project plan --environment production --target hp-prod --json

# Provision (MUTATE — requires acknowledgement on MCP)
portforge project provision \
  --environment production \
  --target lenovo-prod \
  --request-id deploy-1 \
  --json
```

Also: `--target-host-id <uuid>`.

`request_id` is namespaced as `{environment}:{target}:{logical-id}` so the same
logical id on Windows and Lenovo never collides.

---

## Compose

Correct distinction:

```text
HOST:CONTAINER
8000:8000     # Windows
18000:8000    # Lenovo
28000:8000    # HP
```

PortForge mutates the **host/published** side. Container/internal stays stable.

Prefer environment override files (e.g. `.env.production` with `API_HOST_PORT`)
instead of rewriting `API_INTERNAL_PORT`.

---

## Kubernetes

Unchanged safety:

- Auto: `hostPort`, Service `nodePort`
- Never auto: `containerPort`, Service `port`, `targetPort`

---

## Ingress boundary

PortForge may **plan** ingress (scheme, hostname, public port → target host port).

Statuses:

- `INGRESS_PLANNED`
- `TARGET_BOUND`
- `EXTERNAL_NETWORK_UNVERIFIED`

PortForge does **not** in this phase:

- install/configure/restart Nginx/Caddy/Traefik
- manage DNS or TLS certificates
- open firewalls / claim Internet reachability

---

## MCP

Same server: `portforge mcp serve`.

Additive args on existing tools:

- `portforge_project_plan`: `environment`, `target`, `target_host_id`
- `portforge_project_provision`: same + `confirm_mutate`

Capabilities: `environment_targets`, `ingress_plan`.

Stale / mismatch:

- `CONFIG_CHANGED_SINCE_PLAN`
- `PLAN_TARGET_MISMATCH` (Lenovo plan cannot apply to HP)

---

## Examples

### Windows development → Lenovo production

1. Discover workspace  
2. Plan `development` / `windows`  
3. Develop against Windows HOST ports  
4. Plan `production` / `lenovo-prod` (remote allocation — Lenovo state)  
5. Review INTERNAL unchanged + new HOST ports  
6. Approve and provision supported config  
7. Ingress remains planned until external DNS/TLS/proxy are done outside PortForge  

### Mac development → HP production

Same flow with `macbook` / `hp-prod` targets. Allocations are independent;
releasing HP does not release Mac or Lenovo.

---

## Release and rollback

- Release one target allocation only  
- Rollback one environment/target config only  
- Config rollback ≠ allocation release (and vice versa)
