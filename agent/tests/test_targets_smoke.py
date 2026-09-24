from pathlib import Path

import pytest

from portforge_agent.manifest import load_and_validate_manifest
from portforge_agent.targets.ingress import parse_ingress_from_manifest, validate_ingress
from portforge_agent.targets.plan import plan_for_target
from portforge_agent.targets.request_id import namespace_request_id, parse_namespaced_request_id
from portforge_agent.targets.resolve import resolve_target

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "targets" / "multi-host-app"


def test_fixture_env_present_for_fingerprint():
    """`.env` is gitignored globally but force-tracked under fixtures/targets (Phase 16 pattern)."""
    env_path = FIXTURE_ROOT / ".env"
    assert env_path.is_file(), "fixtures/targets/multi-host-app/.env must exist for plan fingerprints"
    text = env_path.read_text(encoding="utf-8")
    assert "API_PORT=8000" in text


def test_manifest_environments_and_ingress_validate():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    assert manifest.environments is not None
    assert manifest.ingress is not None
    assert "production" in manifest.environments


def test_resolve_lenovo_target():
    manifest = load_and_validate_manifest(FIXTURE_ROOT / "portforge.yml")
    target = resolve_target(manifest, "production", target="lenovo-prod")
    assert target.host_id == "33333333-3333-3333-3333-333333333333"
    assert target.alias == "lenovo-prod"


def test_namespace_request_id_roundtrip():
    value = namespace_request_id("production", "lenovo-prod", "deploy-1")
    assert value == "production:lenovo-prod:deploy-1"
    assert parse_namespaced_request_id(value) == ("production", "lenovo-prod", "deploy-1")


def test_plan_for_target_offline_smoke():
    payload = plan_for_target(
        project_root=FIXTURE_ROOT,
        environment="production",
        target="lenovo-prod",
        central_url=None,
        include_local_runtime=False,
    )
    assert payload["environment"] == "production"
    assert payload["target"] == "lenovo-prod"
    assert payload["host_id"] == "33333333-3333-3333-3333-333333333333"
    assert len(payload["services"]) == 4
    assert any(item["name"] == "api-public" for item in payload["ingress"])


def test_validate_ingress_unknown_service():
    bindings = parse_ingress_from_manifest(
        {
            "ingress": [
                {
                    "name": "x",
                    "scheme": "https",
                    "hostname": "a.test",
                    "public_port": 443,
                    "service": "missing",
                    "environment": "production",
                    "target": "lenovo-prod",
                }
            ]
        }
    )
    errors = validate_ingress(bindings[0], ["api"])
    assert errors and errors[0]["code"] == "INVALID_INGRESS"
