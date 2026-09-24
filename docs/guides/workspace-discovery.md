# Workspace Discovery Guide

PortForge can enter an unfamiliar development workspace, statically discover
services and host-relevant ports, detect conflicts, and produce **one
coordinated plan** — without executing project code.

Design: [`docs/design/workspace-discovery.md`](../design/workspace-discovery.md).
MCP: [`docs/guides/mcp.md`](mcp.md).

---

## Quick start

```bash
portforge project discover /path/to/repo --json
```

MCP (same semantics):

```json
{
  "name": "portforge_workspace_discover",
  "arguments": {
    "project_root": "/path/to/repo",
    "include_local_runtime": true
  }
}
```

Then plan:

```bash
portforge project plan --workspace --json
```

MCP:

```json
{
  "name": "portforge_project_plan",
  "arguments": { "workspace": true, "project_root": "/path/to/repo" }
}
```

Provision remains a **MUTATE** step (`confirm_mutate: true`) and reuses the
existing workflow/allocation pipeline.

---

## What discovery does

Static scan only (no `npm`, Compose up, Helm template, kubectl, shell):

| Source | Result |
|--------|--------|
| `.env` / `.env.example` / `.env.local` | Port-like keys only |
| Compose files | Host `ports:` mappings; `expose:` is container-only |
| Dockerfile `EXPOSE` | Evidence only — not host allocation |
| `package.json` | Safe structured patterns; complex scripts → AMBIGUOUS |
| Kubernetes YAML | Reports hostPort/nodePort (mutable) and containerPort/Service port/targetPort (non-mutable) |
| Helm `values*.yaml` | Read-only; not auto-mutable |
| `portforge.yml` | Authoritative PortForge intent |

Ignored directories include `.git`, `node_modules`, `.venv`, `venv`, `dist`,
`build`, `.next`, `coverage`, `target`, `vendor`, and similar.

---

## Classifications

| Class | Meaning | Auto-mutate? |
|-------|---------|--------------|
| EXPLICIT | Clear host-facing declaration | If mutation type supported |
| INFERRED | Strong convention | Only if unambiguous |
| AMBIGUOUS | Likely port; unsafe | **No** |
| UNSUPPORTED | Outside mutation policy | **No** |

**Discovered ≠ automatically mutable.**

Supported automatic mutations (unchanged): dotenv, Compose host ports,
Kubernetes `hostPort`, Service `nodePort`.

---

## Conflicts

| Kind | Meaning |
|------|---------|
| `INTERNAL_PROJECT_CONFLICT` | Two services claim same host port |
| `LOCAL_RUNTIME_CONFLICT` | Port in use locally |
| `CENTRAL_ALLOCATION_CONFLICT` | Port allocated to another project |
| `RESERVATION_CONFLICT` | Local reservation for another project |
| `CONFIGURATION_CONFLICT` | Inconsistent declarations / vs manifest |

Discovery still works when Central is unavailable (`central_available: false`).
Authoritative allocation planning then fails with `CENTRAL_UNAVAILABLE`.

---

## Coordinated plan

- **Minimal change:** free preferred ports are preserved
- **Atomic allocation:** existing batch/`request_id` semantics
- **Workspace fingerprint:** hashes only planned input files
- Stale inputs between plan and apply → `CONFIG_CHANGED_SINCE_PLAN` with
  `changed_paths` — no silent regenerate

Approval: discover = READ, plan = PLAN, provision = MUTATE.

---

## Secrets

PortForge may report `PORT=3000` as `{key: PORT, port: 3000}`.
It must **not** return passwords, API keys, tokens, or entire `.env` files.

---

## Verify / rollback

- Config verification does not execute arbitrary project runtime
- Distinguishes `CONFIG_VERIFIED` vs `RUNTIME_NOT_EXECUTED` where applicable
- Config rollback does **not** release allocations
- Allocation release does **not** rewrite configuration

---

## Limitations

- No Helm template / chart rewrite
- No automatic `containerPort` / Service `port` / `targetPort` mutation
- No AI inside discovery (deterministic parsers only)
- Bounded scan (depth/file caps) — not a full-repo semantic indexer

## Next: environments and targets

After discovery yields INTERNAL ports, use Phase 17 target-aware planning so
Windows/Mac development and Lenovo/HP production get independent HOST ports.

See [`environments-and-targets.md`](environments-and-targets.md).
