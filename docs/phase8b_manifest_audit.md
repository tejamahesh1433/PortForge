# Phase 8B Audit: Project Manifest & Provider-Neutral Agent Adapter

Written before any Phase 8B implementation, per instruction. Covers the
required inspection points and the decisions they lead to.

## 1. Does `.portforge.yml` already have semantics? (critical finding)

**Yes — and they are incompatible with what Phase 8B needs.**

`agent/portforge_agent/config.py::find_project_config()` already searches
for `.portforge.json` / `.portforge.yml` / `.portforge.yaml` (constants in
`detection/evidence.py`: `PORTFORGE_JSON_MANIFEST`, `PORTFORGE_YAML_MANIFESTS`),
walking upward with a bounded, home-directory-safe traversal. This file
serves two existing Phase 3/4 purposes:

1. **Project detection** — its mere presence is one signal used by
   `detection/evidence.py` to identify a project root.
2. **Local reservation sync** — `reserve_ops.py::sync_project_reservations()`
   reads its top-level `project` (string) and `ports` key, where **`ports`
   is a LIST**: `[{port: 3000, service?, purpose?, protocol?}, ...]`. Each
   entry already has an explicit, pre-decided `port` integer. This function
   only ever writes to the **local** reservation file (`ReservationStore`)
   — it never talks to Central, never resolves a host, never allocates.

Phase 8B's manifest needs `ports` to be a **MAP** keyed by name
(`{frontend: {purpose: frontend, protocol: tcp, preferred: 3000}, ...}`),
because the whole point is to ask Central to *pick* a port for a *purpose*
— the opposite direction from Phase 4's "I already know the port, just
remember it locally."

Reusing `.portforge.yml` for the new schema would silently break existing
behavior two ways:

- An **old-style** `.portforge.yml` (`ports` as a list) fed into strict
  Phase 8B validation would be rejected outright (wrong type for `ports`).
- A **new-style** manifest (`ports` as a map) fed into
  `sync_project_reservations()` would silently no-op — `isinstance(entries,
  list)` is `False` for a map, so the function just returns `[]` with no
  error at all. This is the more dangerous direction: no crash, no
  complaint, just silent nothing.

