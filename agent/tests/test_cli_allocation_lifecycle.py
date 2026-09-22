import json
import pytest
from unittest.mock import patch, MagicMock
from portforge_agent.cli import main

@patch("portforge_agent.cli._allocation_client")
def test_cli_allocation_verify(mock_client_factory, capsys):
    mock_client = MagicMock()
    mock_client_factory.return_value = (mock_client, None)
    
    # Mock get_allocation to return a local host
    mock_client.get_allocation.return_value = MagicMock(
        success=True,
        data={
            "allocation_id": "123",
            "host": {"id": "test-host", "hostname": "local"},
            "status": "active",
            "allocations": [
                {"name": "web", "purpose": "frontend", "port": 8000, "protocol": "tcp"}
            ]
        }
    )
    
    with patch("portforge_agent.cli.pf.get_host_id", return_value="test-host"), \
         patch("portforge_agent.bindprobe.probe_bind") as mock_probe:
        
        mock_probe.return_value = MagicMock(available=True)
        
        assert main(["allocation", "verify", "123", "--json"]) == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        
        assert data["allocations"][0]["bind_probe"] == "verified_free"
        assert data["allocations"][0]["verification_source"] == "local"

@patch("portforge_agent.cli._allocation_client")
def test_cli_allocation_verify_remote(mock_client_factory, capsys):
    mock_client = MagicMock()
    mock_client_factory.return_value = (mock_client, None)
    
    # Mock get_allocation to return a remote host
    mock_client.get_allocation.return_value = MagicMock(
        success=True,
        data={
            "allocation_id": "123",
            "host": {"id": "remote-host", "hostname": "remote"},
            "status": "active",
            "allocations": [
                {"name": "web", "purpose": "frontend", "port": 8000, "protocol": "tcp"}
            ]
        }
    )
    
    mock_client.verify_allocation.return_value = MagicMock(
        success=True,
        data={
            "allocation_id": "123",
            "host": {"id": "remote-host", "hostname": "remote"},
            "status": "active",
            "allocations": [
                {"name": "web", "purpose": "frontend", "port": 8000, "protocol": "tcp", "bind_probe": "verified_occupied"}
            ]
        }
    )
    
    with patch("portforge_agent.cli.pf.get_host_id", return_value="test-host"):
        assert main(["allocation", "verify", "123", "--json"]) == 0
        out, _ = capsys.readouterr()
        data = json.loads(out)
        
        assert data["allocations"][0]["bind_probe"] == "verified_occupied"

@patch("portforge_agent.cli._allocation_client")
def test_cli_allocation_list(mock_client_factory, capsys):
    mock_client = MagicMock()
    mock_client_factory.return_value = (mock_client, None)
    
    mock_client.list_allocations.return_value = MagicMock(
        success=True,
        data=[
            {
                "allocation_id": "123",
                "host": {"id": "test-host", "hostname": "local"},
                "status": "active",
                "project": "test",
                "allocations": [
                    {"name": "web", "purpose": "frontend", "port": 8000, "protocol": "tcp"}
                ]
            }
        ]
    )
    
    assert main(["allocation", "list", "--json"]) == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    
    assert len(data) == 1
    assert data[0]["allocation_id"] == "123"

@patch("portforge_agent.cli._allocation_client")
def test_cli_allocation_release_idempotent(mock_client_factory, capsys):
    mock_client = MagicMock()
    mock_client_factory.return_value = (mock_client, None)
    
    mock_client.release_allocation.return_value = MagicMock(
        success=False,
        data={"error": {"code": "ALLOCATION_NOT_FOUND"}}
    )
    
    assert main(["allocation", "release", "123", "--json"]) == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    
    assert data["status"] == "released"
    assert data["idempotent_replay"] == True
