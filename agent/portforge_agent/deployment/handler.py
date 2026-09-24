"""Agent-side constrained deployment execution handler (Phase 18)."""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional
from urllib.parse import urlparse

from .. import platform as pf
from .compose_adapter import (
    ComposeApplyError,
    ComposeValidationError,
    DockerUnavailableError,
    apply as compose_apply,
    compose_project_name,
    inspect_status,
    validate as compose_validate,
)
from .health import evaluate_health
from .package import (
    PackageChecksumError,
    PackageExtractError,
    PackageValidationError,
    extract_archive_safe,
    fetch_package,
)
from .store import activate_revision, remove_revision, revision_dir, stage_revision_dir

logger = logging.getLogger("portforge_agent.deployment")

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DEFAULT_ALLOWED_HOSTS: FrozenSet[str] = frozenset()
_MAX_PACKAGE_BYTES = 100 * 1024 * 1024


class DeploymentValidationError(ValueError):
    """Raised when pending_deployment payload fails validation."""


def _validate_pending(pending: Dict[str, Any]) -> None:
    for banned in ("command", "script", "executable", "shell", "argv", "args"):
        if banned in pending:
            raise DeploymentValidationError(
                f"Deployment payload contains disallowed field {banned!r} -- rejected."
            )

    for field in ("package_sha256", "package_manifest_sha256"):
        value = pending.get(field, "")
        if not isinstance(value, str) or not _SHA256_RE.match(value):
            raise DeploymentValidationError(f"Invalid {field}: must be exactly 64 hex characters")

    uri = pending.get("package_uri", "")
    if not isinstance(uri, str) or not uri.strip():
        raise DeploymentValidationError("package_uri is missing or empty")
    parsed = urlparse(uri)
    if parsed.scheme != "https":
        raise DeploymentValidationError(f"package_uri must use https scheme, got {parsed.scheme!r}")
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise DeploymentValidationError(f"package_uri points to a local/internal address: {uri}")

    for required in ("deployment_id", "project", "environment", "request_id"):
        if not pending.get(required):
            raise DeploymentValidationError(f"{required} is missing or empty")


