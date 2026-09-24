"""Unit tests for Phase 17 target models, request_id, and resolve."""
from __future__ import annotations

import re
import uuid

import pytest

from portforge_agent.manifest import load_and_validate_manifest
from portforge_agent.targets.models import IngressStatus, PortScope, TargetsError
from portforge_agent.targets.request_id import namespace_request_id, parse_namespaced_request_id
from portforge_agent.targets.resolve import resolve_target

from .targets_helpers import FIXTURE_ROOT, HOST_HP, HOST_LENOVO, HOST_WINDOWS


def test_namespace_request_id_shape():
    value = namespace_request_id("development", "windows", "deploy-1")
    assert re.fullmatch(r"^[^:]+:[^:]+:.+$", value)
    assert value == "development:windows:deploy-1"
    assert parse_namespaced_request_id(value) == ("development", "windows", "deploy-1")


def test_namespace_request_id_rejects_empty_parts():
    with pytest.raises(TargetsError) as exc_info:
        namespace_request_id("", "windows", "deploy-1")
    assert exc_info.value.code == "INVALID_REQUEST_ID"


def test_resolve_windows_lenovo_hp_from_fixture():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")

    windows = resolve_target(manifest, "development", target="windows")
    assert windows.host_id == HOST_WINDOWS
    assert windows.alias == "windows"

    lenovo = resolve_target(manifest, "production", target="lenovo-prod")
    assert lenovo.host_id == HOST_LENOVO
    assert lenovo.alias == "lenovo-prod"

    hp = resolve_target(manifest, "production", target="hp-prod")
    assert hp.host_id == HOST_HP
    assert hp.alias == "hp-prod"


def test_invalid_host_id_on_target_host_id():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "production", target_host_id="not-a-uuid")
    assert exc_info.value.code == "INVALID_HOST_ID"


def test_unknown_target_alias():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "production", target="missing-host")
    assert exc_info.value.code == "UNKNOWN_TARGET"


def test_environment_not_found():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "qa", target="lenovo-prod")
    assert exc_info.value.code == "ENVIRONMENT_NOT_FOUND"


def test_target_required_without_environment():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, None, target="windows")
    assert exc_info.value.code == "TARGET_REQUIRED"


def test_target_required_without_alias():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    with pytest.raises(TargetsError) as exc_info:
        resolve_target(manifest, "development")
    assert exc_info.value.code == "TARGET_REQUIRED"


def test_resolve_target_by_uuid_under_environment():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    target = resolve_target(manifest, "production", target=HOST_HP)
    assert target.host_id == HOST_HP
    assert target.alias == HOST_HP
    uuid.UUID(target.host_id)


def test_port_scopes_internal_host_ingress_present():
    assert PortScope.INTERNAL.value == "INTERNAL"
    assert PortScope.HOST.value == "HOST"
    assert PortScope.INGRESS.value == "INGRESS"
    assert set(PortScope) == {PortScope.INTERNAL, PortScope.HOST, PortScope.INGRESS}


def test_ingress_status_never_includes_public_ready():
    statuses = {item.value for item in IngressStatus}
    assert "PUBLIC_READY" not in statuses
    assert IngressStatus.INGRESS_PLANNED.value in statuses
    assert IngressStatus.TARGET_BOUND.value in statuses
