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


def test_install_macos_boots_out_stale_copy_then_bootstraps(monkeypatch, tmp_path):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(service_ops.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(service_ops.paths, "data_dir", lambda: tmp_path)
    plist_path = tmp_path / "LaunchAgents" / f"{gen.LAUNCHD_LABEL}.plist"
    monkeypatch.setattr(gen, "launchd_plist_path", lambda: plist_path)

    calls = []

    def fake_run(args, timeout=15.0):
        calls.append(args)
        return _cp(0)

    monkeypatch.setattr(service_ops, "_run", fake_run)

    result = service_ops.install()

    assert result.success is True
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[1][:2] == ["launchctl", "bootstrap"]
    assert calls[1][2] == "gui/501"
    assert plist_path.exists()


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
