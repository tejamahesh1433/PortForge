"""Unit tests for service_gen.py -- pure definition generation, no
subprocess/native tool calls anywhere in this file. See test_service_ops.py
for the mocked-subprocess install/start/stop/status/uninstall layer.
"""
from __future__ import annotations

import os
import plistlib
from pathlib import Path

from portforge_agent import platform as pf
from portforge_agent import service_gen as gen

_FORBIDDEN_SUBSTRINGS = ["token", "password", "secret", "bearer", "credential"]


# ---------------------------------------------------------------------------
# resolve_python_executable / agent_run_args
# ---------------------------------------------------------------------------


def test_resolve_python_executable_prefers_pythonw_on_windows(monkeypatch):
    monkeypatch.setattr(gen.sys, "executable", r"C:\Program Files\Python313\python.exe")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    monkeypatch.setattr(Path, "exists", lambda self: self.name == "pythonw.exe")

    result = gen.resolve_python_executable()

    assert result.endswith("pythonw.exe")
    assert "Program Files" in result


def test_resolve_python_executable_falls_back_when_pythonw_absent(monkeypatch):
    monkeypatch.setattr(gen.sys, "executable", r"C:\Program Files\Python313\python.exe")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    result = gen.resolve_python_executable()

    assert result.endswith("python.exe")
    assert "pythonw" not in result.lower()


# These tests simulate POSIX-style sys.executable values (Linux/macOS venv
# and system paths) while the suite itself may run on any host OS,
# including Windows. `os.path.isabs` is platform-native (ntpath vs.
# posixpath), and Windows' isabs does not consider a driveless
# "/foo/bar"-style path absolute the way posixpath does -- that mismatch is
# purely a cross-platform testing artifact (a real sys.executable is always
# already absolute in its own OS's terms), so these tests pin `isabs` to
# posix-style semantics rather than relying on the ambient host OS's rules.
def _assume_already_absolute(monkeypatch) -> None:
    monkeypatch.setattr(gen.os.path, "isabs", lambda p: True)


def test_resolve_python_executable_uses_sys_executable_on_non_windows(monkeypatch):
    monkeypatch.setattr(gen.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    _assume_already_absolute(monkeypatch)

    result = gen.resolve_python_executable()

    # No .resolve() -- an already-absolute sys.executable is returned as-is,
    # as a plain string (never round-tripped through pathlib.Path, which
    # would rewrite separators to the host OS's style).
    assert result == "/usr/bin/python3"


def test_agent_run_args_shape():
    args = gen.agent_run_args(python_executable="/usr/bin/python3")
    assert args == ["/usr/bin/python3", "-m", "portforge_agent", "agent", "run"]


# ---------------------------------------------------------------------------
# Regression: virtualenv interpreter identity must be preserved, never
# dereferenced down to a base/system interpreter (see service_gen.py's
# resolve_python_executable docstring for the full rationale).
# ---------------------------------------------------------------------------


def test_resolve_python_executable_preserves_linux_venv_interpreter(monkeypatch):
    monkeypatch.setattr(gen.sys, "executable", "/home/test/project/.venv/bin/python")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    _assume_already_absolute(monkeypatch)

    result = gen.resolve_python_executable()

    assert result == "/home/test/project/.venv/bin/python"


def test_resolve_python_executable_never_calls_path_resolve(monkeypatch):
    """The historical bug: Path(sys.executable).resolve() dereferences a
    virtualenv's python symlink down to the base/system interpreter,
    generating a service that can't import portforge_agent. Patching
    Path.resolve to something obviously wrong proves the fix's result
    doesn't come from it.
    """
    monkeypatch.setattr(gen.sys, "executable", "/home/test/project/.venv/bin/python")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    _assume_already_absolute(monkeypatch)
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: Path("/usr/bin/python3.14"))

    result = gen.resolve_python_executable()

    assert result == "/home/test/project/.venv/bin/python"
    assert result != "/usr/bin/python3.14"


