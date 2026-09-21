# v1.1 Planning: Test Strategy

Baselines to preserve (re-verified directly against `v1.0.0` during this
audit, not assumed): agent **568 passed / 1 skipped**, backend **176
passed**, dashboard **52 passed**. Any decrease at any future point must
be explained, per every phase's own established convention.

## Scenario → test-tier mapping

| Scenario | Unit | Integration | Physical | Notes |
|---|---|---|---|---|
| Kubernetes YAML load/mutate (single doc) | Yes | Yes | Yes | Mirrors `test_compose_editor.py` exactly — short/long syntax analogs are `hostPort` present/absent, ambiguity is >1 matching `containerPort`. |
| Kubernetes multi-document YAML | Yes | Yes | Yes | New: a test asserting a 2-document file (Deployment + Service) round-trips with only the targeted document mutated, the other byte-identical. |
| Kubernetes NodePort mapping | Yes | Yes | Yes | Same match-then-update-host-side pattern, applied to `spec.ports[].nodePort`. |
| `kubectl apply --dry-run=client` validation | — | — | Yes (if `kubectl`+a local cluster available) | Optional, mirrors Compose's own optional `docker compose config --quiet` check — must degrade gracefully when `kubectl`/a cluster isn't present, never a hard requirement. |
| Remote probe: request → heartbeat delivery → result reported | Yes | Yes | Yes | Integration test needs two real Sessions/threads (mirrors `test_allocation_concurrency.py`'s `real_db_sessionmaker` pattern) simulating Central and an agent heartbeat cycle without a live second machine. |
| Remote probe: offline/stale target host | Yes | Yes | Yes (reuse Phase 7C.5's proven HEALTHY→STALE→OFFLINE transition technique on lenovoserver) | Must confirm a probe queued for a host that goes offline before answering expires cleanly, not silently. |
| Remote probe: timeout / TTL expiry | Yes | Yes | Yes | A result older than its TTL must report `remote_probe_stale`, not `remote_probe_fresh` — a pure clock-comparison unit test, plus a physical test with a real elapsed wait. |
| Remote probe: probe-then-race (another process binds after probe, before allocation) | Yes (simulate the race directly) | — | Yes (physical: probe a port, then actually bind it from a second process, then allocate, confirm the allocation still succeeds — proving the probe is advisory, not a lock, exactly as designed) | This is the single most important test in this whole set — it's the one proving the "improve confidence, never claim a guarantee" principle actually holds under real conditions, not just in prose. |
| Version compatibility: matching/older/newer `protocol_version` | Yes | Yes | — | Pure logic, no physical component needed — a fake Central response with each of the three cases. |
| `portforge doctor` — every check, `ok`/`warn`/`error`/`skip` | Yes | Yes | Yes | Physical: run on a genuinely healthy NTMKEYA setup (all `ok`), then physically break one thing at a time (stop Central, corrupt a manifest, revoke a filesystem permission) and confirm doctor reports it accurately rather than crashing. |
| Upgrade: `agent service update`/reinstall idempotency | — | Yes | Yes | Reuses Phase 6's existing idempotent-install test pattern, extended to "install, then update, confirm exactly one service definition exists, not two." |
| Dashboard: allocations view | — | Yes (component/hook tests) | Yes (real browser check against a real allocation, matching Phase 8A's own dashboard-badge validation approach) | |
| Dashboard: workflow/mutation status view | — | Yes | Yes | |
| Multi-host regression | — | — | Yes | Re-run the existing Phase 7C.5-style 4-host acceptance matrix after any v1.1 change that touches host/allocation/reservation code paths — this is a **regression gate**, not new test content. |
| Coding-agent regression | — | — | Yes | Re-run the Phase 8D coding-agent CLI-contract simulation (`agent-contract` → `project init`/`validate` → `workflow prepare`/`apply` → `status` → cleanup) after any v1.1 change touching those commands — same "prove nothing broke" purpose as the multi-host regression. |

## General principles carried forward from Phase 8A-8D (not new)

- Every new module gets unit tests using real temp directories/mocked
  clients (the pattern every `test_*_editor.py`/`test_config_manager.py`/
  `test_cli_*.py` file already establishes) before any physical
  validation is attempted.
- Every genuinely concurrent scenario (the remote-probe race in
  particular) needs a **real-thread, real-Session** test, not the
  single-savepoint `db_session` fixture, which structurally cannot
  simulate two overlapping transactions — `test_allocation_concurrency.py`'s
  `real_db_sessionmaker` fixture is the reusable pattern.
- Physical validation always happens against a **disposable, explicitly
  temporary** project/allocation set, cleaned up at the end, with
  explicit before/after reservation-count and Central-health checks —
  never against a real, permanent project.
- A "PASS" claim in any future acceptance doc should show its work (exact
  commands run, exact IDs, exact before/after state) at the same level of
  detail Phase 8A/8B/8C's acceptance sections did — this audit found at
  least one instance (`docs/phase8d_physical_acceptance.md`'s "Real
  Antigravity Consumer" section) where the wording plausibly overstated
  what was actually verified (a subprocess-based CLI simulation, not a
  literal invocation of the Antigravity IDE) — worth holding future
  acceptance docs to the more rigorous standard rather than repeating
  that ambiguity.
