"""Phase 22: `portforge upgrade` CLI commands -- admin upgrade observability.

Covers: status (upgrade-id and host-id paths), retry, cancel, rollout;
human output, JSON output, missing/invalid args, missing admin token,
central error responses. CentralClient is mocked throughout.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from portforge_agent.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_ARGS = ["--url", "http://central.example"]
_ADMIN_TOKEN = "test-admin-bootstrap-token"


def _mock_result(success=True, data=None, error=None, status_code=200):
    m = MagicMock()
    m.success = success
    m.data = data
    m.error = error
    m.status_code = status_code
    return m


def _mock_client(**method_results):
    """Build a MagicMock CentralClient; keyword args map method names to results."""
    instance = MagicMock()
    for name, result in method_results.items():
        getattr(instance, name).return_value = result
    return instance


_UPGRADE_STATUS_DATA = {
    "id": "upg-aaa",
    "host_id": "host-111",
    "hostname": "box1",
    "current_version": "1.2.0",
    "target_version": "1.3.0",
    "upgrade_state": "FAILED",
    "progress_status": "DOWNLOAD_FAILED",
    "waiting_reason": None,
    "failure_code": "SHA256_MISMATCH",
    "failure_summary": "Downloaded artifact hash did not match.",
    "explanation": "Re-upload the artifact and retry.",
    "operator_actions": ["retry", "cancel"],
    "reconciliation_status": "NEEDS_RETRY",
}

_ROLLOUT_DATA = {
    "request_id": "rollout-xyz",
    "state": "PARTIAL",
    "total_hosts": 10,
    "succeeded_count": 7,
    "failed_count": 2,
    "pending_count": 1,
    "started_at": "2026-09-20T10:00:00Z",
}


# ---------------------------------------------------------------------------
# upgrade status -- upgrade-id path
# ---------------------------------------------------------------------------

def test_upgrade_status_by_upgrade_id_human(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--upgrade-id", "upg-aaa"] + _BASE_ARGS)

    assert rc == 0
    out = capsys.readouterr().out
    assert "1.2.0" in out
    assert "1.3.0" in out
    assert "FAILED" in out
    assert "SHA256_MISMATCH" in out
    assert "operator_actions" in out.lower() or "retry" in out
    client.get_upgrade_status.assert_called_once_with("upg-aaa")


def test_upgrade_status_by_upgrade_id_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--upgrade-id", "upg-aaa"] + _BASE_ARGS + ["--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["upgrade_state"] == "FAILED"
    assert parsed["failure_code"] == "SHA256_MISMATCH"


# ---------------------------------------------------------------------------
# upgrade status -- host-id path
# ---------------------------------------------------------------------------

def test_upgrade_status_by_host_id_calls_correct_method(monkeypatch):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_host_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--host-id", "host-111"] + _BASE_ARGS)

    assert rc == 0
    client.get_host_upgrade_status.assert_called_once_with("host-111")
    client.get_upgrade_status.assert_not_called()


def test_upgrade_status_host_id_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_host_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--host-id", "host-111"] + _BASE_ARGS + ["--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["id"] == "upg-aaa"


# ---------------------------------------------------------------------------
# upgrade status -- list response (array of statuses from host endpoint)
# ---------------------------------------------------------------------------

def test_upgrade_status_list_response_human(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    items = [_UPGRADE_STATUS_DATA, {**_UPGRADE_STATUS_DATA, "id": "upg-bbb", "upgrade_state": "SUCCEEDED"}]
    client = _mock_client(get_host_upgrade_status=_mock_result(data=items))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--host-id", "host-111"] + _BASE_ARGS)

    assert rc == 0
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "SUCCEEDED" in out


# ---------------------------------------------------------------------------
# upgrade status -- missing both flags
# ---------------------------------------------------------------------------

def test_upgrade_status_missing_both_ids_returns_2(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    with patch("portforge_agent.central_client.CentralClient"):
        rc = main(["upgrade", "status"] + _BASE_ARGS)

    assert rc == 2
    assert "required" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# upgrade status -- missing admin token
# ---------------------------------------------------------------------------

def test_upgrade_status_missing_admin_token_returns_1(monkeypatch, capsys):
    monkeypatch.delenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "status", "--upgrade-id", "upg-aaa"] + _BASE_ARGS)

    assert rc == 1
    ctor.assert_not_called()
    assert "admin" in capsys.readouterr().err.lower()


def test_upgrade_status_admin_token_from_flag(monkeypatch, capsys):
    monkeypatch.delenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    client = _mock_client(get_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client) as ctor:
        rc = main(
            ["upgrade", "status", "--upgrade-id", "upg-aaa", "--admin-token", "explicit-tok"]
            + _BASE_ARGS
        )

    assert rc == 0
    ctor.assert_called_once_with("http://central.example", token="explicit-tok")


def test_upgrade_status_explicit_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "env-tok")
    client = _mock_client(get_upgrade_status=_mock_result(data=_UPGRADE_STATUS_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client) as ctor:
        main(
            ["upgrade", "status", "--upgrade-id", "upg-aaa", "--admin-token", "flag-tok"]
            + _BASE_ARGS
        )

    ctor.assert_called_once_with("http://central.example", token="flag-tok")


# ---------------------------------------------------------------------------
# upgrade status -- central error response
# ---------------------------------------------------------------------------

def test_upgrade_status_central_error_returns_1(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_status=_mock_result(success=False, error="Not found", status_code=404))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--upgrade-id", "missing-id"] + _BASE_ARGS)

    assert rc == 1
    assert "Not found" in capsys.readouterr().err


def test_upgrade_status_central_error_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(
        get_upgrade_status=_mock_result(success=False, error="Forbidden", status_code=403)
    )
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "status", "--upgrade-id", "upg-aaa"] + _BASE_ARGS + ["--json"])

    assert rc == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is False
    assert "Forbidden" in (parsed.get("error") or "")


# ---------------------------------------------------------------------------
# upgrade retry
# ---------------------------------------------------------------------------

def test_upgrade_retry_requires_yes_flag(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "retry", "upg-aaa"] + _BASE_ARGS)

    assert rc == 2
    ctor.assert_not_called()
    assert "--yes" in capsys.readouterr().err


def test_upgrade_retry_success_human(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(retry_upgrade=_mock_result(data={"queued": True}, status_code=201))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "retry", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 0
    assert "upg-aaa" in capsys.readouterr().out
    client.retry_upgrade.assert_called_once_with("upg-aaa")


def test_upgrade_retry_success_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(retry_upgrade=_mock_result(data={"queued": True}, status_code=201))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "retry", "upg-aaa", "--yes"] + _BASE_ARGS + ["--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True


def test_upgrade_retry_central_error(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(retry_upgrade=_mock_result(success=False, error="Upgrade already succeeded."))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "retry", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 1
    assert "already succeeded" in capsys.readouterr().err


def test_upgrade_retry_missing_admin_token(monkeypatch, capsys):
    monkeypatch.delenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "retry", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 1
    ctor.assert_not_called()


# ---------------------------------------------------------------------------
# upgrade cancel
# ---------------------------------------------------------------------------

def test_upgrade_cancel_requires_yes_flag(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "cancel", "upg-aaa"] + _BASE_ARGS)

    assert rc == 2
    ctor.assert_not_called()
    assert "--yes" in capsys.readouterr().err


def test_upgrade_cancel_success_human(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(cancel_upgrade=_mock_result(data={}))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "cancel", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 0
    assert "upg-aaa" in capsys.readouterr().out
    client.cancel_upgrade.assert_called_once_with("upg-aaa")


def test_upgrade_cancel_success_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(cancel_upgrade=_mock_result(data={"cancelled": True}))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "cancel", "upg-aaa", "--yes"] + _BASE_ARGS + ["--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True


def test_upgrade_cancel_central_error(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(cancel_upgrade=_mock_result(success=False, error="Cannot cancel a completed upgrade."))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "cancel", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 1
    assert "completed" in capsys.readouterr().err


def test_upgrade_cancel_missing_admin_token(monkeypatch, capsys):
    monkeypatch.delenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "cancel", "upg-aaa", "--yes"] + _BASE_ARGS)

    assert rc == 1
    ctor.assert_not_called()


# ---------------------------------------------------------------------------
# upgrade rollout
# ---------------------------------------------------------------------------

def test_upgrade_rollout_human(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_rollout=_mock_result(data=_ROLLOUT_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "rollout", "rollout-xyz"] + _BASE_ARGS)

    assert rc == 0
    out = capsys.readouterr().out
    assert "PARTIAL" in out
    assert "10" in out  # total_hosts
    assert "7" in out   # succeeded_count
    client.get_upgrade_rollout.assert_called_once_with("rollout-xyz")


def test_upgrade_rollout_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_rollout=_mock_result(data=_ROLLOUT_DATA))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "rollout", "rollout-xyz"] + _BASE_ARGS + ["--json"])

    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["state"] == "PARTIAL"
    assert parsed["total_hosts"] == 10


def test_upgrade_rollout_central_error(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", _ADMIN_TOKEN)
    client = _mock_client(get_upgrade_rollout=_mock_result(success=False, error="Rollout not found.", status_code=404))
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        rc = main(["upgrade", "rollout", "no-such-rollout"] + _BASE_ARGS)

    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_upgrade_rollout_missing_admin_token(monkeypatch, capsys):
    monkeypatch.delenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", raising=False)
    with patch("portforge_agent.central_client.CentralClient") as ctor:
        rc = main(["upgrade", "rollout", "rollout-xyz"] + _BASE_ARGS)

    assert rc == 1
    ctor.assert_not_called()


# ---------------------------------------------------------------------------
# CentralClient method routing -- unit-level checks
# ---------------------------------------------------------------------------

def test_central_client_get_upgrade_status_calls_correct_endpoint():
    from portforge_agent.central_client import CentralClient

    client = CentralClient("https://central.example.com", token="tok")
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.get_upgrade_status("upg-123")

    mock_req.assert_called_once_with("GET", "/api/upgrades/upg-123/status")


def test_central_client_get_host_upgrade_status_calls_correct_endpoint():
    from portforge_agent.central_client import CentralClient

    client = CentralClient("https://central.example.com", token="tok")
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.get_host_upgrade_status("host-456")

    mock_req.assert_called_once_with("GET", "/api/hosts/host-456/upgrade-status")


def test_central_client_retry_upgrade_calls_correct_endpoint():
    from portforge_agent.central_client import CentralClient

    client = CentralClient("https://central.example.com", token="tok")
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.retry_upgrade("upg-789")

    mock_req.assert_called_once_with("POST", "/api/upgrades/upg-789/retry", body={})


def test_central_client_cancel_upgrade_calls_correct_endpoint():
    from portforge_agent.central_client import CentralClient

    client = CentralClient("https://central.example.com", token="tok")
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.cancel_upgrade("upg-789")

    mock_req.assert_called_once_with("POST", "/api/upgrades/upg-789/cancel", body={})


def test_central_client_get_upgrade_rollout_calls_correct_endpoint():
    from portforge_agent.central_client import CentralClient

    client = CentralClient("https://central.example.com", token="tok")
    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.get_upgrade_rollout("rollout-req-001")

    mock_req.assert_called_once_with("GET", "/api/upgrade-rollouts/rollout-req-001")
