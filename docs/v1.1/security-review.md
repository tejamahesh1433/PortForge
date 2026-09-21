# v1.1 Planning: Security Review

Findings only — no code changed. No credentials rotated, no Git history
rewritten, per explicit instruction.

## Historical credential debt (carried forward, deferred)

This is a private repository with pre-existing history that predates the
security discipline established from Phase 7C.4 onward. **Historical
credentials exist in Git history and are not addressed by this task** —
rotation/history-rewrite is explicitly out of scope here and remains
deferred. No credential values are reproduced in this document or any
other v1.1 planning doc.

## Documentation accuracy findings (verified, not speculative)

`docs/security.md` claims enrollment tokens are generated via `portforge
central generate-token` — **this command does not exist** (confirmed:
`portforge central`'s only subcommands are `enroll`/`status`/`sync`). The
real backend capability is `POST /api/agents/enrollment-tokens`
(`require_admin`-gated), with **no CLI wrapper and no dashboard UI for
it** — confirmed via direct search of both. Today, minting a new
enrollment token requires a raw authenticated HTTP call outside any
documented, first-class path. This is a genuine v1.1 gap (a `portforge
central generate-token` command, or a dashboard "generate enrollment
token" button, or both) as well as a doc-accuracy fix — see
`docs/v1.1/install-upgrade-audit.md`, which found the same class of
problem (documented commands that don't match the real CLI) in
`docs/installation.md` independently.

## Audited: agent command authorization

The only authenticated surface in the system: `require_agent` (per-host
bearer token, enroll/heartbeat/observations) and `require_admin`
(bootstrap token, narrowly on minting new enrollment tokens only). Every
other endpoint — including allocation create/get/release and the
dashboard's own reservation writes — is deliberately unauthenticated,
matching the project's explicit, repeatedly-reaffirmed "trusted LAN
control plane" posture (Phase 7C.4 onward). **No change recommended to
this posture in v1.1** — it's a decision, not an oversight, and none of
the audited v1.1 candidate features change the threat model enough to
warrant revisiting it (remote probing, discussed next, is the one
exception worth a closer look).

## Remote probe abuse (new surface, if v1.1-B ships)

`docs/v1.1/remote-probe-design.md`'s design already builds in the
relevant mitigation: probes are delivered/answered over the *existing*,
already-authenticated heartbeat channel (`require_agent`), so a probe
request can only ever be attributed to (and only ever executed by) the
genuine agent for that host — no new credential type needed. The residual
risk is a caller using probe requests as a remote-triggered port-scan
primitive against a host that happens to run a PortForge agent;
mitigation (rate-limit pending-probes-per-host, cap probes per heartbeat
cycle) is specified in that design doc and must land with the feature,
not after.

## Audited: path traversal / config mutation boundaries

Already solved, not a new risk: `config_files.py::resolve_within_root()`
is the single point every Phase 8C/8D target-file path goes through —
resolves symlinks, collapses `..`, and rejects anything outside the
project root, tested directly (`test_config_manager.py`) including the
traversal case, with the symlink-escape case's *test* (not the mechanism
itself) skipped specifically on this Windows dev machine (no Developer
Mode). **v1.1 Kubernetes support must reuse this exact function**, not a
parallel check — flagged explicitly in `kubernetes-design.md`.

Separately, Phase 8D's `workflow.py` initially had a **real, since-fixed**
path-traversal bug of its own: a raw `--request-id` value was used
directly as a directory-name path component
(`.portforge/workflows/<request_id>/`), so `--request-id "../../escape"`
could have written state outside the project root. This was caught (by a
test authored outside this specific planning task, during the
still-in-progress Phase 8D implementation this conversation was doing)
and fixed by hashing `request_id` with SHA-256 before using it as a path
component — confirmed present in the shipped `v1.0.0` code
(`workflow.py::_workflow_dir`). Documented here because it's exactly the
kind of "same class of bug, different code path" risk to watch for if
v1.1 introduces any other user-supplied-string-as-filename pattern (a
Kubernetes resource `name:` in a mapping, for instance — audit that
specifically when `kubernetes-design.md` is implemented).

## Audited: allocation / project ownership

`verify_allocation_ownership` (Phase 8C) already checks allocation
status/project/host match before any config plan/apply proceeds, and
Phase 8D's workflow compensation logic already distinguishes "allocation
created by this attempt" (safe to auto-release on failure) from
"pre-existing allocation reused via idempotency" (never auto-released) —
verified directly in `workflow.py::_compensate`. No gap found here.

## Audited: denial-of-service possibilities

- Every list endpoint is paginated with a hard upper bound
  (`limit≤500`/`≤1000` depending on resource) — no unbounded query
  surface at the API layer today (see `docs/v1.1/performance-review.md`
  for the *cost* side of this, a separate concern from DoS).
- `manifest.py`/`config_files.py` both size-cap input before parsing
  (512 KiB manifests, matching `detection/evidence.py`'s existing
  convention) — a large/hostile file is rejected before being parsed, not
  slow-parsed.
- The remote-probe design (if built) is the one place v1.1 would add a
  genuinely new request-amplification surface (Central → agent → real
  socket operation) and must ship with the rate limits specified in that
  design, not as an afterthought.

## Audited: malformed YAML / command injection / secret logging

- `yaml.safe_load()` only, everywhere, always — confirmed no
  `yaml.load()`/`yaml.unsafe_load()` call exists anywhere in the agent
  package. `ruamel.yaml`'s round-trip mode (Compose editing) is used the
  same way — no custom tag support, no arbitrary object construction.
- No `subprocess`/`os.system`/`eval`/`exec` call anywhere takes manifest
  or config-mapping content as input — confirmed by the same search
  pattern used throughout Phase 8A-8D's own security self-review
  (`docs/phase8b_project_manifest.md`/`phase8c_safe_config.md`'s own
  "security behavior" sections, re-verified here rather than just
  trusted).
- Sensitive-looking dotenv keys (`SECRET|PASSWORD|TOKEN|KEY|CREDENTIAL`)
  have their pre-existing value redacted in every `config plan`/`apply`/
  `status` output (Phase 8C) — confirmed still true, unit-tested.

## Recommended v1.1 security work (ranked)

1. **Document (or build a CLI/dashboard path for) enrollment-token
   generation** — currently a real, undocumented gap in the only
   privileged operation the system has.
2. **Rate-limit the remote-probe queue** if/when v1.1-B ships — must land
   with the feature.
3. Everything else audited above is either already solid or explicitly
   deferred (historical credential debt) with no new action proposed.
