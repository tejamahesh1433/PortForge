"""Cross-platform advisory file lock guarding reservation read-modify-write
cycles.

Deliberately stdlib-only, not a third-party dependency: `msvcrt` (Windows)
and `fcntl` (POSIX/macOS/Linux) are both always available wherever Python
itself runs, so a `flock`/`portalocker`/`filelock` dependency would add
nothing a small platform branch doesn't already solve here.

The lock is a separate file (reservations.lock) from the data file itself,
so locking never interferes with the atomic temp-file-then-replace write in
storage.py.
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_TIMEOUT = 10.0
_POLL_INTERVAL = 0.05


class LockTimeoutError(Exception):
    """Raised when the reservation lock could not be acquired in time
    (another PortForge process is very likely mid-operation).
    """


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def reservation_lock(lock_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[None]:
    """Hold an exclusive lock on `lock_path` for the duration of the `with`
    block. Blocks (polling) up to `timeout` seconds, then raises
    LockTimeoutError. Always releases and closes the file handle on exit,
    even if the body raises.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        # msvcrt.locking() on Windows needs at least one real byte in the
        # region it locks; an empty lock file would make LK_NBLCK behave
        # inconsistently, so ensure the file is never zero-length.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")

        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeoutError(
                    f"Could not acquire reservation lock at {lock_path} within {timeout}s "
                    "(another portforge process may be holding it)"
                )
            time.sleep(_POLL_INTERVAL)

        yield
    finally:
        _unlock(fd)
        os.close(fd)
