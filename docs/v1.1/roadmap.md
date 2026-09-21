# PortForge v1.1 Roadmap

Synthesizes `architecture-audit.md`, `kubernetes-design.md`,
`remote-probe-design.md`, `dashboard-audit.md`, `install-upgrade-audit.md`,
`doctor-design.md`, `version-compatibility.md`, `security-review.md`,
`performance-review.md`, `compatibility-audit.md`, and `test-strategy.md`
into independently-verifiable increments. Each can ship (and be reverted)
on its own — none depends on a later one being done first, except where
explicitly noted.

## v1.1-A — Foundation / Compatibility / Doctor

**Goal**: fix what's actually broken today and give both humans and
coding agents one place to check "is this working," before adding any new
capability.

**Scope**:
- Fix `docs/installation.md` (wrong commands, wrong port, missing
  required env var — all verified in `install-upgrade-audit.md`) and
  `docs/security.md` (`portforge central generate-token` doesn't exist).
- `portforge central generate-token` — closes the enrollment-token gap
  found in `security-review.md` (currently no CLI/dashboard path exists).
- `portforge doctor` / `portforge doctor --json` per `doctor-design.md`.
- Fix `central_sync.py::AGENT_VERSION` to derive from
  `importlib.metadata` instead of a second hardcoded literal.
- `protocol_version` field (additive) on enroll/heartbeat, plus the
  compatibility comparison logic from `version-compatibility.md`.
- `portforge agent service update` (reinstall a service definition in
  place).

**Files/subsystems**: `docs/installation.md`, `docs/security.md`,
`agent/portforge_agent/cli.py` (new subcommands), `central_sync.py`,
`agent_contract.py` (maybe — if `protocol_version` belongs there too),
`backend/app/schemas/agent.py`, `backend/app/api/agents.py`,
`service_gen.py`/`service_ops.py`.

**Public contract changes**: all ADDITIVE (see `compatibility-audit.md`).
**Database changes**: none.
**Security implications**: closes the undocumented-privileged-operation
gap (enrollment token minting); no new attack surface otherwise.
**Automated tests**: doctor's per-check unit/integration tests;
protocol-version comparison unit tests; service-update idempotency test.
**Physical acceptance**: doctor run against a genuinely healthy NTMKEYA
setup, then against deliberately broken states, per `test-strategy.md`.
**Rollback strategy**: every change here is additive or docs-only — revert
is a plain `git revert`, no data migration to undo.
**Definition of Done**: `docs/installation.md` followed literally,
end-to-end, on a clean machine, actually works; `portforge doctor --json`
returns `overall: "ok"` on a healthy setup; baselines still 568/176/52 (+
new tests).

## v1.1-B — Remote Host Probe

**Goal**: let `bind_probe` mean something more than
`"not_remote_capable"` for hosts running an agent, without ever claiming
more certainty than the architecture can back.

**Scope**: per `remote-probe-design.md` — heartbeat-borne probe
request/result queue, `PORTFORGE_PROBE_TTL`-style freshness window,
`bind_probe` gains `remote_probe_fresh`/`remote_probe_stale` values,
surfaced in allocation/recommendation responses and (minimally) the
dashboard host detail page.

**Files/subsystems**: this is the **one increment that needs new backend
schema**, not just client-side code — `backend/app/models/`, a new
migration, `backend/app/api/agents.py` (heartbeat request/response body),
`backend/app/services/recommendation_service.py` +
`allocation_service.py` (consult probe results when building `validation`),
`agent/portforge_agent/runtime/agent.py` (execute delivered probes),
`bindprobe.py` (reused, not changed).

**Public contract changes**: COMPATIBLE CHANGE, narrow — see
`compatibility-audit.md`'s note about `bind_probe`'s exact-string
consumers.
**Database changes**: yes — a new table or new columns (exact shape not
decided in this planning pass, per `remote-probe-design.md`).
**Security implications**: new request-amplification surface; ships with
rate-limiting from day one, not after (see `security-review.md`).
**Automated tests**: the race-condition test in `test-strategy.md` is the
load-bearing one for this whole increment — the feature isn't "done"
without it.
**Physical acceptance**: real probe against a real remote host
(lenovoserver), real offline-host expiry (reuse Phase 7C.5's proven
HEALTHY→STALE→OFFLINE technique), real race test.
**Rollback strategy**: migration must have a real `downgrade()` (matching
every existing migration's own tested pattern); feature-flaggable so
`bind_probe` can fall back to always-`"not_remote_capable"` without a
code revert if a rollback is needed post-ship.
**Definition of Done**: a probed, fresh, genuinely-free port reports
`remote_probe_fresh`; a stale one reports `remote_probe_stale`; the race
test passes; rate limiting is verified under a deliberately abusive probe
volume.

## v1.1-C — Kubernetes Config Integration

**Goal**: extend Phase 8C's mutation architecture to `hostPort`/
`nodePort` in local single-node clusters, per `kubernetes-design.md`'s
explicit IN/OUT scope split.

**Scope**: `config.kubernetes` manifest mapping, multi-document
`ruamel.yaml` load/mutate/dump, a third `config_manager.py` file-kind
branch reusing the exact same plan/apply/rollback/precondition-hash
machinery Compose already proved, optional `kubectl apply --dry-run=client`
validation.

**Files/subsystems**: `agent/portforge_agent/manifest.py` (new mapping
schema), a new `kubernetes_editor.py` (mirrors `compose_editor.py`'s
shape), `config_manager.py` (new branch, not a new engine).

**Public contract changes**: ADDITIVE.
**Database changes**: none — this whole increment is agent-side, exactly
like Compose (see `architecture-audit.md` §2's note that Central has zero
knowledge of config mutations today, by design).
**Security implications**: reuses `resolve_within_root` unchanged — no
new path-safety mechanism to get wrong.
**Automated tests**: single-doc and multi-doc mutation, `hostPort`
add/update, `nodePort` add/update, ambiguity refusal — direct analogs of
every existing `test_compose_editor.py` case.
**Physical acceptance**: a real `kind` (or Docker Desktop Kubernetes)
cluster on NTMKEYA, a real Deployment+Service manifest, `config plan`/
`apply`, `kubectl apply --dry-run=client` (or a real apply into a
throwaway cluster + `kubectl delete`).
**Rollback strategy**: identical to existing Compose rollback (byte-exact
restore) — no new rollback mechanism needed.
**Definition of Done**: a two-document manifest's `hostPort`/`nodePort`
fields are correctly set by `config apply`, every other field/comment/
document in the file is byte-identical to before, and `kubectl` accepts
the result.

## v1.1-D — Dashboard Operational Improvements

**Goal**: close the P1 gaps from `dashboard-audit.md`, the biggest being
"no allocation/workflow/mutation visibility at all."

**Scope**: a minimal Allocations view (list, detail, release button),
minimal workflow/mutation status surfaced on it, a human-readable
stale/offline explanation on host detail, remote-probe state surfaced (if
v1.1-B has shipped by then) — explicitly **not** a redesign of anything
existing.

**Files/subsystems**: `dashboard/app/` (new route(s)), reuses existing
`DataTable`/`PageHeader`/`LoadingState`/`ErrorState`/`EmptyState`
components per `dashboard-audit.md`'s own "what NOT to do."

**Public contract changes**: none (dashboard-only; may need small,
additive backend read endpoints if allocation/mutation listing isn't
already fully served by existing ones — check at implementation time).
**Database changes**: none expected.
**Security implications**: none — read-mostly, and any release action
already goes through the existing unauthenticated (by design) endpoint.
**Automated tests**: component/hook tests matching existing dashboard
test conventions.
**Physical acceptance**: real allocation, real workflow, real mutation,
all visible and correctly displayed in the new view; release button
verified against a real allocation.
**Rollback strategy**: trivial — new routes/components only, remove to
revert.
**Definition of Done**: an operator can see and release an allocation, and
see a workflow/mutation's status, without touching the CLI.

## v1.1-E — Installation / Upgrade Improvements

**Goal**: close the remaining `install-upgrade-audit.md` gaps beyond what
v1.1-A already fixed (docs) — an actual install script.

**Scope**: a PowerShell installer (Windows) and a POSIX shell installer
(macOS/Linux) doing `git clone` + `pip install -e ./agent` +
`portforge agent enroll` prompts. Explicitly **not** PyPI/Homebrew/winget
publishing (per `install-upgrade-audit.md`'s own "do not assume" note) —
that remains a distinct, separately-scoped future decision.

**Files/subsystems**: new `scripts/install.ps1`/`install.sh` (note: the
existing top-level `scripts/` directory currently holds only a stub
`README.md` — this would be its first real content).

**Public contract changes**: none.
**Database changes**: none.
**Security implications**: an install script that clones a git repo and
runs `pip install` is itself something a security-conscious user should
be able to read before running — ship it readable/short, not a piped
`curl | sh` one-liner with no inspection step.
**Automated tests**: script logic tested in CI on a clean container per
OS where feasible; physical test on a genuinely clean NTMKEYA snapshot if
available.
**Rollback strategy**: scripts only, no state to roll back.
**Definition of Done**: a clean Windows/macOS/Linux machine goes from
"nothing" to "enrolled agent" running one script plus answering its
prompts.

## v1.1-F — Full Multi-host + Coding-agent Acceptance

**Goal**: the "did v1.1 actually stay compatible" gate — not new
capability, a regression pass across everything shipped in v1.1-A through
v1.1-E, at the same rigor Phase 7C.5 (four-host) and Phase 8D (coding-agent
CLI contract) already established and this audit re-verified still holds
for v1.0.0.

**Scope**: re-run the real 4-host fleet acceptance matrix and the
coding-agent CLI-contract simulation, this time also covering every new
v1.1 command/field, plus a genuine second physical run of whichever items
from `docs/phase8d_physical_acceptance.md` this audit flagged as
ambiguously worded (the "Real Antigravity Consumer" claim, specifically —
either get a literal Antigravity run this time, or rewrite that section
to accurately describe what a CLI-subprocess simulation does and doesn't
prove).

**Files/subsystems**: none (validation only).
**Public contract changes**: none.
**Database changes**: none.
**Security implications**: none.
**Automated tests**: full baseline re-run (agent/backend/dashboard).
**Physical acceptance**: this increment *is* the physical acceptance
pass.
**Rollback strategy**: n/a.
**Definition of Done**: a single acceptance document, written with the
same specificity as Phase 8A/8B/8C's own acceptance sections (exact
commands, exact IDs, exact before/after state — the bar `test-strategy.md`
calls out explicitly), covering every increment shipped in v1.1.

## Prioritization

| Increment | Value | Complexity | Risk |
|---|---|---|---|
| v1.1-A | **HIGH** — fixes a currently-broken new-user path (`install-upgrade-audit.md`'s headline finding); doctor/compat-checking pays for itself immediately | **LOW** — mostly docs + small additive CLI commands + one small field addition | **LOW** — no schema change, no existing contract touched |
| v1.1-B | **HIGH** — this is the thing v1.0 was explicit it *couldn't* do yet (`bind_probe: "not_remote_capable"`); closing it is a real capability jump | **HIGH** — the only increment needing new backend schema and a genuinely new Central↔agent transport pattern; the race-condition test alone is nontrivial to get right | **MEDIUM** — new attack surface (mitigated, per the design), and it's the one place a subtle bug could cause PortForge to overstate confidence, which would be worse than not having the feature at all |
| v1.1-C | **MEDIUM** — real value for anyone using local Kubernetes dev workflows, but a narrower audience than dotenv/Compose was | **MEDIUM** — reuses proven architecture end-to-end; the genuinely new part is multi-document YAML handling and K8s's five-ways-to-say-"port" distinction | **LOW** — additive, no schema change, `resolve_within_root` reused unchanged |
| v1.1-D | **MEDIUM** — closes a real, visible gap (no allocation visibility at all), but the CLI already covers the same ground for anyone willing to use it | **LOW** — dashboard-only, existing component patterns | **LOW** — read-mostly, no new backend contract expected |
| v1.1-E | **LOW-MEDIUM** — nice-to-have once v1.1-A's docs are actually correct; not blocking anything | **LOW** | **LOW** |
| v1.1-F | **HIGH** (as a gate, not a feature) — without this, "v1.1 shipped" isn't actually a verified claim | **LOW** (it's validation, not new code) | **LOW**, but *skipping it* is the real risk |

## Recommended implementation order

**v1.1-A → v1.1-B → v1.1-C → v1.1-D → v1.1-E → v1.1-F**, with one
adjustment worth naming explicitly: **v1.1-D (dashboard) could
legitimately move earlier**, right after v1.1-A, since it has no
dependency on v1.1-B/C and closes a real, currently-visible gap
(`dashboard-audit.md`'s P1 finding) independent of remote probing or
Kubernetes. The reason to keep it after B/C in the default ordering is
purely that B/C are the higher-value, higher-risk increments worth
resourcing first while there's the most runway before the next release
— not a hard technical dependency. A team with dashboard-focused
capacity available sooner should feel free to run D in parallel with B/C
rather than strictly sequentially.
