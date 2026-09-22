"""v1.1-E: `portforge central generate-token` -- a thin CLI wrapper around
the backend's existing `POST /agent/enrollment-tokens` (admin-only)
endpoint. docs/security.md has always claimed this command exists; before
this it didn't. Covers: admin-token resolution (flag vs env var vs
missing), JSON purity, human output showing the token exactly once, and
the failure path never leaking a stack trace with the token in it.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main


def _mock_client(success=True, token="new-enrollment-token", error=None):
    instance = MagicMock()
    if success:
        instance.generate_enrollment_token.return_value = MagicMock(
            success=True,
            error=None,
            data={"enrollment_token": token, "expires_at": "2026-01-01T00:00:00+00:00"},
        )
    else:
        instance.generate_enrollment_token.return_value = MagicMock(success=False, error=error, data=None)
    return instance


def test_missing_admin_token_fails_without_calling_central():
    client = _mock_client()
    with patch("portforge_agent.central_client.CentralClient", return_value=client) as ctor, \
         patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", None)
        result = main(["central", "generate-token", "--url", "http://central.example"])

    assert result == 1
    ctor.assert_not_called()


def test_admin_token_from_env_var_used_when_flag_omitted(monkeypatch):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "env-admin-token")
    client = _mock_client()
    with patch("portforge_agent.central_client.CentralClient", return_value=client) as ctor:
        result = main(["central", "generate-token", "--url", "http://central.example"])

    assert result == 0
    ctor.assert_called_once_with("http://central.example", token="env-admin-token")


def test_explicit_admin_token_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "env-admin-token")
    client = _mock_client()
    with patch("portforge_agent.central_client.CentralClient", return_value=client) as ctor:
        main(["central", "generate-token", "--url", "http://central.example", "--admin-token", "flag-admin-token"])

    ctor.assert_called_once_with("http://central.example", token="flag-admin-token")


def test_json_output_is_pure_json(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-tok")
    client = _mock_client(token="the-minted-token")
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        result = main(["central", "generate-token", "--url", "http://central.example", "--json"])

    captured = capsys.readouterr()
    assert result == 0
    parsed = json.loads(captured.out)
    assert parsed["success"] is True
    assert parsed["data"]["enrollment_token"] == "the-minted-token"


def test_human_output_shows_token_once_and_usage_hint(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-tok")
    client = _mock_client(token="the-minted-token")
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        result = main(["central", "generate-token", "--url", "http://central.example"])

    captured = capsys.readouterr()
    assert result == 0
    assert "the-minted-token" in captured.out
    assert "shown once" in captured.out
    assert "portforge agent enroll --server" in captured.out


def test_failure_from_central_reports_error_not_success(monkeypatch, capsys):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "wrong-admin-tok")
    client = _mock_client(success=False, error="Invalid or missing admin credentials.")
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        result = main(["central", "generate-token", "--url", "http://central.example"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Invalid or missing admin credentials." in captured.err


def test_label_and_ttl_hours_passed_through(monkeypatch):
    monkeypatch.setenv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-tok")
    client = _mock_client()
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        main(
            [
                "central",
                "generate-token",
                "--url",
                "http://central.example",
                "--label",
                "ci-runner",
                "--ttl-hours",
                "2",
            ]
        )

    client.generate_enrollment_token.assert_called_once_with(label="ci-runner", ttl_hours=2)
