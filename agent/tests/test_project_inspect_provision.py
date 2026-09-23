"""Phase 11+13: project inspect and provision (dry-run vs apply)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main

_HOSTS = {"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"}]}

_MANIFEST = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
    protocol: tcp
  api:
    purpose: api
    protocol: tcp
config:
  dotenv:
    - file: .env
      values:
        FRONTEND_PORT: frontend
"""

_ALLOC = {
    "allocation_id": "11111111-1111-1111-1111-111111111111",
    "project": "jarvis",
    "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"},
    "status": "active",
    "allocations": [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3000},
        {"name": "api", "purpose": "api", "protocol": "tcp", "port": 8000},
    ],
}


def _write_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "portforge.yml"
    path.write_text(_MANIFEST, encoding="utf-8")
    (tmp_path / ".env").write_text("FRONTEND_PORT=1\n", encoding="utf-8")
    return path


def _client(create_data=None, list_items=None):
    instance = MagicMock()
    instance.list_hosts.return_value = MagicMock(success=True, data=_HOSTS)
    instance.get_recommendation.return_value = MagicMock(
        success=True, data={"recommended_port": 3000, "candidates_considered": 1}
    )
    if create_data is not None:
        result = MagicMock(success=True, status_code=201, data=create_data)
        instance.create_allocation.return_value = result
    if list_items is not None:
        instance.list_allocations.return_value = MagicMock(success=True, data={"items": list_items})
    else:
        instance.list_allocations.return_value = MagicMock(success=True, data={"items": []})
    return instance


def test_project_inspect_json_includes_config_targets(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _client(list_items=[_ALLOC])
        assert main(["project", "inspect", str(manifest), "--url", "http://central", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["project"] == "jarvis"
    assert len(payload["services"]) == 2
    assert payload["services"][0]["allocation_status"] == "active"
    assert payload["config_targets"] == [
        {"file": ".env", "type": "dotenv", "mappings": [["FRONTEND_PORT", "frontend"]]}
    ]


def test_project_inspect_does_not_mutate(tmp_path):
    manifest = _write_manifest(tmp_path)
    env_before = (tmp_path / ".env").read_text()
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _client()
        mock_cls.return_value = client
        main(["project", "inspect", str(manifest), "--url", "http://central", "--json"])
        client.create_allocation.assert_not_called()
    assert (tmp_path / ".env").read_text() == env_before
    assert not (tmp_path / ".portforge").exists()


def test_project_provision_dry_run_no_allocation_or_files(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    env_before = (tmp_path / ".env").read_text()
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _client()
        mock_cls.return_value = client
        result = main(
            [
                "project",
                "provision",
                str(manifest),
                "--request-id",
                "dry-run-key",
                "--dry-run",
                "--url",
                "http://central",
                "--json",
            ]
        )

    assert result == 0
    client.create_allocation.assert_not_called()
    assert (tmp_path / ".env").read_text() == env_before
    assert not (tmp_path / ".portforge").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["committed"] is False
    assert payload["ready"] is True


def test_project_provision_apply_calls_workflow(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _client(create_data=_ALLOC)
        mock_cls.return_value = client
        result = main(
            [
                "project",
                "provision",
                str(manifest),
                "--request-id",
                "apply-key",
                "--url",
                "http://central",
                "--json",
            ]
        )

    assert result == 0
    assert client.create_allocation.call_count == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "APPLIED"
    assert payload["config"]["applied"] is True
    assert (tmp_path / ".env").read_text() == "FRONTEND_PORT=3000\n"
