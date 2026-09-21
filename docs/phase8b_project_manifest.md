# Phase 8B: Project Manifest & Provider-Neutral Agent Adapter

Lets any coding agent or tool describe the ports a project needs in a
`portforge.yml` file, without knowing PortForge's internal API. Built
entirely on top of Phase 8A's allocation API — no backend changes were
needed except one additive method (`get_recommendation` on the existing
client) and two additive reservation-schema fields already used for the
Phase 8A dashboard badge. See
[`phase8b_manifest_audit.md`](phase8b_manifest_audit.md) for the full
audit this design is grounded in.

## Manifest schema

```yaml
version: 1

project: jarvis

target:
  host: NTMKEYA

ports:
  frontend:
    purpose: frontend
    protocol: tcp
    preferred: 3000

  api:
    purpose: api
    protocol: tcp
    preferred: 8000

  database:
    purpose: postgres
    protocol: tcp

  redis:
    purpose: redis
    protocol: tcp

# optional
request_id: task-123
```

| Field | Required | Notes |
|---|---|---|
| `version` | yes | Must be `1`. See "Versioning". |
| `project` | yes | 1–255 characters. |
| `target.host` | yes | Hostname or UUID. See "Host resolution". |
| `ports` | yes | A **map** of 1–20 entries, keyed by name. Each key is that request's `name` — names are inherently unique because YAML map keys are unique. |
| `ports.<name>.purpose` | yes | 1–64 characters. Not validated against a local whitelist — Central's `INVALID_REQUEST` error is the source of truth for known purposes (see audit §2). |
| `ports.<name>.protocol` | no | `tcp` (default) or `udp`, lowercase, exact match. |
| `ports.<name>.preferred` | no | 1–65535. A **hint**, never a demand — see "Preferred ports". |
| `request_id` | no | Idempotency key. See "Idempotency". |

**Unknown fields at any level are rejected, not ignored.** A typo like
`protcol: tcp` fails with `MANIFEST_INVALID` rather than silently falling
back to the default protocol.

### Not this file: `.portforge.yml`

Phase 4's `.portforge.yml` (leading dot) is a **completely separate,
unrelated file** used for local project detection and local-only
reservation sync (its own `ports:` key is a **list** of pre-decided
`{port, service, purpose, protocol}` entries — the opposite direction from
this manifest, which asks Central to *pick* a port for a *purpose*). Phase
8B's manifest deliberately uses a different, non-dot-prefixed filename so
the two schemas can never collide. See the audit's §1 for the full
reasoning. Nothing about `.portforge.yml`'s behavior changed in Phase 8B.

## Versioning

`version` must be present and must currently equal `1`. An unsupported
version fails immediately with `UNSUPPORTED_MANIFEST_VERSION` — there is no
fuzzy migration or best-effort interpretation of a future/past version.

## Preferred ports

