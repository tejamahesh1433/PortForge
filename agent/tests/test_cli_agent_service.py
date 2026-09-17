"""CLI-level tests for `portforge agent service <action>` -- dispatch,
JSON output, and exit codes. service_ops itself is mocked here (its own
native-tool-invocation behavior is covered by test_service_ops.py), so
these tests only exercise cli_agent.py's argument parsing and result
rendering.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from portforge_agent.cli import main
from portforge_agent import service_ops


def _result(success: bool = True, message: str = "ok", detail=None) -> service_ops.ServiceOpResult:
    return service_ops.ServiceOpResult(success=success, message=message, detail=detail)


def test_agent_service_install_success_exit_code_and_message(capsys):
    with patch("portforge_agent.cli_agent.service_ops.install", return_value=_result(True, "installed")):
        code = main(["agent", "service", "install"])

    assert code == 0
    assert "installed" in capsys.readouterr().out


def test_agent_service_install_failure_exit_code(capsys):
    with patch("portforge_agent.cli_agent.service_ops.install", return_value=_result(False, "boom")):
        code = main(["agent", "service", "install"])

    assert code == 1
    assert "boom" in capsys.readouterr().out


def test_agent_service_status_json_output(capsys):
    with patch(
        "portforge_agent.cli_agent.service_ops.status", return_value=_result(True, "installed", detail="d")
    ):
        code = main(["agent", "service", "status", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"success": True, "message": "installed", "detail": "d"}


def test_agent_service_start_dispatches_to_service_ops(capsys):
    with patch("portforge_agent.cli_agent.service_ops.start", return_value=_result(True, "started")) as mock_start:
        code = main(["agent", "service", "start"])

    assert code == 0
    mock_start.assert_called_once()


def test_agent_service_stop_dispatches_to_service_ops(capsys):
    with patch("portforge_agent.cli_agent.service_ops.stop", return_value=_result(True, "stopped")) as mock_stop:
        code = main(["agent", "service", "stop"])

    assert code == 0
    mock_stop.assert_called_once()


def test_agent_service_uninstall_dispatches_to_service_ops(capsys):
    with patch(
        "portforge_agent.cli_agent.service_ops.uninstall", return_value=_result(True, "removed")
    ) as mock_uninstall:
        code = main(["agent", "service", "uninstall"])

    assert code == 0
    mock_uninstall.assert_called_once()


def test_agent_service_unsupported_platform_exit_code_and_message(capsys):
    with patch(
        "portforge_agent.cli_agent.service_ops.uninstall",
        side_effect=service_ops.UnsupportedPlatformError("Native service management is not supported on 'unknown'."),
    ):
        code = main(["agent", "service", "uninstall"])

    assert code == 2
    assert "not supported" in capsys.readouterr().err


def test_agent_service_unsupported_platform_json_output(capsys):
    with patch(
        "portforge_agent.cli_agent.service_ops.install",
        side_effect=service_ops.UnsupportedPlatformError("nope"),
    ):
        code = main(["agent", "service", "install", "--json"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"success": False, "error": "nope"}
