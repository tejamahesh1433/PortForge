"""Phase 8B: manifest parsing/validation tests. Pure and network-free --
manifest.py never talks to Central, so these tests need no mocking.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from portforge_agent.manifest import (
    MAX_MANIFEST_BYTES,
    ManifestError,
    ManifestPortRequest,
    discover_manifest_path,
    load_and_validate_manifest,
    parse_manifest_yaml,
    validate_manifest,
)

VALID_YAML = """
version: 1
project: jarvis
target:
  host: NTMKEYA
ports:
  frontend:
    purpose: frontend
    protocol: tcp
    preferred: 3000
  api:
    purpose: api
    protocol: tcp
    preferred: 8000
  database:
    purpose: postgres
    protocol: tcp
  redis:
    purpose: redis
    protocol: tcp
"""


def test_valid_manifest_normalizes_exactly():
    manifest = validate_manifest(parse_manifest_yaml(VALID_YAML))
    assert manifest.version == 1
    assert manifest.project == "jarvis"
    assert manifest.host == "NTMKEYA"
    assert manifest.request_id is None
    assert manifest.requests == [
        ManifestPortRequest(name="frontend", purpose="frontend", protocol="tcp", preferred_port=3000),
        ManifestPortRequest(name="api", purpose="api", protocol="tcp", preferred_port=8000),
        ManifestPortRequest(name="database", purpose="postgres", protocol="tcp", preferred_port=None),
        ManifestPortRequest(name="redis", purpose="redis", protocol="tcp", preferred_port=None),
    ]


def test_malformed_yaml_fails_safely():
    with pytest.raises(ManifestError) as exc_info:
        parse_manifest_yaml("version: 1\nproject: [unterminated")
    assert exc_info.value.code == "MANIFEST_PARSE_ERROR"


def test_yaml_that_is_not_a_mapping_fails():
    with pytest.raises(ManifestError) as exc_info:
        parse_manifest_yaml("- just\n- a\n- list\n")
    assert exc_info.value.code == "MANIFEST_PARSE_ERROR"


def test_unknown_top_level_field_rejected():
    data = {"version": 1, "project": "x", "target": {"host": "H"}, "ports": {"a": {"purpose": "api"}}, "extra": True}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_unknown_port_entry_field_is_not_silently_ignored():
    """A typo like `protcol: tcp` must fail loudly, not silently become
    the default protocol.
    """
    data = {
        "version": 1,
        "project": "x",
        "target": {"host": "H"},
        "ports": {"a": {"purpose": "api", "protcol": "tcp"}},
    }
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_unknown_target_field_rejected():
    data = {
        "version": 1,
        "project": "x",
        "target": {"host": "H", "region": "us-east"},
        "ports": {"a": {"purpose": "api"}},
    }
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_unsupported_version_rejected_no_fuzzy_migration():
    data = {"version": 999, "project": "x", "target": {"host": "H"}, "ports": {"a": {"purpose": "api"}}}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "UNSUPPORTED_MANIFEST_VERSION"


def test_missing_version_rejected():
    data = {"project": "x", "target": {"host": "H"}, "ports": {"a": {"purpose": "api"}}}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_ports_as_list_rejected_not_silently_coerced():
    """The old Phase 4 `.portforge.yml` `ports:` shape is a LIST -- this
    manifest format requires a MAP and must reject a list rather than
    guessing what was meant.
    """
    data = {
        "version": 1,
        "project": "x",
        "target": {"host": "H"},
        "ports": [{"port": 3000, "service": "frontend"}],
    }
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_empty_ports_rejected():
    data = {"version": 1, "project": "x", "target": {"host": "H"}, "ports": {}}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_too_many_ports_rejected():
    ports = {f"svc{i}": {"purpose": "generic"} for i in range(21)}
    data = {"version": 1, "project": "x", "target": {"host": "H"}, "ports": ports}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_invalid_protocol_rejected():
    data = {"version": 1, "project": "x", "target": {"host": "H"}, "ports": {"a": {"purpose": "api", "protocol": "sctp"}}}
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_invalid_preferred_port_rejected():
    data = {
        "version": 1,
        "project": "x",
        "target": {"host": "H"},
        "ports": {"a": {"purpose": "api", "preferred": 99999}},
    }
    with pytest.raises(ManifestError) as exc_info:
        validate_manifest(data)
    assert exc_info.value.code == "MANIFEST_INVALID"


def test_request_id_field_supported():
    data = {
        "version": 1,
        "project": "x",
        "target": {"host": "H"},
        "ports": {"a": {"purpose": "api"}},
        "request_id": "task-123",
    }
    manifest = validate_manifest(data)
    assert manifest.request_id == "task-123"


def test_manifest_not_found(tmp_path):
    with pytest.raises(ManifestError) as exc_info:
        load_and_validate_manifest(tmp_path / "does-not-exist.yml")
    assert exc_info.value.code == "MANIFEST_NOT_FOUND"


def test_manifest_too_large(tmp_path):
    path = tmp_path / "portforge.yml"
    path.write_text("version: 1\nproject: x\n# " + ("a" * (MAX_MANIFEST_BYTES + 1)), encoding="utf-8")
    with pytest.raises(ManifestError) as exc_info:
        load_and_validate_manifest(path)
    assert exc_info.value.code == "MANIFEST_TOO_LARGE"


def test_discover_manifest_path_prefers_yml_over_yaml(tmp_path):
    (tmp_path / "portforge.yaml").write_text("version: 1", encoding="utf-8")
    (tmp_path / "portforge.yml").write_text("version: 1", encoding="utf-8")
    found = discover_manifest_path(str(tmp_path))
    assert found == tmp_path / "portforge.yml"


def test_discover_manifest_path_does_not_walk_upward(tmp_path):
    (tmp_path / "portforge.yml").write_text("version: 1", encoding="utf-8")
    child = tmp_path / "subdir"
    child.mkdir()
    assert discover_manifest_path(str(child)) is None


def test_discover_manifest_path_none_when_absent(tmp_path):
    assert discover_manifest_path(str(tmp_path)) is None


def test_load_and_validate_manifest_end_to_end(tmp_path):
    path = tmp_path / "portforge.yml"
    path.write_text(VALID_YAML, encoding="utf-8")
    manifest = load_and_validate_manifest(path)
    assert manifest.project == "jarvis"
    assert len(manifest.requests) == 4


def test_old_dotfile_portforge_yml_is_a_different_file_entirely(tmp_path):
    """Phase 4's `.portforge.yml` (dot-prefixed) must never be picked up by
    Phase 8B's discovery -- they are deliberately separate files.
    """
    (tmp_path / ".portforge.yml").write_text("project: legacy\nports: []\n", encoding="utf-8")
    assert discover_manifest_path(str(tmp_path)) is None
