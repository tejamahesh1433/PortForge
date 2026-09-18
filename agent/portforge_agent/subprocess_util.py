"""Centralized external-command execution for everything PortForge's
agent runs on the user's behalf -- collector discovery commands
(collectors/base.py's run_command, used by docker/linux/macos) and
native service-manager calls (service_ops.py's _run, schtasks/launchctl/
systemctl). Both go through run_subprocess() here rather than calling
subprocess.run directly, so Windows console-window suppression lives in
exactly one place instead of being patched into each call site.

Physically reproduced on NTMKEYA: the scheduled task correctly launches
the agent via pythonw.exe (windowless), but pythonw.exe having no console
of its own does NOT stop Windows from creating a brand-new console (and
a conhost.exe host process) for a CHILD console application it spawns --
e.g. docker.exe's periodic version/ps probes -- unless that's explicitly
suppressed on the child's own process-creation call. That's the visible
flashing-window symptom this module fixes, for every PortForge-owned
subprocess call, not just Docker's.

Never introduces shell=True, never redirects through cmd.exe, and every
flag added here is Windows-only and applied only when actually running
on Windows -- POSIX behavior (macOS/Linux) is completely unaffected,
verified by never even referencing the Windows-only subprocess
attributes (CREATE_NO_WINDOW, STARTUPINFO, ...) unless sys.platform ==
"win32" first.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict, List, Optional

_IS_WINDOWS = sys.platform == "win32"


def _windows_no_window_kwargs() -> Dict[str, Any]:
    """Extra subprocess.run()/Popen() kwargs that stop Windows from
    creating a visible console window for a child console application.

    Needed regardless of whether the calling process is pythonw.exe or
    python.exe -- pythonw.exe merely lacks a console of its own; it does
    not, by itself, stop Windows from allocating a new one for a
    console-subsystem child process. Two complementary mechanisms, kept
    together for the widest compatibility:

    - `creationflags=CREATE_NO_WINDOW`: the documented, primary way to
      tell CreateProcess() never to allocate a console for the child at
      all.
    - `STARTUPINFO` with `STARTF_USESHOWWINDOW` / `wShowWindow=SW_HIDE`:
      belt-and-suspenders for child processes/wrappers that inspect the
      startup info's show-window hint rather than relying solely on the
      absence of a console.

    Only ever called after confirming `_IS_WINDOWS` -- every attribute
    referenced here (`subprocess.CREATE_NO_WINDOW`, `STARTUPINFO`, ...)
    only exists in the `subprocess` module on Windows.
    """
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def run_subprocess(
    args: List[str], *, timeout: Optional[float] = None, **kwargs: Any
) -> subprocess.CompletedProcess:
    """subprocess.run(), with Windows console-window suppression applied
    automatically when running on Windows. Every other behavior --
    stdout/stderr capture, return code, `FileNotFoundError` on a missing
    executable, `subprocess.TimeoutExpired` on a timeout, and every
    caller-supplied kwarg (`capture_output`, `text`, `check`, ...) --
    passes straight through unchanged; this function only ever adds the
    Windows-only kwargs above, never removes or overrides a caller's own.
    Never passes `shell=True` and never accepts one implicitly: `args`
    must always be a list, exactly like plain `subprocess.run`.
    """
    if _IS_WINDOWS:
        windows_kwargs = _windows_no_window_kwargs()
        windows_kwargs.update(kwargs)
        kwargs = windows_kwargs
    return subprocess.run(args, timeout=timeout, **kwargs)
