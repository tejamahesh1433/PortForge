"""Phase 8B: CLI tests for `project validate/plan/allocate`.

Follows the exact mocking pattern established in test_cli_allocation.py:
patch CentralClient at its definition module (`portforge_agent.central_client`)
since `_allocation_client()` does a local `from .central_client import
CentralClient` inside the function body.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main

_HOSTS_RESPONSE = {
    "items": [
        {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"},
        {"id": "33333333-3333-3333-3333-333333333333", "hostname": "lenovoserver"},
    ]
}

_ALLOCATION_RESPONSE = {
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

_VALID_MANIFEST = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
    protocol: tcp
    preferred: 3000
  api:
    purpose: api
    protocol: tcp
"""


def _write_manifest(tmp_path, text=_VALID_MANIFEST, name="portforge.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _mock_client(create_allocation_data=None, create_allocation_success=True, recommendation_data=None):
    instance = MagicMock()
    instance.list_hosts.return_value = MagicMock(success=True, data=_HOSTS_RESPONSE)
    result = MagicMock()
    result.success = create_allocation_success
    result.data = create_allocation_data
    instance.create_allocation.return_value = result

    rec_result = MagicMock()
    rec_result.success = True
    rec_result.data = recommendation_data or {
        "recommended_port": 3000,
        "candidates_considered": 1,
        "basis": "next free port in range",
    }
    instance.get_recommendation.return_value = rec_result
    return instance


def test_project_validate_json(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client()
        result = main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == {
        "valid": True,
        "version": 1,
        "project": "jarvis",
        "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"},
        "requests": 2,
    }


def test_project_validate_does_not_allocate(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client
        main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])
        client.create_allocation.assert_not_called()


def test_project_validate_unknown_host(tmp_path, capsys):
    manifest_path = _write_manifest(
        tmp_path,
        text=_VALID_MANIFEST.replace("host: NTMKEYA", "host: does-not-exist"),
    )
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client()
        result = main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "HOST_NOT_FOUND"


def test_project_validate_ambiguous_host(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, text=_VALID_MANIFEST.replace("host: NTMKEYA", "host: dup"))
    hosts = {"items": [
        {"id": "aaaa", "hostname": "dup"},
        {"id": "bbbb", "hostname": "dup"},
    ]}
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client()
        client.list_hosts.return_value = MagicMock(success=True, data=hosts)
        mock_cls.return_value = client
        result = main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "HOST_AMBIGUOUS"


def test_project_validate_malformed_manifest(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, text="version: 1\nproject: [unterminated")
    result = main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])
    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "MANIFEST_PARSE_ERROR"


def test_project_validate_unsupported_version(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path, text=_VALID_MANIFEST.replace("version: 1", "version: 999"))
    result = main(["project", "validate", str(manifest_path), "--url", "http://central.example", "--json"])
    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "UNSUPPORTED_MANIFEST_VERSION"


def test_project_plan_is_non_mutating(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client
        result = main(["project", "plan", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 0
    client.create_allocation.assert_not_called()
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["committed"] is False
    assert len(parsed["requests"]) == 2
    assert parsed["requests"][0]["candidate_port"] == 3000


def test_project_plan_uses_recommendation_per_item(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client
        main(["project", "plan", str(manifest_path), "--url", "http://central.example", "--json"])
        assert client.get_recommendation.call_count == 2


def test_project_allocate_creates_one_allocation_not_one_per_port(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(create_allocation_data=_ALLOCATION_RESPONSE)
        mock_cls.return_value = client
        result = main(["project", "allocate", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 0
    assert client.create_allocation.call_count == 1
    _, kwargs = client.create_allocation.call_args
    assert len(kwargs["requests"]) == 2

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["committed"] is True
    assert parsed["allocation_id"] == _ALLOCATION_RESPONSE["allocation_id"]
    assert parsed["ports"] == {"frontend": 3000, "api": 8000}
    assert parsed["allocations"] == _ALLOCATION_RESPONSE["allocations"]


def test_project_allocate_format_env(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(create_allocation_data=_ALLOCATION_RESPONSE)
        result = main(["project", "allocate", str(manifest_path), "--url", "http://central.example", "--format", "env"])

    assert result == 0
    out = capsys.readouterr().out
    assert "FRONTEND_PORT=3000" in out
    assert "API_PORT=8000" in out


def test_project_allocate_cli_request_id_overrides_manifest(tmp_path):
    manifest_with_id = _VALID_MANIFEST + "request_id: manifest-key\n"
    manifest_path = _write_manifest(tmp_path, text=manifest_with_id)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(create_allocation_data=_ALLOCATION_RESPONSE)
        mock_cls.return_value = client
        main(
            [
                "project",
                "allocate",
                str(manifest_path),
                "--request-id",
                "cli-key",
                "--url",
                "http://central.example",
                "--json",
            ]
        )
        _, kwargs = client.create_allocation.call_args
        assert kwargs["request_id"] == "cli-key"


def test_project_allocate_manifest_request_id_used_when_no_cli_flag(tmp_path):
    manifest_with_id = _VALID_MANIFEST + "request_id: manifest-key\n"
    manifest_path = _write_manifest(tmp_path, text=manifest_with_id)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(create_allocation_data=_ALLOCATION_RESPONSE)
        mock_cls.return_value = client
        main(["project", "allocate", str(manifest_path), "--url", "http://central.example", "--json"])
        _, kwargs = client.create_allocation.call_args
        assert kwargs["request_id"] == "manifest-key"


def test_project_allocate_failure_surfaces_phase8a_error_code(tmp_path, capsys):
    manifest_path = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _mock_client(
            create_allocation_success=False,
            create_allocation_data={"error": {"code": "ALLOCATION_UNAVAILABLE", "message": "no ports free", "details": []}},
        )
        mock_cls.return_value = client
        result = main(["project", "allocate", str(manifest_path), "--url", "http://central.example", "--json"])

    assert result == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "ALLOCATION_UNAVAILABLE"


def test_project_validate_by_uuid_matches_hostname_resolution(tmp_path, capsys):
    manifest_uuid = _write_manifest(
        tmp_path,
        text=_VALID_MANIFEST.replace("host: NTMKEYA", "host: 22222222-2222-2222-2222-222222222222"),
        name="by-uuid.yml",
    )
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client()
        result = main(["project", "validate", str(manifest_uuid), "--url", "http://central.example", "--json"])

    assert result == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["host"] == {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"}


def test_project_validate_discovers_manifest_in_cwd(tmp_path, capsys, monkeypatch):
    _write_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client()
        result = main(["project", "validate", "--url", "http://central.example", "--json"])

    assert result == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["valid"] is True


def test_project_validate_no_manifest_found(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = main(["project", "validate", "--url", "http://central.example", "--json"])
    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "MANIFEST_NOT_FOUND"
