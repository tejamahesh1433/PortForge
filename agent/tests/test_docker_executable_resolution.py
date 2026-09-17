"""Tests for _resolve_docker_executable() -- the fix for a physically
reproduced bug on tejamaheshs-MacBook-Pro.local: a macOS LaunchAgent's
PATH is launchd's minimal /usr/bin:/bin:/usr/sbin:/sbin, which doesn't
include Homebrew's or Docker Desktop's install locations, so background
(non-interactive) Docker discovery reported "Required command not found:
docker" even though `docker` worked fine in an interactive shell.

No test here depends on Docker actually being installed on the machine
running the suite: `shutil.which` and filesystem checks (`os.path.isfile`,
`os.access`) are mocked in every test, and `platform.detect_os()` is
mocked wherever the macOS-only fallback path matters, so this passes
identically on Windows, macOS, or Linux CI.
"""
from __future__ import annotations

import pytest

from portforge_agent import platform as pf
from portforge_agent.collectors import docker as docker_module


def test_prefers_normal_path_resolution_when_available(monkeypatch):
    """The common interactive-shell case (and Linux/Windows in general):
    if shutil.which finds it, that's used directly -- no macOS-specific
    fallback logic runs at all.
    """
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: "/some/path/on/PATH/docker")

    result = docker_module._resolve_docker_executable()

    assert result == "/some/path/on/PATH/docker"


def test_falls_back_to_known_macos_locations_when_path_resolution_fails(monkeypatch):
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)

    def fake_isfile(path):
        return path == "/opt/homebrew/bin/docker"

    monkeypatch.setattr(docker_module.os.path, "isfile", fake_isfile)
    monkeypatch.setattr(docker_module.os, "access", lambda path, mode: True)

    result = docker_module._resolve_docker_executable()

    assert result == "/opt/homebrew/bin/docker"


def test_checks_known_macos_locations_in_order(monkeypatch):
    """/usr/local/bin is checked before /opt/homebrew/bin before Docker
    Desktop's bundled location -- matches platform.MACOS_EXTRA_BIN_DIRS
    order, so behavior is deterministic when more than one exists.
    """
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(docker_module.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(docker_module.os, "access", lambda path, mode: True)

    result = docker_module._resolve_docker_executable()

    assert result == "/usr/local/bin/docker"


def test_docker_desktop_bundled_location_is_considered(monkeypatch):
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)

    def fake_isfile(path):
        return path == "/Applications/Docker.app/Contents/Resources/bin/docker"

    monkeypatch.setattr(docker_module.os.path, "isfile", fake_isfile)
    monkeypatch.setattr(docker_module.os, "access", lambda path, mode: True)

    result = docker_module._resolve_docker_executable()

    assert result == "/Applications/Docker.app/Contents/Resources/bin/docker"


def test_directories_absent_from_the_machine_are_skipped(monkeypatch):
    """None of the known locations exist here -- must return None, never
    guess or fabricate a path.
    """
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(docker_module.os.path, "isfile", lambda path: False)

    result = docker_module._resolve_docker_executable()

    assert result is None


def test_candidate_must_actually_be_executable(monkeypatch):
    """A file exists at a known location but isn't executable (e.g. a
    stale/broken install) -- must not be returned as usable.
    """
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(docker_module.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(docker_module.os, "access", lambda path, mode: False)

    result = docker_module._resolve_docker_executable()

    assert result is None


def test_no_macos_fallback_on_non_macos_platforms(monkeypatch):
    """Windows/Linux never consult MACOS_EXTRA_BIN_DIRS at all -- PATH
    resolution failing there just means Docker isn't available, not a
    reason to probe macOS-specific install locations.
    """
    monkeypatch.setattr(docker_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    # If the fallback ran, this would make it "find" something -- proving
    # the assertion below is because the fallback never executed, not
    # because isfile happened to return False.
    monkeypatch.setattr(docker_module.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(docker_module.os, "access", lambda path, mode: True)

    result = docker_module._resolve_docker_executable()

    assert result is None


def test_run_docker_raises_not_found_when_resolution_fails(monkeypatch):
    """_run_docker's "not installed" message is preserved verbatim when
    no executable can be resolved at all -- distinct from a found-but-
    failing invocation (see the next test).
    """
    monkeypatch.setattr(docker_module, "_resolve_docker_executable", lambda: None)

    with pytest.raises(docker_module.DockerUnavailableError, match="Required command not found: docker"):
        docker_module._run_docker(["version"], timeout=1.0)


def test_run_docker_distinguishes_found_but_daemon_unavailable(monkeypatch):
    """CLI found (resolution succeeds) but the invocation itself fails
    with a daemon-down-style error -- must NOT be reported as "command
    not found"; the underlying run_command failure message must survive.
    """
    monkeypatch.setattr(docker_module, "_resolve_docker_executable", lambda: "/usr/local/bin/docker")

    def fake_run_command(args, timeout):
        from portforge_agent.collectors.base import CollectorError

        raise CollectorError("Command 'docker version' failed (exit 1): Cannot connect to the Docker daemon")

    monkeypatch.setattr(docker_module, "run_command", fake_run_command)

    with pytest.raises(docker_module.DockerUnavailableError) as exc_info:
        docker_module._run_docker(["version"], timeout=1.0)

    assert "Cannot connect to the Docker daemon" in str(exc_info.value)
    assert "not found" not in str(exc_info.value).lower()


def test_run_docker_uses_resolved_absolute_path_not_bare_docker(monkeypatch):
    """The resolved absolute path is what actually gets executed --
    never the bare string "docker" (which is exactly what fails to
    resolve under launchd's minimal PATH)."""
    monkeypatch.setattr(docker_module, "_resolve_docker_executable", lambda: "/opt/homebrew/bin/docker")

    captured = {}

    def fake_run_command(args, timeout):
        captured["args"] = args
        return "29.6.1"

    monkeypatch.setattr(docker_module, "run_command", fake_run_command)

    docker_module._run_docker(["version", "--format", "{{.Server.Version}}"], timeout=1.0)

    assert captured["args"][0] == "/opt/homebrew/bin/docker"
    assert captured["args"] != ["docker", "version", "--format", "{{.Server.Version}}"]