`preferred` is a hint: "use this port if it's free." It is never forced.
If occupied at allocation time, PortForge falls back to another free port
in the purpose's range, exactly matching Phase 8A's own
`preferred_port` semantics (`docs/phase8a_agent_allocation.md` "Port
selection") — this manifest layer doesn't add a second, stricter
interpretation.

## Host resolution

`target.host` accepts either a hostname (resolved case-insensitively via
`GET /api/hosts`) or a UUID (validated against the same host list, so an
unknown UUID also fails rather than being silently accepted). This is the
exact same resolver Phase 8A's plain `allocate` command uses
(`project_adapter.resolve_host_ref`) — there is only one host resolver in
the codebase. An ambiguous hostname (more than one host with that name)
fails with `HOST_AMBIGUOUS` rather than arbitrarily picking one.

## Manifest discovery

- An explicit path (`portforge project validate ./portforge.yml`) always
  wins.
- With no path given, PortForge checks the **current directory only** for
  `portforge.yml`, then `portforge.yaml` (first match wins). This is
  deliberately simpler than Phase 4's `.portforge.yml` upward-walking
  discovery — no parent-directory scanning.

## Commands

### `validate` — schema + host resolution only, never mutates

```
$ portforge project validate portforge.yml --json
{
  "valid": true,
  "version": 1,
  "project": "jarvis",
  "host": {"id": "f90db087-f7b4-4647-958c-e8e13051ddc3", "hostname": "NTMKEYA"},
  "requests": 4
}
```

### `plan` — advisory candidates, never mutates

Calls the existing, unmodified `GET /api/recommendations` endpoint once
per manifest entry to show what Central would currently suggest. This is
explicitly advisory — `committed` is always `false`, and the specific
candidate port is **not** guaranteed to still be free by the time you run
`allocate`.

```
$ portforge project plan portforge.yml --json
{
  "schema_version": 1,
  "committed": false,
  "project": "jarvis",
  "host": {"id": "...", "hostname": "NTMKEYA"},
  "requests": [
    {
      "name": "frontend", "purpose": "frontend", "protocol": "tcp",
      "preferred_port": 3000, "candidate_port": 3001,
      "candidates_considered": 2,
      "basis": "..."
    }
  ]
}
```

**Known limitation**: each entry's candidate is computed independently. If
two entries in the same manifest share a purpose, `plan` may suggest the
same port for both — `allocate`'s real atomic bundle does not have this
problem (it claims each port as it resolves it). This is a deliberate
trade-off documented in the audit (§4) rather than a second, bundle-aware
recommendation endpoint built just for `plan`.

### `allocate` — the only command that mutates

Translates the manifest into a normalized request, resolves the host, and
calls Phase 8A's existing `client.create_allocation()` — the identical
code path `portforge allocate` (the plain, non-manifest command) uses.
Creates **one** allocation containing all requested reservations, never one
allocation per port.

```
$ portforge project allocate portforge.yml --json
{
  "schema_version": 1,
  "committed": true,
  "allocation_id": "b286a2ce-a1d2-4895-b6ce-52dcd0cf0b22",
  "project": "jarvis",
  "host": {"id": "f90db087-f7b4-4647-958c-e8e13051ddc3", "hostname": "NTMKEYA"},
  "ports": {"frontend": 3001, "api": 8001, "database": 5432, "redis": 6380},
  "allocations": [ ... ]
}
```

The `ports` map is the fast path for a coding agent: `ports["api"]`
directly, no traversal of the detailed `allocations` list required (that
list is still included for anything that needs the reservation IDs).

### `--format env`

```
$ portforge project allocate portforge.yml --format env
FRONTEND_PORT=3001
API_PORT=8001
DATABASE_PORT=5432
REDIS_PORT=6380
```

Reuses Phase 8A's exact `_env_var_name`/`_render_allocation_env`
transform — no second implementation. Variable names are pure string
transforms, never executed or evaluated. **No `.env` file is written** —
this only prints values to stdout, same as plain `allocate --format env`.

## Idempotency

Two ways to supply a `request_id`, checked in this order:

1. `--request-id` on the command line
2. `request_id:` in the manifest file

The CLI flag wins if both are given, matching this project's existing
"CLI arguments > project config" precedence (`config.py`'s documented
order). If neither is given, the allocation proceeds with no idempotency
protection — identical to plain `allocate`'s existing behavior with no
`--request-id`.

Same `request_id` + identical manifest → returns the existing allocation,
no duplicate reservations, physically verified to survive a Central
restart (see the final report's physical validation section).

## Error contract

Manifest-specific errors (never a stack trace):

| Code | Meaning |
|---|---|
| `MANIFEST_NOT_FOUND` | No manifest at the given/discovered path |
| `MANIFEST_TOO_LARGE` | File exceeds the 512 KiB size bound (checked before parsing) |
| `MANIFEST_PARSE_ERROR` | Not valid YAML, or doesn't parse to a mapping |
| `MANIFEST_INVALID` | Valid YAML, but wrong shape/values/unknown fields |
| `UNSUPPORTED_MANIFEST_VERSION` | `version` isn't `1` |
| `HOST_NOT_FOUND` | `target.host` doesn't resolve to any known host |
| `HOST_AMBIGUOUS` | `target.host` (a hostname) matches more than one host |

`allocate` additionally preserves every Phase 8A allocation error code
unchanged (`HOST_STALE`, `HOST_OFFLINE`, `INVALID_REQUEST`,
`ALLOCATION_UNAVAILABLE`, `IDEMPOTENCY_CONFLICT`) — manifest errors are
never collapsed into a generic `MANIFEST_INVALID`.

## Machine output

`--json` on any of the three commands means stdout contains **only** the
JSON response — no progress text, no log lines. Verified by
`agent/tests/test_cli_project.py` and, physically, by an independent
subprocess-based parser script during validation (see final report).

Exit codes match the existing CLI convention: `0` success, `1` a normal
refused/unavailable outcome from Central, `2` an operational/configuration
error (bad manifest, unresolvable host, bad arguments).

## Recovery / release

There is no separate `project get`/`project release` command — reuse the
existing `portforge allocation get <id>` / `portforge allocation release
<id>` exactly as-is. A manifest-originated allocation is a completely
ordinary Phase 8A allocation; adding a parallel API for it would be a
duplicate with no benefit.

## Security behavior

- **No shell/command interpolation.** Manifest values (`${VAR}`,
  `$(command)`, etc.) are taken as literal data — there is no
  interpolation engine anywhere in this module.
- **Safe YAML only.** `yaml.safe_load()` exclusively — never the
  default/full loader, so a manifest cannot construct an arbitrary Python
  object or invoke a custom tag.
- **Size-bounded before parsing.** Manifests over 512 KiB are rejected
  before `yaml.safe_load()` ever sees their content.
- **No arbitrary environment-variable names take effect.** `--format env`
  only prints to stdout; nothing is executed, sourced, or exported.

## Phase 8C boundary

Phase 8B explicitly does **not**: edit a project's `.env`, Docker Compose,
Kubernetes manifests, or any application source file; start any container
or process; or run any arbitrary project command. It only reads a
manifest, validates it, plans advisory candidates, allocates ports through
the existing Phase 8A API, and prints the results. Turning allocated ports
into an actually-running project's configuration is explicitly out of
scope here — that is Phase 8C.
