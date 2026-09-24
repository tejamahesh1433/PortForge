"""E2E target-aware planning with mocked Central (Phase 17)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from portforge_agent.mcp.errors import McpToolError
from portforge_agent.mcp.plans import load_plan, verify_plan_fresh
from portforge_agent.targets.plan import plan_for_target
from portforge_agent.targets.request_id import namespace_request_id

from .targets_helpers import (
    HP_RECOMMENDATIONS,
    INTERNAL_PORTS,
    LENOVO_RECOMMENDATIONS,
    PLAN_CENTRAL_PATCH_TARGET,
    combined_conflict_allocations,
    copy_targets_fixture,
    hp_conflict_allocations,
    lenovo_conflict_allocations,
    service_map,
    targets_central_mock,
)


def _plan(root, *, environment, target, allocations=None, recommendation_map=None):
    client = targets_central_mock(
        allocation_items=allocations or [],
        recommendation_map=recommendation_map or {},
    )
    with patch(PLAN_CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        return plan_for_target(
            project_root=root,
            environment=environment,
            target=target,
            central_url="http://central.example",
            logical_request_id="deploy-1",
            include_local_runtime=False,
        )


def test_plan_development_windows_preserves_internal_host_ports(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(root, environment="development", target="windows")

    ports = service_map(payload)
    for service, internal in INTERNAL_PORTS.items():
        assert ports[service]["internal_port"] == internal
        assert ports[service]["host_port"] == internal
        assert ports[service]["reason_code"] == "PRESERVE"


def test_plan_production_lenovo_alternate_host_ports_internal_unchanged(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(
        root,
        environment="production",
        target="lenovo-prod",
        allocations=lenovo_conflict_allocations(),
        recommendation_map=LENOVO_RECOMMENDATIONS,
    )

    ports = service_map(payload)
    for service, internal in INTERNAL_PORTS.items():
        assert ports[service]["internal_port"] == internal
        assert ports[service]["host_port"] == LENOVO_RECOMMENDATIONS[service]
    assert ports["api"]["internal_port"] == 8000


def test_plan_production_hp_different_conflicts_different_host_ports(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(
        root,
        environment="production",
        target="hp-prod",
        allocations=hp_conflict_allocations(),
        recommendation_map=HP_RECOMMENDATIONS,
    )

    ports = service_map(payload)
    assert ports["frontend"]["host_port"] == HP_RECOMMENDATIONS["frontend"]
    assert ports["api"]["host_port"] == HP_RECOMMENDATIONS["api"]
    assert ports["postgres"]["host_port"] == INTERNAL_PORTS["postgres"]
    assert ports["redis"]["host_port"] == INTERNAL_PORTS["redis"]
    for service, internal in INTERNAL_PORTS.items():
        assert ports[service]["internal_port"] == internal


def test_windows_plan_independent_of_lenovo_conflicts(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(
        root,
        environment="development",
        target="windows",
        allocations=combined_conflict_allocations(),
        recommendation_map=LENOVO_RECOMMENDATIONS,
    )

    ports = service_map(payload)
    for service, internal in INTERNAL_PORTS.items():
        assert ports[service]["host_port"] == internal
        assert ports[service]["reason_code"] == "PRESERVE"


def test_lenovo_ingress_planned_or_target_bound_maps_to_api(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(
        root,
        environment="production",
        target="lenovo-prod",
        allocations=lenovo_conflict_allocations(),
        recommendation_map=LENOVO_RECOMMENDATIONS,
    )

    assert payload["plan_id"]
    ingress_rows = payload["ingress"]
    assert len(ingress_rows) == 1
    row = ingress_rows[0]
    assert row["service"] == "api"
    assert row["status"] in {"INGRESS_PLANNED", "TARGET_BOUND"}
    assert row["status"] != "PUBLIC_READY"
    assert row["bound_host_port"] == LENOVO_RECOMMENDATIONS["api"]


def test_plan_id_persisted_and_plan_target_mismatch(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(root, environment="production", target="lenovo-prod")
    plan_id = payload["plan_id"]
    assert plan_id

    record = load_plan(root, plan_id)
    assert record["mode"] == "target"
    assert record["environment"] == "production"
    assert record["host_id"] == payload["host_id"]

    manifest = __import__("portforge_agent.manifest", fromlist=["load_and_validate_manifest"]).load_and_validate_manifest(
        root / "portforge.yml"
    )

    with pytest.raises(McpToolError) as exc_info:
        verify_plan_fresh(
            root,
            plan_id,
            manifest,
            environment="production",
            host_id="44444444-4444-4444-4444-444444444444",
        )
    assert exc_info.value.code == "PLAN_TARGET_MISMATCH"

    with pytest.raises(McpToolError) as exc_info_env:
        verify_plan_fresh(
            root,
            plan_id,
            manifest,
            environment="development",
            host_id=payload["host_id"],
        )
    assert exc_info_env.value.code == "PLAN_TARGET_MISMATCH"


def test_config_changed_since_plan_when_fingerprint_input_changes(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = _plan(root, environment="development", target="windows")
    plan_id = payload["plan_id"]

    env_path = root / ".env"
    env_path.write_text(env_path.read_text(encoding="utf-8") + "EXTRA=1\n", encoding="utf-8")

    manifest = __import__("portforge_agent.manifest", fromlist=["load_and_validate_manifest"]).load_and_validate_manifest(
        root / "portforge.yml"
    )
    with pytest.raises(McpToolError) as exc_info:
        verify_plan_fresh(
            root,
            plan_id,
            manifest,
            environment="development",
            host_id=payload["host_id"],
        )
    assert exc_info.value.code == "CONFIG_CHANGED_SINCE_PLAN"
    assert ".env" in exc_info.value.details[0]["changed_paths"]


def test_same_logical_request_id_namespaces_per_target(tmp_path):
    root = copy_targets_fixture(tmp_path)
    windows = _plan(root, environment="development", target="windows")
    lenovo = _plan(
        root,
        environment="production",
        target="lenovo-prod",
        allocations=lenovo_conflict_allocations(),
        recommendation_map=LENOVO_RECOMMENDATIONS,
    )

    assert windows["namespaced_request_id_hint"] == namespace_request_id("development", "windows", "deploy-1")
    assert lenovo["namespaced_request_id_hint"] == namespace_request_id("production", "lenovo-prod", "deploy-1")
    assert windows["namespaced_request_id_hint"] != lenovo["namespaced_request_id_hint"]
