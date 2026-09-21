# Phase 8D Audit: Provider-Neutral Coding-Agent Integration

Written before any Phase 8D implementation, per instruction.

## 1. Phase 8A API/CLI contracts

`POST/GET/DELETE /api/allocations`, unauthenticated. CLI:
`portforge allocate` / `allocation get` / `allocation release`, all with
`--json`/`--format env`, exit 0/1/2. Structured errors
`{"error": {"code","message","details"}}`. `AllocationOut`: `allocation_id,
project, host{id,hostname}, status, allocations[{name,purpose,protocol,port,
reservation_id}], validation, created_at, released_at`. All unchanged and
frozen — Phase 8D calls this surface, never re-implements it.

## 2. Phase 8B manifest/adapter

`agent/portforge_agent/manifest.py` (`portforge.yml`, versioned,
`ManifestPortRequest`/`ProjectManifest`, optional `config:`),
`project_adapter.py` (`resolve_host_ref` — the one host resolver,
`build_normalized_request`, `to_allocation_body`). CLI:
`portforge project validate/plan/allocate`. Phase 8D's `workflow prepare`
and `workflow apply` are **thin orchestrators over this existing code** —
they call `load_and_validate_manifest`, `resolve_host_ref`,
`to_allocation_body`, and `client.create_allocation`/`get_recommendation`
directly, exactly as `project.py`'s own commands already do. No second
manifest parser, no second host resolver.

## 3. Phase 8C config commands

`config_manager.py`: `build_plan`, `persist_plan`,
`find_latest_planned_mutation`, `apply_mutation`, `get_status`,
`rollback_mutation`, all keyed by a `mutation_id` persisted at
`<project_root>/.portforge/mutations/<id>/record.json`. `verify_allocation_ownership`
already checks project/host/status match before any plan/apply proceeds.
Phase 8D's `workflow apply` calls these same functions in sequence — it
does not reimplement plan/apply/rollback.

## 4. Current CLI entry points

`cli.py::build_parser()` — one subparser per top-level command:
`scan/docker/inspect/check/next/reserve/release/reservations/conflicts/
sync-reservations/central/allocate/allocation/project/config/agent`.
**Naming note**: `agent` is already a top-level subcommand group (`agent
enroll/test/sync/status/run/service` — the PortForge host **daemon**,
Phase 5/6). This is a different "agent" than Phase 8D's "coding agent."
To avoid ambiguity, Phase 8D's new commands are named `agent-contract`
(top-level, hyphenated — deliberately NOT nested under the existing
`agent` group) and `workflow` (a new top-level group); `project init` is
nested under the existing `project` group alongside `validate/plan/allocate`,
since it's squarely a manifest-lifecycle operation.

## 5. Documentation

`docs/phase8a_agent_allocation.md`, `docs/phase8b_project_manifest.md`,
`docs/phase8c_safe_config.md` are the three existing external contracts.
Phase 8D adds `docs/phase8d_coding_agent_integration.md` (the workflow
contract) and `docs/agent/PORTFORGE_AGENT.md` +
`docs/agent/portforge-tool-contract.json` (the provider-neutral
instructions/tool spec) — new files, no existing doc is restructured.

## 6. JSON output contracts / exit codes / error contracts

Established and unbroken across all three prior phases: `--json` means
stdout is JSON-only, stderr carries diagnostics only; exit `0` success,
`1` a normal refused/unavailable outcome, `2` an operational/configuration
error; every structured error is `{"error": {"code","message","details"}}`.
Phase 8D's `workflow`/`agent-contract`/`project init` commands follow this
exactly — no new conventions invented.

## 7. Recovery semantics (the key precedent Phase 8D builds on)

Phase 8C's mutation record (`status: PLANNED/APPLIED/ROLLED_BACK`,
persisted to disk, survives process/Central restarts) is the proof this
kind of durable, file-backed recovery state already works and is already
tested (Phase 8C's Central-restart and CLI-interruption-adjacent tests).
Phase 8D's workflow record is a **direct structural extension of the same
idea** — a small JSON file under the SAME `.portforge/` directory, not a
competing persistence model (task §10 explicitly warns against a second
project model).

## 8. Installed executable discovery

`portforge` is installed as a real console script
(`[project.scripts] portforge = "portforge_agent.cli:main"`,
`pyproject.toml`) and confirmed on `PATH` in this environment (`which
portforge` resolves to a real script). This is exactly what
`docs/agent/PORTFORGE_AGENT.md`'s step 1 ("detect PortForge") tells a
coding agent to check for — `portforge --help` (or `python -m
portforge_agent --help` as a fallback if the console script isn't on
`PATH`, e.g. a fresh clone that hasn't been `pip install -e`d yet).
**Also fixed during this audit**: `ruamel.yaml` (added in Phase 8C) wasn't
reflected in the installed package's own metadata (`pip show` didn't list
it) because the editable install was never refreshed after that phase's
`pyproject.toml` edit — re-ran `pip install -e .` to fix; this was a
metadata-accuracy gap, not a runtime bug (the import already worked, since
the dependency happened to be present globally).

## 9. Windows/macOS/Linux command differences

Already fully solved by existing code Phase 8D reuses unchanged: `paths.py`
(per-OS data dir), `platform.py` (OS detection), `cli.py::main()`'s
`stream.reconfigure(errors="replace")` (Windows console encoding safety).
No new OS-specific branches are needed for `workflow`/`agent-contract`/
`project init` — they're pure orchestration over already-portable code.

## Decisions carried into implementation

| Question | Decision |
|---|---|
| Command naming | `agent-contract` (top-level, hyphenated) and `workflow` (new top-level group), to avoid colliding in meaning with the existing `agent` (host daemon) command group. `project init` nested under `project`. |
| Contract versioning | A single integer `contract_version` (starts at `1`), separate from `manifest.SUPPORTED_MANIFEST_VERSION` and separate from the package's own `portforge_version` — three independent version numbers for three independent things, never conflated. |
| Workflow persistence | A new, small `WorkflowRecord` stored at `<project_root>/.portforge/workflows/<request_id>/record.json` — sibling to Phase 8C's `mutations/` directory under the same `.portforge/` root, not a new top-level state directory. |
| Compensation ownership tracking | The workflow record explicitly stores whether *this* workflow attempt created the allocation (`allocation.created_by_this_attempt: bool`) — compensation only ever releases an allocation it created itself, never one reused via idempotent replay (task §7). |
| `workflow apply`'s relationship to Phase 8A/8C | Pure sequencing: call `create_allocation` (or reuse via idempotency), then if `config:` mappings exist call `build_plan`/`persist_plan`/`apply_mutation` — zero duplicated logic, confirmed by code review (no allocation or file-mutation code appears in `workflow.py` itself). |
| "Real Antigravity" test (task §25) | See final report — I do not have a programmatic way to drive the Antigravity IDE as an external actor from within this session; the CLI-contract-only subprocess simulation (matching Phase 8B/8C's own precedent) is what's actually performed and is explicitly called out as such, not conflated with a genuine third-party-agent run. |
