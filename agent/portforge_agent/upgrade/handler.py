"""Agent-side safe upgrade execution handler (Phase 10 / Phase 23).

Control model (from docs/design/agent-upgrade-management.md and
docs/design/self-upgrade-bootstrap.md):
  - Upgrade payload arrives via the authenticated heartbeat response.
  - Only structured fields are accepted: target_version, artifact_url,
    artifact_sha256, artifact_filename, id. No command/script/executable.
  - URL must be https; download is bounded (50 MB ceiling).
  - SHA-256 is verified before any installation -- mismatch fails closed.
  - pip install and service restart are performed by a detached helper
    (portforge_agent.upgrade.helper) spawned after SHA verify, so the
    running agent never pip-installs while holding its own files and
    never drives the service-manager call that kills itself.
  - Restart uses a hard-coded platform adapter inside the helper (never
    payload-derived).
  - Identity files (host.json, credentials) are never touched.

Phase 23 status transitions reported to Central during a successful run:
  DOWNLOADING -> VERIFYING -> RESTARTING -> (helper installs, new process)

After RESTARTING is reported the handoff record is written and the detached
helper is spawned. Central s heartbeat reconciliation advances the state to
VERIFYING_HEALTH -> SUCCEEDED once the expected agent_version is confirmed.
Central - not the old agent process - owns those final transitions.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("portforge_agent.upgrade")

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# Bounded download: prevents a maliciously large artifact from exhausting disk.
_DOWNLOAD_TIMEOUT = 120  # seconds
_MAX_WHEEL_BYTES = 50 * 1024 * 1024  # 50 MB
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_BASE = 2   # seconds; cap at 30s
_DOWNLOAD_BACKOFF_CAP = 30


class UpgradeValidationError(ValueError):
    """Raised when the pending_upgrade payload fails pre-download validation."""


class UpgradeSHA256Error(ValueError):
    """Raised when the downloaded artifact's SHA-256 does not match the manifest."""


class UpgradeTransientDownloadError(OSError):
    """Raised for transient network failures (timeout, connection reset, 5xx).

    These are eligible for bounded retry with exponential backoff.
    Permanent errors (4xx, bad URL, local address rejected) raise RuntimeError.
    """

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_version_tuple(version_str: str):
    """Parse '1.3.0' (or '1.3.0rc1', '1.4.0-rc1') into a comparable int tuple.

    Each dot/dash-separated segment is scanned for a leading numeric prefix.
    Pre-release label suffixes like 'rc1' within a segment are ignored so
    '1.4.0rc1' yields (1, 4, 0). Segments with no leading digit stop the
    scan. Raises ValueError for strings with no leading numeric component.
    """
    numbers = []
    for part in re.split(r"[.\-]", version_str):
        m = re.match(r"^(\d+)", part)
        if m:
            numbers.append(int(m.group(1)))
        else:
            break
    if not numbers:
        raise ValueError(f"Cannot parse version: {version_str!r}")
    return tuple(numbers)


def _validate_payload(
    pending: Dict[str, Any],
    current_version: str,
    allow_downgrade: bool = False,
) -> None:
    """Validate the structured upgrade payload received from Central.

    Pure data validation -- no network I/O, no filesystem access.

    Checks in order:
    1. Reject any 'command', 'script', 'executable', or 'shell' fields.
    2. artifact_sha256 must be exactly 64 hex characters.
    3. artifact_url must use https and must not point to localhost.
    4. target_version must be parseable.
    5. target_version must be >= current_version (unless allow_downgrade=True).

    Raises UpgradeValidationError on any failure.
    """
    # 1. Reject forbidden fields -- Central must never send these.
    for banned in ("command", "script", "executable", "shell"):
        if banned in pending:
            raise UpgradeValidationError(
                f"Upgrade payload contains disallowed field {banned!r} -- rejected."
            )

    # 2. SHA-256 format check.
    sha256 = pending.get("artifact_sha256", "")
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise UpgradeValidationError(
            f"Invalid artifact_sha256: must be exactly 64 hex characters, got {sha256!r}"
        )

    # 3. URL validation.
    url = pending.get("artifact_url", "")
    if not isinstance(url, str) or not url.strip():
        raise UpgradeValidationError("artifact_url is missing or empty")
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UpgradeValidationError(
            f"artifact_url must use https scheme, got {parsed.scheme!r}: {url}"
        )
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise UpgradeValidationError(
            f"artifact_url points to a local/internal address: {url}"
        )

    # 4. target_version parseability.
    target_version = pending.get("target_version", "")
    if not isinstance(target_version, str) or not target_version.strip():
        raise UpgradeValidationError("target_version is missing or empty")
    try:
        target_tuple = _parse_version_tuple(target_version)
    except ValueError as exc:
        raise UpgradeValidationError(f"target_version not parseable: {exc}") from exc

    # 5. Version ordering (upgrade path).
    if not allow_downgrade:
        try:
            current_tuple = _parse_version_tuple(current_version)
        except ValueError:
            current_tuple = ()
        if current_tuple and target_tuple < current_tuple:
            raise UpgradeValidationError(
                f"Refusing downgrade: target {target_version!r} < current {current_version!r}. "
                "Use the admin rollback path for explicit downgrades."
            )