def test_resolve_python_executable_system_python_unaffected(monkeypatch):
    """A genuine non-venv absolute interpreter must continue to work
    exactly as before -- the fix only stops dereferencing symlinks, it
    doesn't change behavior for a plain system interpreter.
    """
    monkeypatch.setattr(gen.sys, "executable", "/usr/bin/python3.11")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    _assume_already_absolute(monkeypatch)

    result = gen.resolve_python_executable()

    assert result == "/usr/bin/python3.11"


def test_resolve_python_executable_makes_relative_interpreter_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gen.sys, "executable", "python")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)

    result = gen.resolve_python_executable()

    assert os.path.isabs(result)
    assert result == os.path.abspath("python")


def test_windows_venv_prefers_sibling_pythonw_with_spaces_in_path(monkeypatch):
    venv_python = r"C:\Users\Test User\project\.venv\Scripts\python.exe"
    monkeypatch.setattr(gen.sys, "executable", venv_python)
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    monkeypatch.setattr(Path, "exists", lambda self: self.name == "pythonw.exe")

    result = gen.resolve_python_executable()

    assert result == r"C:\Users\Test User\project\.venv\Scripts\pythonw.exe"

    definition = gen.build_windows_task(python_executable=result)
    assert definition.command_line == (
        '"C:\\Users\\Test User\\project\\.venv\\Scripts\\pythonw.exe" -m portforge_agent agent run'
    )


def test_windows_pythonw_preference_never_leaves_venv_directory(monkeypatch):
    """The pythonw.exe sibling lookup must only ever look inside the same
    directory as the (unresolved) venv interpreter -- it must never escape
    to a global/system Python installation.
    """
    venv_python = r"C:\Users\Test User\project\.venv\Scripts\python.exe"
    monkeypatch.setattr(gen.sys, "executable", venv_python)
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)

    seen_paths = []

    def fake_exists(self):
        seen_paths.append(self)
        return self.name == "pythonw.exe"

    monkeypatch.setattr(Path, "exists", fake_exists)

    gen.resolve_python_executable()

    assert seen_paths, "expected the pythonw.exe sibling check to run"
    assert all(p.parent == Path(venv_python).parent for p in seen_paths)


def test_build_systemd_unit_preserves_venv_interpreter(monkeypatch):
    monkeypatch.setattr(gen.sys, "executable", "/home/test/project/.venv/bin/python")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    _assume_already_absolute(monkeypatch)

    resolved = gen.resolve_python_executable()
    unit = gen.build_systemd_unit(python_executable=resolved)

    assert resolved == "/home/test/project/.venv/bin/python"
    assert "ExecStart=/home/test/project/.venv/bin/python -m portforge_agent agent run" in unit


