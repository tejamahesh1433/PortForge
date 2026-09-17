"""Tests for persistent host identity (Phase 5)."""
import json
import uuid

import pytest

from portforge_agent import identity


def test_first_run_creates_identity(tmp_path):
    path = tmp_path / "host.json"
    assert identity.load_host_identity(path) is None

    created = identity.get_or_create_host_identity(path)
    assert created.host_id
    uuid.UUID(created.host_id)  # valid UUID format
    assert path.exists()


def test_identity_persists_across_separate_calls(tmp_path):
    path = tmp_path / "host.json"
    first = identity.get_or_create_host_identity(path)
    second = identity.get_or_create_host_identity(path)
    assert first.host_id == second.host_id


def test_identity_survives_simulated_restart(tmp_path):
    """Simulate a process restart: load fresh from disk in a brand new call
    with no in-memory state carried over, and confirm the exact same UUID
    comes back.
    """
    path = tmp_path / "host.json"
    original = identity.get_or_create_host_identity(path)

    # "Restart": nothing but the file on disk is used for this second call.
    reloaded = identity.load_host_identity(path)
    assert reloaded is not None
    assert reloaded.host_id == original.host_id
    assert reloaded.created_at == original.created_at


def test_identity_written_atomically_no_leftover_temp_files(tmp_path):
    path = tmp_path / "host.json"
    identity.get_or_create_host_identity(path)
    leftover = list(tmp_path.glob(".host-*.tmp"))
    assert leftover == []


def test_malformed_json_raises_and_is_not_replaced(tmp_path):
    path = tmp_path / "host.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(identity.HostIdentityError):
        identity.load_host_identity(path)

    # Critically: get_or_create must NOT silently generate a new identity
    # over a malformed file -- that could make this host appear as two
    # different machines to a central server.
    with pytest.raises(identity.HostIdentityError):
        identity.get_or_create_host_identity(path)

    assert path.read_text(encoding="utf-8") == "{not valid json"


def test_unsupported_schema_version_raises(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(
        json.dumps({"schema_version": 999, "host_id": str(uuid.uuid4()), "created_at": "2025-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    with pytest.raises(identity.HostIdentityError):
        identity.load_host_identity(path)


def test_invalid_uuid_format_raises(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(
        json.dumps({"schema_version": 1, "host_id": "not-a-uuid", "created_at": "2025-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    with pytest.raises(identity.HostIdentityError):
        identity.load_host_identity(path)


def test_missing_required_field_raises(tmp_path):
    path = tmp_path / "host.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(identity.HostIdentityError):
        identity.load_host_identity(path)


def test_empty_file_treated_as_absent(tmp_path):
    path = tmp_path / "host.json"
    path.write_text("", encoding="utf-8")
    assert identity.load_host_identity(path) is None
    # get_or_create should be able to create a fresh identity over a
    # genuinely empty file (not malformed, just never written).
    created = identity.get_or_create_host_identity(path)
    assert created.host_id


def test_hostname_change_does_not_change_host_id(tmp_path, monkeypatch):
    """Simulates a hostname change -- never actually renames this machine."""
    path = tmp_path / "host.json"
    monkeypatch.setattr("portforge_agent.identity.pf.get_hostname", lambda: "original-hostname")
    original = identity.get_or_create_host_identity(path)

    monkeypatch.setattr("portforge_agent.identity.pf.get_hostname", lambda: "renamed-hostname")
    after_rename = identity.get_or_create_host_identity(path)

    assert after_rename.host_id == original.host_id
    assert after_rename.hostname_at_creation == "original-hostname"  # historical record unchanged


def test_get_host_id_uses_identity_path(tmp_path, monkeypatch):
    from portforge_agent import platform as pf

    monkeypatch.setattr("portforge_agent.paths.data_dir", lambda: tmp_path)
    host_id = pf.get_host_id()

    on_disk = identity.load_host_identity(tmp_path / "host.json")
    assert on_disk is not None
    assert on_disk.host_id == host_id
