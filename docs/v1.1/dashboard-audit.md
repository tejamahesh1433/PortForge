# v1.1 Planning: Dashboard / UX Audit

Findings only — no dashboard code changed in this task. Based on direct
inspection of `dashboard/app/` and `dashboard/components/` as they exist
at `v1.0.0`, not a redesign proposal.

## What exists today

`Overview` (`app/page.tsx`), `Hosts` (+ `[hostId]` detail), `Ports`,
`Projects` (+ `[projectId]` detail), `Reservations`, `Conflicts`,
`Recommendations`, `Activity`, `Diagnostics`, `Settings`. A command
palette (`components/ui/command.tsx`, Cmd/Ctrl+K) and consistent
`LoadingState`/`ErrorState`/`EmptyState` components are used across pages
(a good, already-established pattern — not a gap).

## P0 — correctness/usability defects

None found. Every page reachable from the sidebar renders real data
through the paginated, tested API surface; no broken links or dead
components were found during this review.

## P1 — important improvements

1. **No allocation visibility at all.** This is the single biggest gap.
   Phase 8A-8D built a whole atomic-allocation/workflow/config-mutation
   system, and the dashboard's only trace of it is a small "allocated"
   badge on a reservation row (added in Phase 8A) with the allocation ID
   in a tooltip. There is no way to:
   - list active allocations, or see one's full bundle (all ports
     together) in one place
   - release an allocation from the UI (only `portforge allocation
     release` today)
   - see a workflow's status (`ALLOCATED`/`APPLIED`/`FAILED`) or a config
     mutation's status (`PLANNED`/`APPLIED`/`ROLLED_BACK`)
   - see which project a mutation touched which files
   An operator debugging "why did the coding agent's config apply fail"
   today has to shell into the CLI (`portforge config status <id>`) —
   there's no dashboard path to that answer at all.

2. **Stale/offline explanation is a raw enum, not a human sentence.**
   `app/hosts/[hostId]/page.tsx` shows `Health Reason: AGENT_STALE` (or
   similar) verbatim — accurate, but requires the reader to already know
   `host_stale_after_seconds`/`host_offline_after_seconds` exist to
   understand *why*. A one-line human translation ("last seen 3m ago,
   exceeds the 2m stale threshold") would close this without inventing
   new data — every value needed is already in the API response.

3. **No remote-probe state surface** — not a v1.0 gap (the capability
   doesn't exist yet), but flagged here so v1.1-B (remote probe) and this
   audit stay linked: if remote probing ships, `bind_probe`'s new values
   (`remote_probe_fresh`/`remote_probe_stale`, see
   `docs/v1.1/remote-probe-design.md`) need a dashboard home too, most
   naturally on the host detail page next to health state.

4. **Reservations page fetches all hosts (≤500) just to resolve
   hostnames**, on every load, client-side
   (`app/reservations/page.tsx`'s `hostnameById` join, `useHosts({limit: 500})`).
   Functionally correct, but see `docs/v1.1/performance-review.md` for
   the cost; from a UX angle it's an extra loading dependency before the
   table can render hostnames, worth resolving alongside a genuine
   allocations view (item 1) rather than patched in isolation.

5. **Project lifecycle has no dedicated timeline.** `Projects` lists
   projects (a free-text label with derived stats) and links into
   per-project reservations/activity, but there's no single view showing
   "this project's ports were allocated at T, config applied at T+1s,
   released at T+2h" — the pieces exist in Activity but aren't correlated
   into a project-scoped story.

## P2 — polish

- Conflict diagnostics show what conflicts, but not a one-line "why" for
  the *specific* conflict type (e.g. bind-address mismatch vs. genuinely
  different owners) — the underlying `conflict_reason` string already
  carries this; it's a display-only gap.
- No dashboard-visible indicator of Phase 8A's known limitation that a
  released allocation's `GET` shows `allocations: []` (a live join, not a
  historical snapshot) — someone clicking into a released allocation
  (once item 1 exists) would see an empty list with no explanation.
- Filtering/search is per-page and independently implemented rather than
  a shared filter-state pattern; not broken, just some duplication across
  `Reservations`/`Ports`/`Activity`.

## What NOT to do

Per instruction, this audit does not propose a redesign, and P2 items are
explicitly *not* being turned into arbitrary cosmetic work. The only
structurally significant proposal is item 1 (P1) — a minimal Allocations/
Workflows view, additive to the existing sidebar, following the exact
existing page patterns (`DataTable`, `PageHeader`, `LoadingState`/
`ErrorState`/`EmptyState`) rather than a new design language.
