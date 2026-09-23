# Multi-Port Stack Allocation

Phase 11 design — documents the **existing** Phase 8A batch allocation API. No parallel stack system is introduced.

Baseline: Protocol **1**, agent contract **1** unchanged. See [`docs/phase8a_agent_allocation.md`](../phase8a_agent_allocation.md) and [`backend/app/services/allocation_service.py`](../../backend/app/services/allocation_service.py).

## POST /api/allocations IS the batch/stack API

An **Allocation** is an atomic multi-port bundle: one `allocations` row (`Allocation.id` is the batch ID), one or more linked `central_reservations` rows, created and released together.

| Route | Handler | Notes |
|-------|---------|-------|
| `POST /api/allocations` | [`backend/app/api/allocations.py`](../../backend/app/api/allocations.py) `create_allocation` | Primary entry |
| `POST /api/allocations/batch` | Same handler (alias) | Optional discoverability alias; identical schema and behavior |

CLI equivalents: `portforge allocate`, `portforge project allocate` — both delegate to `central_client.create_allocation()` via [`agent/portforge_agent/project_adapter.py`](../../agent/portforge_agent/project_adapter.py) `to_allocation_body()`.

## Semantics (reused from Phase 8A)

### Atomicity

All ports in the bundle succeed or none commit. Implemented in [`allocation_service.create_allocation()`](../../backend/app/services/allocation_service.py) inside a single DB transaction with host-scoped advisory lock ([`backend/app/services/host_lock.py`](../../backend/app/services/host_lock.py)).

### Idempotency

Caller-supplied `request_id` + canonical payload hash. Replay returns the same allocation with `idempotent_replay: true` (HTTP 200). Changed payload under the same key → `IDEMPOTENCY_CONFLICT` (409). See [`backend/app/repositories/allocation_repository.py`](../../backend/app/repositories/allocation_repository.py).

### Coordinated recommendation

Each bundle member is resolved via the same recommendation path used by `GET /api/hosts/{id}/recommendations` — coordinated inside the allocation transaction, not as independent per-port POSTs.

### Batch release

`DELETE /api/allocations/{allocation_id}` releases every reservation in the bundle atomically ([`allocation_service.release_allocation()`](../../backend/app/services/allocation_service.py)).

### DECOMMISSIONED rejection

Hosts with `lifecycle_state = DECOMMISSIONED` are refused at allocation time with `HOST_DECOMMISSIONED` (409). Policy lives in `_ensure_host_allocatable()` in [`allocation_service.py`](../../backend/app/services/allocation_service.py); decommission lifecycle is documented in [`docs/design/host-decommission-lifecycle.md`](host-decommission-lifecycle.md).

## Schema

**Migration: NONE.**

Phase 8A already models a bundle:

- `allocations.id` — batch/allocation identifier returned as `allocation_id`
- `central_reservations.allocation_id` — links each port to the bundle
- `allocations.request_id` — idempotency key

No new tables or columns are required for stack/batch semantics. Callers that need a "stack ID" should use `allocation_id`.

## Agent contract

`capabilities.batch_allocation: true` in [`agent/portforge_agent/agent_contract.py`](../../agent/portforge_agent/agent_contract.py) advertises that the default allocation path is already multi-port.
