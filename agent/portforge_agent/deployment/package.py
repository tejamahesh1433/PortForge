"""Constrained deployment package fetch, verify, and safe extract."""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DOWNLOAD_TIMEOUT = 120
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024
_MANIFEST_NAME = "manifest.json"


class PackageValidationError(ValueError):
    """Raised when a package URL or metadata fails validation."""


class PackageChecksumError(ValueError):
    """Raised when downloaded or extracted content fails checksum verification."""


class PackageExtractError(ValueError):
    """Raised when archive extraction fails safety checks."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_uri(uri: str, allowed_hosts: FrozenSet[str]) -> None:
    from urllib.parse import urlparse

    if not isinstance(uri, str) or not uri.strip():
        raise PackageValidationError("package_uri is missing or empty")
    parsed = urlparse(uri)
    if parsed.scheme != "https":
        raise PackageValidationError(f"package_uri must use https scheme, got {parsed.scheme!r}")
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise PackageValidationError(f"package_uri points to a local/internal address: {uri}")
    if allowed_hosts and hostname not in allowed_hosts:
        raise PackageValidationError(f"package_uri host {hostname!r} is not in the approved allowlist")


def _validate_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise PackageValidationError(f"Invalid {field_name}: must be exactly 64 hex characters")


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only when the destination still passes URI policy."""

    def __init__(self, allowed_hosts: FrozenSet[str]):
        super().__init__()
        self._allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validate_uri(newurl, self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_package(
    uri: str,
    expected_sha256: str,
    dest_path: Path,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    allowed_hosts: FrozenSet[str] = frozenset(),
) -> None:
    """Download a deployment package over HTTPS with size and checksum guards."""
    _validate_uri(uri, allowed_hosts)
    _validate_sha256(expected_sha256, "expected_sha256")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(uri, headers={"User-Agent": "PortForge-Agent-Deployment/1"})
    opener = urllib.request.build_opener(_TrustedRedirectHandler(allowed_hosts))
    with opener.open(req, timeout=_DOWNLOAD_TIMEOUT) as response:
        total = 0
        with open(dest_path, "wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PackageValidationError(
                        f"Package exceeds maximum size limit of {max_bytes // (1024 * 1024)} MB"
                    )
                handle.write(chunk)

    actual = _sha256_file(dest_path)
    if actual.lower() != expected_sha256.lower():
        raise PackageChecksumError(
            f"Package SHA-256 mismatch: expected {expected_sha256.lower()}, got {actual.lower()}"
        )


def _safe_member_path(dest_dir: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise PackageExtractError(f"Archive entry escapes destination: {member_name!r}")
    target = (dest_dir / normalized).resolve(strict=False)
    root = dest_dir.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PackageExtractError(
            f"Archive entry {member_name!r} resolves outside {root}"
        ) from exc
    return target


def _extract_zip(archive_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = _safe_member_path(dest_dir, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                out.write(source.read())


def _extract_tar(archive_path: Path, dest_dir: Path) -> None:
    with tarfile.open(archive_path, mode="r:*") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                link_target = member.linkname or ""
                if link_target.startswith("/") or ".." in Path(link_target).parts:
                    raise PackageExtractError(
                        f"Archive symlink/link escape rejected: {member.name!r} -> {link_target!r}"
                    )
            if member.isdir():
                _safe_member_path(dest_dir, member.name).mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg():
                continue
            target = _safe_member_path(dest_dir, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            with extracted as source, open(target, "wb") as out:
                out.write(source.read())


def extract_archive_safe(
    archive_path: Path,
    dest_dir: Path,
    *,
    expected_manifest_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract zip/tar archive with path confinement and optional manifest verify."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.name.lower()
    if suffix.endswith(".zip"):
        _extract_zip(archive_path, dest_dir)
    elif suffix.endswith((".tar.gz", ".tgz", ".tar")):
        _extract_tar(archive_path, dest_dir)
    else:
        raise PackageExtractError(f"Unsupported archive format: {archive_path.name}")

    manifest_path = dest_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackageExtractError(f"Extracted package missing required {_MANIFEST_NAME}")

    manifest_bytes = manifest_path.read_bytes()
    if expected_manifest_sha256 is not None:
        _validate_sha256(expected_manifest_sha256, "expected_manifest_sha256")
        actual = _sha256_bytes(manifest_bytes)
        if actual.lower() != expected_manifest_sha256.lower():
            raise PackageChecksumError(
                "Manifest SHA-256 mismatch: "
                f"expected {expected_manifest_sha256.lower()}, got {actual.lower()}"
            )

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageExtractError(f"Invalid {_MANIFEST_NAME}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PackageExtractError(f"{_MANIFEST_NAME} must be a JSON object")

    for entry in manifest.get("files") or []:
        if not isinstance(entry, dict):
            continue
        relative = entry.get("path")
        expected_file_sha = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_file_sha, str):
            continue
        file_path = _safe_member_path(dest_dir, relative)
        if not file_path.is_file():
            raise PackageExtractError(f"Manifest lists missing file: {relative}")
        actual_file_sha = _sha256_file(file_path)
        if actual_file_sha.lower() != expected_file_sha.lower():
            raise PackageChecksumError(
                f"File {relative} SHA-256 mismatch: "
                f"expected {expected_file_sha.lower()}, got {actual_file_sha.lower()}"
            )

    return manifest


def build_local_package(
    files: Sequence[tuple[str, bytes]],
    *,
    compose_files: Optional[Sequence[str]] = None,
    health_checks: Optional[Sequence[Dict[str, Any]]] = None,
    dest_archive: Optional[Path] = None,
) -> tuple[Path, str, str]:
    """Build a deterministic zip package for tests. Returns (archive, package_sha256, manifest_sha256)."""
    if not files:
        raise ValueError("files must not be empty")

    ordered = sorted(files, key=lambda item: item[0])
    manifest_files: List[Dict[str, Any]] = []
    for relative, content in ordered:
        manifest_files.append(
            {
                "path": relative.replace("\\", "/"),
                "sha256": _sha256_bytes(content),
                "size": len(content),
            }
        )

    compose = list(compose_files) if compose_files is not None else []
    if not compose:
        compose = [manifest_files[0]["path"]]

    manifest: Dict[str, Any] = {
        "version": 1,
        "files": manifest_files,
        "compose_files": compose,
        "health_checks": list(health_checks or ()),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    tmp_dir = tempfile.mkdtemp(prefix="portforge-deploy-pkg-")
    staging = Path(tmp_dir)
    try:
        for relative, content in ordered:
            target = staging / relative.replace("\\", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        (staging / _MANIFEST_NAME).write_bytes(manifest_bytes)

        if dest_archive is None:
            dest_archive = staging / "package.zip"
        with zipfile.ZipFile(dest_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file() and path != dest_archive:
                    archive.write(path, arcname=path.relative_to(staging).as_posix())

        package_sha256 = _sha256_file(dest_archive)
        return dest_archive, package_sha256, manifest_sha256
    except Exception:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
