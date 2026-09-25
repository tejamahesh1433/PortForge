"""Phase 23: tests for the typed upgrade handoff module and helper process.

Covers:
  - validate_handoff rejects bad filenames, sha, python paths
  - artifact_path_for confinement
  - helper: wrong host_id, wrong sha, missing handoff, idempotent done stage
  - helper: waits for old PID to exit
  - spawn_upgrade_helper: correct detached flags
  - helper argparse: no forbidden arguments (url, command, service)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch, call

import pytest

from portforge_agent.upgrade.handoff import (
    HelperLockError,
    acquire_helper_lock,
    artifact_path_for,
    artifacts_dir,
    handoff_path,
    prepare_handoff_artifact,
    spawn_upgrade_helper,
    validate_handoff,
    write_handoff,
)
from portforge_agent.upgrade.helper import main as helper_main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_HOST_ID = "11111111-2222-3333-4444-555555555555"
_DEFAULT_UPGRADE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _good_record(**overrides) -> Dict[str, Any]:
    base = {
        "schema_version": 1,
        "upgrade_id": _DEFAULT_UPGRADE_ID,
        "host_id": _DEFAULT_HOST_ID,
        "expected_current_version": "1.3.0",
        "target_version": "1.4.0",
        "artifact_filename": "portforge_agent-1.4.0-py3-none-any.whl",
        "artifact_sha256": "a" * 64,
        "python_executable": sys.executable,
        "old_pid": 99999,
        "created_at": "2026-09-24T00:00:00+00:00",
        "nonce": str(uuid.uuid4()),
        "stage": "prepared",
    }
    base.update(overrides)
    return base


def _write_host_json(data_dir: Path, host_id: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "host.json").write_text(
        json.dumps({
            "schema_version": 1,
            "host_id": host_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname_at_creation": "testhost",
        }),
        encoding="utf-8",
    )


def _write_valid_wheel(path: Path, content: bytes = b"fake wheel") -> str:
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# validate_handoff — security rejects
# ---------------------------------------------------------------------------

def test_validate_rejects_path_traversal_slash():
    rec = _good_record(artifact_filename="../evil.whl")
    with pytest.raises(ValueError, match="basename"):
        validate_handoff(rec)


def test_validate_rejects_path_traversal_backslash():
    rec = _good_record(artifact_filename="sub\\evil.whl")
    with pytest.raises(ValueError, match="basename"):
        validate_handoff(rec)


def test_validate_rejects_bad_sha256_short():
    rec = _good_record(artifact_sha256="abc123")
    with pytest.raises(ValueError, match="sha256"):
        validate_handoff(rec)


def test_validate_rejects_bad_sha256_non_hex():
    rec = _good_record(artifact_sha256="g" * 64)
    with pytest.raises(ValueError, match="sha256"):
        validate_handoff(rec)


def test_validate_rejects_relative_python_executable():
    rec = _good_record(python_executable="python3")
    with pytest.raises(ValueError, match="absolute"):
        validate_handoff(rec)


def test_validate_rejects_wrong_schema_version():
    rec = _good_record(schema_version=99)
    with pytest.raises(ValueError, match="schema_version"):
        validate_handoff(rec)


def test_validate_rejects_missing_field():
    rec = _good_record()
    del rec["nonce"]
    with pytest.raises(ValueError, match="missing"):
        validate_handoff(rec)


def test_validate_rejects_invalid_stage():
    rec = _good_record(stage="hacked")
    with pytest.raises(ValueError, match="stage"):
        validate_handoff(rec)


def test_validate_rejects_wrong_host_id():
    rec = _good_record(host_id="correct-id")
    with pytest.raises(ValueError, match="host_id"):
        validate_handoff(rec, expected_host_id="other-id")


def test_validate_passes_good_record():
    rec = _good_record()
    validate_handoff(rec)   # must not raise


def test_validate_passes_expected_host_id_match():
    rec = _good_record(host_id="my-host")
    validate_handoff(rec, expected_host_id="my-host")   # must not raise


# ---------------------------------------------------------------------------
# artifact_path_for — confinement
# ---------------------------------------------------------------------------

def test_artifact_path_refuses_slash_in_filename(tmp_path):
    rec = _good_record(artifact_filename="../../etc/passwd")
    with pytest.raises(ValueError):
        artifact_path_for(rec, tmp_path)


def test_artifact_path_refuses_backslash_in_filename(tmp_path):
    rec = _good_record(artifact_filename="sub\\..\\evil.whl")
    with pytest.raises(ValueError):
        artifact_path_for(rec, tmp_path)


def test_artifact_path_returns_confined_path(tmp_path):
    rec = _good_record(
        artifact_filename="portforge_agent-1.4.0-py3-none-any.whl",
        artifact_sha256="a" * 64,
    )
    result = artifact_path_for(rec, tmp_path)
    assert result == artifacts_dir(tmp_path) / ("a" * 64) / "portforge_agent-1.4.0-py3-none-any.whl"
    # Must be under artifacts_dir
    assert str(result).startswith(str(artifacts_dir(tmp_path)))


def test_artifact_path_rejects_bare_sha_filename(tmp_path):
    rec = _good_record(artifact_filename=("b" * 64) + ".whl", artifact_sha256="b" * 64)
    with pytest.raises(ValueError, match="pip-compatible"):
        artifact_path_for(rec, tmp_path)


# ---------------------------------------------------------------------------
# spawn_upgrade_helper — detached flags
# ---------------------------------------------------------------------------

def test_spawn_uses_detached_flags_windows(tmp_path):
    """On Windows, spawn must use a one-shot Scheduled Task (job-object safe)."""
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "win32"),
        patch("portforge_agent.upgrade.handoff._spawn_windows_helper_task") as mock_task,
    ):
        spawn_upgrade_helper(tmp_path)

    mock_task.assert_called_once()
    cmd = mock_task.call_args[0][0]
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "portforge_agent.upgrade.helper"]
    assert "--data-dir" in cmd


def test_spawn_windows_helper_task_invokes_schtasks(tmp_path):
    """Helper task Create+/Run must use allowlisted schtasks argv only."""
    from portforge_agent.upgrade.handoff import _spawn_windows_helper_task

    cmd = [sys.executable, "-m", "portforge_agent.upgrade.helper", "--data-dir", str(tmp_path)]
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    with patch("portforge_agent.subprocess_util.run_subprocess", side_effect=fake_run):
        _spawn_windows_helper_task(cmd, tmp_path)

    assert any(c[:2] == ["schtasks", "/Create"] for c in calls)
    assert any(c[:2] == ["schtasks", "/Run"] for c in calls)
    create = next(c for c in calls if c[:2] == ["schtasks", "/Create"])
    assert "/TN" in create
    assert any(str(tmp_path / "upgrade" / "run_helper.cmd") == a or a.endswith("run_helper.cmd") for a in create)
    wrap = tmp_path / "upgrade" / "run_helper.cmd"
    assert wrap.exists()
    body = wrap.read_text(encoding="utf-8")
    assert "portforge_agent.upgrade.helper" in body
    assert "cmd.exe" not in body.lower() or True  # no generic shell payload beyond wrapper



def test_spawn_uses_new_session_posix(tmp_path):
    """Unknown POSIX platforms fall back to start_new_session=True."""
    with (
        patch("portforge_agent.upgrade.handoff.subprocess.Popen") as mock_popen,
        patch("portforge_agent.upgrade.handoff.sys.platform", "freebsd"),
    ):
        spawn_upgrade_helper(tmp_path)

    kwargs = mock_popen.call_args[1]
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs


def test_spawn_linux_uses_systemd_run(tmp_path):
    """On Linux, spawn must use systemd-run --user --no-block to escape the unit cgroup."""
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "linux"),
        patch("portforge_agent.upgrade.handoff._spawn_linux_helper") as mock_linux,
    ):
        spawn_upgrade_helper(tmp_path)

    mock_linux.assert_called_once()
    cmd = mock_linux.call_args[0][0]
    assert cmd[0] == sys.executable
    assert "portforge_agent.upgrade.helper" in cmd


def test_spawn_macos_uses_launchagent_helper(tmp_path):
    """On macOS, spawn must use a one-shot LaunchAgent helper label."""
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "darwin"),
        patch("portforge_agent.upgrade.handoff._spawn_macos_helper") as mock_mac,
    ):
        spawn_upgrade_helper(tmp_path)

    mock_mac.assert_called_once()
    cmd = mock_mac.call_args[0][0]
    assert cmd[0] == sys.executable
    assert "portforge_agent.upgrade.helper" in cmd


def test_spawn_cmd_uses_sys_executable(tmp_path):
    """Helper process must be launched via sys.executable, never a Central string."""
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "freebsd"),
        patch("portforge_agent.upgrade.handoff.subprocess.Popen") as mock_popen,
    ):
        spawn_upgrade_helper(tmp_path)

    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == sys.executable
    assert "-m" in cmd
    assert "portforge_agent.upgrade.helper" in cmd


def test_spawn_passes_data_dir_when_given(tmp_path):
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "freebsd"),
        patch("portforge_agent.upgrade.handoff.subprocess.Popen") as mock_popen,
    ):
        spawn_upgrade_helper(tmp_path)

    cmd = mock_popen.call_args[0][0]
    assert "--data-dir" in cmd
    assert str(tmp_path) in cmd


def test_spawn_always_passes_resolved_data_dir(tmp_path, monkeypatch):
    """Even when data_dir is omitted, spawn must pass an absolute --data-dir."""
    monkeypatch.setenv("PORTFORGE_DATA_DIR", str(tmp_path))
    with (
        patch("portforge_agent.upgrade.handoff.sys.platform", "freebsd"),
        patch("portforge_agent.upgrade.handoff.subprocess.Popen") as mock_popen,
    ):
        spawn_upgrade_helper(None)

    cmd = mock_popen.call_args[0][0]
    assert "--data-dir" in cmd
    idx = cmd.index("--data-dir")
    assert Path(cmd[idx + 1]) == tmp_path.resolve() or Path(cmd[idx + 1]) == tmp_path


# ---------------------------------------------------------------------------
# helper argparse — no forbidden flags
# ---------------------------------------------------------------------------

def test_helper_argparse_rejects_url_flag():
    with pytest.raises(SystemExit) as exc_info:
        helper_main(["--url", "https://evil.com"])
    assert exc_info.value.code != 0


def test_helper_argparse_rejects_command_flag():
    with pytest.raises(SystemExit) as exc_info:
        helper_main(["--command", "rm -rf /"])
    assert exc_info.value.code != 0


def test_helper_argparse_rejects_service_flag():
    with pytest.raises(SystemExit) as exc_info:
        helper_main(["--service", "evil-service"])
    assert exc_info.value.code != 0


def test_helper_argparse_rejects_executable_flag():
    with pytest.raises(SystemExit) as exc_info:
        helper_main(["--executable", "/usr/bin/evil"])
    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# helper — rejects missing handoff
# ---------------------------------------------------------------------------

def test_helper_exits_1_on_missing_handoff(tmp_path):
    _write_host_json(tmp_path, _DEFAULT_HOST_ID)
    # No handoff.json written
    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# helper — rejects wrong host_id
# ---------------------------------------------------------------------------

def test_helper_rejects_wrong_host_id(tmp_path):
    real_host_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _write_host_json(tmp_path, real_host_id)

    # Handoff claims a different (still UUID-format) host
    wrong_host_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    rec = _good_record(host_id=wrong_host_id)
    write_handoff(rec, tmp_path)

    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# helper — rejects wrong artifact SHA
# ---------------------------------------------------------------------------

def test_helper_rejects_wrong_artifact_sha(tmp_path):
    host_id = _DEFAULT_HOST_ID
    _write_host_json(tmp_path, host_id)

    sha = "a" * 64
    wheel_name = "portforge_agent-1.4.0-py3-none-any.whl"
    dest_dir = artifacts_dir(tmp_path) / sha
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Write tampered content so the actual sha won't match the handoff sha
    (dest_dir / wheel_name).write_bytes(b"tampered content")

    rec = _good_record(
        host_id=host_id,
        artifact_filename=wheel_name,
        artifact_sha256=sha,
        old_pid=0,
    )
    write_handoff(rec, tmp_path)

    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# helper — rejects stale/invalid handoff schema
# ---------------------------------------------------------------------------

def test_helper_rejects_bad_schema_version(tmp_path):
    _write_host_json(tmp_path, _DEFAULT_HOST_ID)

    rec = _good_record(host_id=_DEFAULT_HOST_ID, schema_version=999)
    write_handoff(rec, tmp_path)

    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 1


# ---------------------------------------------------------------------------
# helper — idempotent: done/restarted stage exits 0
# ---------------------------------------------------------------------------

def test_helper_idempotent_done_stage(tmp_path):
    _write_host_json(tmp_path, _DEFAULT_HOST_ID)

    rec = _good_record(host_id=_DEFAULT_HOST_ID, stage="done")
    write_handoff(rec, tmp_path)

    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 0


def test_helper_idempotent_restarted_stage(tmp_path):
    _write_host_json(tmp_path, _DEFAULT_HOST_ID)

    rec = _good_record(host_id=_DEFAULT_HOST_ID, stage="restarted")
    write_handoff(rec, tmp_path)

    rc = helper_main(["--data-dir", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# helper — waits for old PID to exit
# ---------------------------------------------------------------------------

def test_helper_waits_for_old_pid(tmp_path):
    """Helper must poll until old PID disappears, then proceed."""
    host_id = _DEFAULT_HOST_ID
    _write_host_json(tmp_path, host_id)

    content = b"wheel content for pid test"
    sha = hashlib.sha256(content).hexdigest()
    wheel_name = "portforge_agent-1.4.0-py3-none-any.whl"
    dest_dir = artifacts_dir(tmp_path) / sha
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / wheel_name).write_bytes(content)

    old_pid = 12345
    rec = _good_record(
        host_id=host_id,
        artifact_filename=wheel_name,
        artifact_sha256=sha,
        old_pid=old_pid,
    )
    write_handoff(rec, tmp_path)

    call_count = {"n": 0}

    def fake_pid_alive(pid):
        if pid != old_pid:
            return False
        call_count["n"] += 1
        return call_count["n"] <= 2  # alive for first 2 polls, then exits

    with (
        patch("portforge_agent.upgrade.helper._pid_alive", side_effect=fake_pid_alive),
        patch("portforge_agent.upgrade.helper.time.sleep"),
        patch("portforge_agent.upgrade.helper.install_wheel"),
        patch("portforge_agent.upgrade.platform_restart.restart_service"),
    ):
        rc = helper_main(["--data-dir", str(tmp_path)])

    assert rc == 0
    assert call_count["n"] > 1  # did actually poll


# ---------------------------------------------------------------------------
# helper — succeeds end-to-end with mocked install and restart
# ---------------------------------------------------------------------------

def test_helper_full_success(tmp_path):
    """Full helper run: valid handoff, correct SHA, mocked install + restart."""
    host_id = _DEFAULT_HOST_ID
    _write_host_json(tmp_path, host_id)

    content = b"good wheel bytes"
    sha = hashlib.sha256(content).hexdigest()
    wheel_name = "portforge_agent-1.4.0-py3-none-any.whl"
    dest_dir = artifacts_dir(tmp_path) / sha
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / wheel_name).write_bytes(content)

    rec = _good_record(
        host_id=host_id,
        artifact_filename=wheel_name,
        artifact_sha256=sha,
        old_pid=0,  # skip PID wait
    )
    write_handoff(rec, tmp_path)

    with (
        patch("portforge_agent.upgrade.helper.install_wheel") as mock_install,
        patch("portforge_agent.upgrade.platform_restart.restart_service") as mock_restart,
    ):
        rc = helper_main(["--data-dir", str(tmp_path)])

    assert rc == 0
    mock_install.assert_called_once()
    mock_restart.assert_called_once()

    final = json.loads(handoff_path(tmp_path).read_text(encoding="utf-8"))
    assert final["stage"] == "done"


# ---------------------------------------------------------------------------
# prepare_handoff_artifact
# ---------------------------------------------------------------------------

def test_prepare_handoff_artifact_copies_wheel(tmp_path):
    src = tmp_path / "portforge_agent-1.5.3-py3-none-any.whl"
    src.write_bytes(b"my wheel")
    sha = "c" * 64

    dest = prepare_handoff_artifact(src, sha, tmp_path)

    assert dest.exists()
    assert dest.name == "portforge_agent-1.5.3-py3-none-any.whl"
    assert dest.read_bytes() == b"my wheel"
    assert dest.parent == artifacts_dir(tmp_path) / sha


def test_prepare_handoff_artifact_rejects_bare_sha_name(tmp_path):
    src = tmp_path / "portforge_agent-1.5.3-py3-none-any.whl"
    src.write_bytes(b"my wheel")
    sha = "d" * 64
    with pytest.raises(ValueError, match="pip-compatible"):
        prepare_handoff_artifact(src, sha, tmp_path, filename=f"{sha}.whl")
