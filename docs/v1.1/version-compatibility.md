# v1.1 Design: Version Compatibility

**Design only — not implemented in this task.**

## Current state (audited, not assumed)

| Component | Version source | Compared against anything? |
|---|---|---|
| Agent package | `agent/pyproject.toml` (`1.0.0`) | No |
| Agent's `central_sync.py::AGENT_VERSION` | **Hardcoded literal `"1.0.0"`**, separate from `pyproject.toml` | No |
| `agent_contract.py`'s `portforge_version` | `importlib.metadata.version("portforge-agent")` (reads `pyproject.toml` at install time) | No |
| Central/backend | `backend/app/config.py::Settings.version = "1.0.0"` | No |
| `agent-contract`'s `contract_version` | `agent_contract.CONTRACT_VERSION = 1` | Not compared, but exists specifically so a future incompatible contract change has somewhere to signal it |
| `portforge.yml`'s `version:` | `manifest.SUPPORTED_MANIFEST_VERSION = 1` | Compared — the only place any version check exists today (`UNSUPPORTED_MANIFEST_VERSION` if mismatched) |
| Dashboard | `dashboard/package.json` version field | No |

**Two immediate, concrete findings, not proposals:**

1. `central_sync.py::AGENT_VERSION` is a second, independent source of
   truth for the agent's own version, already capable of drifting from
   `pyproject.toml`'s declared version (they happen to both say `1.0.0`
   today by coincidence, not by any enforced link). Cheap, safe,
   worth fixing in v1.1-A: derive it from `importlib.metadata` the same
   way `agent_contract.py` already does.
2. **Nothing anywhere compares agent version to Central version.** An
   agent enrolled against a much older or newer Central gets no warning,
   ever — not at enroll, not at heartbeat, not in the dashboard. This is
   the actual gap this document designs for.

## Proposed compatibility model

PortForge already has four independently-versioned things
(`portforge_version`, `contract_version`, `SUPPORTED_MANIFEST_VERSION`,
and now a proposed one below) — the model must not conflate them. Only
one of the four needs a NEW compatibility rule for v1.1: **agent ↔
Central**, specifically.

### Why semver-major/minor/patch (the task's own example) doesn't fit as-is

PortForge's actual compatibility surface isn't "the whole package
version" — it's a small number of independently-evolving contracts:
- the heartbeat/enrollment/observation request+response shapes
  (`schemas/agent.py`)
- the allocation request+response shape (`schemas/allocation.py`)
- the recommendation response shape (`schemas/recommendation.py`)

A patch release that only fixes a collector bug on the agent side has
*zero* effect on any of these — treating it as needing a compatibility
check would produce constant false-positive warnings. Conversely, a
"patch" release that quietly adds a new required field to an existing
schema (a mistake, not a deliberate move) genuinely would break
compatibility despite being semver-patch. **Conclusion: tie compatibility
to the CONTRACT surfaces that actually change, not to the package version
number treated as a monolith.**

### Proposed rule

1. Introduce a small, separate `PROTOCOL_VERSION` integer (name TBD),
   analogous to `contract_version` and `SUPPORTED_MANIFEST_VERSION` — one
   number representing "the shape of agent↔Central request/response
   bodies," bumped only when one of those shapes changes in a way an
   older counterpart couldn't safely ignore (a new *required* field, a
   removed field, a changed meaning for an existing field). Purely
   additive changes (a new optional field, a new endpoint) do **not**
   bump it — this mirrors exactly how `manifest.py`'s own
   `_TOP_LEVEL_KEYS` whitelist already treats `config:` as additive
   without bumping `SUPPORTED_MANIFEST_VERSION`.
2. Both sides report their `PROTOCOL_VERSION`: the agent already sends
   `agent_version` on every enroll/heartbeat call (add `protocol_version`
   alongside it, additive); Central already reports `version` on
   `/api/health` (add `protocol_version` alongside it).
3. Compatibility rule, derived from what PortForge's contracts actually
   need (not adopted from the task's own semver example verbatim, per
   its own instruction not to):
   - **Equal `PROTOCOL_VERSION`**: fully compatible, no warning.
   - **Agent's `PROTOCOL_VERSION` older than Central's**: compatible with
     a warning — Central is authoritative and newer, an older agent just
     might not benefit from newer optional fields (this is the normal,
     expected state right after a Central-only upgrade).
   - **Agent's `PROTOCOL_VERSION` newer than Central's**: compatible with
     a stronger warning — an agent expecting a required field Central
     doesn't send yet is the actually risky direction (this is the
     unusual case: an agent got upgraded before Central did).
   - There is currently no case where PortForge would need to hard-reject
     a connection over this — the recommendation is **warn, never
     block**, consistent with the project's whole-history preference for
     graceful degradation over hard failures (e.g. a malformed local
     config file degrades to defaults rather than crashing,
     `config.py::load_config`).
4. Surface the warning in exactly two places, both already-existing
   surfaces: `portforge doctor`'s `central_compatibility` check (see
   `doctor-design.md`) and the dashboard's host detail page (next to the
   already-shown `agent_version`).

## What this deliberately does NOT do

- Does not block an old agent from talking to a new Central, or vice
  versa — no hard-incompatibility case has been identified that would
  justify blocking given v1.0's actual API surface.
- Does not touch `contract_version` (agent-contract's own versioning,
  serving a different audience — coding agents reading the contract, not
  the agent-daemon-to-Central wire protocol) or
  `SUPPORTED_MANIFEST_VERSION` (portforge.yml's schema version) — three
  separate numbers stay separate.
- Does not require a migration or schema change for existing enrolled
  hosts — `protocol_version` would default to "unknown" for any agent
  that predates this field, treated the same as "older than Central,"
  the safe default.