def process_pending_deployment(pending: Dict[str, Any], client: Any) -> bool:
    """Claim, fetch, extract, validate, apply, and health-check a deployment job."""
    deployment_id = str(pending.get("deployment_id", ""))
    project = str(pending.get("project", ""))
    environment = str(pending.get("environment", ""))
    host_id = pf.get_host_id()
    revision_id = str(pending.get("revision_id") or pending.get("request_id") or deployment_id)

    try:
        _validate_pending(pending)
    except DeploymentValidationError as exc:
        logger.error("Deployment validation failed: %s", exc)
        return False

    claim_token = pending.get("claim_token")
    if isinstance(claim_token, str) and claim_token.strip():
        token = claim_token.strip()
    else:
        token = ""

    def _status(
        state: str,
        *,
        failure_code: Optional[str] = None,
        failure_reason: Optional[str] = None,
        revision_id: Optional[str] = None,
    ) -> None:
        if not token:
            return
        try:
            kwargs: Dict[str, Any] = {
                "claim_token": token,
                "state": state,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
            }
            if revision_id is not None:
                kwargs["revision_id"] = revision_id
            client.deployment_status(deployment_id, **kwargs)
        except Exception as exc:
            logger.warning("Could not report deployment status %s: %s", state, exc)

    def _health(health: Dict[str, Any]) -> None:
        if not token:
            return
        try:
            client.deployment_health(deployment_id, claim_token=token, health=health)
        except Exception as exc:
            logger.warning("Could not report deployment health: %s", exc)

    def _fail(code: str, reason: str) -> bool:
        logger.error("Deployment %s failed (%s): %s", deployment_id, code, reason)
        _status("FAILED", failure_code=code, failure_reason=reason)
        remove_revision(project, environment, deployment_id, revision_id)
        return False

    if not token:
        try:
            claim_result = client.claim_deployment(deployment_id)
            if not claim_result.success:
                logger.warning(
                    "Deployment claim failed for %s: %s",
                    deployment_id,
                    claim_result.error,
                )
                return False
            claim_body = claim_result.data if isinstance(claim_result.data, dict) else {}
            token = str(claim_body.get("claim_token") or "")
            if not token:
                logger.warning("Deployment claim returned no claim_token for %s", deployment_id)
                return False
        except Exception as exc:
            logger.warning("Deployment claim raised for %s: %s", deployment_id, exc)
            return False

    package_uri = str(pending.get("package_uri", ""))
    package_sha256 = str(pending.get("package_sha256", ""))
    manifest_sha256 = str(pending.get("package_manifest_sha256", ""))

    tmp_dir: Optional[str] = None
    archive_path: Optional[Path] = None

    try:
        _status("TRANSFERRING")
        tmp_dir = tempfile.mkdtemp(prefix="portforge-deployment-")
        archive_path = Path(tmp_dir) / "package.zip"

        try:
            fetch_package(
                package_uri,
                package_sha256,
                archive_path,
                max_bytes=_MAX_PACKAGE_BYTES,
                allowed_hosts=_DEFAULT_ALLOWED_HOSTS,
            )
        except PackageChecksumError as exc:
            return _fail("DEPLOYMENT_CHECKSUM_MISMATCH", str(exc))
        except PackageValidationError as exc:
            return _fail("DEPLOYMENT_PACKAGE_INVALID", str(exc))
        except Exception as exc:
            return _fail("DEPLOYMENT_TRANSFER_FAILED", f"Download failed: {exc}")

        _status("PREPARING")
        try:
            rev_dir = stage_revision_dir(project, environment, deployment_id, revision_id)
        except FileExistsError:
            rev_dir = revision_dir(project, environment, deployment_id, revision_id)
            rev_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return _fail("DEPLOYMENT_STORE_FAILED", f"Could not stage revision: {exc}")

        try:
            manifest = extract_archive_safe(
                archive_path,
                rev_dir,
                expected_manifest_sha256=manifest_sha256,
            )
        except PackageChecksumError as exc:
            return _fail("DEPLOYMENT_CHECKSUM_MISMATCH", str(exc))
        except (PackageExtractError, PackageValidationError) as exc:
            return _fail("DEPLOYMENT_EXTRACT_FAILED", str(exc))

        compose_files = manifest.get("compose_files") or []
        if not isinstance(compose_files, list):
            return _fail("DEPLOYMENT_MANIFEST_INVALID", "compose_files must be a list")
        compose_files = [str(item) for item in compose_files if item]

        health_checks = manifest.get("health_checks") or []
        if not isinstance(health_checks, list):
            health_checks = []

        project_name = compose_project_name(project, environment, host_id)
        workdir = rev_dir

        _status("STARTING")
        try:
            compose_validate(compose_files, project_name, workdir)
        except DockerUnavailableError as exc:
            return _fail("DOCKER_UNAVAILABLE", str(exc))
        except ComposeValidationError as exc:
            return _fail("COMPOSE_VALIDATION_FAILED", str(exc))

        try:
            compose_apply(compose_files, project_name, workdir)
        except DockerUnavailableError as exc:
            return _fail("DOCKER_UNAVAILABLE", str(exc))
        except ComposeApplyError as exc:
            return _fail("COMPOSE_APPLY_FAILED", str(exc))

        _status("VERIFYING")
        compose_status = inspect_status(compose_files, project_name, workdir)
        health = evaluate_health(compose_status, health_checks)
        _health(health)

        overall = health.get("overall")
        if overall == "UNHEALTHY":
            return _fail("DEPLOYMENT_UNHEALTHY", "Declared health checks failed")

        activate_revision(project, environment, deployment_id, revision_id)
        _status("SUCCEEDED", revision_id=revision_id)
        logger.info(
            "Deployment %s succeeded for %s/%s (revision=%s, health=%s)",
            deployment_id,
            project,
            environment,
            revision_id,
            overall,
        )
        return True

    finally:
        try:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