def _download_artifact(url: str, dest_path: Path) -> None:
    """Stream-download the artifact from the validated https URL to dest_path.

    Enforces _MAX_WHEEL_BYTES to prevent disk exhaustion from an oversized response.

    Raises:
        UpgradeTransientDownloadError: on timeout, connection reset, or HTTP 5xx.
            Caller should retry with backoff.
        RuntimeError: on permanent failures (HTTP 4xx, non-HTTP errors that are
            not connection-level).
    """
    req = urllib.request.Request(url, headers={"User-Agent": "PortForge-Agent-Upgrade/1"})
    try:
        response = urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code is not None and exc.code >= 500:
            raise UpgradeTransientDownloadError(
                f"HTTP {exc.code} from artifact server (transient): {exc}"
            ) from exc
        raise RuntimeError(f"HTTP {exc.code} downloading artifact (permanent): {exc}") from exc
    except urllib.error.URLError as exc:
        # URLError wraps socket.timeout, ConnectionReset, DNS failures, etc.
        raise UpgradeTransientDownloadError(
            f"Network error downloading artifact (transient): {exc.reason}"
        ) from exc

    with response:
        total = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_WHEEL_BYTES:
                    raise RuntimeError(
                        f"Artifact exceeds maximum size limit of "
                        f"{_MAX_WHEEL_BYTES // (1024 * 1024)} MB"
                    )
                f.write(chunk)


