"""Platform-specific service restart adapters for agent self-upgrade (Phase 10).

Each function restarts the PortForge agent daemon via the OS-native service
manager. Service names/labels default to production constants and may be
overridden only via local environment variables (never from Central payload):

  PORTFORGE_WINDOWS_TASK_NAME
  PORTFORGE_LINUX_SERVICE_NAME
  PORTFORGE_MACOS_PLIST_LABEL
  PORTFORGE_MACOS_PLIST_PATH

Shell injection is impossible here: every subprocess call uses a list of
strings with no shell=True, and goes through run_subprocess().
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
    """Restart the PortForge user systemd unit (name from env or default)."""
    from ..subprocess_util import run_subprocess

    unit = _linux_service_name()
    result = run_subprocess(
        ["systemctl", "--user", "restart", unit],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"systemctl --user restart failed (rc={result.returncode}): {result.stderr.strip()}"
        )


def restart_via_launchctl(plist_path: Optional[str] = None) -> None:
    """Bootout and bootstrap the PortForge LaunchAgent plist."""
    from ..subprocess_util import run_subprocess

    if plist_path is None:
        plist_path = os.environ.get("PORTFORGE_MACOS_PLIST_PATH")
    if plist_path is None:
        label = _macos_plist_label()
        plist_path = str(Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")

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
