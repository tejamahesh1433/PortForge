"""Platform-specific service restart adapters for agent self-upgrade.

Each function restarts the PortForge agent daemon via the OS-native service
manager. Service names/labels default to production constants and may be
overridden only via local environment variables (never from Central payload):

  PORTFORGE_WINDOWS_TASK_NAME
  PORTFORGE_LINUX_SERVICE_NAME
  PORTFORGE_MACOS_PLIST_LABEL
  PORTFORGE_MACOS_PLIST_PATH

Shell injection is impossible here: every subprocess call uses a list of
strings with no shell=True. macOS upgrade restart (Phase 23C) additionally
detaches an out-of-band kickstarter so a published 1.5.2 process that lazily
imports this module after pip install never bootouts itself under
KeepAlive.SuccessfulExit=false.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("portforge_agent.upgrade.platform_restart")

# Default production identifiers -- never from Central payload.
_DEFAULT_WINDOWS_TASK_NAME = "PortForge Agent"
_DEFAULT_LINUX_SERVICE_NAME = "portforge-agent.service"
_DEFAULT_MACOS_PLIST_LABEL = "com.portforge.agent"


def _windows_task_name() -> str:
    return os.environ.get("PORTFORGE_WINDOWS_TASK_NAME") or _DEFAULT_WINDOWS_TASK_NAME


def _linux_service_name() -> str:
    return os.environ.get("PORTFORGE_LINUX_SERVICE_NAME") or _DEFAULT_LINUX_SERVICE_NAME


def _macos_plist_label() -> str:
    return os.environ.get("PORTFORGE_MACOS_PLIST_LABEL") or _DEFAULT_MACOS_PLIST_LABEL


def restart_via_windows_scheduler() -> None:
    """End and re-run the PortForge Scheduled Task (name from env or default).

    Qualification override: PORTFORGE_WINDOWS_RESTART_HELPER may point to an
    absolute .cmd/.exe/.bat that restarts only the disposable agent. Never
    sourced from Central.
    """
    from ..subprocess_util import run_subprocess

    helper = os.environ.get("PORTFORGE_WINDOWS_RESTART_HELPER")
    if helper:
        helper_path = Path(helper)
        if not helper_path.is_file():
            raise RuntimeError(f"PORTFORGE_WINDOWS_RESTART_HELPER not found: {helper}")
        result = run_subprocess(
            [str(helper_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"restart helper failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        return

    task = _windows_task_name()
    run_subprocess(
        ["schtasks", "/End", "/TN", task],
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = run_subprocess(
        ["schtasks", "/Run", "/TN", task],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Run failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def restart_via_systemd() -> None:
    """Restart the PortForge user systemd unit (name from env or default).

    Uses --no-block so that systemd enqueues the restart asynchronously and
    returns exit 0 immediately, before the old process is stopped.  Without
    --no-block, systemctl waits for the unit to become active; because this
    process *is* the unit, it cannot reach the active state while we are still
    running the subprocess call — the call would block indefinitely or until
    the timeout kills it.  --no-block avoids that deadlock entirely.

    Success is not inferred from the return code alone.  Central's heartbeat
    reconciliation is the authoritative success criterion: VERIFYING_HEALTH and
    SUCCEEDED are only reached once the new process reconnects with the expected
    agent_version.
    """
    from ..subprocess_util import run_subprocess

    unit = _linux_service_name()
    result = run_subprocess(
        ["systemctl", "--user", "restart", "--no-block", unit],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"systemctl --user restart --no-block failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def restart_via_launchctl(plist_path: Optional[str] = None) -> None:
    """Schedule an out-of-band LaunchAgent kickstart (Phase 23C).

    Never ``bootout``/``bootstrap`` the agent job from inside the running
    agent process. Published 1.5.2 upgrades lazily import this module *after*
    pip installs the target into the same venv; the previous self-bootout
    tore down the job mid-sequence and, with KeepAlive.SuccessfulExit=false,
    left launchd stopped with no helper to recover.

    Instead: detach a short-lived kickstarter that waits for *this* PID to
    exit, then ``launchctl kickstart -k`` (bootstrap the known plist first
    if kickstart fails). Label and plist path come only from local env
    overrides or PortForge defaults — never from Central.
    """
    import subprocess
    import sys

    if plist_path is None:
        plist_path = os.environ.get("PORTFORGE_MACOS_PLIST_PATH")
    label = _macos_plist_label()
    if plist_path is None:
        plist_path = str(Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")

    _validate_macos_restart_identity(label, plist_path)

    getuid = getattr(os, "getuid", None)
    if getuid is None:  # pragma: no cover - Darwin-only path
        raise RuntimeError("macOS upgrade restart requires os.getuid")
    domain = f"gui/{getuid()}"
    wait_pid = os.getpid()
    # Detached kickstarter: wait for caller exit, then start the known job.
    # argv is a fixed list (no shell). Label/plist already validated.
    kick_code = (
        "import os, subprocess, sys, time\n"
        f"pid = {int(wait_pid)}\n"
        "deadline = time.monotonic() + 120\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "        time.sleep(0.25)\n"
        "    except ProcessLookupError:\n"
        "        break\n"
        "    except PermissionError:\n"
        "        break\n"
        "time.sleep(0.5)\n"
        f"domain = {domain!r}\n"
        f"label = {label!r}\n"
        f"plist = {plist_path!r}\n"
        "r = subprocess.run(\n"
        "    ['launchctl', 'kickstart', '-k', f'{domain}/{label}'],\n"
        "    capture_output=True, text=True,\n"
        ")\n"
        "if r.returncode == 0:\n"
        "    raise SystemExit(0)\n"
        "subprocess.run(\n"
        "    ['launchctl', 'bootstrap', domain, plist],\n"
        "    capture_output=True, text=True,\n"
        ")\n"
        "r2 = subprocess.run(\n"
        "    ['launchctl', 'kickstart', '-k', f'{domain}/{label}'],\n"
        "    capture_output=True, text=True,\n"
        ")\n"
        "raise SystemExit(0 if r2.returncode == 0 else (r2.returncode or 1))\n"
    )
    subprocess.Popen(
        [sys.executable, "-c", kick_code],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    logger.info(
        "Scheduled macOS upgrade kickstart for label=%s (wait_pid=%s)",
        label,
        wait_pid,
    )


def _validate_macos_restart_identity(label: str, plist_path: str) -> None:
    """Fail closed on non-PortForge labels or plists outside LaunchAgents."""
    import re

    if not re.fullmatch(r"com\.portforge\.agent([.][A-Za-z0-9._-]+)?", label or ""):
        raise RuntimeError(
            f"Refusing macOS upgrade restart for non-PortForge LaunchAgent label: {label!r}"
        )
    path = Path(plist_path).expanduser()
    expected_dir = Path.home() / "Library" / "LaunchAgents"
    try:
        path.resolve().relative_to(expected_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing macOS upgrade restart for plist outside LaunchAgents: {plist_path!r}"
        ) from exc
    if path.suffix != ".plist":
        raise RuntimeError(f"Refusing macOS upgrade restart for non-plist path: {plist_path!r}")


def restart_service() -> None:
    """Detect the running platform and restart the PortForge agent service.

    Raises RuntimeError if restart fails or if the platform is not supported.
    Never executes any string sourced from the Central payload.
    """
    from .. import platform as pf

    os_type = pf.detect_os()
    if os_type == pf.OperatingSystem.WINDOWS:
        restart_via_windows_scheduler()
    elif os_type == pf.OperatingSystem.LINUX:
        restart_via_systemd()
    elif os_type == pf.OperatingSystem.MACOS:
        restart_via_launchctl()
    else:
        raise RuntimeError(
            f"Unsupported platform for automatic service restart: {os_type.value}. "
            "The upgrade wheel was installed but the agent must be restarted manually."
        )
