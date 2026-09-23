"""Fixture-based config plan/apply/rollback on fixtures/sample-stack."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from portforge_agent import config_manager as cm
from portforge_agent.manifest import load_and_validate_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "fixtures" / "sample-stack"


def _allocation():
    return {
        "allocation_id": "sample-alloc-1",
        "project": "sample-stack",
        "status": "active",
        "host": {"id": "host-uuid", "hostname": "workstation"},
        "allocations": [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3100, "reservation_id": "r1"},
            {"name": "api", "purpose": "api", "protocol": "tcp", "port": 30080, "reservation_id": "r2"},
            {"name": "postgres", "purpose": "postgres", "protocol": "tcp", "port": 5433, "reservation_id": "r3"},
            {"name": "redis", "purpose": "redis", "protocol": "tcp", "port": 6380, "reservation_id": "r4"},
            {"name": "metrics", "purpose": "generic", "protocol": "tcp", "port": 9100, "reservation_id": "r5"},
        ],
    }


@pytest.fixture()
def stack_project(tmp_path):
    shutil.copytree(_FIXTURE, tmp_path / "stack")
    root = tmp_path / "stack"
    manifest = load_and_validate_manifest(root / "portforge.yml")
    return root, manifest


def test_sample_stack_plan_apply_rollback_cycle(stack_project):
    root, manifest = stack_project
    allocation = _allocation()

    plan = cm.build_plan(manifest, allocation, root)
    cm.persist_plan(plan, root)
    record = cm.apply_mutation(root, plan.mutation_id)

    assert record["status"] == "APPLIED"
    env_text = (root / ".env").read_text()
    assert "FRONTEND_PORT=3100" in env_text
    compose_text = (root / "docker-compose.yml").read_text()
    assert "3100:3000" in compose_text or '"3100:3000"' in compose_text
    k8s_text = (root / "k8s" / "stack.yaml").read_text()
    assert "hostPort: 9100" in k8s_text
    assert "nodePort: 30080" in k8s_text
    assert "containerPort: 9090" in k8s_text
    assert "targetPort: 8080" in k8s_text

    rolled = cm.rollback_mutation(root, plan.mutation_id)
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["allocation_id"] == allocation["allocation_id"]
    assert (root / ".env").read_bytes() == (_FIXTURE / ".env").read_bytes()


def test_sample_stack_path_traversal_rejected(stack_project, tmp_path):
    root, _manifest = stack_project
    bad_yaml = (root / "portforge.yml").read_text(encoding="utf-8").replace("file: .env", "file: ../escape.env")
    bad_path = tmp_path / "bad-portforge.yml"
    bad_path.write_text(bad_yaml, encoding="utf-8")
    bad = load_and_validate_manifest(bad_path)
    with pytest.raises(cm.ConfigError) as exc_info:
        cm.build_plan(bad, _allocation(), root)
    assert exc_info.value.code == "CONFIG_PATH_OUTSIDE_PROJECT"