**Decision: introduce a separate, non-dot-prefixed manifest filename —
`portforge.yml` / `portforge.yaml`** (exactly the name the Phase 8B task
spec's own examples already use). Zero code sharing with
`find_project_config()` / `sync_project_reservations()` beyond the general
"parse YAML with `yaml.safe_load()`" pattern already established in
`config.py`. The existing `.portforge.yml` file, its discovery, and its
`sync-reservations` behavior are **completely untouched** by this phase —
verified by running the full existing agent test suite unchanged
(`test_config.py`, `test_reserve_ops.py`, `test_sync_physical.py`).

## 2. Phase 8A `AllocationIn` / `AllocationRequestItem`

`backend/app/schemas/allocation.py`:

- `AllocationRequestItem`: `name` (1–255, non-blank), `purpose` (1–64),
  `protocol` (default `"tcp"`, pattern `^(tcp|udp)$`), `preferred_port`
  (optional, 1–65535, a *hint*, never forced).
- `AllocationIn`: `project` (1–255), `host_id` (UUID), `requests` (1–20
  items, `MAX_BUNDLE_SIZE = 20`, unique names enforced by a
  `model_validator`), optional `request_id` (1–255).

Phase 8B's manifest schema and its `ManifestPortRequest`/`ProjectManifest`
dataclasses mirror these bounds exactly (same lengths, same protocol
pattern, same port range, same bundle-size cap). **True code-level reuse
is not possible**: the agent package (`agent/`) and the backend
(`backend/`) are separate deployables with separate dependency sets — the
agent deliberately has no Pydantic/FastAPI dependency (see
`central_client.py`'s docstring: stdlib-only by design). The bounds are
therefore *independently declared constants* in `agent/portforge_agent/manifest.py`,
each with a comment cross-referencing `backend/app/schemas/allocation.py`
as the source of truth, so a future change to one is easy to notice needs
mirroring in the other. This is documented as a known limitation of
"reuse" across a service boundary, not silently ignored.

**Purposes are deliberately NOT validated against a local copy of
`DEFAULT_RANGES`.** The agent's own `config.py::DEFAULT_RANGES` is a
*local-discovery* concept (Phase 4) and could differ from whatever purposes
Central's `recommendation_service.DEFAULT_RANGES` actually supports at
allocation time (a user config file, `~/.config/.../config.yml`, can extend
ranges locally — that has no bearing on what Central accepts). Hardcoding a
whitelist client-side risks silently rejecting a perfectly valid purpose
Central would accept, or accepting one Central will reject with a stale
error message. The manifest validator only checks `purpose` is a non-empty,
length-bounded string; Central's existing `INVALID_REQUEST` error (already
implemented and tested in Phase 8A) is the single source of truth for
"is this purpose known," surfaced honestly to the caller.

## 3. CLI allocation commands (`cli.py`)

`allocate` / `allocation get` / `allocation release` already establish the
patterns Phase 8B reuses directly rather than reinventing:

- `_resolve_central_base_url()` / `_allocation_client()` — URL resolution
  order (`--url` → `PORTFORGE_CENTRAL_URL` → `~/.portforge/central.json`).
  Reused as-is by the new `project` commands.
- `_resolve_host_id(client, host_arg)` — accepts hostname or UUID, resolves
  via `GET /api/hosts`. **Moved** (not duplicated) into the new
  `project_adapter.py` module as `resolve_host_id()`, since both `allocate`
  and the new `project validate/plan/allocate` commands need it, and
  `project_adapter.py` is the natural single-source-of-truth home ("the
  adapter must know nothing about a specific provider" applies equally to
  "know nothing about argparse"). `cli.py`'s `_cmd_allocate` now imports
  it from there — there is exactly one host resolver, not two.
  **Bug found while doing this**: `_resolve_host_id` previously returned a
  single generic error string for both "not found" and "ambiguous" cases,
  and `_cmd_allocate` hardcoded the response code to `HOST_NOT_FOUND`
  regardless — an ambiguous hostname was mislabeled. Fixed by having the
  resolver return a distinct `HOST_NOT_FOUND` / `HOST_AMBIGUOUS` code
  alongside the message, which both callers now surface correctly.
- `_env_var_name()` / `_render_allocation_env()` — reused as-is by
  `project allocate --format env`; no second implementation.
- `_print_allocation_error()` — reused as-is for manifest/host errors too.

## 4. Recommendation logic for `plan`

`plan` must be non-mutating but still "provide useful candidate
information using existing recommendation logic." The existing
`GET /api/recommendations` endpoint (`backend/app/api/recommendations.py`,
unauthenticated, unchanged since Phase 5) already does exactly this — one
purpose/host/protocol in, one advisory `CentralRecommendationOut` out,
explicitly labeled `"central_suggestion"`, no reservation created. Phase 8B
adds one new thin client method, `central_client.py::get_recommendation()`,
that calls this **existing, unmodified** endpoint — no backend change was
needed for `plan` beyond this one additive client method.

Because the endpoint answers one purpose at a time and has no
`preferred_port` or `exclude_ports` parameter, `plan` calls it once per
manifest entry, independently. This has one honest, documented limitation:
if two entries in the same manifest share a purpose, `plan` may suggest the
same candidate port for both (each call doesn't know what the other
suggested) — `allocate`'s real atomic bundle does not have this problem,
since it claims each port as it resolves it. `plan` is explicitly advisory
per the task's own instruction ("do not promise those exact ports will
still be available later"), so this is called out in
`docs/phase8b_project_manifest.md` rather than worked around by inventing
a second, bundle-aware recommendation endpoint (which would be new backend
surface for Phase 8B to own and test, well beyond "provide useful candidate
information").

## 5. Existing YAML dependency and precedent

`pyyaml` is already a required dependency (`pyproject.toml`), used
exclusively via `yaml.safe_load()` — never the default/full loader —
everywhere in this codebase (`config.py`, `detection/evidence.py`).
Phase 8B's manifest loader follows the identical pattern: `safe_load` only,
plus a raw-byte-size check on the file **before** parsing (new — Phase 4's
existing YAML consumers never bounded input size, since they read small,
already-trusted local files; a manifest is the same kind of file, so the
same trust level applies, but the explicit size bound is added anyway per
the task's own instruction, as a deliberate defense-in-depth step this
phase introduces).

## 6. Existing config precedence

`config.py`'s documented precedence — `CLI arguments > project config >
user config > built-in defaults` — informs Phase 8B's idempotency-key
decision (§11 below): a CLI flag should outrank a manifest-declared value,
consistent with the project's existing precedence discipline rather than
inventing a new rule.

## Decisions carried into implementation

| Question | Decision |
|---|---|
| Manifest filename | `portforge.yml` / `portforge.yaml` (no leading dot) — a new, separate file from `.portforge.yml`. Existing `.portforge.yml` semantics are completely unchanged. |
| Discovery scope | Current directory only, no upward walk (deliberately simpler than Phase 4's bounded upward traversal — "if useful" was optional; kept minimal and documented). Explicit path always takes priority. |
| Host resolver | One implementation (`project_adapter.resolve_host_id`), used by both `allocate` and `project validate/plan/allocate`. |
| Purpose validation | Not duplicated client-side; Central's existing `INVALID_REQUEST` is the source of truth. |
| Bundle size cap | 20, mirroring Phase 8A's `MAX_BUNDLE_SIZE` (independently declared constant, cross-referenced in comments — true cross-process reuse isn't possible). |
| `plan` candidate source | Existing `GET /api/recommendations`, one call per manifest entry — no new backend endpoint. |
| Idempotency key source | Both a manifest `request_id` field and a CLI `--request-id` flag are supported; CLI flag wins if both given (matches existing CLI > config precedence). Neither given → allocation proceeds without idempotency protection, identical to plain `allocate`'s existing behavior. |
| `project get` / `project release` | Not implemented — `portforge allocation get/release <id>` already does this; adding a parallel command would be a duplicate API for no benefit. |
