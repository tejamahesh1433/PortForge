"""Typed upgrade handoff: schema, paths, atomic I/O, artifact management,
shared pip install, and detached helper spawning (Phase 23).

Filesystem layout under {data_dir}/upgrade/:
  handoff.json          -- atomic write (temp + os.replace)
  helper.log            -- helper diagnostics (no secrets)
  artifacts/<sha>.whl   -- verified copy of the wheel
  helper.lock           -- exclusive lock preventing parallel helper instances
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VALID_STAGES = frozenset({"prepared", "running", "installed", "restarted", "done", "failed"})
_REQUIRED_FIELDS = (
    "schema_version", "upgrade_id", "host_id", "expected_current_version",
    "target_version", "artifact_filename", "artifact_sha256", "python_executable",
    "old_pid", "created_at", "nonce", "stage",
)

# ---------------------------------------------------------------------------
# Platform-specific exclusive file lock (mirrors reservations/lock.py)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import msvcrt as _msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl as _fcntl

    def _try_lock(fd: int) -> bool:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        except OSError:
            pass


class HelperLockError(OSError):
    """Raised when the upgrade helper lock is already held by another process."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def upgrade_dir(data_dir: Optional[Path] = None) -> Path:
    from .. import paths
    return (data_dir or paths.data_dir()) / "upgrade"


def artifacts_dir(data_dir: Optional[Path] = None) -> Path:
    return upgrade_dir(data_dir) / "artifacts"


def handoff_path(data_dir: Optional[Path] = None) -> Path:
    return upgrade_dir(data_dir) / "handoff.json"


def helper_log_path(data_dir: Optional[Path] = None) -> Path:
    return upgrade_dir(data_dir) / "helper.log"


def lock_path(data_dir: Optional[Path] = None) -> Path:
    return upgrade_dir(data_dir) / "helper.lock"


# ---------------------------------------------------------------------------
# Handoff I/O
# ---------------------------------------------------------------------------

def write_handoff(record: Dict[str, Any], data_dir: Optional[Path] = None) -> None:
    """Atomically write the handoff record using a temp file + os.replace."""
    path = handoff_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=".handoff-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_handoff(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = handoff_path(data_dir)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Handoff file has unexpected shape: {path}")
    return data


def validate_handoff(
    record: Dict[str, Any],
    *,
    expected_host_id: Optional[str] = None,
) -> None:
    """Raise ValueError if the handoff record is invalid or untrusted.

    Checks: schema_version, required fields, no path separators in filename,
    absolute python_executable, 64-hex sha256, valid stage, optional host_id match.
    """
    if record.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported handoff schema_version {record.get('schema_version')!r} "
            f"(expected {_SCHEMA_VERSION})"
        )

    missing = [f for f in _REQUIRED_FIELDS if f not in record]
    if missing:
        raise ValueError(f"Handoff missing required fields: {missing}")

    filename = str(record["artifact_filename"])
    try:
        _safe_wheel_basename(filename)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    python_exe = str(record["python_executable"])
    if not Path(python_exe).is_absolute():
        raise ValueError(
            f"python_executable must be an absolute path, got {python_exe!r}"
        )

    sha = str(record.get("artifact_sha256", ""))
    if not _SHA256_RE.match(sha):
        raise ValueError(
            f"artifact_sha256 must be exactly 64 hex characters, got {sha!r}"
        )

    if record.get("stage") not in _VALID_STAGES:
        raise ValueError(f"Invalid stage {record.get('stage')!r}")

    if expected_host_id is not None and record.get("host_id") != expected_host_id:
        raise ValueError(
            f"host_id mismatch: handoff has {record.get('host_id')!r}, "
            f"expected {expected_host_id!r}"
        )


