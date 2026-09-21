# Phase 8C Audit: Safe Config/File Integration

Written before any Phase 8C implementation, per instruction.

## 1. Phase 8B manifest schema

`agent/portforge_agent/manifest.py`: `ProjectManifest(version, project, host,
requests, request_id)`, `ManifestPortRequest(name, purpose, protocol,
preferred_port)`. Top-level keys are strictly whitelisted
(`_TOP_LEVEL_KEYS = {"version", "project", "target", "ports", "request_id"}`)
— adding `config:` requires extending this set (additive, backward
compatible: an old manifest without `config:` still validates identically,
since the key is optional).

## 2. Allocation output / persistence

`AllocationOut` (`backend/app/schemas/allocation.py`): `allocation_id`,
`project`, `host {id, hostname}`, `status` ("active"/"released"),
`allocations: [{name, purpose, protocol, port, reservation_id}]`,
`validation`, `created_at`, `released_at`. This is exactly what
`GET /api/allocations/{id}` returns — Phase 8C's `config plan`/`apply` will
call this endpoint (via the existing `central_client.get_allocation()`)
to fetch ownership/status/port data, never re-deriving it locally. `status
!= "active"` is how "allocation is inactive" is detected — no new backend
field needed.

## 3. CLI architecture

Established pattern (Phase 8A/8B): `cli.py` subparsers →
`_cmd_*(args) -> int` handlers → structured JSON errors
`{"error": {"code", "message", "details"}}` on `--json`, human text
otherwise; exit 0/1/2 convention. `project_adapter.py` is the
provider-neutral boundary. Phase 8C follows the identical shape: a new
`config` subparser with `plan`/`apply`/`status`/`rollback` sub-subcommands,
a new `config_manager.py` module (mirrors `manifest.py`/
`project_adapter.py`'s separation: pure logic, CLI just wires it up).

## 4. Project-root handling

Phase 8B has no "project root" concept beyond "the manifest's own
directory" (manifest discovery is cwd-only, no upward walk — see
`docs/phase8b_manifest_audit.md`). Phase 8C **defines project root as the
manifest file's parent directory** by default (task's own instruction).
All `config.dotenv[].file` / `config.compose[].file` paths are resolved
relative to this root and must stay inside it after resolving `..` and
symlinks (see §7 below).

## 5. Existing `.env` handling

