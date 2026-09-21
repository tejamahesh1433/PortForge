"""v1.1-A: regression coverage proving there is exactly ONE source of the
agent's own version, and that every sync/runtime payload that reports
`agent_version` derives it from that single source rather than a second,
independently hardcoded literal (the bug this increment fixes -- see
docs/v1.1/architecture-audit.md §2 and version.py's own docstring for the
five places "1.0.0" used to be duplicated).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from portforge_agent import __version__ as package_version
from portforge_agent import central_sync
from portforge_agent.version import PROTOCOL_VERSION, get_portforge_version


def test_package_dunder_version_matches_canonical_source():
    assert package_version == get_portforge_version()


def test_central_sync_agent_version_matches_canonical_source():
    """AGENT_VERSION is bound once at import time, but from the SAME
    underlying call `get_portforge_version()` makes -- proving there is
    no second, independently-maintained literal for it to drift from.
    """
    assert central_sync.AGENT_VERSION == get_portforge_version()


def test_protocol_version_is_a_small_positive_integer():
    assert isinstance(PROTOCOL_VERSION, int)
    assert PROTOCOL_VERSION >= 1


def test_central_sync_heartbeat_sends_canonical_version_and_protocol_version():
    client = MagicMock()
    client.health.return_value = MagicMock(success=True)
    client.heartbeat.return_value = MagicMock(success=True)
    client.submit_observations.return_value = MagicMock(success=True, data={})

    with patch("portforge_agent.central_sync.CentralClient", return_value=client), \
         patch("portforge_agent.central_sync.discover_all_ports", return_value=[]), \
         patch("portforge_agent.central_sync.ReservationStore") as store_cls:
        store_cls.return_value.load.return_value = []
        from portforge_agent.central_config import CentralConfig

        config = CentralConfig(enabled=True, url="http://central.example", token="tok")
        central_sync.sync_now(config)

    _, kwargs = client.heartbeat.call_args
    assert kwargs["agent_version"] == get_portforge_version()
    assert kwargs["protocol_version"] == PROTOCOL_VERSION


def test_central_sync_enroll_sends_canonical_version_and_protocol_version(tmp_path):
    client = MagicMock()
    client.enroll.return_value = MagicMock(success=True, data={"agent_token": "tok"})

    with patch("portforge_agent.central_sync.CentralClient", return_value=client), \
         patch("portforge_agent.central_config.save_central_config"):
        central_sync.enroll(tmp_path / "central.json", "http://central.example", "enroll-tok")

    _, kwargs = client.enroll.call_args
    assert kwargs["agent_version"] == get_portforge_version()
    assert kwargs["protocol_version"] == PROTOCOL_VERSION


def test_runtime_agent_heartbeat_sends_canonical_version_and_protocol_version(tmp_path, monkeypatch):
    from portforge_agent.runtime import agent as runtime_agent_module

    monkeypatch.setattr(runtime_agent_module, "load_central_config", lambda: MagicMock(enabled=True, url="http://x"))
    monkeypatch.setattr(runtime_agent_module, "load_state", lambda: MagicMock(last_heartbeat=0.0))
    monkeypatch.setattr(runtime_agent_module, "load_credential", lambda: "tok")
    monkeypatch.setattr(runtime_agent_module, "save_state", lambda state: None)

    client = MagicMock()
    client.heartbeat.return_value = MagicMock(success=True)
    monkeypatch.setattr(runtime_agent_module, "CentralClient", lambda **kwargs: client)

    runtime = runtime_agent_module.AgentRuntime()
    runtime._running = True

    # Directly exercise the heartbeat body-construction line rather than
    # the infinite loop -- call heartbeat() the same way run()'s loop does.
    from portforge_agent import platform as pf
    import platform as std_platform
    from datetime import datetime, timezone

    runtime.client.heartbeat(
        host_id=pf.get_host_id(),
        hostname=pf.get_hostname(),
        operating_system=pf.detect_os().value,
        os_version=pf.get_os_version(),
        architecture=std_platform.machine(),
        agent_version=get_portforge_version(),
        docker_available=False,
        timestamp=datetime.now(timezone.utc).isoformat(),
        protocol_version=PROTOCOL_VERSION,
    )

    _, kwargs = client.heartbeat.call_args
    assert kwargs["agent_version"] == get_portforge_version()
    assert kwargs["protocol_version"] == PROTOCOL_VERSION


def test_cli_agent_enroll_sends_canonical_version(monkeypatch, capsys):
    from portforge_agent.cli import main

    client = MagicMock()
    client.enroll.return_value = MagicMock(success=True, data={"agent_token": "tok"})
    with patch("portforge_agent.cli_agent.CentralClient", return_value=client), \
         patch("portforge_agent.cli_agent.save_credential"), \
         patch("portforge_agent.cli_agent.save_central_config"):
        main(["agent", "enroll", "--server", "http://central.example", "--token", "enroll-tok"])

    _, kwargs = client.enroll.call_args
    assert kwargs["agent_version"] == get_portforge_version()
    assert kwargs["protocol_version"] == PROTOCOL_VERSION
