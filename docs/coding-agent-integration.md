# Provider-neutral coding-agent integration

PortForge exposes a provider-neutral local CLI. Any automation system can use
the same commands; no caller identity or AI-provider metadata is required.

## Safe workflow

1. Inspect the project: portforge project status --json
2. Validate the manifest: portforge project validate --json
3. Create an allocation with explicit arguments or the project manifest:
   portforge allocate ... --json
4. Read the allocation: portforge allocation get ALLOCATION_ID --json
5. Create a configuration plan:
   portforge config plan MANIFEST --allocation ALLOCATION_ID --json
6. Review the returned mutation ID, target files, fingerprints, and port changes.
7. Apply the exact persisted plan:
   portforge config apply MANIFEST --allocation ALLOCATION_ID --json
8. Verify the allocation:
   portforge allocation verify ALLOCATION_ID --json
9. Use portforge config status or config rollback when recovery is needed.

Project validation and status are read-only. Allocation create/release and
config apply mutate state. Config plan is read-only from the project-file
perspective but persists a local plan record. Verification may create probe
records while leaving allocation and configuration lifecycle state unchanged.

## Automation rules

- Never guess a port or edit configuration before allocation succeeds.
- Always create and inspect a config plan before applying it.
- Apply only the same persisted plan; stale fingerprints are rejected.
- Treat verified_free as an observation, not a future guarantee.
- A failed verification does not release an allocation.
- Config rollback does not release an allocation, and allocation release does not
  rewrite configuration.
- Use allocation IDs, request IDs, mutation IDs, and probe IDs for correlation.
- Never expose environment values, credentials, authorization headers, or full
  configuration files.
- Do not use PortForge for arbitrary remote commands or remote source editing.

## Machine output and recovery

Use --json for automation. Successful commands emit JSON on stdout; errors use
the existing error.code, error.message, and error.details structure.
Human-readable output remains separate.

Exit codes are intentionally small:

- 0: success or successful idempotent replay
- 1: expected domain/resource/conflict failure
- 2: invalid request, manifest/configuration error, or operational setup failure

After a lost response, inspect project status and allocation list/get before
retrying. Repeating an allocation request with the same request ID is
idempotent. Repeating release and verification is safe according to their
existing lifecycle semantics. Repeating an applied config mutation does not
rewrite files; a changed plan fails stale-file protection.

## Boundary

The CLI is the local/project automation boundary. Central REST APIs remain the
fleet boundary. Local config files are not mutated through Central, and no SSH
fallback or arbitrary remote execution is provided.
