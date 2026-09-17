"""Native service/startup definition GENERATION for the current OS.

This module only computes and returns data (strings, bytes, dataclasses) --
it never calls a native service manager and never writes a file itself.
That separation is what makes it fully unit-testable without touching a
real OS service manager; see service_ops.py for the side-effecting
install/start/stop/status/uninstall operations that consume what this
module builds, and cli_agent.py for the `portforge agent service ...`
commands that tie the two together.

Every platform is deliberately pointed at a PER-USER startup target
(Windows ONLOGON task, macOS LaunchAgent, Linux `systemctl --user` unit)
rather than a system-wide one (AT STARTUP task, LaunchDaemon, system
service). PortForge's credentials/config/reservations all live under the
invoking user's own data directory (see paths.data_dir()), so a
system-wide service would either need a stored account password (Windows
"run whether logged on or not"), run as root without the user's own
config (macOS/Linux), or otherwise broaden privileges beyond what the
agent actually needs. Per-user startup also means none of these three
installs ever requires administrator/root elevation.

Nothing generated here ever embeds a credential, bootstrap token,
enrollment token, GitHub token, or database password -- the generated
command line is always exactly `<python> -m portforge_agent agent run`
with no extra arguments, and `agent run` reads everything it needs from
the existing on-disk config/credential files (see credentials.py,
central_config.py). Generated definitions are also machine-specific (they
embed this host's Python interpreter path and this user's data
directory), which is why they must never be committed -- see the
"physical validation" notes for how to reproduce/inspect them on a given
host instead of storing one in git.
"""
from __future__ import annotations

import plistlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import paths
from . import platform as pf

# Stable identifiers for the generated definition on each platform. Keeping
# these as module-level constants (rather than recomputing a string per
# call) is what makes service_ops.py's "does it already exist" / uninstall
# logic simple: every operation addresses the exact same name.
WINDOWS_TASK_NAME = "PortForge Agent"
LAUNCHD_LABEL = "com.portforge.agent"
SYSTEMD_UNIT_NAME = "portforge-agent.service"


def resolve_python_executable(prefer_windowless: bool = True) -> str:
    """Absolute path to the interpreter the service definition should run.

    On Windows, prefers ``pythonw.exe`` over ``python.exe`` when it exists
    alongside the current interpreter -- pythonw.exe never attaches a
    console window, satisfying the "avoid opening a console window where
    practical" requirement. Every other platform (and the Windows fallback,
    if pythonw.exe is somehow absent) uses ``sys.executable`` directly.
    """
    exe = Path(sys.executable).resolve()
    if prefer_windowless and pf.detect_os() == pf.OperatingSystem.WINDOWS:
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            return str(windowless)
    return str(exe)


def agent_run_args(python_executable: Optional[str] = None) -> List[str]:
    """The full `portforge agent run` command line, as a list of tokens.

    Always a list, never a pre-joined shell string -- every native tool
    invocation below (subprocess.run with a list, schtasks's /TR value,
    launchd's ProgramArguments array, systemd's ExecStart=) has its own
    quoting rules, and building from tokens lets each platform's generator
    apply exactly the quoting it needs rather than un-quoting and
    re-quoting a string.
    """
    exe = python_executable or resolve_python_executable()
    return [exe, "-m", "portforge_agent", "agent", "run"]


def _quote_if_needed(value: str) -> str:
    """Wrap a token in double quotes if it contains whitespace.

    Used for Windows schtasks /TR values and systemd ExecStart= lines,
    both of which follow this same convention (unlike POSIX shells, which
    would need backslash-escaping for embedded quotes too -- not a concern
    here since these tokens are absolute paths, never user-supplied
    strings with embedded quote characters).
    """
    if any(c.isspace() for c in value):
        return f'"{value}"'
    return value


# ---------------------------------------------------------------------------
# Windows: Task Scheduler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowsTaskDefinition:
    """Everything needed to create the Task Scheduler task via schtasks.exe.

    Trigger is ONLOGON (start when the current user logs on), not AT
    SYSTEM STARTUP: startup-before-login tasks run under SYSTEM/a stored
    account by default, which either can't reach this user's
    %LOCALAPPDATA%\\PortForge credentials or requires schtasks to be given
    an account password (a secret we must never store) via /RU + /RP.
    ONLOGON tasks created without /RU run as the creating user with no
    password prompt, and /RL LIMITED keeps the run level standard rather
    than requiring "highest privileges" -- so installing this task never
    needs administrator elevation.
    """

    task_name: str
    command_line: str  # full /TR value, already quoted where needed
    trigger: str = "ONLOGON"
    run_level: str = "LIMITED"


