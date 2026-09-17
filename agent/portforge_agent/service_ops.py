"""Native service-manager OPERATIONS: install/start/stop/status/uninstall.

Every call into a native tool (schtasks.exe on Windows, launchctl on
macOS, systemctl on Linux) goes through the `_run()` wrapper below, so
unit tests can mock exactly one thing per platform and never actually
install/start/stop a real host service -- see the project's test suite
(tests/test_service_ops.py) for how that mocking is done.

See service_gen.py for the pure, side-effect-free definition generation
this module consumes (command lines, plist bytes, unit file text). This
module is only concerned with: writing the generated definition to its
platform-conventional location, invoking the native tool to
register/start/stop/query/remove it, and translating the result into a
uniform ServiceOpResult the CLI layer can render and turn into an exit
code.

Do not modify: ExpressVPN, Tailscale, Docker's own configuration, the
Windows firewall, SSH configuration, or any unrelated systemd unit or
launchd agent/daemon -- every operation below addresses only PortForge's
own task/label/unit name (service_gen.WINDOWS_TASK_NAME /
service_gen.LAUNCHD_LABEL / service_gen.SYSTEMD_UNIT_NAME) and touches no
other native service definition.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from . import paths
from . import platform as pf
from . import service_gen as gen

# Seconds to wait between a controlled bootout and the follow-up bootstrap
# when reinstalling over an already-registered (possibly running) LaunchAgent.
# launchd needs this time to fully drain the process before it will accept a
# new bootstrap for the same label; without the pause the bootstrap returns
# errno 5 (Input/output error) non-deterministically.
_LAUNCHD_BOOTOUT_SETTLE_SECS: float = 2.0


@dataclass
class ServiceOpResult:
    success: bool
    message: str
    detail: Optional[str] = None
    returncode: Optional[int] = None


class UnsupportedPlatformError(Exception):
    """Raised when service management is attempted on an OS PortForge's
    service_ops doesn't implement (only Windows/macOS/Linux are supported;
    OperatingSystem.UNKNOWN always raises this).
    """


def _run(args: List[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    """The single indirection point every native tool call goes through.
    Deliberately thin (no CollectorError-style exception translation) so
    tests can `patch("portforge_agent.service_ops._run")` once per test and
    control returncode/stdout/stderr directly for every scenario.
    """
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


# ---------------------------------------------------------------------------
# Windows: Task Scheduler
# ---------------------------------------------------------------------------

# Physical validation on a real (non-admin) Windows host found that some
# environments refuse *any* schtasks.exe create/query/run/end/delete call
# for a standard user -- confirmed via a control test with a throwaway task
# name and the exact same /SC ONLOGON /RL LIMITED flags PortForge uses,
# which failed identically. That means "no elevation required" does not
# hold universally: it's the intended, common case, but this environment's
# own Task Scheduler ACL/policy can still require an elevated session. Per
# the "must not silently require Administrator privileges unless genuinely
# necessary; if elevation is required for a particular mode, detect and
# explain it" requirement, every Windows operation below recognizes this
# specific failure and replaces schtasks's bare "Access is denied" with an
# actionable explanation instead of a raw pass-through error.
_WINDOWS_ELEVATION_HINT = (
    "Task Scheduler refused this operation for the current (non-administrator) "
    "user. Some Windows environments restrict schtasks.exe task creation/"
    "management to elevated sessions even for a per-user ONLOGON task at the "
    "standard (LIMITED) run level -- this is a local Windows/Group Policy "
    "restriction, not something PortForge can route around. Retry from an "
    "elevated ('Run as administrator') PowerShell or Command Prompt, or ask "
    "your system administrator to grant Task Scheduler access to this account."
)


def _is_access_denied(text: str) -> bool:
    return "access is denied" in text.lower()


def _windows_failure_result(
    result: subprocess.CompletedProcess, *, generic_message: str, denied_message: str
) -> ServiceOpResult:
    """Build the ServiceOpResult for a failed schtasks.exe call, detecting
    the environment-requires-elevation case (see _WINDOWS_ELEVATION_HINT
    above) and giving it a distinct, actionable message rather than
    surfacing schtasks's bare "Access is denied" unexplained.
    """
    detail = (result.stderr or result.stdout).strip()
    if _is_access_denied(detail):
        return ServiceOpResult(
            False, denied_message, detail=f"{_WINDOWS_ELEVATION_HINT}\n\nRaw error: {detail}",
            returncode=result.returncode,
        )
    return ServiceOpResult(False, generic_message, detail=detail, returncode=result.returncode)


def _install_windows() -> ServiceOpResult:
    definition = gen.build_windows_task()
    result = _run(gen.windows_create_args(definition))
    if result.returncode == 0:
        return ServiceOpResult(
            True,
            f"Task Scheduler task '{definition.task_name}' installed "
            "(trigger: user logon, no elevation required).",
            detail=result.stdout.strip(),
        )
    return _windows_failure_result(
        result,
        generic_message=f"Failed to create Task Scheduler task '{definition.task_name}'.",
        denied_message=(
            f"Failed to create Task Scheduler task '{definition.task_name}': "
            "administrator elevation is required in this environment."
        ),
    )


def _status_windows() -> ServiceOpResult:
    result = _run(gen.windows_query_args())
    if result.returncode == 0:
        return ServiceOpResult(True, "Task Scheduler task is installed.", detail=result.stdout.strip())
    return _windows_failure_result(
        result,
        generic_message="Task Scheduler task is not installed.",
        denied_message="Could not check Task Scheduler task status: administrator elevation is required in this environment.",
    )


def _start_windows() -> ServiceOpResult:
    result = _run(gen.windows_run_args())
    if result.returncode == 0:
        return ServiceOpResult(True, "Task Scheduler task triggered.", detail=result.stdout.strip())
    return _windows_failure_result(
        result,
        generic_message="Failed to trigger Task Scheduler task.",
        denied_message="Failed to trigger Task Scheduler task: administrator elevation is required in this environment.",
    )


def _stop_windows() -> ServiceOpResult:
    result = _run(gen.windows_end_args())
    if result.returncode == 0:
        return ServiceOpResult(True, "Task Scheduler task ended.", detail=result.stdout.strip())
    return _windows_failure_result(
        result,
        generic_message="Failed to end Task Scheduler task (it may not currently be running).",
        denied_message="Failed to end Task Scheduler task: administrator elevation is required in this environment.",
    )


def _uninstall_windows() -> ServiceOpResult:
    result = _run(gen.windows_delete_args())
    if result.returncode == 0:
        return ServiceOpResult(True, "Task Scheduler task removed.", detail=result.stdout.strip())
    combined = (result.stderr or result.stdout or "").lower()
    if "cannot find" in combined or "does not exist" in combined:
        return ServiceOpResult(
            True, "Task Scheduler task was already absent (nothing to remove).",
            detail=(result.stderr or result.stdout).strip(),
        )
    return _windows_failure_result(
        result,
        generic_message="Failed to remove Task Scheduler task.",
        denied_message="Failed to remove Task Scheduler task: administrator elevation is required in this environment.",
    )


# ---------------------------------------------------------------------------
# macOS: launchd (per-user LaunchAgent, gui/<uid> domain)
# ---------------------------------------------------------------------------


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_service_target() -> str:
    return f"{_launchd_domain()}/{gen.LAUNCHD_LABEL}"


def _launchd_is_registered() -> bool:
    """Return True if com.portforge.agent is currently known to launchd.

    `launchctl print <target>` exits 0 when the service is registered in
    the gui/<uid> domain (whether running or intentionally stopped) and
    non-zero when it is not registered at all.  We use this as the
    single, authoritative registration probe rather than inspecting stdout
    text, which varies between macOS versions.
    """
    result = _run(["launchctl", "print", _launchd_service_target()])
    return result.returncode == 0


def _install_macos() -> ServiceOpResult:
    log_dir = paths.data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_path = gen.launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(gen.build_launchd_plist_bytes(log_dir=log_dir))

    # Determine whether com.portforge.agent is currently registered in
    # launchd's gui/<uid> domain.  `launchctl print <target>` exits 0 when
    # the service is known to launchd (running *or* stopped but still
    # bootstrapped) and non-zero when it isn't registered at all.
    #
    # We use this state rather than always doing a blind bootout+bootstrap
    # because:
    #   • bootstrap refuses with I/O error (5) if the label is already
    #     registered -- even if we just wrote a fresh plist to disk.
    #   • bootout of a running service leaves launchd in an intermediate
    #     "teardown" state; issuing bootstrap immediately afterwards can
    #     race and produce the same I/O error.
    #   • The safest idempotent strategy is:
    #       - If NOT registered  → bootstrap normally.
    #       - If registered      → bootout (wait for launchd to drain) then
    #                              bootstrap to reload the updated plist.
    already_registered = _launchd_is_registered()

    if already_registered:
        # Controlled removal of the old registration so we can reload.
        bootout = _run(["launchctl", "bootout", _launchd_service_target()])
        if bootout.returncode != 0:
            # bootout failed for a reason other than "not registered" --
            # surface it rather than silently continuing into a broken state.
            combined = (bootout.stderr or bootout.stdout or "").lower()
            if "could not find" not in combined and "no such process" not in combined:
                return ServiceOpResult(
                    False,
                    "Failed to remove existing LaunchAgent registration before reinstall.",
                    detail=(bootout.stderr or bootout.stdout).strip(),
                    returncode=bootout.returncode,
                )

    result = _run(["launchctl", "bootstrap", _launchd_domain(), str(plist_path)])
    if result.returncode == 0:
        return ServiceOpResult(
            True, f"LaunchAgent '{gen.LAUNCHD_LABEL}' installed at {plist_path}.",
            detail=result.stdout.strip(),
        )

    # errno 5 / I/O error is the launchd drain-race: the old process has been
    # signalled but launchd has not yet released the label registration.  One
    # retry after a short settle window is deterministic and sufficient.
    combined = (result.stderr or result.stdout or "").lower()
    if result.returncode == 5 or "input/output error" in combined:
        time.sleep(_LAUNCHD_BOOTOUT_SETTLE_SECS)
        result = _run(["launchctl", "bootstrap", _launchd_domain(), str(plist_path)])
        if result.returncode == 0:
            return ServiceOpResult(
                True, f"LaunchAgent '{gen.LAUNCHD_LABEL}' installed at {plist_path}.",
                detail=result.stdout.strip(),
            )

    return ServiceOpResult(
        False, "Failed to bootstrap LaunchAgent.",
        detail=(result.stderr or result.stdout).strip(),
        returncode=result.returncode,
    )


def _status_macos() -> ServiceOpResult:
    result = _run(["launchctl", "print", _launchd_service_target()])
    if result.returncode == 0:
        return ServiceOpResult(True, "LaunchAgent is loaded.", detail=result.stdout.strip())
    return ServiceOpResult(
        False, "LaunchAgent is not loaded.", detail=(result.stderr or result.stdout).strip(),
        returncode=result.returncode,
    )


def _start_macos() -> ServiceOpResult:
    result = _run(["launchctl", "kickstart", "-k", _launchd_service_target()])
    if result.returncode == 0:
        return ServiceOpResult(True, "LaunchAgent kickstarted.", detail=result.stdout.strip())
    return ServiceOpResult(
        False, "Failed to kickstart LaunchAgent.", detail=(result.stderr or result.stdout).strip(),
        returncode=result.returncode,
    )


def _stop_macos() -> ServiceOpResult:
    result = _run(["launchctl", "kill", "SIGTERM", _launchd_service_target()])
    if result.returncode == 0:
        return ServiceOpResult(True, "LaunchAgent sent SIGTERM.", detail=result.stdout.strip())
    return ServiceOpResult(
        False, "Failed to signal LaunchAgent (it may not currently be running).",
        detail=(result.stderr or result.stdout).strip(), returncode=result.returncode,
    )


def _uninstall_macos() -> ServiceOpResult:
    plist_path = gen.launchd_plist_path()
    existed = plist_path.exists()

    boot_result = _run(["launchctl", "bootout", _launchd_service_target()])

    if existed:
        try:
            plist_path.unlink()
        except FileNotFoundError:
            pass

    if not existed and boot_result.returncode != 0:
        return ServiceOpResult(True, "LaunchAgent was already absent (nothing to remove).")
    return ServiceOpResult(
        True, f"LaunchAgent '{gen.LAUNCHD_LABEL}' removed.", detail=boot_result.stdout.strip()
    )


# ---------------------------------------------------------------------------
# Linux: systemd (user service, `systemctl --user`)
# ---------------------------------------------------------------------------


def _install_linux() -> ServiceOpResult:
    unit_path = gen.systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(gen.build_systemd_unit(), encoding="utf-8")

    reload_result = _run(["systemctl", "--user", "daemon-reload"])
    if reload_result.returncode != 0:
        return ServiceOpResult(
            False, "systemctl --user daemon-reload failed.",
            detail=(reload_result.stderr or reload_result.stdout).strip(), returncode=reload_result.returncode,
        )

    enable_result = _run(["systemctl", "--user", "enable", gen.SYSTEMD_UNIT_NAME])
    if enable_result.returncode == 0:
        return ServiceOpResult(
            True,
            f"systemd user unit '{gen.SYSTEMD_UNIT_NAME}' installed and enabled at {unit_path}.",
            detail=enable_result.stdout.strip(),
        )
    return ServiceOpResult(
        False, "Failed to enable systemd user unit.", detail=(enable_result.stderr or enable_result.stdout).strip(),
        returncode=enable_result.returncode,
    )


def _status_linux() -> ServiceOpResult:
    result = _run(["systemctl", "--user", "status", gen.SYSTEMD_UNIT_NAME])
    if result.returncode == 4:  # unit not found
        return ServiceOpResult(
            False, "systemd user unit is not installed.", detail=(result.stderr or result.stdout).strip(),
            returncode=result.returncode,
        )
    # 0 = active; systemctl status also exits non-zero (commonly 3) for a
    # unit that exists but is currently inactive -- that's a legitimate,
    # non-error state to report, not a tool failure, so success tracks the
    # unit's activity rather than treating every non-zero exit as failure.
    return ServiceOpResult(
        result.returncode == 0,
        result.stdout.strip() or "systemd user unit status retrieved.",
        detail=result.stdout.strip(),
        returncode=result.returncode,
    )


def _start_linux() -> ServiceOpResult:
    result = _run(["systemctl", "--user", "start", gen.SYSTEMD_UNIT_NAME])
    if result.returncode == 0:
        return ServiceOpResult(True, "systemd user unit started.", detail=result.stdout.strip())
    return ServiceOpResult(
        False, "Failed to start systemd user unit.", detail=(result.stderr or result.stdout).strip(),
        returncode=result.returncode,
    )


def _stop_linux() -> ServiceOpResult:
    result = _run(["systemctl", "--user", "stop", gen.SYSTEMD_UNIT_NAME])
    if result.returncode == 0:
        return ServiceOpResult(True, "systemd user unit stopped.", detail=result.stdout.strip())
    return ServiceOpResult(
        False, "Failed to stop systemd user unit.", detail=(result.stderr or result.stdout).strip(),
        returncode=result.returncode,
    )


def _uninstall_linux() -> ServiceOpResult:
    unit_path = gen.systemd_unit_path()
    existed = unit_path.exists()

    disable_result = _run(["systemctl", "--user", "disable", "--now", gen.SYSTEMD_UNIT_NAME])

    if existed:
        try:
            unit_path.unlink()
        except FileNotFoundError:
            pass
    _run(["systemctl", "--user", "daemon-reload"])

    if not existed and disable_result.returncode != 0:
        return ServiceOpResult(True, "systemd user unit was already absent (nothing to remove).")
    return ServiceOpResult(
        True, f"systemd user unit '{gen.SYSTEMD_UNIT_NAME}' removed.", detail=disable_result.stdout.strip()
    )


# ---------------------------------------------------------------------------
# Platform dispatch -- the only functions cli_agent.py calls directly.
# ---------------------------------------------------------------------------

_DISPATCH = {
    pf.OperatingSystem.WINDOWS: {
        "install": _install_windows, "status": _status_windows,
        "start": _start_windows, "stop": _stop_windows, "uninstall": _uninstall_windows,
    },
    pf.OperatingSystem.MACOS: {
        "install": _install_macos, "status": _status_macos,
        "start": _start_macos, "stop": _stop_macos, "uninstall": _uninstall_macos,
    },
    pf.OperatingSystem.LINUX: {
        "install": _install_linux, "status": _status_linux,
        "start": _start_linux, "stop": _stop_linux, "uninstall": _uninstall_linux,
    },
}


def _dispatch(action: str) -> ServiceOpResult:
    os_ = pf.detect_os()
    platform_ops = _DISPATCH.get(os_)
    if platform_ops is None:
        raise UnsupportedPlatformError(
            f"Native service management is not supported on '{os_.value}'. "
            "PortForge supports Windows (Task Scheduler), macOS (launchd), and Linux (systemd) only."
        )
    return platform_ops[action]()


def install() -> ServiceOpResult:
    return _dispatch("install")


def uninstall() -> ServiceOpResult:
    return _dispatch("uninstall")


def start() -> ServiceOpResult:
    return _dispatch("start")


def stop() -> ServiceOpResult:
    return _dispatch("stop")


def status() -> ServiceOpResult:
    return _dispatch("status")