def test_launchd_plist_preserves_venv_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(gen.sys, "executable", "/Users/test/project/.venv/bin/python")
    monkeypatch.setattr(gen.pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    _assume_already_absolute(monkeypatch)

    resolved = gen.resolve_python_executable()
    plist_dict = gen.build_launchd_plist_dict(python_executable=resolved, log_dir=tmp_path)

    assert resolved == "/Users/test/project/.venv/bin/python"
    assert plist_dict["ProgramArguments"][0] == "/Users/test/project/.venv/bin/python"


# ---------------------------------------------------------------------------
# Windows: Task Scheduler
# ---------------------------------------------------------------------------


def test_build_windows_task_quotes_paths_with_spaces():
    definition = gen.build_windows_task(python_executable=r"C:\Program Files\Python313\pythonw.exe")

    assert definition.command_line == (
        '"C:\\Program Files\\Python313\\pythonw.exe" -m portforge_agent agent run'
    )
    assert definition.task_name == gen.WINDOWS_TASK_NAME
    assert definition.trigger == "ONLOGON"  # not AT STARTUP -- see module docstring
    assert definition.run_level == "LIMITED"  # never requires elevation


def test_build_windows_task_no_quoting_needed_without_spaces():
    definition = gen.build_windows_task(python_executable=r"C:\Python313\pythonw.exe")
    assert definition.command_line == r"C:\Python313\pythonw.exe -m portforge_agent agent run"


def test_windows_create_args_construction():
    definition = gen.build_windows_task(python_executable=r"C:\Python313\pythonw.exe")
    args = gen.windows_create_args(definition)

    assert args[:2] == ["schtasks", "/Create"]
    assert args[args.index("/TN") + 1] == gen.WINDOWS_TASK_NAME
    assert args[args.index("/TR") + 1] == definition.command_line
    assert args[args.index("/SC") + 1] == "ONLOGON"
    assert args[args.index("/RL") + 1] == "LIMITED"
    assert "/F" in args  # forces overwrite -- what makes install idempotent


def test_windows_query_run_end_delete_args():
    assert gen.windows_query_args() == ["schtasks", "/Query", "/TN", gen.WINDOWS_TASK_NAME, "/FO", "LIST", "/V"]
    assert gen.windows_run_args() == ["schtasks", "/Run", "/TN", gen.WINDOWS_TASK_NAME]
    assert gen.windows_end_args() == ["schtasks", "/End", "/TN", gen.WINDOWS_TASK_NAME]
    assert gen.windows_delete_args() == ["schtasks", "/Delete", "/TN", gen.WINDOWS_TASK_NAME, "/F"]


# ---------------------------------------------------------------------------
# macOS: launchd
# ---------------------------------------------------------------------------


def test_build_launchd_plist_dict_structure(tmp_path):
    d = gen.build_launchd_plist_dict(python_executable="/usr/bin/python3", log_dir=tmp_path)

    assert d["Label"] == gen.LAUNCHD_LABEL
    assert d["ProgramArguments"] == ["/usr/bin/python3", "-m", "portforge_agent", "agent", "run"]
    assert d["RunAtLoad"] is True
    assert d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["StandardOutPath"] == str(tmp_path / "agent.out.log")
    assert d["StandardErrorPath"] == str(tmp_path / "agent.err.log")


def test_build_launchd_plist_bytes_round_trips_via_plistlib(tmp_path):
    raw = gen.build_launchd_plist_bytes(python_executable="/usr/bin/python3", log_dir=tmp_path)
    parsed = plistlib.loads(raw)

    assert parsed == gen.build_launchd_plist_dict(python_executable="/usr/bin/python3", log_dir=tmp_path)


def test_launchd_plist_path_is_per_user_launchagents(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = gen.launchd_plist_path()

    assert result == tmp_path / "Library" / "LaunchAgents" / f"{gen.LAUNCHD_LABEL}.plist"
    assert "LaunchDaemons" not in str(result)  # never system-wide -- see module docstring


# ---------------------------------------------------------------------------
# Linux: systemd (user service)
# ---------------------------------------------------------------------------


def test_build_systemd_unit_contents():
    unit = gen.build_systemd_unit(python_executable="/usr/bin/python3")

    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    assert "ExecStart=/usr/bin/python3 -m portforge_agent agent run" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=10" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "WantedBy=default.target" in unit  # user target, not multi-user.target


def test_build_systemd_unit_quotes_exec_start_path_with_spaces():
    unit = gen.build_systemd_unit(python_executable="/usr/local/my python/bin/python3")
    assert 'ExecStart="/usr/local/my python/bin/python3" -m portforge_agent agent run' in unit


def test_systemd_unit_path_is_user_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = gen.systemd_unit_path()

    assert result == tmp_path / ".config" / "systemd" / "user" / gen.SYSTEMD_UNIT_NAME


# ---------------------------------------------------------------------------
# Cross-platform: no generated definition ever embeds a credential
# ---------------------------------------------------------------------------


def test_no_generated_definition_embeds_credential_like_content(tmp_path):
    windows_def = gen.build_windows_task(python_executable="/usr/bin/python3")
    linux_unit = gen.build_systemd_unit(python_executable="/usr/bin/python3")
    macos_plist = gen.build_launchd_plist_bytes(
        python_executable="/usr/bin/python3", log_dir=tmp_path
    ).decode("utf-8")

    for text in (windows_def.command_line, linux_unit, macos_plist):
        lowered = text.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"found forbidden substring {forbidden!r} in: {text}"
