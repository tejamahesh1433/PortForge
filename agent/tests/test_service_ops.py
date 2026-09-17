"""Unit tests for service_ops.py -- the native install/start/stop/status/
uninstall operations. Every native tool invocation is mocked via
`service_ops._run`; nothing here ever spawns a real schtasks.exe,
launchctl, or systemctl, and no real host service is installed. See
service_gen.py's own tests (test_service_gen.py) for the pure definition
generation these operations consume.
"""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from portforge_agent import platform as pf
from portforge_agent import service_gen as gen
from portforge_agent import service_ops


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Windows: Task Scheduler
# ---------------------------------------------------------------------------


def test_install_windows_success(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch("portforge_agent.service_ops._run", return_value=_cp(0, "SUCCESS")) as mock_run:
        result = service_ops.install()

    assert result.success is True
    called_args = mock_run.call_args[0][0]
    assert called_args[:2] == ["schtasks", "/Create"]
    assert "/F" in called_args


def test_install_windows_failure_surfaces_native_error(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch("portforge_agent.service_ops._run", return_value=_cp(1, "", "ERROR: Access is denied.")):
        result = service_ops.install()

    assert result.success is False
    assert result.returncode == 1
    assert "access is denied" in result.detail.lower()


def test_install_windows_twice_is_idempotent(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        first = service_ops.install()
        second = service_ops.install()

    assert first.success and second.success
    assert mock_run.call_count == 2
    for call in mock_run.call_args_list:
        assert "/F" in call[0][0]


def test_status_windows_not_installed(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch(
        "portforge_agent.service_ops._run",
        return_value=_cp(1, "", "ERROR: The system cannot find the file specified."),
    ) as mock_run:
        result = service_ops.status()

    assert result.success is False
    assert mock_run.call_args[0][0] == gen.windows_query_args()


def test_start_stop_windows_command_construction(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        result = service_ops.start()
    assert result.success is True
    assert mock_run.call_args[0][0] == gen.windows_run_args()

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        result = service_ops.stop()
    assert result.success is True
    assert mock_run.call_args[0][0] == gen.windows_end_args()


def test_uninstall_windows_already_absent_is_success(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch(
        "portforge_agent.service_ops._run",
        return_value=_cp(1, "", "ERROR: The system cannot find the file specified."),
    ):
        result = service_ops.uninstall()

    assert result.success is True
    assert "already absent" in result.message.lower()


def test_uninstall_windows_real_failure_is_reported(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch("portforge_agent.service_ops._run", return_value=_cp(1, "", "ERROR: Access is denied.")):
        result = service_ops.uninstall()

    assert result.success is False


def test_uninstall_windows_when_already_absent_does_not_error_twice(monkeypatch):
    """Uninstall must behave cleanly whether or not the service exists --
    calling it twice in a row (second time on an already-absent task)
    must not raise or report a hard failure.
    """
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    with patch(
        "portforge_agent.service_ops._run",
        return_value=_cp(1, "", "ERROR: The system cannot find the file specified."),
    ):
        first = service_ops.uninstall()
        second = service_ops.uninstall()

    assert first.success is True
    assert second.success is True


# ---------------------------------------------------------------------------
# macOS: launchd
# ---------------------------------------------------------------------------

# Helper: build a fake _run that returns `not_registered_rc` for the initial
# "print" probe and `registered_rc` for every subsequent call. Used to
# simulate the two main registration states without touching real launchd.

def _make_probe_run(monkeypatch, *, probe_rc, subsequent_rc=0):
    """Patch service_ops._run so the first call (the registration probe)
    returns `probe_rc`, and all subsequent calls return `subsequent_rc`."""
    call_count = [0]

    def fake_run(args, timeout=15.0):
        call_count[0] += 1
        if call_count[0] == 1:
            return _cp(probe_rc)
        return _cp(subsequent_rc)

    monkeypatch.setattr(service_ops, "_run", fake_run)
    return call_count


def _setup_macos(monkeypatch, tmp_path):
    """Common monkeypatching for macOS install tests."""
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(service_ops.paths, "data_dir", lambda: tmp_path)
    plist_path = tmp_path / "Library" / "LaunchAgents" / f"{gen.LAUNCHD_LABEL}.plist"
    monkeypatch.setattr(gen, "launchd_plist_path", lambda: plist_path)
    return plist_path


# 1. First install when not registered
def test_install_macos_first_install_not_registered(monkeypatch, tmp_path):
    """When the service is not registered: probe returns non-zero, skip
    bootout, bootstrap directly."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    calls = []

    def fake_run(args, timeout=15.0):
        calls.append(list(args))
        # probe: print → non-zero (not registered); bootstrap → 0
        if args[0:2] == ["launchctl", "print"]:
            return _cp(1, "", "Could not find service")
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is True
    assert plist_path.exists()
    cmds = [c[:2] for c in calls]
    assert ["launchctl", "print"] in cmds       # registration probe
    assert ["launchctl", "bootout"] not in cmds  # skipped when not registered
    assert ["launchctl", "bootstrap"] in cmds


# 2. Repeated install while registered and running
def test_install_macos_idempotent_while_registered_running(monkeypatch, tmp_path):
    """When the service IS registered (running): probe returns 0, so we do
    a controlled bootout then bootstrap.  Must succeed and end healthy."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    calls = []

    def fake_run(args, timeout=15.0):
        calls.append(list(args))
        return _cp(0)   # probe=0 (registered), bootout=0, bootstrap=0

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is True
    assert plist_path.exists()
    cmds = [c[:2] for c in calls]
    assert ["launchctl", "print"] in cmds     # probe
    assert ["launchctl", "bootout"] in cmds   # controlled removal
    assert ["launchctl", "bootstrap"] in cmds # re-register


# 3. Repeated install while registered but intentionally stopped
def test_install_macos_idempotent_while_registered_stopped(monkeypatch, tmp_path):
    """When the service is registered but stopped: `launchctl print` still
    returns 0 (service is known to launchd), so we still go through the
    controlled bootout+bootstrap cycle."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    calls = []

    def fake_run(args, timeout=15.0):
        calls.append(list(args))
        # print → 0 (registered/stopped); bootout → 0; bootstrap → 0
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is True
    cmds = [c[:2] for c in calls]
    assert ["launchctl", "bootout"] in cmds
    assert ["launchctl", "bootstrap"] in cmds


# 4. Bootstrap failure that is NOT an already-registered condition is surfaced
def test_install_macos_unrecoverable_bootstrap_failure_surfaced(monkeypatch, tmp_path):
    """A real bootstrap failure (not an already-registered race) must be
    returned as ServiceOpResult(success=False), not swallowed."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    def fake_run(args, timeout=15.0):
        if args[0:2] == ["launchctl", "print"]:
            return _cp(1, "", "Could not find service")   # not registered
        if args[0:2] == ["launchctl", "bootstrap"]:
            return _cp(5, "", "Bootstrap failed: 5: Input/output error")
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)
    monkeypatch.setattr(service_ops.time, "sleep", lambda _: None)

    result = service_ops.install()

    assert result.success is False
    assert result.returncode == 5
    assert "bootstrap" in result.message.lower() or "failed" in result.message.lower()


# 4b. Bootstrap errno-5 drain-race: retry succeeds
def test_install_macos_bootstrap_io_error_retries_and_succeeds(monkeypatch, tmp_path):
    """When bootstrap returns errno 5 (I/O error / drain race) the first time,
    install must wait then retry once.  If the retry succeeds, the result
    must be ServiceOpResult(success=True)."""
    plist_path = _setup_macos(monkeypatch, tmp_path)
    bootstrap_calls = [0]

    def fake_run(args, timeout=15.0):
        if args[0:2] == ["launchctl", "print"]:
            return _cp(1, "", "Could not find service")   # not registered
        if args[0:2] == ["launchctl", "bootstrap"]:
            bootstrap_calls[0] += 1
            if bootstrap_calls[0] == 1:
                return _cp(5, "", "Bootstrap failed: 5: Input/output error")
            return _cp(0)   # retry succeeds
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)
    slept = [0.0]
    monkeypatch.setattr(service_ops.time, "sleep", lambda s: slept.__setitem__(0, s))

    result = service_ops.install()

    assert result.success is True
    assert bootstrap_calls[0] == 2   # two bootstrap attempts
    assert slept[0] == service_ops._LAUNCHD_BOOTOUT_SETTLE_SECS


# 5. Real bootout failure (not a benign "not found") is surfaced
def test_install_macos_unrecoverable_bootout_failure_surfaced(monkeypatch, tmp_path):
    """When bootout returns non-zero for a real (non-trivial) reason, the
    install must abort and report the failure rather than blindly proceeding
    to bootstrap."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    def fake_run(args, timeout=15.0):
        if args[0:2] == ["launchctl", "print"]:
            return _cp(0)   # service IS registered
        if args[0:2] == ["launchctl", "bootout"]:
            return _cp(5, "", "Bootout failed: permission denied")
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is False
    assert "registration" in result.message.lower() or "reinstall" in result.message.lower()


# 6. Plist remains structurally valid after reinstall
def test_install_macos_plist_valid_after_reinstall(monkeypatch, tmp_path):
    """The written plist must be parse-able by plistlib with correct structure."""
    import plistlib
    plist_path = _setup_macos(monkeypatch, tmp_path)

    def fake_run(args, timeout=15.0):
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    service_ops.install()

    assert plist_path.exists()
    d = plistlib.loads(plist_path.read_bytes())
    assert d["Label"] == gen.LAUNCHD_LABEL
    assert d["RunAtLoad"] is True
    assert d["KeepAlive"] == {"SuccessfulExit": False}
    assert "ProgramArguments" in d
    assert "StandardOutPath" in d
    assert "StandardErrorPath" in d


# 7. No duplicate plist file created
def test_install_macos_no_duplicate_plist(monkeypatch, tmp_path):
    """Repeated installs must leave exactly one plist, not multiple copies."""
    plist_path = _setup_macos(monkeypatch, tmp_path)

    def fake_run(args, timeout=15.0):
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    service_ops.install()
    service_ops.install()
    service_ops.install()

    # Only one file matching the label should exist under LaunchAgents
    la_dir = plist_path.parent
    portforge_plists = list(la_dir.glob("*portforge*.plist"))
    assert len(portforge_plists) == 1
    assert portforge_plists[0] == plist_path


# 8. Venv interpreter is preserved in ProgramArguments
def test_install_macos_venv_interpreter_preserved(monkeypatch, tmp_path):
    """ProgramArguments must start with the venv python, not a system one."""
    import plistlib
    plist_path = _setup_macos(monkeypatch, tmp_path)
    fake_exe = "/Users/test/Projects/PortForge/.venv/bin/python"
    monkeypatch.setattr(gen, "resolve_python_executable", lambda: fake_exe)

    def fake_run(args, timeout=15.0):
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    service_ops.install()

    d = plistlib.loads(plist_path.read_bytes())
    assert d["ProgramArguments"][0] == fake_exe
    assert d["ProgramArguments"][1:] == ["-m", "portforge_agent", "agent", "run"]


# 9. Multiple successive installs all succeed (install → install → install)
def test_install_macos_repeated_installs_all_succeed(monkeypatch, tmp_path):
    """Running service install three times in a row must all return success."""
    _setup_macos(monkeypatch, tmp_path)

    def fake_run(args, timeout=15.0):
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    results = [service_ops.install() for _ in range(3)]
    assert all(r.success for r in results)



def test_status_macos_target_construction(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 42, raising=False)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0, "state = running")) as mock_run:
        result = service_ops.status()

    assert result.success is True
    assert mock_run.call_args[0][0] == ["launchctl", "print", f"gui/42/{gen.LAUNCHD_LABEL}"]


def test_start_stop_macos_command_construction(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 42, raising=False)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        service_ops.start()
    assert mock_run.call_args[0][0] == ["launchctl", "kickstart", "-k", f"gui/42/{gen.LAUNCHD_LABEL}"]

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        service_ops.stop()
    assert mock_run.call_args[0][0] == ["launchctl", "kill", "SIGTERM", f"gui/42/{gen.LAUNCHD_LABEL}"]


def test_uninstall_macos_already_absent_is_success(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 42, raising=False)
    monkeypatch.setattr(gen, "launchd_plist_path", lambda: tmp_path / "nonexistent.plist")

    with patch("portforge_agent.service_ops._run", return_value=_cp(1, "", "Could not find service")):
        result = service_ops.uninstall()

    assert result.success is True
    assert "already absent" in result.message.lower()


def test_uninstall_macos_removes_existing_plist_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 42, raising=False)
    plist_path = tmp_path / f"{gen.LAUNCHD_LABEL}.plist"
    plist_path.write_bytes(b"<plist/>")
    monkeypatch.setattr(gen, "launchd_plist_path", lambda: plist_path)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)):
        result = service_ops.uninstall()

    assert result.success is True
    assert not plist_path.exists()


# ---------------------------------------------------------------------------
# Linux: systemd (user service)
# ---------------------------------------------------------------------------


def test_install_linux_writes_unit_reloads_and_enables(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    unit_path = tmp_path / "systemd" / "user" / gen.SYSTEMD_UNIT_NAME
    monkeypatch.setattr(gen, "systemd_unit_path", lambda: unit_path)

    calls = []

    def fake_run(args, timeout=15.0):
        calls.append(args)
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is True
    assert unit_path.exists()
    assert "ExecStart=" in unit_path.read_text(encoding="utf-8")
    assert calls[0] == ["systemctl", "--user", "daemon-reload"]
    assert calls[1] == ["systemctl", "--user", "enable", gen.SYSTEMD_UNIT_NAME]


def test_status_linux_unit_not_found(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    with patch("portforge_agent.service_ops._run", return_value=_cp(4, "", "Unit not found.")):
        result = service_ops.status()

    assert result.success is False
    assert result.returncode == 4


def test_status_linux_inactive_unit_is_not_a_tool_error(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    with patch("portforge_agent.service_ops._run", return_value=_cp(3, "Active: inactive (dead)")):
        result = service_ops.status()

    assert result.success is False
    assert result.returncode == 3
    assert "inactive" in result.detail.lower()


def test_start_stop_linux_command_construction(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        service_ops.start()
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "start", gen.SYSTEMD_UNIT_NAME]

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)) as mock_run:
        service_ops.stop()
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "stop", gen.SYSTEMD_UNIT_NAME]


def test_uninstall_linux_removes_unit_file(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    unit_path = tmp_path / gen.SYSTEMD_UNIT_NAME
    unit_path.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(gen, "systemd_unit_path", lambda: unit_path)

    with patch("portforge_agent.service_ops._run", return_value=_cp(0)):
        result = service_ops.uninstall()

    assert result.success is True
    assert not unit_path.exists()


def test_uninstall_linux_already_absent_is_success(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    unit_path = tmp_path / gen.SYSTEMD_UNIT_NAME  # never created
    monkeypatch.setattr(gen, "systemd_unit_path", lambda: unit_path)

    with patch("portforge_agent.service_ops._run", return_value=_cp(1, "", "Unit portforge-agent.service not loaded.")):
        result = service_ops.uninstall()

    assert result.success is True
    assert "already absent" in result.message.lower()


# ---------------------------------------------------------------------------
# Unsupported platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op", [service_ops.install, service_ops.uninstall, service_ops.start, service_ops.stop, service_ops.status]
)
def test_unsupported_platform_raises(monkeypatch, op):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.UNKNOWN)
    with pytest.raises(service_ops.UnsupportedPlatformError):
        op()
