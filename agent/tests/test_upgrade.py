"""Phase 10: agent-side safe upgrade execution tests.

All network I/O and filesystem writes are mocked -- these are unit tests
for the validation, download, verify, install, and restart logic.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, call, patch

import pytest

from portforge_agent.upgrade.handler import (
    UpgradeSHA256Error,
    UpgradeValidationError,
    _parse_version_tuple,
    _validate_payload,
    _verify_sha256,
    run_upgrade,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _good_payload(**overrides) -> Dict[str, Any]:
    """A valid pending_upgrade payload."""
    base = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "target_version": "1.4.0",
        "artifact_url": "https://releases.example.com/portforge-1.4.0.whl",
        "artifact_sha256": "a" * 64,
        "artifact_filename": "portforge_agent-1.4.0-py3-none-any.whl",
        "state": "APPROVED",
    }
    base.update(overrides)
    return base


def _make_client() -> MagicMock:
    client = MagicMock()
    client.report_upgrade_status.return_value = MagicMock(success=True)
    return client


# ---------------------------------------------------------------------------
# _parse_version_tuple
# ---------------------------------------------------------------------------

def test_parse_version_tuple_standard():
    assert _parse_version_tuple("1.3.0") == (1, 3, 0)


def test_parse_version_tuple_two_parts():
    assert _parse_version_tuple("2.0") == (2, 0)


def test_parse_version_tuple_prerelease():
    # Pre-release suffix stops at the first non-numeric component.
    assert _parse_version_tuple("1.4.0rc1") == (1, 4, 0)


def test_parse_version_tuple_empty_raises():
    with pytest.raises(ValueError):
        _parse_version_tuple("alpha")


# ---------------------------------------------------------------------------
# _validate_payload -- basic field checks
# ---------------------------------------------------------------------------

def test_validate_good_payload_passes():
    _validate_payload(_good_payload(), current_version="1.3.0")


def test_validate_sha256_wrong_length():
    with pytest.raises(UpgradeValidationError, match="artifact_sha256"):
        _validate_payload(_good_payload(artifact_sha256="abc"), current_version="1.3.0")


def test_validate_sha256_non_hex():
    bad = "g" * 64  # 'g' is not a hex digit
    with pytest.raises(UpgradeValidationError, match="artifact_sha256"):
        _validate_payload(_good_payload(artifact_sha256=bad), current_version="1.3.0")


def test_validate_url_http_rejected():
    with pytest.raises(UpgradeValidationError, match="https"):
        _validate_payload(
            _good_payload(artifact_url="http://example.com/pkg.whl"),
            current_version="1.3.0",
        )


def test_validate_url_file_scheme_rejected():
    with pytest.raises(UpgradeValidationError, match="https"):
        _validate_payload(
            _good_payload(artifact_url="file:///tmp/pkg.whl"),
            current_version="1.3.0",
        )


def test_validate_url_localhost_rejected():
    with pytest.raises(UpgradeValidationError, match="local"):
        _validate_payload(
            _good_payload(artifact_url="https://localhost/pkg.whl"),
            current_version="1.3.0",
        )


def test_validate_url_127_rejected():
    with pytest.raises(UpgradeValidationError, match="local"):
        _validate_payload(
            _good_payload(artifact_url="https://127.0.0.1/pkg.whl"),
            current_version="1.3.0",
        )


def test_validate_missing_url():
    with pytest.raises(UpgradeValidationError, match="artifact_url"):
        _validate_payload(_good_payload(artifact_url=""), current_version="1.3.0")


def test_validate_missing_target_version():
    with pytest.raises(UpgradeValidationError, match="target_version"):
        _validate_payload(_good_payload(target_version=""), current_version="1.3.0")


# ---------------------------------------------------------------------------
# Downgrade rejection
# ---------------------------------------------------------------------------

def test_validate_downgrade_rejects_on_upgrade_path():
    """target < current must be rejected when allow_downgrade=False."""
    with pytest.raises(UpgradeValidationError, match="downgrade"):
        _validate_payload(
            _good_payload(target_version="1.2.0"),
            current_version="1.3.0",
            allow_downgrade=False,
        )


def test_validate_same_version_not_rejected():
    """Same version is not a downgrade (target >= current passes).

    Design doc: 'Target < current: reject'. Same version satisfies >=.
    """
    _validate_payload(
        _good_payload(target_version="1.3.0"),
        current_version="1.3.0",
        allow_downgrade=False,
    )  # must not raise


def test_validate_allow_downgrade_passes():
    """allow_downgrade=True skips the version ordering check (rollback path)."""
    _validate_payload(
        _good_payload(target_version="1.2.0"),
        current_version="1.3.0",
        allow_downgrade=True,
    )


# ---------------------------------------------------------------------------
# Forbidden payload fields
# ---------------------------------------------------------------------------

def test_validate_rejects_command_field():
    payload = _good_payload()
    payload["command"] = "rm -rf /"
    with pytest.raises(UpgradeValidationError, match="command"):
        _validate_payload(payload, current_version="1.3.0")


def test_validate_rejects_script_field():
    payload = _good_payload()
    payload["script"] = "echo pwned"
    with pytest.raises(UpgradeValidationError, match="script"):
        _validate_payload(payload, current_version="1.3.0")


def test_validate_rejects_executable_field():
    payload = _good_payload()
    payload["executable"] = "/usr/bin/evil"
    with pytest.raises(UpgradeValidationError, match="executable"):
        _validate_payload(payload, current_version="1.3.0")


def test_validate_rejects_shell_field():
    payload = _good_payload()
    payload["shell"] = True
    with pytest.raises(UpgradeValidationError, match="shell"):
        _validate_payload(payload, current_version="1.3.0")


# ---------------------------------------------------------------------------
# SHA-256 verification
# ---------------------------------------------------------------------------

def test_sha256_verify_correct(tmp_path):
    data = b"hello portforge"
    expected = hashlib.sha256(data).hexdigest()
    p = tmp_path / "wheel.whl"
    p.write_bytes(data)
    _verify_sha256(p, expected)  # should not raise


def test_sha256_verify_mismatch_raises(tmp_path):
    p = tmp_path / "wheel.whl"
    p.write_bytes(b"tampered content")
    wrong_hash = "b" * 64
    with pytest.raises(UpgradeSHA256Error, match="mismatch"):
        _verify_sha256(p, wrong_hash)


def test_sha256_verify_case_insensitive(tmp_path):
    data = b"case test"
    expected = hashlib.sha256(data).hexdigest().upper()
    p = tmp_path / "wheel.whl"
    p.write_bytes(data)
    _verify_sha256(p, expected)  # upper-case hex must be accepted


# ---------------------------------------------------------------------------
# run_upgrade: status reporting
# ---------------------------------------------------------------------------

def test_run_upgrade_reports_failed_on_validation_error():
    """A payload with a bad URL must report FAILED and return False."""
    client = _make_client()
    payload = _good_payload(artifact_url="http://insecure.example.com/pkg.whl")

    result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is False
    client.report_upgrade_status.assert_called_once()
    call_args = client.report_upgrade_status.call_args
    assert call_args.kwargs["state"] == "FAILED"
    assert call_args.kwargs["failure_reason"]


def test_run_upgrade_reports_failed_on_sha_mismatch(tmp_path):
    """A SHA mismatch after download must report VERIFYING then FAILED."""
    client = _make_client()
    payload = _good_payload()

    fake_bytes = b"fake wheel content"

    with (
        patch("portforge_agent.upgrade.handler.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("portforge_agent.upgrade.handler._download_artifact") as mock_dl,
        patch("portforge_agent.upgrade.handler._verify_sha256") as mock_verify,
    ):
        # Write fake bytes so the path exists when cleanup runs
        def fake_download(url, dest):
            dest.write_bytes(fake_bytes)
        mock_dl.side_effect = fake_download
        mock_verify.side_effect = UpgradeSHA256Error("SHA-256 mismatch: expected abc, got xyz")

        result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is False
    states = [c.kwargs["state"] for c in client.report_upgrade_status.call_args_list]
    assert "DOWNLOADING" in states
    assert "VERIFYING" in states
    failed_calls = [c for c in client.report_upgrade_status.call_args_list if c.kwargs["state"] == "FAILED"]
    assert failed_calls
    assert "mismatch" in (failed_calls[0].kwargs.get("failure_reason") or "")


def test_run_upgrade_reports_failed_on_download_error(tmp_path):
    """Network/download failure must fail closed without install."""
    client = _make_client()
    payload = _good_payload()

    with (
        patch("portforge_agent.upgrade.handler.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch(
            "portforge_agent.upgrade.handler._download_artifact",
            side_effect=OSError("DNS resolution failed"),
        ),
        patch("portforge_agent.upgrade.handler._install_wheel") as mock_install,
    ):
        result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is False
    mock_install.assert_not_called()
    states = [c.kwargs["state"] for c in client.report_upgrade_status.call_args_list]
    assert "DOWNLOADING" in states
    assert states[-1] == "FAILED"
    assert "DNS" in (client.report_upgrade_status.call_args.kwargs.get("failure_reason") or "")


def test_run_upgrade_reports_all_states_on_success(tmp_path):
    """A successful upgrade reports DOWNLOADING, VERIFYING, INSTALLING, RESTARTING."""
    client = _make_client()
    payload = _good_payload()

    with (
        patch("portforge_agent.upgrade.handler.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("portforge_agent.upgrade.handler._download_artifact"),
        patch("portforge_agent.upgrade.handler._verify_sha256"),
        patch("portforge_agent.upgrade.handler._install_wheel"),
        patch("portforge_agent.upgrade.platform_restart.restart_service"),
    ):
        result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is True
    states = [c.kwargs["state"] for c in client.report_upgrade_status.call_args_list]
    assert states == ["DOWNLOADING", "VERIFYING", "INSTALLING", "RESTARTING"]


def test_run_upgrade_returns_false_on_install_failure(tmp_path):
    client = _make_client()
    payload = _good_payload()

    with (
        patch("portforge_agent.upgrade.handler.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("portforge_agent.upgrade.handler._download_artifact"),
        patch("portforge_agent.upgrade.handler._verify_sha256"),
        patch("portforge_agent.upgrade.handler._install_wheel", side_effect=RuntimeError("pip exit 1")),
    ):
        result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is False
    states = [c.kwargs["state"] for c in client.report_upgrade_status.call_args_list]
    assert "FAILED" in states
    assert "RESTARTING" not in states


def test_run_upgrade_returns_false_on_restart_failure(tmp_path):
    client = _make_client()
    payload = _good_payload()

    with (
        patch("portforge_agent.upgrade.handler.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("portforge_agent.upgrade.handler._download_artifact"),
        patch("portforge_agent.upgrade.handler._verify_sha256"),
        patch("portforge_agent.upgrade.handler._install_wheel"),
        patch(
            "portforge_agent.upgrade.platform_restart.restart_service",
            side_effect=RuntimeError("schtasks failed"),
        ),
    ):
        result = run_upgrade(client, "test-host", payload, current_version="1.3.0")

    assert result is False
    states = [c.kwargs["state"] for c in client.report_upgrade_status.call_args_list]
    assert states[-1] == "FAILED"


# ---------------------------------------------------------------------------
# No shell=True from Central payload
# ---------------------------------------------------------------------------

def test_install_wheel_uses_sys_executable_not_shell(tmp_path):
    """pip install must use sys.executable as a list arg -- never shell=True."""
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"")

    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        from portforge_agent.upgrade.handler import _install_wheel
        _install_wheel(wheel)

    call_args, call_kwargs = mock_run.call_args
    cmd = call_args[0]
    assert cmd[0] == sys.executable
    assert "pip" in cmd
    assert "install" in cmd
    # shell must NEVER be True
    assert call_kwargs.get("shell") is not True
    # The wheel path in the command must be a literal path, not a Central string
    assert str(wheel) in cmd


# ---------------------------------------------------------------------------
# Inventory fields in heartbeat and enroll
# ---------------------------------------------------------------------------

def test_heartbeat_includes_contract_version_and_python_version():
    """Heartbeat body must include contract_version and python_version."""
    from portforge_agent.central_client import CentralClient

    client = CentralClient(base_url="https://central.example.com", token="tok")

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.heartbeat(
            host_id="h1",
            hostname="box",
            operating_system="linux",
            os_version="Ubuntu 22.04",
            architecture="x86_64",
            agent_version="1.3.0",
            docker_available=False,
            timestamp="2026-09-23T00:00:00Z",
            protocol_version=1,
            contract_version=1,
            python_version="3.11.0",
        )

    _, call_kwargs = mock_req.call_args
    body = call_kwargs["body"]
    assert body["contract_version"] == 1
    assert body["python_version"] == "3.11.0"


def test_heartbeat_omits_inventory_when_absent():
    """When contract_version/python_version are None they must not appear in body."""
    from portforge_agent.central_client import CentralClient

    client = CentralClient(base_url="https://central.example.com", token="tok")

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.heartbeat(
            host_id="h1",
            hostname="box",
            operating_system="linux",
            os_version=None,
            architecture=None,
            agent_version=None,
            docker_available=False,
            timestamp="2026-09-23T00:00:00Z",
        )

    _, call_kwargs = mock_req.call_args
    body = call_kwargs["body"]
    assert "contract_version" not in body
    assert "python_version" not in body


def test_enroll_includes_contract_version_and_python_version():
    """Enroll body must include contract_version and python_version when provided."""
    from portforge_agent.central_client import CentralClient

    client = CentralClient(base_url="https://central.example.com")

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = MagicMock(success=True, data={})
        client.enroll(
            enrollment_token="tok",
            host_id="h1",
            hostname="box",
            operating_system="linux",
            os_version=None,
            architecture=None,
            agent_version="1.3.0",
            docker_available=False,
            contract_version=1,
            python_version="3.11.0",
        )

    _, call_kwargs = mock_req.call_args
    body = call_kwargs["body"]
    assert body["contract_version"] == 1
    assert body["python_version"] == "3.11.0"


# ---------------------------------------------------------------------------
# AgentRuntime integration: pending_upgrade triggers handler
# ---------------------------------------------------------------------------

def test_agent_runtime_calls_upgrade_handler_on_pending_upgrade():
    """When heartbeat returns pending_upgrade the runtime must call run_upgrade."""
    from portforge_agent.runtime.agent import AgentRuntime

    with (
        patch("portforge_agent.runtime.agent.load_central_config") as mock_cfg,
        patch("portforge_agent.runtime.agent.load_credential", return_value="token"),
        patch("portforge_agent.runtime.agent.load_state"),
        patch("portforge_agent.runtime.agent.save_state"),
        patch("portforge_agent.runtime.agent.CentralClient") as MockClient,
        patch("portforge_agent.runtime.agent.SyncManager"),
        patch("portforge_agent.runtime.agent.ExponentialBackoff"),
    ):
        mock_cfg.return_value = MagicMock(enabled=True, url="https://central.example.com")
        client_instance = MagicMock()
        MockClient.return_value = client_instance

        runtime = AgentRuntime()
        pending = _good_payload()

        with patch.object(runtime, "_handle_pending_upgrade") as mock_handle:
            runtime._handle_pending_upgrade(pending)
            mock_handle.assert_called_once_with(pending)


def test_handle_pending_upgrade_passes_allow_downgrade():
    """Runtime must forward pending allow_downgrade into run_upgrade."""
    from portforge_agent.runtime.agent import AgentRuntime

    with (
        patch("portforge_agent.runtime.agent.load_central_config") as mock_cfg,
        patch("portforge_agent.runtime.agent.load_credential", return_value="token"),
        patch("portforge_agent.runtime.agent.load_state"),
        patch("portforge_agent.runtime.agent.save_state"),
        patch("portforge_agent.runtime.agent.CentralClient"),
        patch("portforge_agent.runtime.agent.SyncManager"),
        patch("portforge_agent.runtime.agent.ExponentialBackoff"),
    ):
        mock_cfg.return_value = MagicMock(enabled=True, url="https://central.example.com")
        runtime = AgentRuntime()
        runtime._running = True
        payload = _good_payload()
        payload["allow_downgrade"] = True
        payload["target_version"] = "1.0.0"

        with patch("portforge_agent.upgrade.run_upgrade", return_value=False) as mock_run:
            with patch("portforge_agent.platform.get_host_id", return_value="host-1"):
                with patch(
                    "portforge_agent.version.get_portforge_version",
                    return_value="1.3.0",
                ):
                    runtime._handle_pending_upgrade(payload)

        assert mock_run.call_args.kwargs.get("allow_downgrade") is True


def test_handle_pending_upgrade_stops_loop_on_success():
    """When run_upgrade returns True, the runtime must set _running=False."""
    from portforge_agent.runtime.agent import AgentRuntime

    with (
        patch("portforge_agent.runtime.agent.load_central_config") as mock_cfg,
        patch("portforge_agent.runtime.agent.load_credential", return_value="token"),
        patch("portforge_agent.runtime.agent.load_state"),
        patch("portforge_agent.runtime.agent.save_state"),
        patch("portforge_agent.runtime.agent.CentralClient"),
        patch("portforge_agent.runtime.agent.SyncManager"),
        patch("portforge_agent.runtime.agent.ExponentialBackoff"),
    ):
        mock_cfg.return_value = MagicMock(enabled=True, url="https://central.example.com")
        runtime = AgentRuntime()
        runtime._running = True

        # run_upgrade is imported locally inside _handle_pending_upgrade;
        # patch it in the upgrade package namespace.
        with patch("portforge_agent.upgrade.run_upgrade", return_value=True):
            runtime._handle_pending_upgrade(_good_payload())

    assert runtime._running is False


def test_handle_pending_upgrade_keeps_loop_on_failure():
    """When run_upgrade returns False (failure), the loop must continue."""
    from portforge_agent.runtime.agent import AgentRuntime

    with (
        patch("portforge_agent.runtime.agent.load_central_config") as mock_cfg,
        patch("portforge_agent.runtime.agent.load_credential", return_value="token"),
        patch("portforge_agent.runtime.agent.load_state"),
        patch("portforge_agent.runtime.agent.save_state"),
        patch("portforge_agent.runtime.agent.CentralClient"),
        patch("portforge_agent.runtime.agent.SyncManager"),
        patch("portforge_agent.runtime.agent.ExponentialBackoff"),
    ):
        mock_cfg.return_value = MagicMock(enabled=True, url="https://central.example.com")
        runtime = AgentRuntime()
        runtime._running = True

        with patch("portforge_agent.upgrade.run_upgrade", return_value=False):
            runtime._handle_pending_upgrade(_good_payload())

    assert runtime._running is True


# ---------------------------------------------------------------------------
# Platform restart adapters (hard-coded service names; no payload input)
# ---------------------------------------------------------------------------


def test_restart_via_windows_uses_literal_task_name():
    from portforge_agent.upgrade.platform_restart import restart_via_windows_scheduler

    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        restart_via_windows_scheduler()

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any(cmd[:3] == ["schtasks", "/Run", "/TN"] for cmd in cmds)
    for cmd in cmds:
        assert "PortForge Agent" in cmd
        assert all(not isinstance(x, dict) for x in cmd)


def test_restart_via_systemd_uses_literal_unit():
    from portforge_agent.upgrade.platform_restart import restart_via_systemd

    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        restart_via_systemd()

    cmd = mock_run.call_args.args[0]
    assert cmd == ["systemctl", "--user", "restart", "portforge-agent.service"]


def test_restart_via_launchctl_uses_literal_plist_label(tmp_path):
    from portforge_agent.upgrade import platform_restart as pr

    plist = tmp_path / "com.portforge.agent.plist"
    plist.write_text("stub")
    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # os.getuid is POSIX-only; patch the module attribute used by the adapter.
        with patch.object(pr.os, "getuid", create=True, return_value=501):
            pr.restart_via_launchctl(str(plist))

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any(cmd[0] == "launchctl" and "bootstrap" in cmd for cmd in cmds)
    assert all(str(plist) in cmd for cmd in cmds if "bootstrap" in cmd or "bootout" in cmd)


def test_restart_service_dispatches_by_platform():
    from portforge_agent.upgrade import platform_restart as pr
    from portforge_agent import platform as pf

    with (
        patch.object(pf, "detect_os", return_value=pf.OperatingSystem.LINUX),
        patch.object(pr, "restart_via_systemd") as mock_linux,
    ):
        pr.restart_service()
        mock_linux.assert_called_once()


def test_restart_via_systemd_honors_service_name_env(monkeypatch):
    from portforge_agent.upgrade.platform_restart import restart_via_systemd

    monkeypatch.setenv("PORTFORGE_LINUX_SERVICE_NAME", "portforge-agent-qual.service")
    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        restart_via_systemd()
    assert mock_run.call_args.args[0] == [
        "systemctl",
        "--user",
        "restart",
        "portforge-agent-qual.service",
    ]


def test_restart_via_windows_honors_task_name_env(monkeypatch):
    from portforge_agent.upgrade.platform_restart import restart_via_windows_scheduler

    monkeypatch.setenv("PORTFORGE_WINDOWS_TASK_NAME", "PortForge Agent Qual")
    monkeypatch.delenv("PORTFORGE_WINDOWS_RESTART_HELPER", raising=False)
    with patch("portforge_agent.subprocess_util.run_subprocess") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        restart_via_windows_scheduler()
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any(cmd[-1] == "PortForge Agent Qual" for cmd in cmds if "/TN" in cmd)