def build_windows_task(python_executable: Optional[str] = None) -> WindowsTaskDefinition:
    args = agent_run_args(python_executable)
    command_line = " ".join(_quote_if_needed(a) for a in args)
    return WindowsTaskDefinition(task_name=WINDOWS_TASK_NAME, command_line=command_line)


def windows_create_args(definition: WindowsTaskDefinition) -> List[str]:
    """The full schtasks.exe argument list to create/overwrite the task.

    /F forces overwrite of an existing task of the same name, which is
    what makes `portforge agent service install` idempotent -- running it
    twice replaces the definition in place instead of erroring or
    duplicating it.
    """
    return [
        "schtasks",
        "/Create",
        "/TN",
        definition.task_name,
        "/TR",
        definition.command_line,
        "/SC",
        definition.trigger,
        "/RL",
        definition.run_level,
        "/F",
    ]


def windows_query_args(task_name: str = WINDOWS_TASK_NAME) -> List[str]:
    return ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"]


def windows_run_args(task_name: str = WINDOWS_TASK_NAME) -> List[str]:
    return ["schtasks", "/Run", "/TN", task_name]


def windows_end_args(task_name: str = WINDOWS_TASK_NAME) -> List[str]:
    return ["schtasks", "/End", "/TN", task_name]


def windows_delete_args(task_name: str = WINDOWS_TASK_NAME) -> List[str]:
    return ["schtasks", "/Delete", "/TN", task_name, "/F"]


# ---------------------------------------------------------------------------
# macOS: launchd
# ---------------------------------------------------------------------------


def launchd_log_paths(log_dir: Optional[Path] = None) -> "tuple[Path, Path]":
    log_dir = log_dir or (paths.data_dir() / "logs")
    return log_dir / "agent.out.log", log_dir / "agent.err.log"


def build_launchd_plist_dict(
    python_executable: Optional[str] = None, log_dir: Optional[Path] = None
) -> Dict:
    """The plist content as a plain dict (pre-serialization).

    Exposed separately from the bytes-producing function below so tests
    can assert on structure directly rather than round-tripping XML.

    KeepAlive.SuccessfulExit=False means launchd restarts the job if it
    exits with a non-zero status (a crash) but leaves it stopped after a
    clean exit (status 0) -- which is exactly what `agent run` returns
    both when a SIGTERM/SIGINT asks it to stop and when central sync
    isn't configured yet, so `portforge agent service stop` doesn't fight
    launchd's own restart logic.
    """
    stdout_path, stderr_path = launchd_log_paths(log_dir)
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": agent_run_args(python_executable),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }


def build_launchd_plist_bytes(
    python_executable: Optional[str] = None, log_dir: Optional[Path] = None
) -> bytes:
    """XML plist bytes, generated via the stdlib `plistlib` module so the
    output is guaranteed valid, parseable plist syntax (no hand-rolled XML
    escaping to get wrong).
    """
    return plistlib.dumps(
        build_launchd_plist_dict(python_executable, log_dir), fmt=plistlib.FMT_XML
    )


def launchd_plist_path() -> Path:
    """Per-user LaunchAgents directory -- never LaunchDaemons (system-wide,
    root-owned, wrong context for per-user PortForge state).
    """
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


# ---------------------------------------------------------------------------
# Linux: systemd (user service)
# ---------------------------------------------------------------------------


def build_systemd_unit(python_executable: Optional[str] = None) -> str:
    """The unit file content as text.

    A *user* unit (see systemd_unit_path()), not a system unit: PortForge
    state lives under the invoking user's own data directory, and a
    `systemctl --user` service naturally runs as that user with whatever
    group memberships (e.g. `docker`) they already have -- no new
    privileges granted, and no root/system service needed.

    Restart=on-failure + RestartSec=10 recovers from crashes without a
    tight restart loop; KillSignal=SIGTERM (systemd's default, listed
    explicitly for clarity) and TimeoutStopSec=20 give `agent run`'s
    signal handler time to shut down cleanly on `systemctl --user stop`.
    """
    args = agent_run_args(python_executable)
    exec_start = " ".join(_quote_if_needed(a) for a in args)
    return (
        "[Unit]\n"
        "Description=PortForge Agent\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "KillSignal=SIGTERM\n"
        "TimeoutStopSec=20\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
