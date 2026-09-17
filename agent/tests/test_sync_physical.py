from typing import List
from unittest.mock import patch, MagicMock

import pytest

from portforge_agent.models import DiscoveredPort, Protocol, Source, PortState
from portforge_agent.reservations.models import Reservation
from portforge_agent.runtime.sync import SyncManager
from portforge_agent.runtime.state import RuntimeState
from portforge_agent.central_client import CentralClient
import portforge_agent.platform as pf

def _create_discovered_port(port: int, bind_address: str, protocol: Protocol = Protocol.TCP, source: Source = Source.PROCESS, host_port: int = None, project_name: str = None) -> DiscoveredPort:
    from datetime import datetime, timezone
    import uuid
    dp = DiscoveredPort(
        hostname="testhost",
        host_id="test-host-id",
        operating_system="linux",
        port=port,
        protocol=protocol,
        bind_address=bind_address,
        source=source,
        project_name=project_name
    )
    if host_port is not None:
        dp.host_port = host_port
    
    # Needs timestamps for snapshot
    dp.first_seen = datetime.now(timezone.utc)
    dp.last_seen = datetime.now(timezone.utc)
    return dp

@pytest.fixture
def mock_platform():
    with patch("portforge_agent.runtime.sync.pf.get_host_id", return_value="test-host-id") as mock_host:
        yield mock_host

@pytest.fixture
def sync_manager(tmp_path):
    state = RuntimeState()
    client = MagicMock(spec=CentralClient)
    
    with patch("portforge_agent.runtime.sync.save_state"):
        with patch("portforge_agent.runtime.sync.reservations_path", return_value=tmp_path / "reservations.json"):
            yield SyncManager(client, state)

def _mock_snapshot(sync_manager, discovered: List[DiscoveredPort], reservations: List[Reservation]) -> dict:
    with patch("portforge_agent.runtime.sync.discover_all_ports", return_value=discovered):
        with patch("portforge_agent.runtime.sync.ReservationStore") as mock_store:
            instance = MagicMock()
            instance.load.return_value = reservations
            mock_store.return_value = instance
            
            # Prevent state save side effects in test
            with patch.object(sync_manager.state, 'save', create=True):
                return sync_manager.capture_snapshot()

def test_dual_stack_preserved(sync_manager, mock_platform):
    # Test 1: Dual-stack preserved (0.0.0.0:3000 and :::3000)
    discovered = [
        _create_discovered_port(3000, "0.0.0.0"),
        _create_discovered_port(3000, "::")
    ]
    snapshot = _mock_snapshot(sync_manager, discovered, [])
    assert len(snapshot["observations"]) == 2
    addresses = {obs["bind_address"] for obs in snapshot["observations"]}
    assert addresses == {"0.0.0.0", "::"}
    
def test_specific_addresses_preserved(sync_manager, mock_platform):
    # Test 2: Specific addresses preserved
    discovered = [
        _create_discovered_port(52365, "127.0.0.1"),
        _create_discovered_port(52365, "192.168.4.29")
    ]
    snapshot = _mock_snapshot(sync_manager, discovered, [])
    assert len(snapshot["observations"]) == 2
    addresses = {obs["bind_address"] for obs in snapshot["observations"]}
    assert addresses == {"127.0.0.1", "192.168.4.29"}

def test_system_dual_stack_preserved(sync_manager, mock_platform):
    # Test 3: System dual stack preserved (e.g. 22)
    discovered = [
        _create_discovered_port(22, "0.0.0.0", source=Source.SYSTEM),
        _create_discovered_port(22, "::", source=Source.SYSTEM)
    ]
    snapshot = _mock_snapshot(sync_manager, discovered, [])
    assert len(snapshot["observations"]) == 2
    assert snapshot["observations"][0]["state"] == PortState.SYSTEM.value
    assert snapshot["observations"][1]["state"] == PortState.SYSTEM.value

def test_docker_dual_stack_preserved(sync_manager, mock_platform):
    # Test 4: Docker dual stack preserved
    d1 = _create_discovered_port(80, "0.0.0.0", source=Source.DOCKER, host_port=8080)
    d1.container_id = "abcd"
    d1.container_name = "test_container"
    d2 = _create_discovered_port(80, "::", source=Source.DOCKER, host_port=8080)
    d2.container_id = "abcd"
    d2.container_name = "test_container"
    
    discovered = [d1, d2]
    snapshot = _mock_snapshot(sync_manager, discovered, [])
    assert len(snapshot["observations"]) == 2
    assert snapshot["observations"][0]["container_id"] == "abcd"
    assert snapshot["observations"][1]["container_id"] == "abcd"
    # Note: snapshot port should be the port (80)
    assert snapshot["observations"][0]["port"] == 80
    
def test_address_agnostic_reservation_multiple_listeners(sync_manager, mock_platform):
    # Test 5: Address-agnostic reservation mapping to multiple distinct listeners on the same logical port
    discovered = [
        _create_discovered_port(8000, "127.0.0.1", project_name="my_proj"),
        _create_discovered_port(8000, "192.168.1.100", project_name="wrong_proj")
    ]
    reservations = [
        Reservation(reservation_id="r1", host_id="test-host-id", port=8000, protocol=Protocol.TCP, project="my_proj", bind_address=None)
    ]
    
    snapshot = _mock_snapshot(sync_manager, discovered, reservations)
    assert len(snapshot["observations"]) == 2
    
    # 127.0.0.1 matches the project -> ACTIVE
    # 192.168.1.100 does not match -> CONFLICT
    obs_127 = next(o for o in snapshot["observations"] if o["bind_address"] == "127.0.0.1")
    obs_192 = next(o for o in snapshot["observations"] if o["bind_address"] == "192.168.1.100")
    
    assert obs_127["state"] == PortState.ACTIVE.value
    assert obs_192["state"] == PortState.CONFLICT.value

def test_address_specific_reservation(sync_manager, mock_platform):
    # Test 6: Address-specific reservation uniquely identifying only its bound listener
    discovered = [
        _create_discovered_port(8000, "127.0.0.1", project_name="my_proj"),
        _create_discovered_port(8000, "192.168.1.100", project_name="my_proj")
    ]
    reservations = [
        Reservation(reservation_id="r2", host_id="test-host-id", port=8000, protocol=Protocol.TCP, project="my_proj", bind_address="127.0.0.1")
    ]
    
    snapshot = _mock_snapshot(sync_manager, discovered, reservations)
    assert len(snapshot["observations"]) == 2
    
    obs_127 = next(o for o in snapshot["observations"] if o["bind_address"] == "127.0.0.1")
    obs_192 = next(o for o in snapshot["observations"] if o["bind_address"] == "192.168.1.100")
    
    # 127.0.0.1 matches reservation -> ACTIVE
    # 192.168.1.100 has no reservation for it -> ACTIVE (because there's a listener, and it defaults to ACTIVE if no reservation matches)
    assert obs_127["state"] == PortState.ACTIVE.value
    assert obs_192["state"] == PortState.ACTIVE.value

def test_no_listener_reservation_skipped(sync_manager, mock_platform):
    # Test 7: No-listener reservation intentionally skipped in Phase 6 telemetry
    discovered = []
    reservations = [
        Reservation(reservation_id="r3", host_id="test-host-id", port=8000, protocol=Protocol.TCP, project="my_proj")
    ]
    
    snapshot = _mock_snapshot(sync_manager, discovered, reservations)
    assert len(snapshot["observations"]) == 0
