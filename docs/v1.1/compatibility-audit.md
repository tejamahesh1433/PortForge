# v1.1 Planning: API / CLI Stability Audit

v1.0.0 is a tagged, released contract. This document identifies what must
be treated as stable and classifies every v1.1 candidate discussed in the
other planning docs as **ADDITIVE**, **COMPATIBLE CHANGE**, or **BREAKING
CHANGE**.

## Interfaces that must be treated as stable

- **CLI command names and subcommand tree** (`scan/docker/inspect/check/
  next/reserve/release/reservations/conflicts/sync-reservations/central/
  allocate/allocation/project/config/agent-contract/workflow/agent`) and
  their existing flags.
- **`--json` output shapes** for every existing command — every field
  currently present, at its current type/meaning.
- **Exit codes** (`0`/`1`/`2` convention) for every existing command.
- **`agent-contract`'s `contract_version: 1` shape** — the whole point of
  this field existing is to let it change deliberately; v1.0's shape
  under `contract_version: 1` must not silently change underneath a
  coding agent that read it once and cached it.
- **`portforge.yml`'s `version: 1` schema** — same reasoning, enforced by
  `_TOP_LEVEL_KEYS` etc. already rejecting anything not in the whitelist.
- **Phase 8A's allocation API** (`POST/GET/DELETE /api/allocations`) —
  request/response shape, error codes, idempotency semantics.
- **Phase 8C's config-mutation record shape** (`record.json`'s fields) —
  an existing `.portforge/mutations/<id>/record.json` on a user's disk
  must remain readable by whatever `config status` ships in v1.1.
- **Phase 8D's workflow record shape** — same reasoning, for
  `.portforge/workflows/<hash>/record.json`.

## v1.1 candidates classified

| Candidate | Classification | Reasoning |
|---|---|---|
| `portforge doctor` (new command) | **ADDITIVE** | New subcommand, touches nothing existing. |
| `portforge central generate-token` (new command, closing the gap found in `security-review.md`) | **ADDITIVE** | New subcommand. |
| `agent_version` comparison / `protocol_version` field on enroll+heartbeat | **ADDITIVE** | New optional field on existing request/response bodies; an agent or Central that doesn't know about it simply doesn't send/read it — no existing field changes meaning. |
| `AGENT_VERSION` derived from `importlib.metadata` instead of a hardcoded literal | **COMPATIBLE CHANGE** | Value should be identical in practice (both currently say `"1.0.0"`); worth noting as *technically* observable if the two ever drift before the fix ships — but fixing them to match is closing a bug, not introducing one. |
| Kubernetes config mapping (`config.kubernetes` or similar) | **ADDITIVE** | New optional manifest key + new `config` sub-kind, exactly mirroring how `config.compose` was added in Phase 8C without touching `config.dotenv`. |
| Remote bind-probe (`bind_probe` gaining `remote_probe_fresh`/`remote_probe_stale` values) | **COMPATIBLE CHANGE, careful** | `bind_probe` is currently always the literal string `"not_remote_capable"`; any consumer doing an exact-string comparison against that one value (rather than treating it as an open string field) would need to widen its check. Recommend documenting this explicitly as the one v1.1 change with a real, if narrow, compatibility note — not hiding it as purely additive. |
| Allocation dashboard view | **ADDITIVE** | Dashboard-only, new routes, touches no existing API contract. |
| Fixing `docs/installation.md`/`docs/security.md` inaccuracies | **ADDITIVE (docs only)** | No code or contract change at all — pure correction. |
| `portforge agent service update` (new command) | **ADDITIVE** | New subcommand. |

## No breaking changes identified for v1.1

Every candidate surfaced across this planning pass classifies as
ADDITIVE or a narrow COMPATIBLE CHANGE. This is a genuinely good sign for
v1.1's scope as currently proposed — nothing here requires a
`contract_version` or `SUPPORTED_MANIFEST_VERSION` bump. If Kubernetes
support (v1.1-C) turns out to need a *required* new field on an existing
shared structure during implementation (not expected, per
`kubernetes-design.md`'s additive design), that would be the one candidate
to re-classify as BREAKING and gate behind a version bump at that point —
called out here so implementation doesn't quietly slide from additive to
breaking without the classification being revisited.
