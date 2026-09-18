"""Tests for subprocess_util.run_subprocess() -- the fix for a physically
reproduced bug on NTMKEYA: the scheduled agent (correctly launched via
pythonw.exe, so it has no console of its own) still flashed a visible
console window every time it spawned a child console application
(docker.exe's periodic version/ps probes), because pythonw.exe lacking a
console does not by itself stop Windows from allocating a brand-new one
for a console-subsystem CHILD process.

subprocess.run itself is mocked in every test here -- nothing spawns a
real process, and _IS_WINDOWS is monkeypatched per test rather than
relying on the actual host OS, so this suite behaves identically on
Windows, macOS, or Linux CI.
"""
from __future__ import annotations

import subprocess

import pytest

from portforge_agent import subprocess_util


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Windows: console suppression
# ---------------------------------------------------------------------------


def test_windows_includes_create_no_window(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    subprocess_util.run_subprocess(["docker", "version"])

    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_windows_includes_startupinfo_hidden(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    subprocess_util.run_subprocess(["docker", "ps", "-q"])

    startupinfo = captured["kwargs"]["startupinfo"]
    assert startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startupinfo.wShowWindow == subprocess.SW_HIDE


def test_windows_docker_version_probe_uses_safe_execution_path(monkeypatch):
    """Regression #2: the exact command physically observed flashing a
    console (docker.exe version --format {{.Server.Version}}) must carry
    the suppression flags when run through run_command.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _cp(stdout="29.6.1")

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    from portforge_agent.collectors.base import run_command

    run_command(["docker.EXE", "version", "--format", "{{.Server.Version}}"])

    assert captured["args"] == ["docker.EXE", "version", "--format", "{{.Server.Version}}"]
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_windows_docker_ps_discovery_uses_safe_execution_path(monkeypatch):
    """Regression #3: the exact command physically observed flashing a
    console (docker.exe ps -q) must carry the suppression flags.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _cp(stdout="")

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    from portforge_agent.collectors.base import run_command

    run_command(["docker.EXE", "ps", "-q"])

    assert captured["args"] == ["docker.EXE", "ps", "-q"]
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_windows_service_ops_calls_use_safe_execution_path(monkeypatch):
    """schtasks.exe calls (portforge agent service install/status/...)
    must also go through the same suppression -- the architecture is
    reusable for every PortForge-owned background command, not just
    Docker's.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    from portforge_agent import service_ops

    service_ops._run(["schtasks", "/Query", "/TN", "PortForge Agent"])

    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# Behavior preservation
# ---------------------------------------------------------------------------


def test_stdout_stderr_returncode_unchanged(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_util.subprocess, "run", lambda args, **kwargs: _cp(returncode=7, stdout="out", stderr="err")
    )

    result = subprocess_util.run_subprocess(["cmd"], capture_output=True, text=True)

    assert result.returncode == 7
    assert result.stdout == "out"
    assert result.stderr == "err"


def test_timeout_propagates(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)

    def fake_run(args, **kwargs):
        assert kwargs["timeout"] == 5.0
        raise subprocess.TimeoutExpired(cmd=args, timeout=5.0)

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess_util.run_subprocess(["docker", "ps"], timeout=5.0)


def test_missing_executable_raises_file_not_found(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)

    def fake_run(args, **kwargs):
        raise FileNotFoundError(f"No such file: {args[0]}")

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    with pytest.raises(FileNotFoundError):
        subprocess_util.run_subprocess(["nonexistent"])


def test_run_command_missing_executable_still_raises_collector_error(monkeypatch):
    """End-to-end through collectors/base.py's run_command: the existing
    "not found" vs. "found but failing" distinction survives unchanged.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)

    def fake_run(args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    from portforge_agent.collectors.base import CollectorError, run_command

    with pytest.raises(CollectorError, match="Required command not found"):
        run_command(["docker"])


# ---------------------------------------------------------------------------
# POSIX: no Windows-only flags leak in
# ---------------------------------------------------------------------------


def test_non_windows_never_passes_windows_only_kwargs(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", False)
    captured = {}

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    subprocess_util.run_subprocess(["docker", "version"], capture_output=True, text=True)

    assert "creationflags" not in captured["kwargs"]
    assert "startupinfo" not in captured["kwargs"]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True


def test_non_windows_never_touches_windows_only_subprocess_attributes(monkeypatch):
    """A stronger guarantee than just "the kwargs are absent": on a real
    POSIX system, `subprocess.STARTUPINFO`/`CREATE_NO_WINDOW` don't even
    exist as attributes -- referencing them would raise AttributeError.
    Deleting them here (if present, e.g. because this suite itself runs
    on Windows) proves run_subprocess never touches them on the non-
    Windows path, not just that a mock happened not to receive them.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", False)
    for attr in ("STARTUPINFO", "CREATE_NO_WINDOW", "STARTF_USESHOWWINDOW", "SW_HIDE"):
        monkeypatch.delattr(subprocess_util.subprocess, attr, raising=False)

    monkeypatch.setattr(subprocess_util.subprocess, "run", lambda args, **kwargs: _cp())

    # Must not raise AttributeError.
    subprocess_util.run_subprocess(["lsof", "-i"])


def test_no_shell_true_anywhere(monkeypatch):
    for is_windows in (True, False):
        monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", is_windows)
        captured = {}

        def fake_run(args, **kwargs):
            captured["kwargs"] = kwargs
            return _cp()

        monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

        subprocess_util.run_subprocess(["docker", "version"])

        assert "shell" not in captured["kwargs"]


# ---------------------------------------------------------------------------
# Caller kwargs are never silently dropped or overridden
# ---------------------------------------------------------------------------


def test_caller_kwargs_pass_through_on_windows(monkeypatch):
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    subprocess_util.run_subprocess(["docker", "ps"], capture_output=True, text=True, check=False)

    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["check"] is False
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_caller_supplied_windows_kwargs_win_over_defaults(monkeypatch):
    """An explicit caller override always wins -- run_subprocess adds
    defaults, it never clobbers a caller's own choice.
    """
    monkeypatch.setattr(subprocess_util, "_IS_WINDOWS", True)
    captured = {}
    custom_flags = 0x12345678

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return _cp()

    monkeypatch.setattr(subprocess_util.subprocess, "run", fake_run)

    subprocess_util.run_subprocess(["docker", "ps"], creationflags=custom_flags)

    assert captured["kwargs"]["creationflags"] == custom_flags
