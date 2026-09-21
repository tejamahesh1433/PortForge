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