def _safe_wheel_basename(name: str) -> str:
    """Return a pip-compatible wheel basename; reject paths and bare-SHA names."""
    filename = str(name)
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        raise ValueError(
            f"artifact_filename must be a plain basename without path separators: {filename!r}"
        )
    if not filename.endswith(".whl"):
        raise ValueError(f"artifact_filename must end with .whl: {filename!r}")
    stem = filename[:-4]
    # Content-addressed "{sha256}.whl" is not a valid PEP 427 wheel name; pip rejects it.
    if _SHA256_RE.match(stem):
        raise ValueError(
            f"artifact_filename must be a pip-compatible wheel name, not a bare SHA: {filename!r}"
        )
    # PEP 427: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
    if len(stem.split("-")) < 5:
        raise ValueError(
            f"Invalid wheel filename (need >=5 hyphen-separated parts): {filename!r}"
        )
    return filename


def artifact_path_for(record: Dict[str, Any], data_dir: Optional[Path] = None) -> Path:
    """Return the artifacts-dir-confined path for the artifact in a handoff record.

    Layout: ``<upgrade>/artifacts/<sha256>/<wheel-basename>`` so the on-disk
    name remains pip-compatible while staying content-addressed.

    Raises ValueError if the filename is unsafe or the resolved path would
    escape the artifacts directory.
    """
    arts_dir = artifacts_dir(data_dir)
    filename = _safe_wheel_basename(str(record["artifact_filename"]))
    sha = str(record["artifact_sha256"]).lower()
    if not _SHA256_RE.match(sha):
        raise ValueError(f"artifact_sha256 must be exactly 64 hex characters, got {sha!r}")
    candidate = arts_dir / sha / filename
    try:
        resolved = candidate.resolve()
        arts_resolved = arts_dir.resolve()
        resolved.relative_to(arts_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Artifact path escapes artifacts directory: {candidate}"
        ) from exc
    return candidate


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

@contextmanager
def acquire_helper_lock(data_dir: Optional[Path] = None) -> Iterator[None]:
    """Hold an exclusive helper lock for the duration of the with-block.

    Raises HelperLockError immediately if the lock is already held
    (non-blocking: no retry or timeout).
    """
    lp = lock_path(data_dir)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lp), os.O_CREAT | os.O_RDWR)
    acquired = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        if not _try_lock(fd):
            raise HelperLockError(f"Upgrade helper lock already held: {lp}")
        acquired = True
        yield
    finally:
        if acquired:
            _unlock(fd)
        os.close(fd)


# ---------------------------------------------------------------------------
# Artifact management
# ---------------------------------------------------------------------------

def prepare_handoff_artifact(
    src_wheel: Path,
    sha256: str,
    data_dir: Optional[Path] = None,
    *,
    filename: Optional[str] = None,
) -> Path:
    """Copy the verified wheel into the upgrade artifacts directory.

    Layout: ``artifacts/<sha256>/<pip-compatible-basename>``.

    The SHA directory makes the store content-addressed; the basename must
    remain a valid PEP 427 wheel name so ``pip install`` accepts it.
    """
    sha = str(sha256).lower()
    if not _SHA256_RE.match(sha):
        raise ValueError(f"sha256 must be exactly 64 hex characters, got {sha256!r}")
    base = _safe_wheel_basename(filename or src_wheel.name)
    dest_dir = artifacts_dir(data_dir) / sha
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / base
    shutil.copy2(src_wheel, dest)
    return dest


# ---------------------------------------------------------------------------
# Shared pip install (used by both helper.py and legacy _install_wheel)
# ---------------------------------------------------------------------------

