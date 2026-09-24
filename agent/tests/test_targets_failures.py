"""Target-aware planning failure modes and concurrency."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from portforge_agent.targets.models import TargetsError
from portforge_agent.targets.plan import plan_for_target
from portforge_agent.targets.ingress import parse_ingress_from_manifest, validate_ingress
from portforge_agent.targets.resolve import resolve_target
from portforge_agent.manifest import load_and_validate_manifest

from .targets_helpers import (
    HOST_LENOVO,
    PLAN_CENTRAL_PATCH_TARGET,
    copy_targets_fixture,
    targets_central_mock,
)


def test_decommissioned_host_raises_host_decommissioned(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock(decommissioned_host_id=HOST_LENOVO)
    with patch(PLAN_CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(TargetsError) as exc_info:
            plan_for_target(
                project_root=root,
                environment="production",
                target="lenovo-prod",
                central_url="http://central.example",
            )
    assert exc_info.value.code == "HOST_DECOMMISSIONED"


def test_unknown_target_raises():
    from .targets_helpers import FIXTURE_ROOT

    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "production", target="unknown-box")
    assert exc_info.value.code == "UNKNOWN_TARGET"


def test_invalid_uuid_raises_invalid_host_id():
    from .targets_helpers import FIXTURE_ROOT

    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "production", target_host_id="bad-id")
    assert exc_info.value.code == "INVALID_HOST_ID"


def test_ingress_to_missing_service_invalid_ingress():
    bindings = parse_ingress_from_manifest(
        {
            "ingress": [
                {
                    "name": "ghost",
                    "scheme": "https",
                    "hostname": "example.test",
                    "public_port": 443,
                    "service": "missing-service",
                    "environment": "production",
                    "target": "lenovo-prod",
                }
            ]
        }
    )
    errors = validate_ingress(bindings[0], ["api", "frontend"])
    assert errors
    assert errors[0]["code"] == "INVALID_INGRESS"


def test_central_unavailable_soft_without_central_url(tmp_path):
    root = copy_targets_fixture(tmp_path)
    payload = plan_for_target(
        project_root=root,
        environment="development",
        target="windows",
        central_url=None,
    )
    assert payload["central_available"] is False
    assert all(row["reason_code"] == "CENTRAL_UNAVAILABLE" for row in payload["services"])


def test_central_list_allocations_failure_preserves_ports(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock(list_allocations_success=False)
    with patch(PLAN_CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        payload = plan_for_target(
            project_root=root,
            environment="development",
            target="windows",
            central_url="http://central.example",
        )
    assert payload["central_available"] is False
    assert all(row["reason_code"] == "PRESERVE" for row in payload["services"])


def test_central_unavailable_hard_when_list_hosts_fails(tmp_path):
    root = copy_targets_fixture(tmp_path)
    client = targets_central_mock(list_hosts_success=False)
    with patch(PLAN_CENTRAL_PATCH_TARGET) as mock_cls:
        mock_cls.return_value = client
        with pytest.raises(TargetsError) as exc_info:
            plan_for_target(
                project_root=root,
                environment="development",
                target="windows",
                central_url="http://central.example",
            )
    assert exc_info.value.code == "HOST_NOT_FOUND"


def test_concurrent_plan_for_target_two_targets(tmp_path):
    root = copy_targets_fixture(tmp_path)
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def worker(key: str, environment: str, target: str) -> None:
        client = targets_central_mock()
        with patch(PLAN_CENTRAL_PATCH_TARGET) as mock_cls:
            mock_cls.return_value = client
            payload = plan_for_target(
                project_root=root,
                environment=environment,
                target=target,
                central_url="http://central.example",
                include_local_runtime=False,
            )
        with lock:
            results[key] = payload

    threads = [
        threading.Thread(target=worker, args=("windows", "development", "windows")),
        threading.Thread(target=worker, args=("lenovo", "production", "lenovo-prod")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert results["windows"]["host_id"] != results["lenovo"]["host_id"]
    assert results["windows"]["plan_id"] != results["lenovo"]["plan_id"]
