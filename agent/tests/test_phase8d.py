from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main
from portforge_agent.manifest import load_and_validate_manifest

HOST = {"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "NTMKEYA"}]}
ALLOC = {"allocation_id": "alloc-1", "project": "demo", "host": {"id": HOST["items"][0]["id"], "hostname": "NTMKEYA"}, "status": "active", "allocations": [{"name": "api", "purpose": "api", "protocol": "tcp", "port": 8000, "reservation_id": "r1"}]}
MANIFEST = """version: 1
project: demo
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
    protocol: tcp
"""


def client():
    value = MagicMock()
    value.list_hosts.return_value = MagicMock(success=True, data=HOST)
    value.get_recommendation.return_value = MagicMock(success=True, data={"recommended_port": 8000})
    value.create_allocation.return_value = MagicMock(success=True, data=ALLOC)
    return value


def test_agent_contract_version(capsys):
    assert main(["agent-contract", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract_version"] == 1
    assert payload["capabilities"]["workflow_apply"] is True


def test_project_init_stdout_is_valid_and_non_destructive(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["project", "init", "--project", "demo", "--host", "NTMKEYA", "--port", "api:api:tcp", "--stdout"]) == 0
    assert not (tmp_path / "portforge.yml").exists()
    proposed = tmp_path / "proposed.yml"; proposed.write_text(capsys.readouterr().out)
    assert load_and_validate_manifest(proposed).project == "demo"


def test_project_init_refuses_overwrite(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path); (tmp_path / "portforge.yml").write_text("keep")
    assert main(["project", "init", "--project", "demo", "--host", "NTMKEYA", "--port", "api:api", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "MANIFEST_ALREADY_EXISTS"
    assert (tmp_path / "portforge.yml").read_text() == "keep"


def test_workflow_prepare_and_apply_are_idempotent(tmp_path, capsys):
    manifest = tmp_path / "portforge.yml"; manifest.write_text(MANIFEST)
    with patch("portforge_agent.central_client.CentralClient") as cls:
        instance = client(); cls.return_value = instance
        base=[str(manifest), "--url", "http://central", "--json"]
        assert main(["workflow", "prepare", *base]) == 0
        assert json.loads(capsys.readouterr().out)["ready"] is True
        command=["workflow", "apply", *base, "--request-id", "stable"]
        assert main(command) == 0; first=json.loads(capsys.readouterr().out)
        assert main(command) == 0; second=json.loads(capsys.readouterr().out)
    assert first == second
    assert instance.create_allocation.call_count == 1


def test_workflow_request_id_cannot_escape_project(tmp_path):
    manifest = tmp_path / "portforge.yml"; manifest.write_text(MANIFEST)
    with patch("portforge_agent.central_client.CentralClient") as cls:
        cls.return_value=client()
        assert main(["workflow", "apply", str(manifest), "--url", "http://central", "--request-id", "../../escape", "--json"]) == 0
    assert not (tmp_path.parent / "escape").exists()



def test_workflow_preserves_explicit_idempotent_replay_on_config_failure(tmp_path):
    from portforge_agent.manifest import load_and_validate_manifest
    from portforge_agent.project_adapter import NormalizedHostRef
    from portforge_agent.workflow import WorkflowError, apply_workflow

    manifest_path = tmp_path / "portforge.yml"
    manifest_path.write_text(MANIFEST + "config:\n  dotenv:\n    - file: ../escape.env\n      values:\n        API_PORT: api\n")
    instance = client()
    instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data={**ALLOC, "idempotent_replay": True})
    try:
        apply_workflow(
            instance,
            load_and_validate_manifest(manifest_path),
            NormalizedHostRef(id=HOST["items"][0]["id"], hostname="NTMKEYA"),
            tmp_path,
            "pre-existing",
        )
    except WorkflowError as exc:
        assert exc.code == "CONFIG_PATH_OUTSIDE_PROJECT"
    else:
        raise AssertionError("expected config failure")
    instance.release_allocation.assert_not_called()



def test_workflow_persists_allocated_state_before_config_planning(tmp_path, monkeypatch):
    from portforge_agent.manifest import load_and_validate_manifest
    from portforge_agent.project_adapter import NormalizedHostRef
    from portforge_agent.workflow import apply_workflow, get_workflow_status
    manifest_path = tmp_path / "portforge.yml"
    manifest_path.write_text(MANIFEST + "config:\n  dotenv:\n    - file: .env\n      values:\n        API_PORT: api\n")
    (tmp_path / ".env").write_text("KEEP=1\n")
    instance = client()
    instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data=ALLOC)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr("portforge_agent.workflow.cm.build_plan", interrupt)
    try:
        apply_workflow(instance, load_and_validate_manifest(manifest_path), NormalizedHostRef(id=HOST["items"][0]["id"], hostname="NTMKEYA"), tmp_path, "interrupted")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("expected simulated interruption")
    status = get_workflow_status(tmp_path, "interrupted")
    assert status["status"] == "ALLOCATED"
    assert status["allocation"]["id"] == ALLOC["allocation_id"]
    assert status["config"]["applied"] is False
