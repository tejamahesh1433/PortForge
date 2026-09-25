"""Detached upgrade helper process (Phase 23).

Run as: python -m portforge_agent.upgrade.helper [--data-dir PATH]

Reads the local typed handoff record written by the running agent, waits for
the old agent process to exit, re-verifies the artifact SHA-256, installs the
wheel via the recorded python executable, and restarts the PortForge service.

Security model -- this module MUST NOT accept:
  - arbitrary URLs (download already completed + verified by the agent)
  - arbitrary executables or shell commands
  - arbitrary service names (only PortForge platform constants / env overrides)
  - arbitrary filesystem destinations (confined under data_dir/upgrade/)

The only accepted argument is --data-dir for qualification overrides.
All install parameters come exclusively from the local, signed-by-presence
handoff record, never from the command line.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .handoff import (
    HelperLockError,
    acquire_helper_lock,
    artifact_path_for,
    helper_log_path,
    install_wheel,
    read_handoff,
    validate_handoff,
    write_handoff,
)

_PID_WAIT_TIMEOUT = 120   # seconds
_PID_POLL_INTERVAL = 0.25  # seconds

logger = logging.getLogger("portforge_agent.upgrade.helper")


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID currently exists."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) is unreliable on Windows (WinError 87 for missing PIDs).
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists; we lack permission to signal it
    except OSError:
        return False


def _wait_for_pid_exit(pid: int) -> bool:
    """Poll until pid disappears or timeout. Returns True if pid exited."""
    if not _pid_alive(pid):
        return True
    deadline = time.monotonic() + _PID_WAIT_TIMEOUT
    while _pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PID_POLL_INTERVAL)
    return True


def _verify_sha256(path: Path, expected_hex: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise ValueError(
            f"SHA-256 mismatch: expected {expected_hex.lower()}, got {actual.lower()}"
        )


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setFormatter(fmt)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(sh)


def _set_stage(handoff: Dict[str, Any], stage: str, data_dir: Optional[Path], **extra: Any) -> None:
    handoff["stage"] = stage
    handoff.update(extra)
    try:
        write_handoff(handoff, data_dir)
    except Exception as exc:
        logger.warning("Could not persist stage=%s: %s", stage, exc)


def _run_helper(data_dir: Optional[Path]) -> int:
    """Core helper logic; returns the process exit code."""

    # 1. Read and structurally validate the handoff record.
    try:
        handoff = read_handoff(data_dir)
    except FileNotFoundError:
        logger.error("No handoff file found; nothing to do")
        return 1
    except Exception as exc:
        logger.error("Failed to read handoff: %s", exc)
        return 1

    try:
        validate_handoff(handoff)
    except ValueError as exc:
        logger.error("Handoff validation failed: %s", exc)
        return 1

    # 2. Verify host_id against the local identity file (stale/foreign host guard).
    try:
        from .. import paths as _paths
        from ..identity import load_host_identity
        id_path = (data_dir or _paths.data_dir()) / "host.json"
        identity = load_host_identity(id_path)
        if identity is None:
            logger.error("No host identity file found at %s; refusing install", id_path)
            return 1
        if handoff.get("host_id") != identity.host_id:
            logger.error(
                "host_id mismatch: handoff=%r local=%r; refusing install",
                handoff.get("host_id"), identity.host_id,
            )
            return 1
    except Exception as exc:
        logger.error("Cannot verify host identity: %s", exc)
        return 1

    stage = handoff.get("stage", "")

    # 3. Idempotent: already finished.
    if stage in ("done", "restarted"):
        logger.info("Handoff stage is %r; nothing to do", stage)
        return 0

    # 4. Mark running (only if still prepared; running means we or a prior
    #    helper instance already started — continue regardless).
    if stage == "prepared":
        _set_stage(handoff, "running", data_dir)

    # 5. Wait for the old agent process to exit.
    old_pid = int(handoff.get("old_pid", 0))
    if old_pid and old_pid != os.getpid():
        logger.info("Waiting for old agent process (pid=%d) to exit...", old_pid)
        if not _wait_for_pid_exit(old_pid):
            reason = f"Timed out ({_PID_WAIT_TIMEOUT}s) waiting for old process (pid={old_pid})"
            logger.error(reason)
            _set_stage(handoff, "failed", data_dir, failure_reason=reason)
            return 1
        logger.info("Old agent process (pid=%d) has exited", old_pid)

    # 6. Re-verify artifact SHA-256 before touching pip.
    try:
        artifact = artifact_path_for(handoff, data_dir)
    except (KeyError, ValueError) as exc:
        reason = f"Invalid artifact path: {exc}"
        logger.error(reason)
        _set_stage(handoff, "failed", data_dir, failure_reason=reason)
        return 1

    try:
        _verify_sha256(artifact, handoff["artifact_sha256"])
    except Exception as exc:
        reason = f"SHA-256 re-verification failed: {exc}"
        logger.error(reason)
        _set_stage(handoff, "failed", data_dir, failure_reason=reason)
        return 1

    # 7. pip install (skip if already done on a previous run).
    if stage != "installed":
        python_exe = str(handoff["python_executable"])
        logger.info("Installing %s via %s", artifact.name, python_exe)
        try:
            install_wheel(python_exe, artifact)
        except Exception as exc:
            reason = f"pip install failed: {exc}"
            logger.error(reason)
            _set_stage(handoff, "failed", data_dir, failure_reason=reason)
            return 1
        _set_stage(handoff, "installed", data_dir)

    # 8. Restart PortForge service via platform adapter.
    logger.info("Requesting PortForge service restart...")
    try:
        from .platform_restart import restart_service
        restart_service()
    except Exception as exc:
        # Log but do not treat as fatal: the service manager may still succeed
        # (mirrors the Phase 10 handler rationale for this same case).
        logger.warning(
            "Service restart call raised — service manager may still handle it: %s", exc
        )

    _set_stage(handoff, "restarted", data_dir)
    _set_stage(handoff, "done", data_dir)
    logger.info("Upgrade helper finished: stage=done")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portforge_agent.upgrade.helper",
        description="PortForge detached upgrade helper (Phase 23). Internal use only.",
    )
    # --data-dir is the ONLY accepted argument: qualification / multi-instance only.
    # No --url, --command, --service, --executable, or --script flags.
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Override PortForge data directory (qualification only)",
    )
    args = parser.parse_args(argv)

    data_dir: Optional[Path] = Path(args.data_dir) if args.data_dir else None

    log_path = helper_log_path(data_dir)
    _setup_logging(log_path)
    logger.info("Upgrade helper started (pid=%d)", os.getpid())

    try:
        with acquire_helper_lock(data_dir):
            return _run_helper(data_dir)
    except HelperLockError:
        # Another helper instance holds the lock. If there's a valid handoff
        # with a nonce, that instance is handling the same upgrade — exit 0.
        try:
            handoff = read_handoff(data_dir)
            if handoff.get("nonce"):
                logger.info(
                    "Another helper is running (nonce=%s); deferring", handoff.get("nonce")
                )
                return 0
        except Exception:
            pass
        logger.warning("Helper lock held and no valid handoff nonce; exiting 1")
        return 1
    except Exception as exc:
        logger.error("Upgrade helper fatal error: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
