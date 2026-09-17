"""Mock-based tests for the Linux collector.

`ss` cannot actually run on this development machine (Windows), so per
project convention we mock its output instead of pretending to execute
another operating system's commands.
"""
from portforge_agent.collectors.base import CollectorError, ProcessMetadata
from portforge_agent.collectors import linux as linux_module
from portforge_agent.collectors.linux import LinuxCollector
from portforge_agent.models import Protocol, Source

_SS_OUTPUT = (
    'tcp   LISTEN 0      128         0.0.0.0:3000        0.0.0.0:*    users:(("node",pid=1234,fd=23))\n'
    'tcp   LISTEN 0      128       127.0.0.1:5432        0.0.0.0:*    users:(("postgres",pid=2345,fd=7))\n'
    'tcp   LISTEN 0      128            [::]:8000           [::]:*    users:(("python3",pid=3456,fd=5))\n'
    'tcp   LISTEN 0      128         0.0.0.0:9090        0.0.0.0:*\n'
    'udp   UNCONN 0      0           0.0.0.0:68           0.0.0.0:*\n'
    'tcp   ESTAB  0      0        10.0.0.5:52344      93.1.1.1:443\n'
)


def test_collect_parses_listening_tcp_and_bound_udp(monkeypatch):
    monkeypatch.setattr(linux_module, "run_command", lambda args, timeout=10.0: _SS_OUTPUT)
    monkeypatch.setattr(
        linux_module, "safe_process_metadata", lambda pid: ProcessMetadata()
    )

    ports = LinuxCollector().collect()

    # ESTAB (outbound) connection must be excluded.
    assert {p.port for p in ports} == {3000, 5432, 8000, 9090, 68}

    node_port = next(p for p in ports if p.port == 3000)
    assert node_port.protocol == Protocol.TCP
    assert node_port.pid == 1234
    assert node_port.process_name == "node"
    assert node_port.bind_address == "0.0.0.0"
    assert node_port.raw_state == "LISTEN"
    assert node_port.source == Source.PROCESS

    v6_port = next(p for p in ports if p.port == 8000)
    assert v6_port.bind_address == "::"

    udp_port = next(p for p in ports if p.port == 68)
    assert udp_port.protocol == Protocol.UDP
    assert udp_port.raw_state == "UNCONN"


def test_collect_handles_missing_process_column_permission_denied(monkeypatch):
    monkeypatch.setattr(linux_module, "run_command", lambda args, timeout=10.0: _SS_OUTPUT)
    monkeypatch.setattr(
        linux_module, "safe_process_metadata", lambda pid: ProcessMetadata()
    )

    ports = LinuxCollector().collect()
    no_owner_port = next(p for p in ports if p.port == 9090)
    assert no_owner_port.pid is None
    assert no_owner_port.process_name is None
    assert no_owner_port.source == Source.SYSTEM


def test_collect_uses_psutil_enrichment_over_ss_process_name(monkeypatch):
    monkeypatch.setattr(linux_module, "run_command", lambda args, timeout=10.0: _SS_OUTPUT)
    monkeypatch.setattr(
        linux_module,
        "safe_process_metadata",
        lambda pid: ProcessMetadata(name="node-enriched", path="/usr/bin/node", working_directory="/srv"),
    )

    ports = LinuxCollector().collect()
    node_port = next(p for p in ports if p.port == 3000)
    assert node_port.process_name == "node-enriched"
    assert node_port.process_path == "/usr/bin/node"
    assert node_port.working_directory == "/srv"


def test_collect_passes_through_command_line_and_parent_facts(monkeypatch):
    monkeypatch.setattr(linux_module, "run_command", lambda args, timeout=10.0: _SS_OUTPUT)
    monkeypatch.setattr(
        linux_module,
        "safe_process_metadata",
        lambda pid: ProcessMetadata(
            name="node",
            command_line=["node", "server.js"],
            parent_pid=42,
            parent_name="bash",
            parent_working_directory="/home/dev",
        ),
    )

    ports = LinuxCollector().collect()
    node_port = next(p for p in ports if p.port == 3000)
    assert node_port.command_line == ["node", "server.js"]
    assert node_port.parent_pid == 42
    assert node_port.parent_process_name == "bash"
    assert node_port.parent_working_directory == "/home/dev"


def test_collect_returns_empty_when_ss_unavailable(monkeypatch):
    def _fail(args, timeout=10.0):
        raise CollectorError("ss: command not found")

    monkeypatch.setattr(linux_module, "run_command", _fail)

    ports = LinuxCollector().collect()
    assert ports == []