def install_wheel(python_exe: str, wheel_path: Path) -> None:
    """Install wheel into the interpreter identified by python_exe via pip.

    shell=False is enforced (list argv). Raises RuntimeError on pip failure.
    python_exe must be an absolute path (callers are responsible; the helper
    validates this from the handoff before calling here).
    """
    from ..subprocess_util import run_subprocess

    result = run_subprocess(
        [
            python_exe, "-m", "pip", "install",
            "--upgrade", "--force-reinstall", "--no-deps",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pip install failed (rc={result.returncode}): {result.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Detached helper spawning
# ---------------------------------------------------------------------------

def spawn_upgrade_helper(data_dir: Optional[Path] = None) -> None:
    """Spawn the upgrade helper as a fully detached process.

    Windows: launch via a one-shot Scheduled Task so the helper escapes the
    Task Scheduler Job Object that owns the running agent. DETACHED_PROCESS
    alone is insufficient — when the agent task ends, Windows terminates
    every process in that job, including a DETACHED child.

    POSIX: start_new_session=True creates a new session/process group.

    Always passes an absolute --data-dir so the helper does not depend on
    inheriting PORTFORGE_DATA_DIR (Scheduled Tasks start with a clean env).

    Helper logs to helper.log via its own FileHandler (no secret fields).
    """
    from .. import paths as _paths

    resolved_dir = Path(data_dir) if data_dir is not None else _paths.data_dir()
    cmd = [
        sys.executable,
        "-m",
        "portforge_agent.upgrade.helper",
        "--data-dir",
        str(resolved_dir),
    ]

    # Security: never accept caller-supplied argv beyond --data-dir.
    if cmd[1:3] != ["-m", "portforge_agent.upgrade.helper"]:
        raise RuntimeError("Refusing to spawn non-allowlisted upgrade helper command")

    log_path = helper_log_path(resolved_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        _spawn_windows_helper_task(cmd, resolved_dir)
        return

    if sys.platform.startswith("linux"):
        _spawn_linux_helper(cmd, resolved_dir, log_path)
        return

    if sys.platform == "darwin":
        _spawn_macos_helper(cmd, resolved_dir, log_path)
        return

    log_fh = open(log_path, "ab")
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
    finally:
        log_fh.close()


def _spawn_macos_helper(cmd: list, data_dir: Path, log_path: Path) -> None:
    """Launch helper via a one-shot LaunchAgent so it outlives the agent job.

    Preserves production KeepAlive semantics on the main agent plist
    (SuccessfulExit=false, NetworkState=true, RunAtLoad=true). The helper
    uses a distinct label under the same gui domain.
    """
    from ..subprocess_util import run_subprocess

    label = "com.portforge.agent.upgradehelper"
    uid = os.getuid()
    domain = f"gui/{uid}"
    plist_path = upgrade_dir(data_dir) / f"{label}.plist"
    # ProgramArguments as XML array; values are allowlisted argv only.
    args_xml = "\n".join(f"    <string>{_xml_escape(a)}</string>" for a in cmd)
    env_xml = (
        f"    <key>PORTFORGE_DATA_DIR</key>\n"
        f"    <string>{_xml_escape(str(data_dir))}</string>\n"
    )
    mac_label = os.environ.get("PORTFORGE_MACOS_PLIST_LABEL")
    if mac_label:
        env_xml += (
            f"    <key>PORTFORGE_MACOS_PLIST_LABEL</key>\n"
            f"    <string>{_xml_escape(mac_label)}</string>\n"
        )
    mac_path = os.environ.get("PORTFORGE_MACOS_PLIST_PATH")
    if mac_path:
        env_xml += (
            f"    <key>PORTFORGE_MACOS_PLIST_PATH</key>\n"
            f"    <string>{_xml_escape(mac_path)}</string>\n"
        )
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
{env_xml}  </dict>
  <key>WorkingDirectory</key>
  <string>{_xml_escape(str(data_dir))}</string>
  <key>StandardOutPath</key>
  <string>{_xml_escape(str(log_path))}</string>
  <key>StandardErrorPath</key>
  <string>{_xml_escape(str(log_path))}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist, encoding="utf-8")

    # Best-effort unload prior helper job.
    run_subprocess(
        ["launchctl", "bootout", domain, str(plist_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    boot = run_subprocess(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if boot.returncode != 0:
        # Fallback: session-detached Popen (may still be killed with the agent job).
        log_fh = open(log_path, "ab")
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            log_fh.close()
        return
    kick = run_subprocess(
        ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if kick.returncode != 0:
        raise RuntimeError(
            f"launchctl kickstart helper failed (rc={kick.returncode}): {kick.stderr.strip()}"
        )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _spawn_linux_helper(cmd: list, data_dir: Path, log_path: Path) -> None:
    """Launch helper outside the agent systemd unit cgroup.

    A plain Popen child stays in the service cgroup; when the agent exits
    with status 0, systemd still tears down the cgroup and SIGKILLs the
    helper mid-wait. Prefer ``systemd-run --user --no-block`` (transient
    oneshot). Fall back to double-fork daemonization.
    """
    from ..subprocess_util import run_subprocess

    unit = f"portforge-upgrade-helper-{os.getpid()}.service"
    # Best-effort clear of a prior transient unit with the same name.
    run_subprocess(
        ["systemctl", "--user", "reset-failed", unit],
        capture_output=True,
        text=True,
        timeout=15,
    )
    argv = [
        "systemd-run",
        "--user",
        "--no-block",
        "--collect",
        f"--unit={unit}",
        "--property=Type=oneshot",
        f"--property=Environment=PORTFORGE_DATA_DIR={data_dir}",
        f"--property=Environment=PORTFORGE_LINUX_SERVICE_NAME="
        f"{os.environ.get('PORTFORGE_LINUX_SERVICE_NAME') or 'portforge-agent.service'}",
        *cmd,
    ]
    result = run_subprocess(argv, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return

    # Fallback: classic double-fork so the helper is reparented to init and
    # is not a member of the agent unit cgroup.
    logger = __import__("logging").getLogger("portforge_agent.upgrade.handoff")
    logger.warning(
        "systemd-run spawn failed (rc=%s): %s; falling back to double-fork",
        result.returncode,
        (result.stderr or result.stdout or "").strip(),
    )
    log_fh = open(log_path, "ab")
    try:
        # First fork
        pid = os.fork()
        if pid > 0:
            # Parent: wait for intermediate to exit so we don't leave a zombie.
            os.waitpid(pid, 0)
            return
        # Intermediate
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            os._exit(0)
        # Grandchild: become the helper
        try:
            log_fh.close()
        except Exception:
            pass
        os.environ["PORTFORGE_DATA_DIR"] = str(data_dir)
        os.execv(cmd[0], cmd)
    except Exception:
        try:
            log_fh.close()
        except Exception:
            pass
        raise
    finally:
        try:
            log_fh.close()
        except Exception:
            pass


def _windows_helper_task_name() -> str:
    base = os.environ.get("PORTFORGE_WINDOWS_TASK_NAME") or "PortForge Agent"
    # Fixed suffix — never from Central / handoff payload.
    return f"{base} UpgradeHelper"


def _spawn_windows_helper_task(cmd: list, data_dir: Path) -> None:
    """Create+Run a one-shot Scheduled Task for the typed upgrade helper."""
    from ..subprocess_util import run_subprocess

    wrap = upgrade_dir(data_dir) / "run_helper.cmd"
    wrap.parent.mkdir(parents=True, exist_ok=True)
    # Propagate the service identity env vars the helper needs for restart.
    # Values come only from local process env / PortForge constants — never
    # from Central. Quote each argv element for cmd.exe.
    task_name = os.environ.get("PORTFORGE_WINDOWS_TASK_NAME") or "PortForge Agent"
    quoted = " ".join(f'"{part}"' for part in cmd)
    wrap.write_text(
        "\r\n".join(
            [
                "@echo off",
                f'set "PORTFORGE_DATA_DIR={data_dir}"',
                f'set "PORTFORGE_WINDOWS_TASK_NAME={task_name}"',
                quoted,
                "",
            ]
        ),
        encoding="utf-8",
    )

    task = _windows_helper_task_name()
    # Best-effort delete of a prior helper task (ignore missing).
    run_subprocess(
        ["schtasks", "/Delete", "/TN", task, "/F"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    create = run_subprocess(
        [
            "schtasks",
            "/Create",
            "/TN",
            task,
            "/TR",
            str(wrap),
            "/SC",
            "ONCE",
            "/ST",
            "23:59",
            "/F",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if create.returncode != 0:
        raise RuntimeError(
            f"Failed to create upgrade helper task {task!r}: {create.stderr.strip()}"
        )
    run = run_subprocess(
        ["schtasks", "/Run", "/TN", task],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if run.returncode != 0:
        raise RuntimeError(
            f"Failed to run upgrade helper task {task!r}: {run.stderr.strip()}"
        )
