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


# ---------------------------------------------------------------------------
# v1.1-C: Kubernetes config integrated into the existing workflow commands
# (no new `portforge kubernetes ...` command -- see task §19).
# ---------------------------------------------------------------------------

K8S_MANIFEST = MANIFEST + (
    "config:\n"
    "  kubernetes:\n"
    "    - file: k8s/app.yaml\n"
    "      hostPorts:\n"
    "        - kind: Deployment\n"
    "          name: api\n"
    "          container: api\n"
    "          containerPort: 8000\n"
    "          allocation: api\n"
)

K8S_APP_YAML = (
    "apiVersion: apps/v1\n"
    "kind: Deployment\n"
    "metadata:\n"
    "  name: api\n"
    "spec:\n"
    "  template:\n"
    "    spec:\n"
    "      containers:\n"
    "        - name: api\n"
    "          image: myapi:latest\n"
    "          ports:\n"
    "            - containerPort: 8000\n"
    "              protocol: TCP\n"
)


def test_workflow_prepare_lists_kubernetes_config_file(tmp_path, capsys):
    manifest = tmp_path / "portforge.yml"
    manifest.write_text(K8S_MANIFEST)
    with patch("portforge_agent.central_client.CentralClient") as cls:
        cls.return_value = client()
        assert main(["workflow", "prepare", str(manifest), "--url", "http://central", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {"file": "k8s/app.yaml", "type": "kubernetes"} in payload["config_files"]


def test_workflow_apply_with_kubernetes_config_end_to_end(tmp_path):
    from portforge_agent.manifest import load_and_validate_manifest
    from portforge_agent.project_adapter import NormalizedHostRef
    from portforge_agent.workflow import apply_workflow

    manifest_path = tmp_path / "portforge.yml"
    manifest_path.write_text(K8S_MANIFEST)
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_APP_YAML)

    instance = client()
    instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data=ALLOC)

    result = apply_workflow(
        instance,
        load_and_validate_manifest(manifest_path),
        NormalizedHostRef(id=HOST["items"][0]["id"], hostname="NTMKEYA"),
        tmp_path,
        "k8s-e2e",
    )

    assert result["status"] == "APPLIED"
    assert result["config"]["applied"] is True
    applied_text = (tmp_path / "k8s" / "app.yaml").read_text()
    assert "hostPort: 8000" in applied_text
    assert "containerPort: 8000" in applied_text  # match key never touched


def test_workflow_json_output_pure_json_with_kubernetes_config(tmp_path, capsys):
    manifest = tmp_path / "portforge.yml"
    manifest.write_text(K8S_MANIFEST)
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_APP_YAML)
    with patch("portforge_agent.central_client.CentralClient") as cls:
        instance = client()
        instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data=ALLOC)
        cls.return_value = instance
        assert main(["workflow", "apply", str(manifest), "--url", "http://central", "--request-id", "pure-json", "--json"]) == 0
    out = capsys.readouterr().out
    # No progress banners around JSON -- the whole stdout must parse as one object.
    payload = json.loads(out)
    assert payload["config"]["applied"] is True


def test_workflow_kubernetes_missing_target_fails_and_compensates(tmp_path):
    from portforge_agent.manifest import load_and_validate_manifest
    from portforge_agent.project_adapter import NormalizedHostRef
    from portforge_agent.workflow import WorkflowError, apply_workflow

    manifest_path = tmp_path / "portforge.yml"
    manifest_path.write_text(K8S_MANIFEST.replace("container: api", "container: ghost"))
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_APP_YAML)

    instance = client()
    instance.create_allocation.return_value = MagicMock(success=True, status_code=201, data=ALLOC)
    instance.release_allocation.return_value = MagicMock(success=True)

    try:
        apply_workflow(
            instance,
            load_and_validate_manifest(manifest_path),
            NormalizedHostRef(id=HOST["items"][0]["id"], hostname="NTMKEYA"),
            tmp_path,
            "k8s-fail",
        )
    except WorkflowError as exc:
        assert exc.code == "KUBERNETES_CONTAINER_NOT_FOUND"
    else:
        raise AssertionError("expected config failure")
    # Newly-created allocation must be released since config planning failed.
    instance.release_allocation.assert_called_once_with(ALLOC["allocation_id"])
    # And the Kubernetes file itself must be untouched (plan never wrote it).
    assert "hostPort" not in (tmp_path / "k8s" / "app.yaml").read_text()
