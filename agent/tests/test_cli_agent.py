import pytest
from unittest.mock import patch, MagicMock
from portforge_agent.cli import main

def test_agent_status_command(capsys):
    with patch("portforge_agent.cli_agent.load_central_config") as mock_config, \
         patch("portforge_agent.cli_agent.load_state") as mock_state, \
         patch("portforge_agent.cli_agent.pf.get_host_id", return_value="test-uuid"), \
         patch("portforge_agent.cli_agent.pf.get_hostname", return_value="test-host"), \
         patch("portforge_agent.cli_agent.pf.detect_os") as mock_os:
         
        mock_os.return_value.value = "windows"
        mock_config.return_value.url = None
        mock_config.return_value.enabled = False
        mock_state.return_value.last_scan = 0
        mock_state.return_value.last_sync = 0
        mock_state.return_value.last_heartbeat = 0
        mock_state.return_value.last_sync_error = None
        mock_state.return_value.sequence_id = 0
        mock_state.return_value.last_observation_count = 0
        
        main(["agent", "status"])
        
        captured = capsys.readouterr()
        assert "PortForge Agent Status" in captured.out
        assert "test-uuid" in captured.out
        assert "Not Enrolled" in captured.out

def test_agent_enroll_command(capsys):
    with patch("portforge_agent.cli_agent.CentralClient") as mock_client, \
         patch("portforge_agent.cli_agent.save_credential") as mock_save_cred, \
         patch("portforge_agent.cli_agent.save_central_config") as mock_save_config:
        
        instance = mock_client.return_value
        res = MagicMock()
        res.success = True
        res.data = {"agent_token": "test-token"}
        instance.enroll.return_value = res
        
        result = main(["agent", "enroll", "--server", "http://test", "--token", "secret"])
        assert result == 0
        
        captured = capsys.readouterr()
        assert "Successfully enrolled agent" in captured.out
        mock_save_cred.assert_called_once_with("test-token")
        mock_save_config.assert_called_once()
