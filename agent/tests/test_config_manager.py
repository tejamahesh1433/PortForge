"""Phase 8C: config_manager orchestration tests (plan/apply/status/rollback).

Operates directly on config_manager.py with real temp directories and
real files -- no CentralClient mocking needed here since config_manager
takes already-fetched allocation data as a plain dict (CLI-level wiring
tests, including the allocation-fetch step, live in test_cli_config.py).
"""
from __future__ import annotations

import hashlib
import os
import platform

import pytest

from portforge_agent import config_manager as cm
from portforge_agent.config_files import ConfigPathError, resolve_within_root
from portforge_agent.manifest import parse_manifest_yaml, validate_manifest

MANIFEST_YAML = """
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
  compose:
    - file: compose.yaml
      services:
        api:
          ports:
            - allocation: api
              container: 8000
"""


def _manifest():
    return validate_manifest(parse_manifest_yaml(MANIFEST_YAML))


def _allocation(frontend_port=3001, api_port=8001, status="active", project="jarvis", host_id="host-uuid"):
    return {
        "allocation_id": "alloc-1",
        "project": project,
        "status": status,
        "host": {"id": host_id, "hostname": "NTMKEYA"},
        "allocations": [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": frontend_port, "reservation_id": "r1"},
            {"name": "api", "purpose": "api", "protocol": "tcp", "port": api_port, "reservation_id": "r2"},
        ],
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- ownership verification -------------------------------------------------


def test_ownership_ok():
    cm.verify_allocation_ownership(_allocation(), _manifest(), "host-uuid")  # must not raise


def test_ownership_project_mismatch():
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(_allocation(project="other"), _manifest(), "host-uuid")
    assert exc_info.value.code == "ALLOCATION_PROJECT_MISMATCH"


def test_ownership_host_mismatch():
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(_allocation(host_id="different"), _manifest(), "host-uuid")
    assert exc_info.value.code == "ALLOCATION_HOST_MISMATCH"


def test_ownership_inactive():
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(_allocation(status="released"), _manifest(), "host-uuid")
    assert exc_info.value.code == "ALLOCATION_INACTIVE"


def test_ownership_missing_referenced_name():
    allocation = _allocation()
    allocation["allocations"] = [allocation["allocations"][0]]  # drop "api"
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(allocation, _manifest(), "host-uuid")
    assert exc_info.value.code == "CONFIG_MAPPING_INVALID"


# --- plan is non-mutating ---------------------------------------------------


def test_plan_is_completely_non_mutating(tmp_path):
    (tmp_path / ".env").write_text("EXISTING=1\n")
    (tmp_path / "compose.yaml").write_text('services:\n  api:\n    ports:\n      - "9000:8000"\n')
    before_env = (tmp_path / ".env").read_bytes()
    before_compose = (tmp_path / "compose.yaml").read_bytes()
    before_env_mtime = (tmp_path / ".env").stat().st_mtime

    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    assert (tmp_path / ".env").read_bytes() == before_env
    assert (tmp_path / "compose.yaml").read_bytes() == before_compose
    assert (tmp_path / ".env").stat().st_mtime == before_env_mtime


def test_plan_no_config_declared_fails(tmp_path):
    manifest_yaml = MANIFEST_YAML.split("config:")[0]
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(manifest, _allocation(), tmp_path)
    assert exc_info.value.code == "CONFIG_NOT_DECLARED"


def test_plan_shows_before_after_correctly(tmp_path):
    (tmp_path / ".env").write_text("FRONTEND_PORT=9999\n")
    (tmp_path / "compose.yaml").write_text('services:\n  api:\n    ports:\n      - "7777:8000"\n')
    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    payload = cm.plan_to_json(plan)
    dotenv_changes = next(f for f in payload["changes"] if f["file"] == ".env")["changes"]
    assert {"key": "FRONTEND_PORT", "before": "9999", "after": "3001", "action": "update"} in dotenv_changes
    compose_changes = next(f for f in payload["changes"] if f["file"] == "compose.yaml")["changes"]
    assert compose_changes[0]["before"] == "7777"
    assert compose_changes[0]["after"] == "8001"


# --- path safety -------------------------------------------------------------


def test_config_path_outside_project_rejected(tmp_path):
    manifest_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
config:
  dotenv:
    - file: ../outside.env
      values:
        API_PORT: api
"""
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("SECRET=1\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(manifest, _allocation(), project_dir)
    assert exc_info.value.code == "CONFIG_PATH_OUTSIDE_PROJECT"
    assert outside.read_text() == "SECRET=1\n"  # outside file untouched


@pytest.mark.skipif(
    platform.system() == "Windows", reason="creating symlinks requires elevated privilege on this Windows machine"
)
def test_symlink_escape_rejected(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.env").write_text("SECRET=1\n")
    os.symlink(str(outside_dir / "secret.env"), str(project_dir / ".env"))

    with pytest.raises(ConfigPathError):
        resolve_within_root(project_dir, ".env")
    assert (outside_dir / "secret.env").read_text() == "SECRET=1\n"


# --- dotenv duplicate key ----------------------------------------------------


def test_dotenv_duplicate_key_fails_plan_zero_mutation(tmp_path):
    (tmp_path / ".env").write_text("API_PORT=1\nAPI_PORT=2\n")
    before = (tmp_path / ".env").read_bytes()
    manifest_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
config:
  dotenv:
    - file: .env
      values:
        API_PORT: api
"""
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(manifest, _allocation(), tmp_path)
    assert exc_info.value.code == "DOTENV_DUPLICATE_KEY"
    assert (tmp_path / ".env").read_bytes() == before


# --- compose file must already exist ----------------------------------------


def test_compose_file_not_found(tmp_path):
    (tmp_path / ".env").write_text("FRONTEND_PORT=1\nAPI_PORT=1\n")
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(_manifest(), _allocation(), tmp_path)
    assert exc_info.value.code == "CONFIG_FILE_NOT_FOUND"


# --- apply / status / rollback lifecycle -------------------------------------


def _write_project(tmp_path):
    (tmp_path / ".env").write_text("EXISTING=1\nFRONTEND_PORT=9999\n")
    (tmp_path / "compose.yaml").write_text('services:\n  api:\n    ports:\n      - "9000:8000"\n')


def test_full_apply_rollback_cycle_exact_bytes(tmp_path):
    _write_project(tmp_path)
    before_env = (tmp_path / ".env").read_bytes()
    before_compose = (tmp_path / "compose.yaml").read_bytes()

    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    record = cm.apply_mutation(tmp_path, plan.mutation_id)
    assert record["status"] == "APPLIED"
    assert (tmp_path / ".env").read_text() == "EXISTING=1\nFRONTEND_PORT=3001\nAPI_PORT=8001\n"
    assert "8001:8000" in (tmp_path / "compose.yaml").read_text()

    status = cm.get_status(tmp_path, plan.mutation_id)
    assert status["status"] == "APPLIED"
    assert status["allocation_id"] == "alloc-1"
    assert status["project"] == "jarvis"

    rolled = cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert _sha256((tmp_path / ".env").read_bytes()) == _sha256(before_env)
    assert (tmp_path / ".env").read_bytes() == before_env
    assert (tmp_path / "compose.yaml").read_bytes() == before_compose


def test_apply_is_idempotent_no_duplicate_rewrite(tmp_path):
    _write_project(tmp_path)
    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    first = cm.apply_mutation(tmp_path, plan.mutation_id)
    mtime_after_first = (tmp_path / ".env").stat().st_mtime
    second = cm.apply_mutation(tmp_path, plan.mutation_id)

    assert first["status"] == second["status"] == "APPLIED"
    assert first["files"][0]["after_hash"] == second["files"][0]["after_hash"]
    assert (tmp_path / ".env").stat().st_mtime == mtime_after_first  # not rewritten a second time


def test_changed_since_plan_blocks_apply(tmp_path):
    _write_project(tmp_path)
    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    (tmp_path / ".env").write_text("EDITED=true\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.apply_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_PLAN"
    assert (tmp_path / ".env").read_text() == "EDITED=true\n"  # user edit preserved
    # compose.yaml (unaffected file) also untouched
    assert '"9000:8000"' in (tmp_path / "compose.yaml").read_text()


def test_changed_since_apply_blocks_rollback(tmp_path):
    _write_project(tmp_path)
    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    cm.apply_mutation(tmp_path, plan.mutation_id)

    (tmp_path / ".env").write_text("EDITED_AFTER_APPLY=true\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_APPLY"
    assert (tmp_path / ".env").read_text() == "EDITED_AFTER_APPLY=true\n"


def test_multi_file_apply_failure_restores_first_file(tmp_path, monkeypatch):
    _write_project(tmp_path)
    before_env = (tmp_path / ".env").read_bytes()

    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    real_atomic_write = cm.atomic_write
    call_count = {"n": 0}

    def _flaky_atomic_write(path, content):
        # Let backups and the .env real-target write succeed; fail only
        # the SECOND real target write (compose.yaml, directly under the
        # project root -- not its backup under .portforge/mutations/.../files/).
        call_count["n"] += 1
        if path.parent == tmp_path and path.name == "compose.yaml":
            raise OSError("simulated disk failure")
        return real_atomic_write(path, content)

    monkeypatch.setattr(cm, "atomic_write", _flaky_atomic_write)

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.apply_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_APPLY_FAILED"

    # .env must have been restored to its original bytes -- no partial
    # final state where .env is updated but compose.yaml is not.
    assert (tmp_path / ".env").read_bytes() == before_env


def test_status_never_returns_backup_contents(tmp_path):
    (tmp_path / ".env").write_text("SECRET_TOKEN=super-secret-value\nFRONTEND_PORT=1\n")
    manifest_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
config:
  dotenv:
    - file: .env
      values:
        FRONTEND_PORT: frontend
"""
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    plan = cm.build_plan(manifest, _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    cm.apply_mutation(tmp_path, plan.mutation_id)
    status = cm.get_status(tmp_path, plan.mutation_id)
    dumped = str(status)
    assert "super-secret-value" not in dumped


def test_sensitive_key_before_value_redacted_in_plan(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=super-secret-value\nFRONTEND_PORT=1\n")
    manifest_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
config:
  dotenv:
    - file: .env
      values:
        API_KEY: frontend
"""
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    plan = cm.build_plan(manifest, _allocation(), tmp_path)
    payload = cm.plan_to_json(plan)
    change = payload["changes"][0]["changes"][0]
    assert change["before"] == "<redacted>"
    assert change["after"] == "3001"  # AFTER is always a plain allocated port -- never redacted


def test_rollback_unknown_mutation_id(tmp_path):
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.rollback_mutation(tmp_path, "00000000-0000-0000-0000-000000000000")
    assert exc_info.value.code == "CONFIG_MUTATION_NOT_FOUND"


def test_find_latest_planned_mutation(tmp_path):
    _write_project(tmp_path)
    plan1 = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan1, tmp_path)
    found = cm.find_latest_planned_mutation(tmp_path, "alloc-1")
    assert found == plan1.mutation_id


def test_find_latest_planned_mutation_none_when_absent(tmp_path):
    assert cm.find_latest_planned_mutation(tmp_path, "alloc-1") is None


# ---------------------------------------------------------------------------
# v1.1-C: Kubernetes config plan/apply/rollback
# ---------------------------------------------------------------------------

K8S_MANIFEST_YAML = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
  api_nodeport:
    purpose: generic
    preferred: 30080
config:
  kubernetes:
    - file: k8s/app.yaml
      hostPorts:
        - kind: Deployment
          name: frontend
          container: web
          containerPort: 3000
          allocation: frontend
      nodePorts:
        - name: api-svc
          servicePort: 8080
          allocation: api_nodeport
"""

K8S_YAML_TEXT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  template:
    spec:
      containers:
        - name: web
          image: nginx
          ports:
            - containerPort: 3000
              protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: api-svc
spec:
  type: NodePort
  ports:
    - port: 8080
      targetPort: 8080
      protocol: TCP
"""


def _k8s_manifest():
    return validate_manifest(parse_manifest_yaml(K8S_MANIFEST_YAML))


def _k8s_allocation(frontend_port=31500, nodeport=30080, status="active", project="jarvis", host_id="host-uuid"):
    return {
        "allocation_id": "alloc-k8s-1",
        "project": project,
        "status": status,
        "host": {"id": host_id, "hostname": "NTMKEYA"},
        "allocations": [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": frontend_port, "reservation_id": "r1"},
            {"name": "api_nodeport", "purpose": "generic", "protocol": "tcp", "port": nodeport, "reservation_id": "r2"},
        ],
    }


def _write_k8s_project(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_YAML_TEXT)


def test_kubernetes_ownership_missing_referenced_name():
    allocation = _k8s_allocation()
    allocation["allocations"] = [allocation["allocations"][0]]  # drop api_nodeport
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(allocation, _k8s_manifest(), "host-uuid")
    assert exc_info.value.code == "CONFIG_MAPPING_INVALID"


def test_kubernetes_ownership_host_mismatch():
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(_k8s_allocation(host_id="different"), _k8s_manifest(), "host-uuid")
    assert exc_info.value.code == "ALLOCATION_HOST_MISMATCH"


def test_kubernetes_ownership_project_mismatch():
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.verify_allocation_ownership(_k8s_allocation(project="other"), _k8s_manifest(), "host-uuid")
    assert exc_info.value.code == "ALLOCATION_PROJECT_MISMATCH"


def test_kubernetes_plan_is_non_mutating(tmp_path):
    _write_k8s_project(tmp_path)
    before = (tmp_path / "k8s" / "app.yaml").read_bytes()
    before_mtime = (tmp_path / "k8s" / "app.yaml").stat().st_mtime

    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    assert (tmp_path / "k8s" / "app.yaml").read_bytes() == before
    assert (tmp_path / "k8s" / "app.yaml").stat().st_mtime == before_mtime


def test_kubernetes_plan_shows_correct_before_after(tmp_path):
    _write_k8s_project(tmp_path)
    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(frontend_port=31500, nodeport=30080), tmp_path)
    payload = cm.plan_to_json(plan)
    k8s_changes = next(f for f in payload["changes"] if f["file"] == "k8s/app.yaml")["changes"]
    host_change = next(c for c in k8s_changes if c["field"] == "hostPort")
    node_change = next(c for c in k8s_changes if c["field"] == "nodePort")
    assert host_change == {
        "kind": "Deployment", "name": "frontend", "field": "hostPort", "container": "web",
        "match_port": 3000, "allocation": "frontend", "before": None, "after": 31500, "action": "update",
    }
    assert node_change["after"] == 30080
    assert node_change["allocation"] == "api_nodeport"


def test_kubernetes_file_not_found(tmp_path):
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    assert exc_info.value.code == "CONFIG_FILE_NOT_FOUND"


def test_kubernetes_missing_target_container_fails_plan_zero_mutation(tmp_path):
    _write_k8s_project(tmp_path)
    bad_manifest_yaml = K8S_MANIFEST_YAML.replace("container: web", "container: ghost")
    manifest = validate_manifest(parse_manifest_yaml(bad_manifest_yaml))
    before = (tmp_path / "k8s" / "app.yaml").read_bytes()
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(manifest, _k8s_allocation(), tmp_path)
    assert exc_info.value.code == "KUBERNETES_CONTAINER_NOT_FOUND"
    assert (tmp_path / "k8s" / "app.yaml").read_bytes() == before


def test_kubernetes_ambiguous_resource_fails_plan(tmp_path):
    _write_k8s_project(tmp_path)
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_YAML_TEXT + "---\n" + K8S_YAML_TEXT.split("---")[0])
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    assert exc_info.value.code == "KUBERNETES_RESOURCE_AMBIGUOUS"


