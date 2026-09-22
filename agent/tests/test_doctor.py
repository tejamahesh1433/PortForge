"""v1.1-A: `portforge doctor` tests -- healthy path, Central unavailable,
Docker unavailable, no/valid/invalid manifest, read-only proof, secret
redaction. Mirrors docs/v1.1/doctor-design.md and the increment's own
"Doctor checks" requirements.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from portforge_agent import doctor
from portforge_agent.manifest import parse_manifest_yaml, validate_manifest
from portforge_agent.version import PROTOCOL_VERSION, get_portforge_version

VALID_MANIFEST = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
    protocol: tcp
"""

INVALID_MANIFEST = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  api:
    purpose: api
    protcol: tcp
"""


def _healthy_client(central_protocol_version=PROTOCOL_VERSION):
    client = MagicMock()
    client.health.return_value = MagicMock(
        success=True,
        data={"status": "ok", "service": "portforge", "database": "connected", "version": "1.1.1", "protocol_version": central_protocol_version},
    )
    return client


# --- healthy path -------------------------------------------------------


def test_healthy_path_all_checks_present_and_overall_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=True)), \
         patch("portforge_agent.doctor.load_credential", return_value="tok"), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    assert report.overall == "ok"
    ids = {c.id for c in report.checks}
    assert ids == {
        "cli_version", "protocol_version", "central_connectivity", "central_health",
        "protocol_compatibility", "agent_identity", "agent_service", "docker", "collector",
        "manifest", "filesystem",
    }
    assert all(c.status in ("ok", "skip") for c in report.checks)


def test_portforge_version_and_protocol_version_in_report(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))
    assert report.portforge_version == get_portforge_version()
    assert report.protocol_version == PROTOCOL_VERSION


# --- Central unavailable -------------------------------------------------


def test_central_unreachable_reports_error_but_other_checks_continue(tmp_path):
    client = MagicMock()
    client.health.return_value = MagicMock(success=False, error="connection refused")

    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    central = next(c for c in report.checks if c.id == "central_connectivity")
    assert central.status == "error"
    assert report.overall == "error"
    # every other check still ran and reported something -- doctor didn't hang or crash
    assert len(report.checks) == 11
    docker_check = next(c for c in report.checks if c.id == "docker")
    assert docker_check.status == "ok"


def test_no_central_url_configured_is_skip_not_error(tmp_path):
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=None, start_dir=str(tmp_path))

    central = next(c for c in report.checks if c.id == "central_connectivity")
    assert central.status == "skip"
    assert report.overall == "ok"  # a skip must never make the run "degraded" or "error"


# --- Docker unavailable ---------------------------------------------------


def test_docker_unavailable_is_warn_not_error(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=False), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    docker_check = next(c for c in report.checks if c.id == "docker")
    assert docker_check.status == "warn"
    assert report.overall == "degraded"


def test_docker_check_exception_is_handled_gracefully(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", side_effect=OSError("docker daemon socket missing")), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    docker_check = next(c for c in report.checks if c.id == "docker")
    assert docker_check.status == "warn"
    # doctor kept going -- did not raise/crash
    assert len(report.checks) == 11


# --- manifest scenarios ---------------------------------------------------


def test_no_manifest_is_skip_and_does_not_make_overall_unhealthy(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    manifest_check = next(c for c in report.checks if c.id == "manifest")
    assert manifest_check.status == "skip"
    assert report.overall == "ok"


def test_valid_manifest_reports_ok(tmp_path):
    (tmp_path / "portforge.yml").write_text(VALID_MANIFEST, encoding="utf-8")
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    manifest_check = next(c for c in report.checks if c.id == "manifest")
    assert manifest_check.status == "ok"
    assert "1 request" in manifest_check.message


def test_invalid_manifest_reports_error_using_existing_validator(tmp_path):
    """Doctor must use the SAME manifest parser/validator as every other
    command -- not a second implementation.
    """
    (tmp_path / "portforge.yml").write_text(INVALID_MANIFEST, encoding="utf-8")
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    manifest_check = next(c for c in report.checks if c.id == "manifest")
    assert manifest_check.status == "error"
    assert manifest_check.details["code"] == "MANIFEST_INVALID"
    assert report.overall == "error"

    # Confirm this is really the SAME validator, not a reimplementation:
    with pytest.raises(Exception):
        validate_manifest(parse_manifest_yaml(INVALID_MANIFEST))


# --- read-only proof -------------------------------------------------------


def test_doctor_creates_no_files_in_project_root(tmp_path):
    (tmp_path / "portforge.yml").write_text(VALID_MANIFEST, encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        doctor.run_doctor(client=client, start_dir=str(tmp_path))

    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after  # no .portforge/, no mutation record, no backup, nothing


def test_doctor_never_calls_any_mutating_client_method(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        doctor.run_doctor(client=client, start_dir=str(tmp_path))

    client.create_allocation.assert_not_called()
    client.release_allocation.assert_not_called()
    client.sync_reservation.assert_not_called()
    client.enroll.assert_not_called()


# --- secret redaction -------------------------------------------------------


def test_report_never_contains_agent_token(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=True)), \
         patch("portforge_agent.doctor.load_credential", return_value="super-secret-agent-token-value"), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    dumped = str(report.to_dict())
    assert "super-secret-agent-token-value" not in dumped


def test_report_never_contains_admin_bootstrap_token_or_db_password(tmp_path):
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        report = doctor.run_doctor(client=client, start_dir=str(tmp_path))

    dumped = str(report.to_dict()).lower()
    for forbidden in ("bootstrap", "db_password", "database_url", "ssh"):
        assert forbidden not in dumped


# --- v1.1-B: doctor must never create a remote bind-probe (task §25) -------


def test_doctor_never_calls_recommendation_or_allocation_endpoints(tmp_path):
    """Only `get_recommendation`/`create_allocation` (server-side) can ever
    create a HostProbe row -- doctor must never call either, directly or
    indirectly, on the CentralClient it's given.
    """
    client = _healthy_client()
    with patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)), \
         patch("portforge_agent.doctor.load_credential", return_value=None), \
         patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")), \
         patch("portforge_agent.doctor.is_docker_available", return_value=True), \
         patch("portforge_agent.doctor.discover_all_ports", return_value=[]):
        doctor.run_doctor(client=client, start_dir=str(tmp_path))

    client.get_recommendation.assert_not_called()
    client.create_allocation.assert_not_called()
    assert not hasattr(client, "submit_probe_result") or not client.submit_probe_result.called
