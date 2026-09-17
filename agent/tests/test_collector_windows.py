"""Mock-based tests for the Windows collector's parsing/filtering logic.

These tests mock psutil so they are deterministic and runnable on any OS,
independent of what is actually listening on the current machine.
"""
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from portforge_agent.collectors.base import CollectorError
from portforge_agent.collectors.windows import WindowsCollector
from portforge_agent.models import Protocol, Source


def _laddr(ip, port):
    return SimpleNamespace(ip=ip, port=port)


def _conn(type_, laddr, status, pid):
    return SimpleNamespace(type=type_, laddr=laddr, status=status, pid=pid)


@pytest.fixture
def fake_psutil():
    fake = MagicMock()
    fake.CONN_LISTEN = "LISTEN"
    fake.CONN_NONE = "NONE"
    fake.AccessDenied = type("AccessDenied", (Exception,), {})
    fake.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake.ZombieProcess = type("ZombieProcess", (Exception,), {})
    return fake


def _install_fake_psutil(monkeypatch, fake):
    import sys

    monkeypatch.setitem(sys.modules, "psutil", fake)


def test_collect_includes_listening_tcp_and_bound_udp(monkeypatch, fake_psutil):
    fake_psutil.net_connections.return_value = [
        _conn(socket.SOCK_STREAM, _laddr("0.0.0.0", 3000), "LISTEN", 1111),
        _conn(socket.SOCK_STREAM, _laddr("127.0.0.1", 5432), "ESTABLISHED", 2222),
        _conn(socket.SOCK_DGRAM, _laddr("0.0.0.0", 6000), "NONE", 3333),
    ]

    child_proc = MagicMock()
    child_proc.name.return_value = "node.exe"
    child_proc.exe.return_value = "C:\\node.exe"
    child_proc.cwd.return_value = "C:\\app"
    child_proc.cmdline.return_value = ["node.exe", "server.js"]
    child_proc.ppid.return_value = 42

    parent_proc = MagicMock()
    parent_proc.name.return_value = "cmd.exe"
    parent_proc.cwd.return_value = "C:\\Users\\dev"

    def _process_side_effect(pid):
        return parent_proc if pid == 42 else child_proc

    fake_psutil.Process.side_effect = _process_side_effect

    _install_fake_psutil(monkeypatch, fake_psutil)

    ports = WindowsCollector().collect()

    # Established TCP connection must be excluded; only LISTEN + UDP kept.
    assert {p.port for p in ports} == {3000, 6000}
    tcp_port = next(p for p in ports if p.port == 3000)
    assert tcp_port.protocol == Protocol.TCP
    assert tcp_port.process_name == "node.exe"
    assert tcp_port.process_path == "C:\\node.exe"
    assert tcp_port.working_directory == "C:\\app"
    assert tcp_port.source == Source.PROCESS
    assert tcp_port.command_line == ["node.exe", "server.js"]
    assert tcp_port.parent_pid == 42
    assert tcp_port.parent_process_name == "cmd.exe"
    assert tcp_port.parent_working_directory == "C:\\Users\\dev"


def test_collect_skips_connections_without_local_address(monkeypatch, fake_psutil):
    fake_psutil.net_connections.return_value = [
        _conn(socket.SOCK_STREAM, None, "LISTEN", 1111),
        _conn(socket.SOCK_STREAM, (), "LISTEN", 1111),
    ]
    _install_fake_psutil(monkeypatch, fake_psutil)

    ports = WindowsCollector().collect()
    assert ports == []


def test_collect_handles_process_lookup_failure_gracefully(monkeypatch, fake_psutil):
    fake_psutil.net_connections.return_value = [
        _conn(socket.SOCK_STREAM, _laddr("0.0.0.0", 8000), "LISTEN", 9999),
    ]
    fake_psutil.Process.side_effect = fake_psutil.NoSuchProcess("gone")
    _install_fake_psutil(monkeypatch, fake_psutil)

    ports = WindowsCollector().collect()

    assert len(ports) == 1
    assert ports[0].process_name is None
    assert ports[0].pid == 9999
    assert ports[0].command_line is None
    assert ports[0].parent_pid is None


def test_collect_raises_collector_error_on_access_denied(monkeypatch, fake_psutil):
    fake_psutil.net_connections.side_effect = fake_psutil.AccessDenied("nope")
    _install_fake_psutil(monkeypatch, fake_psutil)

    with pytest.raises(CollectorError):
        WindowsCollector().collect()


def test_collect_deduplicates_identical_observations(monkeypatch, fake_psutil):
    fake_psutil.net_connections.return_value = [
        _conn(socket.SOCK_STREAM, _laddr("0.0.0.0", 3000), "LISTEN", 1111),
        _conn(socket.SOCK_STREAM, _laddr("0.0.0.0", 3000), "LISTEN", 1111),
    ]
    proc = MagicMock()
    proc.name.return_value = "node.exe"
    proc.exe.side_effect = fake_psutil.AccessDenied("no")
    proc.cwd.side_effect = fake_psutil.AccessDenied("no")
    proc.cmdline.side_effect = fake_psutil.AccessDenied("no")
    proc.ppid.side_effect = fake_psutil.AccessDenied("no")
    fake_psutil.Process.return_value = proc
    _install_fake_psutil(monkeypatch, fake_psutil)

    ports = WindowsCollector().collect()
    assert len(ports) == 1
