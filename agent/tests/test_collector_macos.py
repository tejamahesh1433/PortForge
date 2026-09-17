"""Mock-based tests for the macOS collector.

macOS's `lsof` cannot actually run on this development machine (Windows),
so per project convention we mock its output instead of pretending to
execute another operating system's commands.
"""
from unittest.mock import patch

from portforge_agent.collectors.base import CollectorError, ProcessMetadata
from portforge_agent.collectors import macos as macos_module
from portforge_agent.collectors.macos import MacOSCollector
from portforge_agent.models import Protocol, Source

_LSOF_TCP = (
    "COMMAND   PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
    "node    1234   user   23u  IPv4 0x1234      0t0  TCP *:3000 (LISTEN)\n"
    "postgres 2345  user    7u  IPv6 0x5678      0t0  TCP [::1]:5432 (LISTEN)\n"
)

_LSOF_UDP = (
    "COMMAND   PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
    "mdnsd    100   user    5u  IPv4 0x9999      0t0  UDP *:5353\n"
)


def _fake_run_command(args, timeout=10.0):
    if "-iTCP" in args:
        return _LSOF_TCP
    if "-iUDP" in args:
        return _LSOF_UDP
    raise AssertionError(f"unexpected command: {args}")


def test_collect_parses_tcp_and_udp(monkeypatch):
    monkeypatch.setattr(macos_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        macos_module, "safe_process_metadata", lambda pid: ProcessMetadata()
    )

    ports = MacOSCollector().collect()

    assert {p.port for p in ports} == {3000, 5432, 5353}

    tcp_wildcard = next(p for p in ports if p.port == 3000)
    assert tcp_wildcard.bind_address == "0.0.0.0"
    assert tcp_wildcard.protocol == Protocol.TCP
    assert tcp_wildcard.pid == 1234
    assert tcp_wildcard.process_name == "node"
    assert tcp_wildcard.raw_state == "LISTEN"
    assert tcp_wildcard.source == Source.PROCESS

    tcp_v6 = next(p for p in ports if p.port == 5432)
    assert tcp_v6.bind_address == "::1"

    udp = next(p for p in ports if p.port == 5353)
    assert udp.protocol == Protocol.UDP
    assert udp.raw_state == "NONE"


def test_collect_uses_psutil_enrichment_when_available(monkeypatch):
    monkeypatch.setattr(macos_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        macos_module,
        "safe_process_metadata",
        lambda pid: ProcessMetadata(name="node", path="/usr/local/bin/node", working_directory="/srv/app"),
    )

    ports = MacOSCollector().collect()
    tcp_wildcard = next(p for p in ports if p.port == 3000)
    assert tcp_wildcard.process_path == "/usr/local/bin/node"
    assert tcp_wildcard.working_directory == "/srv/app"


def test_collect_passes_through_command_line_and_parent_facts(monkeypatch):
    monkeypatch.setattr(macos_module, "run_command", _fake_run_command)
    monkeypatch.setattr(
        macos_module,
        "safe_process_metadata",
        lambda pid: ProcessMetadata(
            name="node",
            command_line=["node", "server.js"],
            parent_pid=42,
            parent_name="zsh",
            parent_working_directory="/Users/dev",
        ),
    )

    ports = MacOSCollector().collect()
    tcp_wildcard = next(p for p in ports if p.port == 3000)
    assert tcp_wildcard.command_line == ["node", "server.js"]
    assert tcp_wildcard.parent_pid == 42
    assert tcp_wildcard.parent_process_name == "zsh"
    assert tcp_wildcard.parent_working_directory == "/Users/dev"


def test_collect_survives_one_protocol_failing(monkeypatch):
    def _partial_failure(args, timeout=10.0):
        if "-iTCP" in args:
            raise CollectorError("lsof not found")
        return _LSOF_UDP

    monkeypatch.setattr(macos_module, "run_command", _partial_failure)
    monkeypatch.setattr(
        macos_module, "safe_process_metadata", lambda pid: ProcessMetadata()
    )

    ports = MacOSCollector().collect()
    assert {p.port for p in ports} == {5353}


def test_collect_returns_empty_when_both_protocols_fail(monkeypatch):
    def _always_fail(args, timeout=10.0):
        raise CollectorError("lsof not found")

    monkeypatch.setattr(macos_module, "run_command", _always_fail)

    ports = MacOSCollector().collect()
    assert ports == []
