"""Phase 8A: CLI tests for `allocate` / `allocation get` / `allocation release`.

Mocks `CentralClient` at its definition module (`portforge_agent.central_client`)
-- `cli.py`'s `_allocation_client()` does a local `from .central_client
import CentralClient` inside the function body, so patching the class at
its origin module is what actually takes effect, following this test
file's neighbors' (test_cli_agent.py) established pattern.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main

_SAMPLE_ALLOCATION = {
    "allocation_id": "11111111-1111-1111-1111-111111111111",
    "project": "jarvis",
    "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"},
    "status": "active",
    "allocations": [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3000, "reservation_id": "r1"},
        {"name": "api", "purpose": "api", "protocol": "tcp", "port": 8000, "reservation_id": "r2"},
    ],
    "validation": {"snapshot_age_seconds": 2, "host_health_state": "HEALTHY", "bind_probe": "not_remote_capable"},
    "created_at": "2026-09-21T00:00:00Z",
    "released_at": None,
}


def _mock_client(success=True, data=None):
    instance = MagicMock()
    result = MagicMock()
    result.success = success
    result.data = data
    instance.list_hosts.return_value = MagicMock(
        success=True, data={"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"}]}
    )
    instance.create_allocation.return_value = result
    instance.get_allocation.return_value = result
    instance.release_allocation.return_value = result
    return instance


def test_allocate_via_flags_json_output_is_pure_json(capsys):
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=_SAMPLE_ALLOCATION)

        result = main(
            [
                "allocate",
                "--host",
                "NTMKEYA",
                "--project",
                "jarvis",
                "--request",
                "frontend:frontend:tcp",
                "--request",
                "api:api:tcp",
                "--url",
                "http://central.example",
                "--json",
            ]
        )

    assert result == 0
    captured = capsys.readouterr()
    # stdout must be JSON only -- no human log/progress text mixed in.
    parsed = json.loads(captured.out)
    assert parsed["allocation_id"] == _SAMPLE_ALLOCATION["allocation_id"]
    assert captured.err == ""


def test_allocate_resolves_hostname_to_uuid_before_calling_central():
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(success=True, data=_SAMPLE_ALLOCATION)
        mock_cls.return_value = client

        main(
            [
                "allocate",
                "--host",
                "NTMKEYA",
                "--project",
                "jarvis",
                "--request",
                "api:api:tcp",
                "--url",
                "http://central.example",
                "--json",
            ]
        )

        client.create_allocation.assert_called_once()
        _, kwargs = client.create_allocation.call_args
        assert kwargs["host_id"] == "22222222-2222-2222-2222-222222222222"


def test_allocate_via_file(tmp_path, capsys):
    request_file = tmp_path / "portforge.request.json"
    request_file.write_text(
        json.dumps(
            {
                "request_id": "jarvis-bootstrap-001",
                "project": "jarvis",
                "host": "NTMKEYA",
                "requests": [{"name": "frontend", "purpose": "frontend", "protocol": "tcp"}],
            }
        ),
        encoding="utf-8",
    )

    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=_SAMPLE_ALLOCATION)
        result = main(["allocate", "--file", str(request_file), "--url", "http://central.example", "--json"])

    assert result == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["allocation_id"] == _SAMPLE_ALLOCATION["allocation_id"]


def test_allocate_via_stdin(monkeypatch, capsys):
    import io

    payload = {
        "project": "jarvis",
        "host": "NTMKEYA",
        "requests": [{"name": "api", "purpose": "api", "protocol": "tcp"}],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=_SAMPLE_ALLOCATION)
        result = main(["allocate", "--stdin", "--url", "http://central.example", "--json"])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["allocation_id"] == _SAMPLE_ALLOCATION["allocation_id"]


def test_allocate_format_env_output(capsys):
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=_SAMPLE_ALLOCATION)
        result = main(
            [
                "allocate",
                "--host",
                "NTMKEYA",
                "--project",
                "jarvis",
                "--request",
                "frontend:frontend:tcp",
                "--request",
                "api:api:tcp",
                "--url",
                "http://central.example",
                "--format",
                "env",
            ]
        )

    assert result == 0
    out = capsys.readouterr().out
    assert "FRONTEND_PORT=3000" in out
    assert "API_PORT=8000" in out


def test_allocate_missing_url_is_operational_error(capsys, monkeypatch):
    monkeypatch.delenv("PORTFORGE_CENTRAL_URL", raising=False)
    with patch("portforge_agent.central_config.load_central_config") as mock_config:
        mock_config.return_value.url = None
        result = main(
            ["allocate", "--host", "NTMKEYA", "--project", "jarvis", "--request", "api:api:tcp", "--json"]
        )
    assert result == 2


def test_allocate_central_error_response_surfaces_code(capsys):
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(
            success=False,
            data={"error": {"code": "ALLOCATION_UNAVAILABLE", "message": "no ports free", "details": []}},
        )
        mock_cls.return_value = client
        result = main(
            [
                "allocate",
                "--host",
                "NTMKEYA",
                "--project",
                "jarvis",
                "--request",
                "api:api:tcp",
                "--url",
                "http://central.example",
                "--json",
            ]
        )

    assert result == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "ALLOCATION_UNAVAILABLE"


def test_allocation_get_json(capsys):
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=_SAMPLE_ALLOCATION)
        result = main(["allocation", "get", _SAMPLE_ALLOCATION["allocation_id"], "--url", "http://central.example", "--json"])

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "active"


def test_allocation_release_json(capsys):
    released = dict(_SAMPLE_ALLOCATION, status="released", allocations=[])
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(success=True, data=released)
        result = main(
            ["allocation", "release", _SAMPLE_ALLOCATION["allocation_id"], "--url", "http://central.example", "--json"]
        )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "released"


def test_invalid_request_flag_syntax_is_operational_error(capsys):
    result = main(
        [
            "allocate",
            "--host",
            "NTMKEYA",
            "--project",
            "jarvis",
            "--request",
            "not-enough-parts",
            "--url",
            "http://central.example",
            "--json",
        ]
    )
    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "INVALID_REQUEST"
