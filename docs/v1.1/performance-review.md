# v1.1 Planning: Performance / Scale Audit

Findings only — no optimization performed, per instruction ("do not
optimize prematurely, document only concrete findings").

## Current practical scale (observed, not assumed)

PortForge's own fleet during development: 4 hosts, low tens of
reservations per host, a few hundred activity events. Every finding below
is real code behavior, not a problem at *today's* scale — flagged because
each one degrades in a way worth knowing about before scale changes,
which is exactly what this audit was asked to establish ahead of time.

## Concrete findings

### 1. `find_available_port()` fetches up to 10,000 reservation rows per call, filters in Python

`backend/app/services/recommendation_service.py:91` —
`reservation_repo.list(host_id=host_id, limit=10_000)`, then a Python loop
checks each row's `(port, protocol)` against the candidate range. This is
the **hot path** for every recommendation lookup, every `project plan`/
`workflow prepare` candidate preview, and every port resolved inside an
allocation bundle (`allocation_service.py::_resolve_candidate` calls it
once per requested port — up to 20 times for one bundle, per
`MAX_BUNDLE_SIZE`). At today's scale (dozens of reservations per host)
this is unmeasurable; at a hypothetical host with reservations
approaching the 10,000-row cap, this becomes a real per-call cost
multiplied by up to 20 for one allocation. A targeted query (`WHERE
host_id = ? AND protocol = ? AND port BETWEEN ? AND ?`) would scale
independently of total reservation count. **Not urgent at v1.0 scale —
documented so it's a known, deliberate deferral, not a surprise later.**

### 2. `_build_allocation_out()` fetches up to 1,000 reservations per host, filters to one allocation in Python

`backend/app/services/allocation_service.py:144,340` —
`reservation_repo.list(host_id=..., limit=1000)` then `if
r.allocation_id == allocation.id` in Python, on every `GET
/api/allocations/{id}` and every release. A `WHERE allocation_id = ?`
query would be both cheaper and correct regardless of how many *other*
reservations that host happens to have — today it's bounded by "reads at
most 1000 rows," which is safe but not tight.

### 3. `_port_is_free()` re-fetches the full reservation list on every candidate attempt

`backend/app/services/allocation_service.py:188` — same `limit=10_000`
full-list-then-filter pattern as finding 1, called once per candidate
port checked within `_resolve_candidate`. Compounds with finding 1 for
the same bundle (both functions independently re-fetch the same host's
reservation list rather than sharing one fetched set across the whole
allocation call).

### 4. Dashboard reservations page fetches up to 500 hosts on every load, purely for a client-side join

`dashboard/app/reservations/page.tsx` — `useHosts({limit: 500})` runs
unconditionally alongside the (correctly paginated, ≤100) reservations
fetch, solely to resolve `host_id → hostname` for display. At today's
scale (4 hosts) this is free; at a few hundred hosts it becomes a
real, unnecessary payload on a page that only needs to resolve a handful
of host IDs actually present in the current page of reservations. A
batched "resolve these specific host IDs" endpoint, or moving the
join server-side into the reservations response, would remove this
scaling with host count. No backend endpoint like this exists today —
this would be new surface, not just a client-side fix.

### 5. Snapshot ingestion is already correctly incremental, not a full-table concern

Checked specifically because "unnecessary full snapshots" was called out
in the audit instructions: `ingestion_service.py`'s duplicate-safe design
(Phase 6, `test_ingestion_duplicate_bindings.py`) diffs against existing
`port_observations` rows for that host rather than replacing the whole
table — this is **not** a finding, included to confirm it was actually
checked rather than assumed clean.

### 6. Pagination itself: no findings

Every list endpoint (`hosts`, `ports`, `reservations`, `activity`) is
bounded (`limit≤500` or `≤1000`) with `offset`-based paging — no
unbounded result set exists at the HTTP boundary anywhere. This is a
genuinely solid foundation; the findings above are all about work done
*inside* a single request (fetch-then-filter-in-Python), not about
missing pagination at the API surface.

## Recommendation for v1.1

None of these findings block v1.1 or require action as a prerequisite —
they're real but not urgent at current scale, exactly the "document only
concrete findings, don't optimize prematurely" instruction. If v1.1-F
(multi-host acceptance, per the roadmap) exercises a meaningfully larger
fleet than today's 4 hosts, findings 1-3 are the ones to watch first,
since they're on the allocation hot path a coding-agent workflow directly
waits on.