**None.** No dotenv parser/writer exists anywhere in the codebase today.
Phase 8C introduces the first one (`agent/portforge_agent/dotenv_editor.py`),
hand-rolled (stdlib only — a `.env` file's grammar is simple enough
`configparser`/third-party libraries add nothing meaningful over careful
line-based parsing, consistent with this project's "add a dependency only
when it buys something stdlib genuinely can't" discipline).

## 6. Existing Compose parsing/detection

`detection/evidence.py::PROJECT_MARKERS` already recognizes
`docker-compose.yml`/`.yaml`/`compose.yml`/`.yaml` as filenames, but
**purely as a presence signal** for project-type detection — their content
is never parsed. There is no Compose YAML content model anywhere in the
codebase. Phase 8C introduces the first one.

## 7. Existing YAML dependency

`pyyaml>=6.0`, `yaml.safe_load()` only, used for `.portforge.yml` and
Phase 8B's `portforge.yml` manifest parsing. **Critical limitation for
Compose**: `yaml.safe_load()` + `yaml.safe_dump()` is a lossy round-trip —
it does not preserve comments, key ordering, anchors, or flow-vs-block
style. Task §16 explicitly requires preserving Compose file style and
allows evaluating a round-trip library. **Decision: add `ruamel.yaml` as a
new, explicit dependency**, used *only* by the Compose editor
(`compose_editor.py`) — manifest parsing (`manifest.py`) continues to use
plain `pyyaml.safe_load()` unchanged, since that already has no
preservation requirement (manifests aren't rewritten, only read).
`ruamel.yaml`'s round-trip mode (`YAML(typ="rt")`) is the standard tool
for exactly this ("preserve comments/anchors/style, only change specific
scalar values") and, used this way, does not construct arbitrary Python
objects — it stays in the same safety class as `safe_load` (no code
execution from file content), it simply also remembers formatting
metadata `safe_load` throws away. This is a real, additive dependency
(not vendored/reimplemented), justified because hand-rolling
comment/anchor/style-preserving YAML editing would be substantially riskier
and larger in scope than the library doing it correctly.

## 8. Atomic file-writing utilities (reused, not reinvented)

`reservations/storage.py::ReservationStore.save()` already establishes the
exact pattern Phase 8C needs: `tempfile.mkstemp()` in the **same
directory** as the target (so the eventual `os.replace()` is on the same
filesystem/volume), write, `flush()` + `os.fsync()`, then `os.replace()`
(atomic on both POSIX `rename(2)` and Windows `MoveFileExW` +
`MOVEFILE_REPLACE_EXISTING`). Phase 8C's `atomic_write()` helper
(`config_files.py`) is a direct generalization of this existing function
to an arbitrary path, not a new pattern.

## 9. Backup utilities

**None exist.** Phase 8C introduces the first one: before any real file is
replaced, its original bytes are copied into a mutation-scoped backup
directory (see §22 below) using the same atomic-write primitive.

## 10. Git-awareness

**None exists anywhere in the codebase.** Phase 8C adds a small, entirely
optional helper (`git_status.py`) that shells out to `git status
--porcelain=v1 -- <path>` per target file, wrapped in try/except — git
being absent, not a repo, or the call failing degrades to "unknown", never
an error and never a requirement. Used purely as **informational metadata**
in `config plan`'s output (task §31: "git must not be required... never
overwrite user edits based merely on Git state"). No git command that
mutates anything (`commit`/`checkout`/`reset`/`add`) is ever invoked.

## Risks identified and how each is addressed

| Risk | Mitigation |
|---|---|
| **File mutation risk**: a crash mid-write corrupts a real project file | Reuse the proven write-temp-then-`os.replace()` pattern; never open the real target file for writing directly. |
| **Symlink risk**: `.env` or `compose.yaml` is (or a parent directory is) a symlink pointing outside the project root | Resolve every target path with `Path.resolve(strict=False)` and verify the resolved path is inside the resolved project root **before any read or write**; reject with `CONFIG_PATH_OUTSIDE_PROJECT` otherwise. Tested explicitly (§36). |
| **Path traversal risk**: `file: ../outside.env` or an absolute path outside the root | Same resolve-and-contain check as above, applied uniformly to every declared `file:` — traversal and symlink escape are the same check, not two different code paths, so there's one thing to get right. |
| **Encoding/newline concerns** | `.env` files are read/written as UTF-8 text; original newline style (`\n` vs `\r\n`) is detected from the existing file and preserved on rewrite (Python's universal-newlines text mode is avoided for the dotenv editor specifically so `\r\n` files aren't silently normalized to `\n`). Compose files are handled the same way via `ruamel.yaml`'s own newline-preserving round-trip. |
| **Compose syntax concerns**: short (`"8000:8000"`) vs long (`target:`/`published:`/`protocol:`) syntax, existing anchors, flow vs block style | `ruamel.yaml` round-trip mode preserves whatever style the file already uses; the editor only mutates the specific scalar node(s) a mapping targets, never rewrites the surrounding structure. |
| **dotenv syntax concerns**: quoting, inline comments, `export` prefixes, blank lines | Hand-rolled line-based editor that recognizes `KEY=value` (optionally `export KEY=value`), preserves comment lines and blank lines verbatim, and only rewrites the value portion of a matched line — never touches lines it doesn't recognize as the mapped key. |
| **Rollback requirements** | Every apply captures the original bytes of every touched file before any write, keyed by a `mutation_id`; rollback restores those exact bytes and is refused (not overridden) if the live file's content hash no longer matches what was applied. |
| **Working-tree safety**: never surprise a developer's own edits | Precondition content hashing at both plan→apply and apply→rollback boundaries (`CONFIG_CHANGED_SINCE_PLAN` / `CONFIG_CHANGED_SINCE_APPLY`); git status is informational only, never load-bearing for safety decisions. |

## Decisions carried into implementation

| Question | Decision |
|---|---|
| Where does `config:` live? | Inside `portforge.yml`, as a new optional top-level key — additive, does not touch `.portforge.yml`. |
| Project root | Manifest's parent directory, always. No separate `--project-root` flag in this phase (not requested; the task allows it "only if needed" and the manifest-directory default already covers every physical test case). |
| Backup/mutation storage | `<project_root>/.portforge/mutations/<mutation_id>/` — a **directory**, sibling to (never inside) `.portforge.yml`/`portforge.yml`. Contains a `record.json` (mutation metadata) and a `files/` subdirectory holding pre-apply byte-for-byte backups, named by a safe, collision-free encoding of each file's project-relative path. |
| New dependency | `ruamel.yaml`, Compose editing only. |
| Plan persistence | Plans ARE persisted (as `record.json` with `status: "PLANNED"`) specifically so `apply` can be invoked without repeating the plan computation and so `CONFIG_CHANGED_SINCE_PLAN` has a concrete original-hash baseline to compare against — see §19/§20 of the implementation. |
| Kubernetes | Deferred entirely (see final report) — dotenv + Compose alone already exercise the full mutation/rollback architecture this phase must prove; adding a third target now would expand scope without adding architectural coverage. |
