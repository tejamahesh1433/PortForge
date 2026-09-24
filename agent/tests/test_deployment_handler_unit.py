"""Phase 18: deployment handler unit tests with mocked docker/fetch."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from portforge_agent.deployment.handler import process_pending_deployment
from portforge_agent.deployment.package import build_local_package


def _pending(**overrides) -> Dict[str, Any]:
    base = {
        "deployment_id": "dep-11111111-1111-1111-1111-111111111111",
        "request_id": "req-1",
        "project": "demo-app",
        "environment": "staging",
        "package_uri": "https://artifacts.example.com/demo.zip",
        "package_sha256": "a" * 64,
        "package_manifest_sha256": "b" * 64,
        "claim_token": "claim-token-123",
        "plan_hash": "c" * 64,
    }
    base.update(overrides)
    return base


def _make_client() -> MagicMock:
    client = MagicMock()
    client.claim_deployment.return_value = MagicMock(
        success=True, data={"claim_token": "claim-token-123"}
    )
    client.deployment_status.return_value = MagicMock(success=True)
    client.deployment_health.return_value = MagicMock(success=True)
    return client


def test_checksum_fail_never_applies(tmp_path, monkeypatch):
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(package_sha256=package_sha, package_manifest_sha256=manifest_sha)

    apply_mock = MagicMock()
    validate_mock = MagicMock()

    from portforge_agent.deployment.package import PackageChecksumError

    def _fake_fetch(uri, expected_sha256, dest_path, **kwargs):
        raise PackageChecksumError("Package SHA-256 mismatch")

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _fake_fetch)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))

    ok = process_pending_deployment(pending, client)
    assert ok is False
    validate_mock.assert_not_called()
    apply_mock.assert_not_called()
    failed_calls = [
        call
        for call in client.deployment_status.call_args_list
        if call.kwargs.get("state") == "FAILED"
    ]
    assert failed_calls
    assert failed_calls[-1].kwargs["failure_code"] == "DEPLOYMENT_CHECKSUM_MISMATCH"


def test_successful_flow_applies_and_reports(tmp_path, monkeypatch):
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(package_sha256=package_sha, package_manifest_sha256=manifest_sha)

    def _fake_fetch(uri, expected_sha256, dest_path, **kwargs):
        dest_path.write_bytes(archive.read_bytes())

    validate_mock = MagicMock()
    apply_mock = MagicMock()
    inspect_mock = MagicMock(return_value={"available": True, "services": [{"state": "running"}]})

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _fake_fetch)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.inspect_status", inspect_mock)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))

    ok = process_pending_deployment(pending, client)
    assert ok is True
    validate_mock.assert_called_once()
    apply_mock.assert_called_once()
    client.deployment_status.assert_any_call(
        pending["deployment_id"],
        claim_token="claim-token-123",
        state="SUCCEEDED",
        failure_code=None,
        failure_reason=None,
        revision_id="req-1",
    )


def test_compose_validation_failure_does_not_apply(tmp_path, monkeypatch):
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(package_sha256=package_sha, package_manifest_sha256=manifest_sha)

    def _fake_fetch(uri, expected_sha256, dest_path, **kwargs):
        dest_path.write_bytes(archive.read_bytes())

    from portforge_agent.deployment.compose_adapter import ComposeValidationError

    validate_mock = MagicMock(side_effect=ComposeValidationError("invalid compose"))
    apply_mock = MagicMock()

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _fake_fetch)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))

    ok = process_pending_deployment(pending, client)
    assert ok is False
    apply_mock.assert_not_called()
    client.deployment_status.assert_any_call(
        pending["deployment_id"],
        claim_token="claim-token-123",
        state="FAILED",
        failure_code="COMPOSE_VALIDATION_FAILED",
        failure_reason="invalid compose",
    )


def test_rejects_disallowed_payload_fields():
    client = _make_client()
    pending = _pending(command="rm -rf /")
    ok = process_pending_deployment(pending, client)
    assert ok is False
    client.deployment_status.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 18.6: port recheck + test interrupt
# ---------------------------------------------------------------------------

def _make_fetch_copy(archive: "Path"):
    """Return a fake fetch function that copies the archive to dest_path."""
    def _fake_fetch(uri, expected_sha256, dest_path, **kwargs):
        dest_path.write_bytes(archive.read_bytes())
    return _fake_fetch


def test_port_conflict_never_calls_apply(tmp_path, monkeypatch):
    """When pre-apply port recheck finds an unexpected occupant, compose_apply is never called."""
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(
        package_sha256=package_sha,
        package_manifest_sha256=manifest_sha,
        ports_json={
            "services": [
                {"name": "api", "host_port": 18000, "protocol": "tcp"}
            ]
        },
    )

    from portforge_agent.deployment.port_recheck import (
        PortConflictError,
        PortRecheckResult,
        PortRecheckVerdict,
    )

    mock_recheck = MagicMock(
        return_value=[
            PortRecheckResult(
                service="api",
                protocol="tcp",
                host_port=18000,
                verdict=PortRecheckVerdict.UNEXPECTED_OCCUPANT,
                reason="Port held by some-other-project",
            )
        ]
    )
    apply_mock = MagicMock()
    validate_mock = MagicMock()

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _make_fetch_copy(archive))
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.recheck_host_ports_live", mock_recheck)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))

    ok = process_pending_deployment(pending, client)
    assert ok is False
    apply_mock.assert_not_called()

    failed_calls = [
        call
        for call in client.deployment_status.call_args_list
        if call.kwargs.get("state") == "FAILED"
    ]
    assert failed_calls, "Expected a FAILED status report"
    assert failed_calls[-1].kwargs["failure_code"] == "DEPLOYMENT_PORT_CONFLICT"


def test_expected_owner_port_allows_apply(tmp_path, monkeypatch):
    """When the port is held by the expected compose project, apply proceeds normally."""
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(
        package_sha256=package_sha,
        package_manifest_sha256=manifest_sha,
        ports_json={
            "services": [{"name": "api", "host_port": 18000, "protocol": "tcp"}]
        },
    )

    from portforge_agent.deployment.port_recheck import (
        PortRecheckResult,
        PortRecheckVerdict,
    )

    mock_recheck = MagicMock(
        return_value=[
            PortRecheckResult(
                service="api",
                protocol="tcp",
                host_port=18000,
                verdict=PortRecheckVerdict.EXPECTED_EXISTING_DEPLOYMENT,
                reason="Port held by expected compose project 'pf-demo-app-staging-...'",
            )
        ]
    )
    validate_mock = MagicMock()
    apply_mock = MagicMock()
    inspect_mock = MagicMock(
        return_value={"available": True, "services": [{"state": "running"}]}
    )

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _make_fetch_copy(archive))
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.inspect_status", inspect_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.recheck_host_ports_live", mock_recheck)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))

    ok = process_pending_deployment(pending, client)
    assert ok is True
    apply_mock.assert_called_once()


def test_before_apply_interrupt_prevents_apply(tmp_path, monkeypatch):
    """PORTFORGE_DEPLOYMENT_TEST_INTERRUPT=before_apply prevents compose_apply without FAILED."""
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, package_sha, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )

    client = _make_client()
    pending = _pending(package_sha256=package_sha, package_manifest_sha256=manifest_sha)

    apply_mock = MagicMock()
    validate_mock = MagicMock()

    monkeypatch.setattr("portforge_agent.deployment.handler.fetch_package", _make_fetch_copy(archive))
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_validate", validate_mock)
    monkeypatch.setattr("portforge_agent.deployment.handler.compose_apply", apply_mock)
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PORTFORGE_DEPLOYMENT_TEST_INTERRUPT", "before_apply")

    ok = process_pending_deployment(pending, client)
    assert ok is False
    apply_mock.assert_not_called()

    # No FAILED status must have been reported (leave deployment in STARTING for recovery).
    failed_calls = [
        call
        for call in client.deployment_status.call_args_list
        if call.kwargs.get("state") == "FAILED"
    ]
    assert not failed_calls, f"Unexpected FAILED calls: {failed_calls}"
