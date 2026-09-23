"""Platform-specific service restart adapters for agent self-upgrade (Phase 10).

Each function restarts the PortForge agent daemon via the OS-native service
manager. All service names/labels are hard-coded constants -- never sourced
from any Central payload field.

Shell injection is impossible here: every subprocess call uses a literal
list of strings with no user-supplied interpolation, shell=False (the
default), and goes through run_subprocess() which also enforces no-shell.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("portforge_agent.upgrade.platform_restart")

# Hard-coded service identifiers -- never from Central payload.
_WINDOWS_TASK_NAME = "PortForge Agent"
_LINUX_SERVICE_NAME = "portforge-agent.service"
_MACOS_PLIST_LABEL = "com.portforge.agent"


def restart_via_windows_scheduler() -> None:
    """End and re-run the 'PortForge Agent' scheduled task.

    /End is sent first (may fail if not running; ignored). /Run starts a
    new instance immediately. Both calls use the literal task name above --
    not a string from any network payload.
    """
    from ..subprocess_util import run_subprocess

    run_subprocess(
        ["schtasks", "/End", "/TN", _WINDOWS_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = run_subprocess(
        ["schtasks", "/Run", "/TN", _WINDOWS_TASK_NAME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Run failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def restart_via_systemd() -> None:
    """Restart portforge-agent.service via systemctl --user."""
    from ..subprocess_util import run_subprocess

    result = run_subprocess(
        ["systemctl", "--user", "restart", _LINUX_SERVICE_NAME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"systemctl --user restart failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def restart_via_launchctl(plist_path: Optional[str] = None) -> None:
    """Bootout and bootstrap the PortForge LaunchAgent plist.

    bootout (unload) is attempted first; errors are ignored in case the
    agent is not currently loaded. bootstrap (load+start) must succeed.
    """
    from ..subprocess_util import run_subprocess

    if plist_path is None:
        plist_path = str(
            Path.home() / "Library" / "LaunchAgents" / f"{_MACOS_PLIST_LABEL}.plist"
        )

    uid = os.getuid()
    target = f"gui/{uid}"

    run_subprocess(
        ["launchctl", "bootout", target, plist_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = run_subprocess(
        ["launchctl", "bootstrap", target, plist_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"launchctl bootstrap failed (rc={result.returncode}): {result.stderr.strip()}"
        )


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
