import pytest
from unittest.mock import patch, MagicMock
from portforge_agent.runtime.state import RuntimeState
from portforge_agent.runtime.sync import SyncManager
from portforge_agent.evaluate import EvaluatedPort
from portforge_agent.models import PortState, DiscoveredPort, Protocol, Source

def test_sync_manager_capture_snapshot():
    state = RuntimeState()
    client = MagicMock()
    manager = SyncManager(client, state)
    
    with patch("portforge_agent.runtime.sync.discover_all_ports") as mock_discover, \
         patch("portforge_agent.runtime.sync.ReservationStore") as mock_store, \
         patch("portforge_agent.evaluate.evaluate_physical") as mock_evaluate, \
         patch("portforge_agent.runtime.sync.pf.get_host_id", return_value="test-uuid"):

        mock_store.return_value.load.return_value = []

        # Mock evaluate returning 1 port
        port = DiscoveredPort(
            port=8080,
            protocol=Protocol.TCP,
            source=Source.PROCESS,
            state=PortState.ACTIVE,
            hostname="test",
            host_id="test-uuid",
            operating_system="windows",
            bind_address="0.0.0.0"
        )
        # Needs timestamps for snapshot
        from datetime import datetime, timezone
        port.first_seen = datetime.now(timezone.utc)
        port.last_seen = datetime.now(timezone.utc)
        
        mock_discover.return_value = [port]
        
        mock_evaluate.return_value = EvaluatedPort(
            host_id="test-uuid",
            port=8080,
            protocol=Protocol.TCP,
            state=PortState.ACTIVE,
            discovered=port
        )

        snapshot = manager.capture_snapshot()

        assert snapshot["sequence"] == 1
        assert len(snapshot["observations"]) == 1
        assert snapshot["observations"][0]["port"] == 8080
        assert snapshot["observations"][0]["state"] == "ACTIVE"
        
        assert state.sequence_id == 1
        assert state.last_observation_count == 1

def test_sync_manager_flush_snapshot_success():
    state = RuntimeState()
    client = MagicMock()
    client.submit_observations.return_value.success = True
    
    manager = SyncManager(client, state)
    
    snapshot = {
        "scan_id": "test-scan",
        "observed_at": 1000.0,
        "sequence": 1,
        "observations": []
    }
    
    with patch("portforge_agent.runtime.sync.pf.get_host_id", return_value="test-uuid"):
        success = manager.flush_snapshot(snapshot)
        
    assert success is True
    assert client.submit_observations.called
    assert state.last_sync_error is None

def test_sync_manager_flush_snapshot_failure():
    state = RuntimeState()
    client = MagicMock()
    client.submit_observations.return_value.success = False
    client.submit_observations.return_value.error = "timeout"
    
    manager = SyncManager(client, state)
    
    snapshot = {
        "scan_id": "test-scan",
        "observed_at": 1000.0,
        "sequence": 1,
        "observations": []
    }
    
    with patch("portforge_agent.runtime.sync.pf.get_host_id", return_value="test-uuid"):
        success = manager.flush_snapshot(snapshot)
        
    assert success is False
    assert manager._buffered_snapshot == snapshot
    assert state.last_sync_error == "timeout"
