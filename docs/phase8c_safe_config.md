# Phase 8C: Safe Config/File Integration

Maps a committed Phase 8A allocation into explicitly-declared project
config files. Two targets: dotenv files and Docker Compose. Kubernetes is
deliberately deferred (see "Kubernetes decision" below). See
[`phase8c_config_audit.md`](phase8c_config_audit.md) for the audit this
design is grounded in.

```
portforge.yml
   |
   v
validate / plan / allocate          (Phase 8B/8A -- unchanged)
   |
   v
committed allocation
   |
   v
config plan       <- non-mutating, persists a mutation record
   |
   v
config apply      <- the ONLY command that writes real project files
   |
   v
config status / config rollback
```

**`project allocate` never mutates a project's files, and only `config
apply` ever writes to them.** This is deliberate and load-bearing:
allocation and config mutation are two separate lifecycle operations you
opt into independently.

## Config schema (extends the Phase 8B manifest, optional)

```yaml
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend: {purpose: frontend, protocol: tcp}
  api: {purpose: api, protocol: tcp}
  database: {purpose: postgres, protocol: tcp}

config:                     # optional -- a manifest without this key
  dotenv:                   # validates exactly as it did in Phase 8B
    - file: .env
      values:
        FRONTEND_PORT: frontend   # ENV_NAME: <a ports.<name> key>
        API_PORT: api
        POSTGRES_PORT: database

  compose:
    - file: compose.yaml
      services:
        frontend:
          ports:
            - allocation: frontend   # <a ports.<name> key>
              container: 3000        # the CONTAINER port to match/update
        api:
          ports:
            - allocation: api
              container: 8000
```

Every `config.dotenv[].values` value and every `config.compose[].services.*.ports[].allocation`
must reference one of the manifest's own `ports:` entries — an unknown
reference fails at manifest-validation time with `CONFIG_MAPPING_INVALID`,
before any network call. **PortForge never infers which file or key
receives a port; every mapping is explicit.** Unknown fields anywhere in
`config:` are rejected, not ignored, exactly like the rest of the
manifest.

### Backward compatibility

`config:` is entirely optional. Every Phase 8B manifest without it
validates identically to before — proven by the full, unmodified Phase 8B
test suite passing unchanged. `validate`/`plan`/`allocate` retain their
exact Phase 8B semantics; this phase adds four new commands, changes zero
existing ones.

## Project root and path safety

The project root defaults to the **manifest's own directory** (override
with `--project-root` on any `config` command). Every declared `file:`
path is resolved against it and must remain inside it after resolving
`..` and symlinks — **the same single check** handles both path traversal
(`../outside.env`, an absolute path elsewhere) and symlink escape (an
in-project symlink pointing outside the root). A path that fails this
check is rejected with `CONFIG_PATH_OUTSIDE_PROJECT` before any read.

## Dotenv behavior

A hand-rolled, line-based editor (no dotenv parser existed anywhere in
this codebase before Phase 8C):

- Only mapped keys are ever rewritten. Every other line — comments, blank
  lines, unrelated assignments — passes through unchanged.
- `export KEY=value` prefixes and trailing `# comment`s on a rewritten
  line are preserved.
- Newline style (`\n` vs `\r\n`) and trailing-newline presence are
  preserved when only updating existing keys; a missing mapped key is
  appended cleanly at the end.
- A manifest may target a `.env` file that doesn't exist yet — `plan`
  shows `"action": "will_create"`; Compose files are never auto-created
  (see "Compose behavior").
- **`DOTENV_DUPLICATE_KEY`**: if a mapped key appears as an assignment
  more than once in the file, PortForge refuses to guess which occurrence
  controls behavior. Checked for every mapped key before any line is
  rewritten — a duplicate on one key blocks the whole file's changes, zero
  mutation.

## Compose behavior

Uses `ruamel.yaml`'s round-trip mode (a new, explicit dependency — see the
audit's §7/§16 for why plain `pyyaml` can't do this) so comments, key
order, anchors, and flow-vs-block style survive untouched; only the
specific port value(s) a mapping targets are ever changed, mutated in
place on the already-parsed structure.

- Only Compose files **explicitly referenced by the manifest** are ever
  touched — PortForge never scans for `docker-compose.yml`/`compose.yaml`
  and picks one.
- A mapping always specifies `service`, `allocation` (the request name),
  and `container` (the container port to match) — the container port is
  **never inferred** from the allocated host port.
- Both short (`"8000:8000"`, `"127.0.0.1:8000:8000"`, `"8000:8000/udp"`)
  and long (`{target, published, protocol, ...}`) Compose port syntaxes
  are supported. An existing entry matching `(container, protocol)` has
  **only its host/published side updated** — the entry's own style and
  every other field (IP prefix, `mode`, etc.) survive. No match → a new
  short-syntax entry is appended. **More than one match is ambiguous** —
  `COMPOSE_PORT_AMBIGUOUS`, zero mutation, rather than guessing.
