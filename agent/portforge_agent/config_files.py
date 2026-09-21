"""Phase 8C: low-level, project-root-bounded file I/O primitives.

Every real project file Phase 8C touches goes through exactly two
functions here: `resolve_within_root()` before any read, and
`atomic_write()` before any write. This is deliberate -- it means there is
exactly one place symlink/traversal safety is enforced, and exactly one
place a crash-mid-write can happen, both reused everywhere (dotenv editor,
compose editor, backup snapshots, and the real target-file replace) rather
than each caller re-implementing its own version.

`atomic_write()` generalizes the write-temp-then-`os.replace()` pattern
already proven in `reservations/storage.py::ReservationStore.save()` (see
docs/phase8c_config_audit.md §8) to an arbitrary path.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional


class ConfigPathError(Exception):
    """Raised by `resolve_within_root` -- always maps to
    CONFIG_PATH_OUTSIDE_PROJECT at the CLI/config_manager layer.
    """


def resolve_within_root(project_root: Path, relative_file: str) -> Path:
    """Resolves `relative_file` against `project_root` and verifies the
    RESOLVED path (symlinks followed, `..` collapsed) is still inside the
    RESOLVED project root. This is the single safety check protecting
    against both path traversal (`../outside.env`, an absolute path
    outside the root) and symlink escape (an in-project symlink whose
    target resolves outside the root) -- see
    docs/phase8c_config_audit.md's risk table for why these are treated as
    one check, not two.

    `strict=False` is required: for a dotenv file that doesn't exist yet
    (Phase 8C may create one), `Path.resolve(strict=True)` would raise
    before containment could even be checked.
    """
    root = project_root.resolve(strict=True)
    candidate = (project_root / relative_file).resolve(strict=False)

    try:
        candidate.relative_to(root)
    except ValueError:
        raise ConfigPathError(
            f"'{relative_file}' resolves to {candidate}, which is outside the project root {root}."
        )
    return candidate


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file_bytes(path: Path) -> Optional[bytes]:
    """Returns the file's raw bytes, or None if it doesn't exist. Any
    other OSError (permission denied, etc.) propagates -- that's a real
    operational problem, not "file absent."
    """
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def sha256_of_path(path: Path) -> Optional[str]:
    data = read_file_bytes(path)
    return None if data is None else sha256_bytes(data)


def atomic_write(path: Path, content: bytes) -> None:
    """Write-temp-then-`os.replace()`: a crash mid-write leaves the
    original file (or nothing, if `path` didn't exist yet) untouched,
    never a half-written target. The temp file is created in the SAME
    directory as `path` so the final `os.replace()` is same-filesystem
    (required for atomicity on both POSIX and Windows).
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=".portforge-config-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
