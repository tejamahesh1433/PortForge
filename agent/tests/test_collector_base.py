"""Tests for safe_process_metadata's command-line/parent-process facts
(Phase 3 additions to the shared collector helper).
"""
import sys
from unittest.mock import MagicMock

from portforge_agent.collectors.base import safe_process_metadata


def _install_fake_psutil(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "psutil", fake)


def _base_fake_psutil():
    fake = MagicMock()
    fake.AccessDenied = type("AccessDenied", (Exception,), {})
    fake.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake.ZombieProcess = type("ZombieProcess", (Exception,), {})
    return fake


def test_command_line_and_parent_facts_are_collected(monkeypatch):
    fake_psutil = _base_fake_psutil()

    child = MagicMock()
    child.name.return_value = "node.exe"
    child.exe.return_value = "C:\\node.exe"
    child.cwd.return_value = "C:\\app"
    child.cmdline.return_value = ["node.exe", "server.js"]
    child.ppid.return_value = 42

    parent = MagicMock()
    parent.name.return_value = "cmd.exe"
    parent.cwd.return_value = "C:\\Users\\dev"

    fake_psutil.Process.side_effect = lambda pid: parent if pid == 42 else child
    _install_fake_psutil(monkeypatch, fake_psutil)

    metadata = safe_process_metadata(1234)

    assert metadata.command_line == ["node.exe", "server.js"]
    assert metadata.parent_pid == 42
    assert metadata.parent_name == "cmd.exe"
    assert metadata.parent_working_directory == "C:\\Users\\dev"


def test_cmdline_failure_leaves_command_line_none(monkeypatch):
    fake_psutil = _base_fake_psutil()

    child = MagicMock()
    child.name.return_value = "node.exe"
    child.exe.side_effect = fake_psutil.AccessDenied("no")
    child.cwd.side_effect = fake_psutil.AccessDenied("no")
    child.cmdline.side_effect = fake_psutil.AccessDenied("no")
    child.ppid.side_effect = fake_psutil.AccessDenied("no")
    fake_psutil.Process.return_value = child
    _install_fake_psutil(monkeypatch, fake_psutil)

    metadata = safe_process_metadata(1234)

    assert metadata.command_line is None
    assert metadata.parent_pid is None
    assert metadata.parent_name is None
    assert metadata.parent_working_directory is None


def test_empty_cmdline_is_normalized_to_none(monkeypatch):
    fake_psutil = _base_fake_psutil()
    child = MagicMock()
    child.cmdline.return_value = []
    child.ppid.return_value = 0
    fake_psutil.Process.return_value = child
    _install_fake_psutil(monkeypatch, fake_psutil)

    metadata = safe_process_metadata(1234)
    assert metadata.command_line is None


def test_parent_process_vanished_does_not_raise(monkeypatch):
    fake_psutil = _base_fake_psutil()

    child = MagicMock()
    child.name.return_value = "node.exe"
    child.cmdline.return_value = ["node.exe"]
    child.ppid.return_value = 42

    def _process_side_effect(pid):
        if pid == 42:
            raise fake_psutil.NoSuchProcess("gone")
        return child

    fake_psutil.Process.side_effect = _process_side_effect
    _install_fake_psutil(monkeypatch, fake_psutil)

    metadata = safe_process_metadata(1234)

    assert metadata.parent_pid == 42
    assert metadata.parent_name is None
    assert metadata.parent_working_directory is None


def test_parent_field_access_permission_denied_does_not_raise(monkeypatch):
    fake_psutil = _base_fake_psutil()

    child = MagicMock()
    child.cmdline.return_value = []
    child.ppid.return_value = 42

    parent = MagicMock()
    parent.name.side_effect = fake_psutil.AccessDenied("no")
    parent.cwd.side_effect = fake_psutil.AccessDenied("no")

    fake_psutil.Process.side_effect = lambda pid: parent if pid == 42 else child
    _install_fake_psutil(monkeypatch, fake_psutil)

    metadata = safe_process_metadata(1234)

    assert metadata.parent_pid == 42
    assert metadata.parent_name is None
    assert metadata.parent_working_directory is None


def test_zero_or_none_pid_returns_empty_metadata():
    assert safe_process_metadata(None).command_line is None
    assert safe_process_metadata(0).command_line is None