def test_kubernetes_nodeport_out_of_range_fails_plan_zero_mutation(tmp_path):
    _write_k8s_project(tmp_path)
    before = (tmp_path / "k8s" / "app.yaml").read_bytes()
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(_k8s_manifest(), _k8s_allocation(nodeport=8080), tmp_path)
    assert exc_info.value.code == "KUBERNETES_NODEPORT_OUT_OF_RANGE"
    assert (tmp_path / "k8s" / "app.yaml").read_bytes() == before


def test_kubernetes_service_wrong_type_fails_plan(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "app.yaml").write_text(K8S_YAML_TEXT.replace("type: NodePort", "type: ClusterIP"))
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    assert exc_info.value.code == "KUBERNETES_SERVICE_TYPE_UNSUPPORTED"


def test_kubernetes_path_outside_project_rejected(tmp_path):
    manifest_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
config:
  kubernetes:
    - file: ../outside.yaml
      hostPorts:
        - kind: Deployment
          name: frontend
          container: web
          containerPort: 3000
          allocation: frontend
"""
    manifest = validate_manifest(parse_manifest_yaml(manifest_yaml))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("kind: Secret\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(manifest, _k8s_allocation(), project_dir)
    assert exc_info.value.code == "CONFIG_PATH_OUTSIDE_PROJECT"
    assert outside.read_text() == "kind: Secret\n"


@pytest.mark.skipif(
    platform.system() == "Windows", reason="creating symlinks requires elevated privilege on this Windows machine"
)
def test_kubernetes_symlink_escape_rejected(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.yaml").write_text("kind: Secret\n")
    os.symlink(str(outside_dir / "secret.yaml"), str(project_dir / "app.yaml"))

    with pytest.raises(ConfigPathError):
        resolve_within_root(project_dir, "app.yaml")
    assert (outside_dir / "secret.yaml").read_text() == "kind: Secret\n"


def test_kubernetes_full_apply_rollback_cycle_exact_bytes(tmp_path):
    _write_k8s_project(tmp_path)
    before = (tmp_path / "k8s" / "app.yaml").read_bytes()

    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    record = cm.apply_mutation(tmp_path, plan.mutation_id)
    assert record["status"] == "APPLIED"

    applied_text = (tmp_path / "k8s" / "app.yaml").read_text()
    assert "hostPort: 31500" in applied_text
    assert "nodePort: 30080" in applied_text
    assert "containerPort: 3000" in applied_text  # unchanged match key
    assert "targetPort: 8080" in applied_text  # never owned by PortForge

    status = cm.get_status(tmp_path, plan.mutation_id)
    assert status["status"] == "APPLIED"
    assert status["files"][0]["type"] == "kubernetes"

    rolled = cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert _sha256((tmp_path / "k8s" / "app.yaml").read_bytes()) == _sha256(before)
    assert (tmp_path / "k8s" / "app.yaml").read_bytes() == before


def test_kubernetes_apply_is_idempotent_no_duplicate_rewrite(tmp_path):
    _write_k8s_project(tmp_path)
    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    first = cm.apply_mutation(tmp_path, plan.mutation_id)
    mtime_after_first = (tmp_path / "k8s" / "app.yaml").stat().st_mtime
    second = cm.apply_mutation(tmp_path, plan.mutation_id)

    assert first["status"] == second["status"] == "APPLIED"
    assert first["files"][0]["after_hash"] == second["files"][0]["after_hash"]
    assert (tmp_path / "k8s" / "app.yaml").stat().st_mtime == mtime_after_first


def test_kubernetes_external_edit_before_apply_blocks_apply(tmp_path):
    _write_k8s_project(tmp_path)
    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)

    (tmp_path / "k8s" / "app.yaml").write_text("kind: EditedExternally\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.apply_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_PLAN"
    assert (tmp_path / "k8s" / "app.yaml").read_text() == "kind: EditedExternally\n"


def test_kubernetes_external_edit_before_rollback_blocks_rollback(tmp_path):
    _write_k8s_project(tmp_path)
    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    cm.apply_mutation(tmp_path, plan.mutation_id)

    (tmp_path / "k8s" / "app.yaml").write_text("kind: EditedAfterApply\n")

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_APPLY"
    assert (tmp_path / "k8s" / "app.yaml").read_text() == "kind: EditedAfterApply\n"


def test_kubernetes_allocation_remains_active_after_config_rollback(tmp_path):
    """Phase 8C semantics preserved for the new file kind too: config
    rollback restores the file, it never releases/touches the allocation
    (this test only proves nothing in the config-mutation layer implies
    release -- release is a separate, allocation_service-owned action)."""
    _write_k8s_project(tmp_path)
    allocation = _k8s_allocation()
    plan = cm.build_plan(_k8s_manifest(), allocation, tmp_path)
    cm.persist_plan(plan, tmp_path)
    cm.apply_mutation(tmp_path, plan.mutation_id)
    rolled = cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["allocation_id"] == "alloc-k8s-1"  # unchanged -- config layer never mutates allocation state


def test_combined_dotenv_and_kubernetes_config_multi_file_apply(tmp_path):
    (tmp_path / ".env").write_text("EXISTING=1\n")
    _write_k8s_project(tmp_path)
    combined_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
  api_nodeport:
    purpose: generic
    preferred: 30080
config:
  dotenv:
    - file: .env
      values:
        FRONTEND_PORT: frontend
  kubernetes:
    - file: k8s/app.yaml
      hostPorts:
        - kind: Deployment
          name: frontend
          container: web
          containerPort: 3000
          allocation: frontend
      nodePorts:
        - name: api-svc
          servicePort: 8080
          allocation: api_nodeport
"""
    manifest = validate_manifest(parse_manifest_yaml(combined_yaml))
    plan = cm.build_plan(manifest, _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    record = cm.apply_mutation(tmp_path, plan.mutation_id)
    assert record["status"] == "APPLIED"
    assert (tmp_path / ".env").read_text() == "EXISTING=1\nFRONTEND_PORT=31500\n"
    assert "hostPort: 31500" in (tmp_path / "k8s" / "app.yaml").read_text()

    rolled = cm.rollback_mutation(tmp_path, plan.mutation_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert (tmp_path / ".env").read_text() == "EXISTING=1\n"
    assert "hostPort:" not in (tmp_path / "k8s" / "app.yaml").read_text()


def test_combined_config_failure_in_kubernetes_restores_earlier_dotenv_write(tmp_path, monkeypatch):
    """Task §29/§15: a controlled failure in a LATER file (kubernetes) must
    restore an EARLIER file (dotenv) already written this same apply call
    -- no partial config state remains."""
    (tmp_path / ".env").write_text("EXISTING=1\n")
    _write_k8s_project(tmp_path)
    combined_yaml = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
config:
  dotenv:
    - file: .env
      values:
        FRONTEND_PORT: frontend
  kubernetes:
    - file: k8s/app.yaml
      hostPorts:
        - kind: Deployment
          name: frontend
          container: web
          containerPort: 3000
          allocation: frontend
"""
    manifest = validate_manifest(parse_manifest_yaml(combined_yaml))
    allocation = {
        "allocation_id": "alloc-combined-1", "project": "jarvis", "status": "active",
        "host": {"id": "host-uuid", "hostname": "NTMKEYA"},
        "allocations": [{"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 31500, "reservation_id": "r1"}],
    }
    plan = cm.build_plan(manifest, allocation, tmp_path)
    cm.persist_plan(plan, tmp_path)
    before_env = (tmp_path / ".env").read_bytes()

    real_atomic_write = cm.atomic_write

    def _flaky_atomic_write(path, content):
        if path.parent == tmp_path / "k8s" and path.name == "app.yaml":
            raise OSError("simulated disk failure writing kubernetes file")
        return real_atomic_write(path, content)

    monkeypatch.setattr(cm, "atomic_write", _flaky_atomic_write)

    with pytest.raises(cm.ConfigError) as exc_info:
        cm.apply_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_APPLY_FAILED"
    assert (tmp_path / ".env").read_bytes() == before_env  # earlier write restored, no partial state


def test_kubernetes_status_never_leaks_secrets(tmp_path):
    (tmp_path / "k8s").mkdir()
    secret_bearing_yaml = K8S_YAML_TEXT.replace(
        "image: nginx", "image: nginx\n          env:\n            - name: DB_PASSWORD\n              value: super-secret-value"
    )
    (tmp_path / "k8s" / "app.yaml").write_text(secret_bearing_yaml)
    plan = cm.build_plan(_k8s_manifest(), _k8s_allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    cm.apply_mutation(tmp_path, plan.mutation_id)
    status = cm.get_status(tmp_path, plan.mutation_id)
    assert "super-secret-value" not in str(status)


@pytest.mark.skipif(platform.system() == "Windows", reason="Windows does not expose POSIX mode bits for this preservation assertion")
def test_atomic_write_preserves_existing_permissions(tmp_path):
    target = tmp_path / ".env"
    target.write_text("PORT=8000\n")
    target.chmod(0o640)
    from portforge_agent.config_files import atomic_write
    atomic_write(target, b"PORT=8127\n")
    assert target.read_text() == "PORT=8127\n"
    assert target.stat().st_mode & 0o777 == 0o640


def test_apply_validates_rendered_compose_before_commit(tmp_path, monkeypatch):
    _write_project(tmp_path)
    plan = cm.build_plan(_manifest(), _allocation(), tmp_path)
    cm.persist_plan(plan, tmp_path)
    def reject(text):
        raise cm.ComposeError("CONFIG_PARSE_ERROR", "simulated post-write parse failure")

    monkeypatch.setattr(cm, "load_compose", reject)
    before = (tmp_path / "compose.yaml").read_bytes()
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.apply_mutation(tmp_path, plan.mutation_id)
    assert exc_info.value.code == "CONFIG_VALIDATION_FAILED"
    assert (tmp_path / "compose.yaml").read_bytes() == before
    assert (tmp_path / ".env").read_text() == "EXISTING=1\nFRONTEND_PORT=9999\n"
