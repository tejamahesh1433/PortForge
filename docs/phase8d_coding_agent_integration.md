# Phase 8D Coding-Agent Integration

PortForge exposes one provider-neutral contract, version 1, through `agent-contract`. No model API, provider SDK, interactive prompt, database access, or provider-specific allocation logic is used.

## Workflow

`workflow prepare` validates and resolves the manifest, returns advisory candidates and declared config-file impact, and performs no reservation or file mutation. `workflow apply` delegates allocation to Phase 8A, then delegates explicit dotenv/Compose mappings to Phase 8C. `workflow status` reads durable project-local state from `.portforge/workflows`.

A caller-selected request ID binds the normalized manifest, allocation, and optional mutation. An unchanged retry returns the persisted result without another allocation or file rewrite. Changed input fails with `WORKFLOW_IDEMPOTENCY_CONFLICT`.

If config fails after a newly created allocation, PortForge rolls back an applied mutation and releases that allocation. A reused allocation is preserved. Phase 8C changed-since-plan/apply checks remain authoritative. JSON commands emit one document to stdout and use structured errors.

## Manifest Initialization

`project init` creates a minimal valid manifest from explicit `--port name:purpose[:protocol]` values. `--stdout` is non-writing. Existing files are never overwritten; this phase intentionally has no `--force` and never guesses config mappings.

## Recovery and Limits

Read `workflow status`, then retry the same unchanged request or follow its recovery commands. Roll back config before releasing an allocation. Allocation remains host-scoped; equal numeric ports on different hosts are valid. Central does not perform remote bind probing. Provider-specific adapters are deferred until a real environment requires only a thin instruction wrapper.
