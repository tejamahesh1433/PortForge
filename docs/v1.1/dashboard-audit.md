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

1. **~~No allocation visibility at all.~~ RESOLVED in v1.1-D** — see
   `docs/v1.1/v1.1-d-implementation.md`. `/allocations` (list, filterable,
   URL-backed) and `/allocations/[id]` (detail, release action, live
   per-binding probe evidence) now exist, plus a Project Detail
   Allocations tab and an Overview metric card. Workflow status
   (`ALLOCATED`/`APPLIED`/`FAILED`) and config-mutation status
   (`PLANNED`/`APPLIED`/`ROLLED_BACK`) remain **not** shown, by design —
   v1.1-D's own data audit confirmed both are genuinely agent-local
   (`.portforge/workflows/`, `.portforge/mutations/`), never synced to
   Central, so a global view would have to be fabricated. Allocation
   Detail instead carries a labeled note pointing at the real CLI commands
   (`portforge workflow status`, `portforge config status`) — this is a
   deliberate honesty boundary, not a remaining gap in this item.

2. **Stale/offline explanation is a raw enum, not a human sentence.**
   `app/hosts/[hostId]/page.tsx` shows `Health Reason: AGENT_STALE` (or
   similar) verbatim — accurate, but requires the reader to already know
   `host_stale_after_seconds`/`host_offline_after_seconds` exist to
   understand *why*. A one-line human translation ("last seen 3m ago,
   exceeds the 2m stale threshold") would close this without inventing
   new data — every value needed is already in the API response.

3. **~~No remote-probe state surface~~ RESOLVED in v1.1-D.** The actual
   shipped `bind_probe` vocabulary (`verified_free`/`verified_occupied`/
   `not_remote_capable`/`unavailable`/`expired` — see
   `docs/v1.1/v1.1-b-implementation.md`, not the `remote_probe_fresh`/
   `_stale` names this audit originally guessed at) is now shown via
   `BindProbeBadge` on allocation list/detail, and Host Detail gained a
   dedicated Remote Probe Capability card. See
   `docs/v1.1/v1.1-d-implementation.md` §5/§6.

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