- Compose files are **never created from scratch** — inventing a base
  `services:` structure is out of scope for this phase; a missing Compose
  file fails with `CONFIG_FILE_NOT_FOUND`.
- After generating proposed content, PortForge validates it's still valid
  YAML with a `services:` structure before ever replacing the real file;
  physical validation additionally ran the real `docker compose config
  --quiet` against the applied file (informational — never a requirement,
  since Compose isn't guaranteed to be installed).

## Allocation ownership (checked before plan or apply proceeds)

| Check | Error |
|---|---|
| Allocation exists | `CONFIG_ALLOCATION_NOT_FOUND` |
| `status == "active"` | `ALLOCATION_INACTIVE` |
| Allocation's `project` matches the manifest's `project` | `ALLOCATION_PROJECT_MISMATCH` |
| Allocation's `host.id` matches the manifest's resolved `target.host` | `ALLOCATION_HOST_MISMATCH` |
| Every `config` mapping's referenced request name exists in the allocation | `CONFIG_MAPPING_INVALID` |

PortForge never applies an allocation to a project it doesn't belong to.

## `config plan` — non-mutating

Loads the manifest, resolves and ownership-verifies `--allocation`,
computes exact proposed changes for every declared file, and **persists a
mutation record** (`<project_root>/.portforge/mutations/<mutation_id>/record.json`)
— but writes nothing to the real target files, creates no backups, and
touches no target-file timestamps. Persisting the plan is PortForge's own
bookkeeping, not a mutation of the project's tracked files.

```json
{
  "committed": false,
  "mutation_id": "2e31618d-...",
  "allocation_id": "a1cd5f4d-...",
  "project": "jarvis",
  "changes": [
    {"file": ".env", "type": "dotenv", "action": "will_update",
     "changes": [{"key": "API_PORT", "before": "8080", "after": "8001", "action": "update"}]},
    {"file": "compose.yaml", "type": "compose", "action": "will_update",
     "changes": [{"service": "api", "container_port": 8000, "protocol": "tcp", "before": "9090", "after": "8001", "action": "update"}]}
  ]
}
```

A dotenv key that looks sensitive (matches `SECRET|PASSWORD|TOKEN|KEY|CREDENTIAL`,
case-insensitive) has its `before` value redacted (`"<redacted>"`) in every
output — plan, apply, and status. `after` is never redacted: Phase 8C's
mapped values are always allocated port integers, never secrets.

## `config apply` — the only mutating command

`config apply <manifest> --allocation <id>` takes the **same inputs as
`plan`**, not a raw mutation id (matching the task's own CLI shape).
Internally, it looks up the **most recently persisted `PLANNED` mutation**
for that exact `(project_root, allocation_id)` pair — this is a deliberate
interpretation choice: apply always operates against a real, prior plan's
hash baseline rather than silently re-planning inline, which is what makes
`CONFIG_CHANGED_SINCE_PLAN` meaningful. No pending plan found →
`CONFIG_MUTATION_NOT_FOUND` ("run `config plan` first").

### Precondition hashes

Before writing anything, apply re-reads every target file's live content
and compares its hash to the hash captured at plan time — checked for
**every file before any file is touched**. Any mismatch →
`CONFIG_CHANGED_SINCE_PLAN`, zero mutation, the external edit is preserved
exactly. This is what stops a coding agent (or a human) from silently
losing an edit made between plan and apply.

### Atomic multi-file apply

1. Re-verify every file's precondition hash (all of them, before any write).
2. Back up every file's current bytes into the mutation directory.
3. Write every target file via the same write-temp-then-`os.replace()`
   primitive already proven in `reservations/storage.py`.
4. If any individual write fails partway through, every target already
   written **this call** is restored from the backup just taken, before
   the failure is surfaced as `CONFIG_APPLY_FAILED` — no partial final
   state where one file is updated and another isn't.

### Backup storage and security

`<project_root>/.portforge/mutations/<mutation_id>/` — a directory,
**sibling to** (never inside) `.portforge.yml`/`portforge.yml`. Contains
`record.json` (metadata + the exact proposed new content for each file)
and `files/<safe-name>` (pre-apply byte-for-byte backups). Backup bytes
are never printed, never included in `status`/`plan`/`apply` JSON output,
and are treated as sensitive by convention (a `.env` backup can contain
real secrets) — only file paths and hashes ever appear in machine output.

### Apply idempotency

Re-running `config apply` for an already-`APPLIED` mutation is a no-op:
the existing record is returned unchanged, and no file is rewritten a
second time (verified by comparing file `mtime` before/after the second
call in the physical validation run).

## `config status` — read-only

`config status <mutation_id> --project-root <dir>` (no manifest or
`--allocation` needed — it only reads the local mutation record). Returns
`mutation_id`, `allocation_id`, `project`, `status`, timestamps, and each
file's `action` — never backup contents.

## `config rollback`

`config rollback <mutation_id>` restores every file's **exact original
bytes** (verified physically via SHA-256 comparison). Same precondition
discipline as apply, mirrored at the other boundary: every file's live
hash is compared against what apply left it as (`after_hash`) before any
restore — any mismatch → `CONFIG_CHANGED_SINCE_APPLY`, zero mutation, the
newer edit is preserved. **Rollback never touches the allocation** — it
remains `active` until separately released via the existing
`portforge allocation release <id>`. This separation is deliberate (task
§26): config rollback and allocation release are independent lifecycle
operations, always sequenced by the caller, never implied by each other.

## Error contract

| Code | Meaning |
|---|---|
| `CONFIG_NOT_DECLARED` | Manifest has no `config:` mappings |
| `CONFIG_PATH_OUTSIDE_PROJECT` | A `file:` resolves outside the project root (traversal or symlink escape) |
| `CONFIG_FILE_NOT_FOUND` | A referenced Compose file doesn't exist (dotenv files may be created; Compose files may not) |
| `CONFIG_PARSE_ERROR` | A target file isn't valid YAML / doesn't have a `services:` mapping |
| `MANIFEST_INVALID` / `CONFIG_MAPPING_INVALID` | Manifest-level `config:` shape/reference errors |
| `DOTENV_DUPLICATE_KEY` | A mapped dotenv key appears more than once |
| `COMPOSE_SERVICE_NOT_FOUND` | A mapped service doesn't exist in the Compose file |
| `COMPOSE_PORT_AMBIGUOUS` | More than one existing port entry matches the same container port/protocol |
| `CONFIG_ALLOCATION_NOT_FOUND` | `--allocation` doesn't resolve via Central |
| `ALLOCATION_PROJECT_MISMATCH` / `ALLOCATION_HOST_MISMATCH` / `ALLOCATION_INACTIVE` | Ownership verification failed |
| `CONFIG_CHANGED_SINCE_PLAN` / `CONFIG_CHANGED_SINCE_APPLY` | A precondition hash didn't match; zero mutation |
| `CONFIG_APPLY_FAILED` / `CONFIG_ROLLBACK_FAILED` | An apply/rollback failed partway; originals restored where possible |
| `CONFIG_MUTATION_NOT_FOUND` | No mutation record with that id (or no pending plan for `apply`) |

Never a stack trace. `--json` output on every `config` command is
JSON-only on stdout; diagnostics (if any) go to stderr; non-zero exit on
failure.

## Git awareness

If `git` is available, `config plan`'s human output may note a target
file's tracked/untracked/modified status as informational metadata.
**Git is never required** (its absence, or the repo not being a git repo
at all, degrades silently — never an error), and git status is **never**
used to decide whether a mutation is safe — content hashes are the only
authoritative safety signal. PortForge never runs `git commit`, `git
checkout`, `git reset`, or any other mutating git command.

## Known filesystem limitations

- `os.replace()` is atomic per-file on both POSIX and Windows, but two
  separate `os.replace()` calls (one per target file) are not a single
  atomic transaction at the OS level — Phase 8C achieves "no partial final
  state" through its own backup-and-restore-on-failure logic (see "Atomic
  multi-file apply"), not through a filesystem-level multi-file
  transaction (which doesn't exist in a portable form).
- Symlink-escape protection was verified by direct unit test on this
  development machine's filesystem semantics, but creating a symlink for
  the test itself requires elevated privilege on Windows without Developer
  Mode enabled — that specific test is skipped in that environment (the
  underlying `resolve_within_root()` check itself is platform-independent
  and unconditional; only the *test's own setup step* is affected).

## Kubernetes decision

**Deferred entirely**, not attempted in a reduced form. dotenv + Compose
already exercise the complete mutation/rollback architecture this phase
was scoped to prove (path safety, precondition hashing, atomic multi-file
apply, backup/rollback, ownership verification). Kubernetes manifests
have materially different semantics (multiple resource kinds, `kubectl
apply`'s own three-way-merge behavior, no single obvious "the port value"
location, cluster-context concerns) that would expand this phase's scope
well beyond proving the same architecture a third time. Proposed as a
distinct Phase 8C.1 (or later), building on this phase's `config_manager.py`
plan/apply/status/rollback shape rather than inventing a new one.

## Coding-agent usage

```
portforge project allocate --json          # Phase 8B/8A, unchanged
portforge config plan --allocation <id> --json
portforge config apply --allocation <id> --json
# read the project's own .env / compose.yaml -- ordinary file reads
portforge config status <mutation-id> --project-root . --json
portforge config rollback <mutation-id> --project-root . --json
portforge allocation release <id> --json    # separate from rollback
```

A provider-neutral simulation exercising exactly this sequence (subprocess
calls to the CLI only, never PortForge internals) is documented in the
final report's physical validation section.
