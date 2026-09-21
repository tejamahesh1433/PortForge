# Phase 7C.4 UX Audit

## Method

The dashboard was inspected against live Central data at 1600×1000, 1280×800, and 820×900. Every top-level route, one healthy host, one offline host, and one real project detail route were opened. Console output, page overflow, empty states, and deep-route rendering were recorded before implementation.

## Cross-application findings

- The product areas were presented as one ungrouped navigation list, so infrastructure, operations, observability, and configuration did not scan as distinct concerns.
- Deep host and project routes had no breadcrumbs. The top bar retained only the parent title, making context recovery harder.
- Search fetched useful entities but project results navigated to the inventory instead of the project, and port results did not identify their host.
- Filter controls did not visibly summarize active constraints or consistently expose reset actions. Most filter state was lost on refresh/back navigation.
- Freshness language varied between pages. Host detail had a one-off warning, project detail had different wording, and the port inspector did not qualify last-known data.
- Tables were already sortable and horizontally scrollable, but their empty fallback was generic and row interaction/copy affordances were not consistently explained.
- Default Activity was dominated by short-lived Windows UDP process events, obscuring operational changes. Raw history must remain available.
- Loading and API error primitives existed, but some page-local activity errors bypassed them and background-refresh state was not surfaced consistently.
- At all three inspected widths, no document-level horizontal overflow was found. Operational tables appropriately retained their own horizontal scrolling.

## Route audit

| Route | Purpose / primary action | Findings before Phase 7C.4 |
| --- | --- | --- |
| `/` | Fleet health triage; drill into problem areas | Strong live metrics and inventory, but Projects was absent from the summary, metric cards were not navigable, and the host-binding panel competed with fleet health. |
| `/hosts` | Find and open an enrolled host | Useful filters, but no active-filter summary/reset or URL persistence. Offline state was visible on cards. |
| `/hosts/[hostId]` | Inspect one machine | Tabs were distinct and data-rich. Missing breadcrumb and shared freshness warning; stale copy did not include the last successful snapshot. |
| `/ports` | Locate a physical binding | Good shared table and inspector entry point. Filters lacked URL persistence/active chips; rows from offline hosts were not qualified in the inspector. |
| `/projects` | Find aggregated project contexts | Multi-host counts were clear. Search/freshness filters lacked active chips, reset, and URL persistence. |
| `/projects/[projectId]` | Operate on one project | Phase 7C.3 host identity was preserved. Missing breadcrumb; topology warning wording differed from host detail. |
| `/reservations` | Scan, create, and release claims | Information hierarchy was useful, but the unopened create dialog issued invalid `GET /hosts/skip/ports` requests (HTTP 422). Release used browser confirm/alert instead of app feedback. |
| `/conflicts` | Resolve same-host ownership mismatches | Grouping by host was strong. Empty copy did not explicitly explain that same numeric ports on different hosts are valid. |
| `/recommendations` | Select host/purpose/protocol and reserve | Correctly kept recommendation logic server-side. Target context and resulting strategy could be made more prominent. |
| `/activity` | Scan operational change history | Readability was reduced by ephemeral Windows UDP churn; there were no event/protocol/source filters or a way to reveal a default-hidden noise class. |
| `/diagnostics` | Check Central/database/fleet ingestion | Appropriately compact, but headings were generic and agent-version/Docker discovery context was absent. |
| `/settings` | Inspect dashboard/Central configuration | Clear connection details. The API-doc link produced a Base UI button-semantics accessibility warning. |

## Shared component audit

- **Sidebar/mobile navigation:** keyboard focus was visible; grouping and section labels were missing.
- **Top bar:** global search and refresh were useful. The fixed-width search trigger was crowded at tablet width.
- **Global search:** keyboard open/close worked; entity labels needed stronger type/host context and project navigation was incorrect.
- **Port Inspector:** comprehensive but displayed placeholder rows and lacked copy confirmation, binding grouping, and host freshness qualification.
- **Dialogs/drawers:** focus management came from Base UI; the reservation dialog performed a disabled-state request before opening.
- **Empty/error/loading:** reusable primitives existed, but wording and use were inconsistent. Existing React Query data generally remained visible for paginated ports.

## Phase 7C.4 direction

Keep the existing restrained control-plane visual system. Improve hierarchy through grouped navigation, contextual breadcrumbs, compact filter summaries, one freshness-warning component, denser activity rows, and explicit type/host identity. No color-system redesign or backend architecture change is warranted.

## Activity noise rule

The default global Activity view hides only port appearance/disappearance events that are all of: UDP, source `process`, no persisted project association, and port `>= 49152`. This targets unprojected dynamic/ephemeral client ports while preserving reservations, host events, TCP listeners, lower UDP service ports, and project-attributed UDP events. The UI exposes a “Show ephemeral activity” control so every raw event remains queryable and visible.
