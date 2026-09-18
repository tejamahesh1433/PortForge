"""Common collector interface and shared helpers.

Every OS-specific collector implements :class:`BaseCollector`, isolating
platform quirks behind a single ``collect()`` method that returns a list of
:class:`~portforge_agent.models.DiscoveredPort`.
"""
from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from ..subprocess_util import run_subprocess

logger = logging.getLogger("portforge_agent.collectors")


class CollectorError(Exception):
    """Raised when a collector cannot run at all (missing tool, etc.).

    Discovery.py catches this so one broken collector never crashes a scan.
    """


@dataclass
class ProcessMetadata:
    """Best-effort metadata about the process that owns a socket.

    These are all RAW FACTS -- collectors gather them, they don't interpret
    them. Project/purpose detection (agent/portforge_agent/detection/) reads
    them as evidence but never writes back into this structure.
    """

    name: Optional[str] = None
    path: Optional[str] = None
    working_directory: Optional[str] = None
    command_line: Optional[List[str]] = None
    parent_pid: Optional[int] = None
    parent_name: Optional[str] = None
    parent_working_directory: Optional[str] = None


def safe_process_metadata(pid: Optional[int]) -> ProcessMetadata:
    """Look up process metadata without ever raising.

    Processes can disappear between being observed on a socket and being
    inspected, and looking up another user's process (or its parent) can
    raise a permission error. Both cases are expected, not exceptional, so
    we swallow them and return whatever partial metadata we managed to
    collect.
    """
    if pid is None or pid <= 0:
        return ProcessMetadata()

    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        logger.debug("psutil unavailable; cannot resolve metadata for pid=%s", pid)
        return ProcessMetadata()

    metadata = ProcessMetadata()
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return metadata

    try:
        metadata.name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    try:
        metadata.path = proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    try:
        metadata.working_directory = proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    try:
        cmdline = proc.cmdline()
        metadata.command_line = list(cmdline) if cmdline else None
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    parent_pid: Optional[int] = None
    try:
        parent_pid = proc.ppid()
        metadata.parent_pid = parent_pid
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    if parent_pid:
        try:
            parent = psutil.Process(parent_pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            parent = None
        if parent is not None:
            try:
                metadata.parent_name = parent.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            try:
                metadata.parent_working_directory = parent.cwd()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    return metadata


def run_command(args: List[str], timeout: float = 10.0) -> str:
    """Run a read-only discovery command and return its stdout.

    Non-zero exit codes are tolerated (e.g. ``lsof`` exits 1 when it could
    not read *some* sockets it doesn't own, while still printing everything
    it could) as long as there is stdout to parse. Only a missing binary or
    a hard timeout is treated as a collector-level failure.

    Goes through subprocess_util.run_subprocess rather than calling
    subprocess.run directly, so on Windows this never flashes a console
    window for the child (e.g. docker.exe's periodic probes) even when
    running under a background/non-interactive parent -- see that
    module's docstring for the physical reproduction and rationale.
    """
    try:
        proc = run_subprocess(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CollectorError(f"Required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectorError(f"Command timed out: {' '.join(args)}") from exc

    if proc.returncode != 0 and not proc.stdout.strip():
        raise CollectorError(
            f"Command '{' '.join(args)}' failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )

    if proc.stderr.strip():
        logger.debug("Command '%s' stderr: %s", " ".join(args), proc.stderr.strip())

    return proc.stdout


class BaseCollector(ABC):
    """Interface every OS-specific port collector must implement."""

    @abstractmethod
    def collect(self) -> List["DiscoveredPort"]:  # noqa: F821 - see models.py
        """Return the list of ports currently in use on this host.

        Implementations must never raise for expected, per-item failures
        (a vanished process, a permission-denied lookup); they should skip
        that item and keep going. Raising :class:`CollectorError` is
        reserved for the collector being unable to run at all.
        """
        raise NotImplementedError
