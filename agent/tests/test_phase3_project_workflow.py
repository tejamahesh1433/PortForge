from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import yaml
from portforge_agent.cli import main
from portforge_agent.manifest import ManifestError, parse_manifest_yaml, validate_manifest


def test_phase3_init_minimal_and_existing_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["project", "init", "--json"]) == 0
    data = yaml.safe_load((tmp_path / "portforge.yml").read_text())
    assert data["project"] == tmp_path.name
    assert data["ports"] == {}
    assert main(["project", "init", "--json"]) == 2


def test_phase3_init_nested_discovery(tmp_path, monkeypatch, capsys):
    (tmp_path / "portforge.yml").write_text("version: 1\nproject: demo\nports: {}\n")
    nested = tmp_path / "src" / "api"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert main(["project", "validate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project"] == "demo"


def test_phase3_range_and_preferred_consistency():
    manifest = validate_manifest(parse_manifest_yaml("""
version: 1
project: demo
ports:
  api:
    purpose: api
    preferred: 8127
    range: 8000-8999
"""))
    assert manifest.requests[0].requested_range == "8000-8999"
    for bad in ("0-100", "9000-8000", "65535-65536"):
        try:
            validate_manifest({"version": 1, "project": "demo", "ports": {"api": {"purpose": "api", "range": bad}}})
        except ManifestError:
            pass
        else:
            raise AssertionError(bad)


def test_phase3_status_central_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "portforge.yml").write_text("version: 1\nproject: demo\nports:\n  api:\n    purpose: api\n")
    monkeypatch.delenv("PORTFORGE_CENTRAL_URL", raising=False)
    with patch("portforge_agent.cli._allocation_client", return_value=(None, "offline")):
        result = main(["project", "status", "--json"])
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_valid"] is True
    assert payload["central_available"] is False
    assert payload["services"][0]["allocation_status"] == "UNALLOCATED"


def test_phase3_status_mixed_allocations(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "portforge.yml").write_text("version: 1\nproject: demo\ntarget:\n  host: H\nports:\n  api:\n    purpose: api\n  worker:\n    purpose: generic\n")
    client = MagicMock()
    client.list_allocations.return_value = MagicMock(success=True, data={"items": [{"allocation_id": "a1", "project": "demo", "status": "active", "host": {"hostname": "H"}, "allocations": [{"name": "api", "port": 8127, "bind_probe": "verified"}]}]})
    with patch("portforge_agent.central_client.CentralClient", return_value=client):
        assert main(["project", "status", "--url", "http://central", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {row["service"]: row for row in payload["services"]}
    assert rows["api"]["allocation_status"] == "active"
    assert rows["worker"]["allocation_status"] == "UNALLOCATED"
