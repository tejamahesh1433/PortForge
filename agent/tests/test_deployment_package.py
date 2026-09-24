"""Phase 18: deployment package fetch/extract safety tests."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portforge_agent.deployment.package import (
    PackageChecksumError,
    PackageExtractError,
    PackageValidationError,
    _TrustedRedirectHandler,
    build_local_package,
    extract_archive_safe,
    fetch_package,
)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def test_build_local_package_deterministic(tmp_path):
    files = [
        ("docker-compose.yml", b"services:\n  api:\n    image: demo\n"),
        ("override.yml", b"services:\n  api:\n    ports:\n      - '8080:80'\n"),
    ]
    archive1, pkg_sha1, manifest_sha1 = build_local_package(
        files, compose_files=["docker-compose.yml", "override.yml"], dest_archive=tmp_path / "a.zip"
    )
    archive2, pkg_sha2, manifest_sha2 = build_local_package(
        files, compose_files=["docker-compose.yml", "override.yml"], dest_archive=tmp_path / "b.zip"
    )
    assert pkg_sha1 == pkg_sha2
    assert manifest_sha1 == manifest_sha2
    assert archive1.read_bytes() == archive2.read_bytes()


def test_extract_archive_safe_verifies_manifest_and_files(tmp_path):
    compose = b"services:\n  web:\n    image: nginx\n"
    archive, _, manifest_sha = build_local_package(
        [("docker-compose.yml", compose)],
        compose_files=["docker-compose.yml"],
        dest_archive=tmp_path / "package.zip",
    )
    dest = tmp_path / "extracted"
    manifest = extract_archive_safe(archive, dest, expected_manifest_sha256=manifest_sha)
    assert manifest["compose_files"] == ["docker-compose.yml"]
    assert (dest / "docker-compose.yml").read_bytes() == compose


def test_extract_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.zip"
    _write_zip(
        archive_path,
        {
            "manifest.json": b'{"files":[],"compose_files":[]}',
            "../escape.txt": b"nope",
        },
    )
    with pytest.raises(PackageExtractError, match="escape"):
        extract_archive_safe(archive_path, tmp_path / "out")


def test_extract_rejects_absolute_posix_path(tmp_path):
    archive_path = tmp_path / "abs.zip"
    _write_zip(
        archive_path,
        {
            "manifest.json": b'{"files":[],"compose_files":[]}',
            "/etc/passwd": b"nope",
        },
    )
    with pytest.raises(PackageExtractError, match="absolute path"):
        extract_archive_safe(archive_path, tmp_path / "out")


def test_extract_rejects_absolute_windows_path(tmp_path):
    archive_path = tmp_path / "winabs.zip"
    _write_zip(
        archive_path,
        {
            "manifest.json": b'{"files":[],"compose_files":[]}',
            "C:/Windows/system32/evil.txt": b"nope",
        },
    )
    with pytest.raises(PackageExtractError, match="absolute path"):
        extract_archive_safe(archive_path, tmp_path / "out")


def test_extract_rejects_unc_backslash_path(tmp_path):
    """\\\\server\\share\\... UNC paths (backslash form) must be rejected."""
    archive_path = tmp_path / "unc_back.zip"
    _write_zip(
        archive_path,
        {
            "manifest.json": b'{"files":[],"compose_files":[]}',
            "\\\\server\\share\\evil.txt": b"nope",
        },
    )
    with pytest.raises(PackageExtractError, match="absolute path"):
        extract_archive_safe(archive_path, tmp_path / "out")


def test_extract_rejects_unc_slash_path(tmp_path):
    """//server/share/... UNC paths (forward-slash form) must be rejected."""
    archive_path = tmp_path / "unc_slash.zip"
    _write_zip(
        archive_path,
        {
            "manifest.json": b'{"files":[],"compose_files":[]}',
            "//server/share/evil.txt": b"nope",
        },
    )
    with pytest.raises(PackageExtractError, match="absolute path"):
        extract_archive_safe(archive_path, tmp_path / "out")


def test_extract_manifest_checksum_mismatch(tmp_path):
    compose = b"services: {}\n"
    archive, _, _ = build_local_package(
        [("docker-compose.yml", compose)],
        dest_archive=tmp_path / "package.zip",
    )
    with pytest.raises(PackageChecksumError, match="Manifest SHA-256 mismatch"):
        extract_archive_safe(archive, tmp_path / "out", expected_manifest_sha256="a" * 64)


def test_fetch_package_checksum_mismatch(tmp_path):
    dest = tmp_path / "pkg.zip"
    body = b"payload"
    response = MagicMock()
    response.read.side_effect = [body, b""]
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("urllib.request.OpenerDirector.open", return_value=response):
        with pytest.raises(PackageChecksumError, match="Package SHA-256 mismatch"):
            fetch_package(
                "https://artifacts.example.com/pkg.zip",
                "b" * 64,
                dest,
                allowed_hosts=frozenset({"artifacts.example.com"}),
            )


def test_fetch_package_rejects_http(tmp_path):
    with pytest.raises(PackageValidationError, match="https"):
        fetch_package("http://artifacts.example.com/pkg.zip", "a" * 64, tmp_path / "pkg.zip")


def test_fetch_package_rejects_disallowed_host(tmp_path):
    with pytest.raises(PackageValidationError, match="allowlist"):
        fetch_package(
            "https://evil.example.com/pkg.zip",
            "a" * 64,
            tmp_path / "pkg.zip",
            allowed_hosts=frozenset({"artifacts.example.com"}),
        )


def test_trusted_redirect_handler_rejects_disallowed_destination():
    """Redirect targets must pass the same HTTPS + allowlist policy as the origin URI."""
    import urllib.request

    handler = _TrustedRedirectHandler(frozenset({"artifacts.example.com"}))
    req = urllib.request.Request("https://artifacts.example.com/pkg.zip")
    with pytest.raises(PackageValidationError, match="allowlist"):
        handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "https://evil.example.com/pkg.zip",
        )


def test_trusted_redirect_handler_rejects_http_destination():
    import urllib.request

    handler = _TrustedRedirectHandler(frozenset())
    req = urllib.request.Request("https://artifacts.example.com/pkg.zip")
    with pytest.raises(PackageValidationError, match="https"):
        handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "http://artifacts.example.com/pkg.zip",
        )