def _verify_sha256(path: Path, expected_hex: str) -> None:
    """Compute the SHA-256 of the file at path and compare to expected_hex.

    Raises UpgradeSHA256Error on mismatch -- always fails closed (no install).
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        raise UpgradeSHA256Error(
            f"SHA-256 mismatch -- artifact may be corrupt or tampered with. "
            f"Expected {expected_hex.lower()}, got {actual.lower()}"
        )


def _install_wheel(wheel_path: Path) -> None:
    """Thin wrapper kept for backward compat with tests and qualification.

    Phase 23: run_upgrade no longer calls this directly; the detached helper
    calls handoff.install_wheel() with the recorded python_executable.
    """
    from .handoff import install_wheel
    install_wheel(sys.executable, wheel_path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_upgrade(
    client: Any,
    host_id: str,
    pending: Dict[str, Any],
    current_version: str,
    allow_downgrade: bool = False,
    data_dir: Optional[Path] = None,
) -> bool:
    """Execute a safe, structured upgrade received from Central.

    Arguments:
        client:          CentralClient instance used for status reporting.
        host_id:         This host's UUID string (for logging).
        pending:         The pending_upgrade dict from the heartbeat response.
        current_version: The currently installed agent version string.
        allow_downgrade: If True, skip the version ordering check. Use only
                         for the admin rollback path (same install/restart code).

    Returns True when the restart was successfully triggered (process should
    then exit), False on any validation or installation failure.

    Status transitions reported to Central:
        DOWNLOADING → VERIFYING → INSTALLING → RESTARTING  (then new process)
        FAILED (with failure_reason) on any error.

    Never touches identity files (host.json, credentials). Never evaluates
    any string from the Central payload as a shell command.
    """
    upgrade_id = str(pending.get("id", ""))
    target_version = str(pending.get("target_version", ""))

    def _report(state: str, failure_reason: Optional[str] = None) -> None:
        try:
            client.report_upgrade_status(
                upgrade_id=upgrade_id,
                state=state,
                failure_reason=failure_reason,
            )
        except Exception as exc:
            logger.warning("Could not report upgrade status %s: %s", state, exc)

    # Validate -- no I/O, no network, no filesystem.
    try:
        _validate_payload(pending, current_version, allow_downgrade=allow_downgrade)
    except UpgradeValidationError as exc:
        logger.error("Upgrade validation failed for host %s: %s", host_id, exc)
        _report("FAILED", failure_reason=str(exc))
        return False

    artifact_url = str(pending.get("artifact_url", ""))
    artifact_sha256 = str(pending.get("artifact_sha256", ""))
    raw_filename = pending.get("artifact_filename")
    if not raw_filename:
        from urllib.parse import urlparse

        raw_filename = Path(urlparse(artifact_url).path).name or "artifact.whl"
    # Sanitize filename: strip any path components to prevent path traversal.
    safe_filename = Path(str(raw_filename)).name or "artifact.whl"

    tmp_dir = None
    tmp_path = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="portforge-upgrade-")
        tmp_path = Path(tmp_dir) / safe_filename

        # DOWNLOADING — up to _DOWNLOAD_MAX_ATTEMPTS with exponential backoff
        # for transient network errors.  SHA/install errors are never retried.
        _report("DOWNLOADING")
        logger.info(
            "Upgrade: downloading %s from %s (host=%s)",
            target_version, artifact_url, host_id,
        )
        download_error: Optional[Exception] = None
        for attempt in range(_DOWNLOAD_MAX_ATTEMPTS):
            if attempt > 0:
                delay = min(_DOWNLOAD_BACKOFF_BASE ** attempt, _DOWNLOAD_BACKOFF_CAP)
                logger.info("Upgrade: download attempt %d/%d, backoff %.0fs", attempt + 1, _DOWNLOAD_MAX_ATTEMPTS, delay)
                time.sleep(delay)
            # Delete any partial file from the previous attempt
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            try:
                _download_artifact(artifact_url, tmp_path)
                download_error = None
                break
            except UpgradeTransientDownloadError as exc:
                download_error = exc
                logger.warning("Upgrade download transient error (attempt %d/%d): %s", attempt + 1, _DOWNLOAD_MAX_ATTEMPTS, exc)
                # continue to retry
            except Exception as exc:
                # Permanent error — do not retry
                download_error = exc
                logger.error("Upgrade download permanent error: %s", exc)
                break

        if download_error is not None:
            logger.error("Upgrade download failed after %d attempt(s): %s", _DOWNLOAD_MAX_ATTEMPTS, download_error)
            _report("FAILED", failure_reason=f"Download failed: {download_error}")
            return False

        # VERIFYING
        _report("VERIFYING")
        try:
            _verify_sha256(tmp_path, artifact_sha256)
        except UpgradeSHA256Error as exc:
            logger.error("Upgrade SHA-256 verification failed: %s", exc)
            _report("FAILED", failure_reason=str(exc))
            return False

        # Phase 23: copy artifact to upgrade area, write typed handoff, spawn
        # the detached helper that installs + restarts after this process exits.
        import uuid as _uuid
        from datetime import datetime, timezone
        from .handoff import prepare_handoff_artifact, write_handoff, spawn_upgrade_helper

        try:
            stored_wheel = prepare_handoff_artifact(
                tmp_path,
                artifact_sha256,
                data_dir,
                filename=safe_filename if str(safe_filename).endswith(".whl") else None,
            )
            record = {
                "schema_version": 1,
                "upgrade_id": upgrade_id,
                "host_id": host_id,
                "expected_current_version": current_version,
                "target_version": target_version,
                "artifact_filename": stored_wheel.name,
                "artifact_sha256": artifact_sha256,
                "python_executable": sys.executable,
                "old_pid": os.getpid(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "nonce": str(_uuid.uuid4()),
                "stage": "prepared",
            }
            write_handoff(record, data_dir)
        except Exception as exc:
            logger.error("Upgrade handoff prepare failed: %s", exc)
            _report("FAILED", failure_reason=f"Handoff prepare failed: {exc}")
            return False

        _report("RESTARTING")
        logger.info("Upgrade: handoff prepared, spawning helper for %s", target_version)

        try:
            spawn_upgrade_helper(data_dir)
        except Exception as exc:
            logger.error("Upgrade: failed to spawn helper: %s", exc)
            _report("FAILED", failure_reason=f"Helper spawn failed: {exc}")
            return False

        logger.info("Upgrade to %s: helper spawned, agent process exiting.", target_version)
        return True

    finally:
        # Clean up temp files. On the success path the process is about to
        # exit anyway, but clean up defensively on all failure paths.
        try:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if tmp_dir is not None:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
