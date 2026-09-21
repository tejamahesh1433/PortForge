"""Phase 8C: CLI tests for `config plan/apply/status/rollback`.

Follows the same mocking pattern as test_cli_allocation.py/test_cli_project.py:
patch CentralClient at its definition module.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main

_HOSTS_RESPONSE = {"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"}]}

_ALLOCATION = {
    "allocation_id": "alloc-1",
    "project": "jarvis",
    "status": "active",
    "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"},
    "allocations": [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3001, "reservation_id": "r1"},
        {"name": "api", "purpose": "api", "protocol": "tcp", "port": 8001, "reservation_id": "r2"},
    ],
}

MANIFEST_TEXT = """
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
        API_PORT: api
"""


def _mock_client(allocation=None, allocation_success=True):
    instance = MagicMock()
    instance.list_hosts.return_value = MagicMock(success=True, data=_HOSTS_RESPONSE)
    result = MagicMock()
    result.success = allocation_success
    result.data = allocation
    result.error = None if allocation_success else "not found"
    instance.get_allocation.return_value = result
    return instance


def _setup_project(tmp_path):
    (tmp_path / "portforge.yml").write_text(MANIFEST_TEXT, encoding="utf-8")
    (tmp_path / ".env").write_text("EXISTING=1\n", encoding="utf-8")
    return tmp_path / "portforge.yml"


def test_config_plan_json(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=_ALLOCATION)
        result = main(
            ["config", "plan", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"]
        )

    assert result == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["committed"] is False
    assert parsed["allocation_id"] == "alloc-1"
    env_change = next(f for f in parsed["changes"] if f["file"] == ".env")
    assert {"key": "FRONTEND_PORT", "before": None, "after": "3001", "action": "append"} in env_change["changes"]


def test_config_plan_does_not_touch_real_files(tmp_path):
    manifest_path = _setup_project(tmp_path)
    before = (tmp_path / ".env").read_bytes()
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=_ALLOCATION)
        main(["config", "plan", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"])
    assert (tmp_path / ".env").read_bytes() == before


def test_config_plan_project_mismatch(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)
    bad_alloc = dict(_ALLOCATION, project="someone-else")
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=bad_alloc)
        result = main(
            ["config", "plan", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"]
        )
    assert result == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "ALLOCATION_PROJECT_MISMATCH"


def test_config_plan_allocation_not_found(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation_success=False)
        result = main(
            ["config", "plan", str(manifest_path), "--allocation", "bogus", "--url", "http://central.example", "--json"]
        )
    assert result == 2
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "CONFIG_ALLOCATION_NOT_FOUND"


def test_config_apply_without_prior_plan_fails(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=_ALLOCATION)
        result = main(
            ["config", "apply", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"]
        )
    assert result == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "CONFIG_MUTATION_NOT_FOUND"


def test_config_plan_then_apply_then_status_then_rollback(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)

    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=_ALLOCATION)

        plan_result = main(
            ["config", "plan", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"]
        )
        assert plan_result == 0
        plan_payload = json.loads(capsys.readouterr().out)
        mutation_id = plan_payload["mutation_id"]

        apply_result = main(
            ["config", "apply", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"]
        )
        assert apply_result == 0
        apply_payload = json.loads(capsys.readouterr().out)
        assert apply_payload["status"] == "APPLIED"

    assert (tmp_path / ".env").read_text() == "EXISTING=1\nFRONTEND_PORT=3001\nAPI_PORT=8001\n"

    status_result = main(["config", "status", mutation_id, "--project-root", str(tmp_path), "--json"])
    assert status_result == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["status"] == "APPLIED"

    rollback_result = main(["config", "rollback", mutation_id, "--project-root", str(tmp_path), "--json"])
    assert rollback_result == 0
    rollback_payload = json.loads(capsys.readouterr().out)
    assert rollback_payload["status"] == "ROLLED_BACK"
    assert (tmp_path / ".env").read_text() == "EXISTING=1\n"


def test_config_apply_json_output_is_pure_json(tmp_path, capsys):
    manifest_path = _setup_project(tmp_path)
    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        mock_cls.return_value = _mock_client(allocation=_ALLOCATION)
        main(["config", "plan", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"])
        capsys.readouterr()
        main(["config", "apply", str(manifest_path), "--allocation", "alloc-1", "--url", "http://central.example", "--json"])
        captured = capsys.readouterr()
    assert captured.err == ""
    json.loads(captured.out)  # must not raise


def test_config_status_unknown_mutation(tmp_path, capsys):
    result = main(["config", "status", "00000000-0000-0000-0000-000000000000", "--project-root", str(tmp_path), "--json"])
    assert result == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["code"] == "CONFIG_MUTATION_NOT_FOUND"
