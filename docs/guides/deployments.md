# Deployments (Phase 18)

Constrained **Docker Compose** deployment to a PortForge target host, driven
from CLI/MCP (often on Windows/Mac) through Central typed jobs.

Design: [`docs/design/deployment-orchestration.md`](../design/deployment-orchestration.md)  
Schema: [`docs/design/deployment-schema-proposal.md`](../design/deployment-schema-proposal.md)

---

## Workflow

```text
discover → target plan (Phase 17) → deployment plan (read-only)
        → explicit approval → apply
        → agent: fetch package → checksum → extract → compose validate → up
        → health verify → SUCCEEDED | FAILED (+ rollback if known-good exists)
```

## Commands (conceptual)

```bash
portforge deployment plan --environment production --target lenovo-prod --json
portforge deployment apply --request-id deploy-1 ... --json   # MUTATE
portforge deployment status --id <uuid> --json
portforge deployment rollback --id <uuid> --json              # MUTATE
```

MCP mirrors these with `confirm_mutate` on apply/rollback.

## Security

- **No** generic shell / SSH / arbitrary Docker or Compose argv
- Package: HTTPS URI + `package_sha256` + inner `package_manifest_sha256`
- Paths confined under PortForge data `deployments/`
- Secrets never packaged or logged
- Ingress/DNS/TLS remain external requirements

## Host lifecycle

| Action | History |
|--------|---------|
| Decommission | Retained |
| Remove Record | CASCADE deleted |

## Compatibility

Protocol/contract/MCP schema stay **1**. v1.4 agents ignore `pending_deployment`.
