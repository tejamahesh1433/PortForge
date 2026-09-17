"""Tests for configuration: defaults, user overrides, project config
discovery/precedence, and malformed-input handling.
"""
import json

from portforge_agent.config import (
    DEFAULT_RANGES,
    PortForgeConfig,
    find_project_config,
    load_config,
)


def test_defaults_when_no_config_file_present(tmp_path, monkeypatch):
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [tmp_path / "config.yml"])
    config = load_config()
    assert config.ranges == DEFAULT_RANGES
    assert config.exclusions == []


def test_is_excluded():
    config = PortForgeConfig()
    config.exclusions = []
    from portforge_agent.config import Exclusion

    config.exclusions = [Exclusion(22, 22), Exclusion(50000, 50100)]
    assert config.is_excluded(22) is True
    assert config.is_excluded(50050) is True
    assert config.is_excluded(8000) is False


def test_user_config_json_overrides_range_and_adds_exclusion(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "ranges": {"internal-api": {"start": 8500, "end": 8599}},
                "exclude": ["22", "50000-50100"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [config_path])

    config = load_config()
    assert config.range_for("internal-api").start == 8500
    assert config.range_for("internal-api").end == 8599
    # defaults are preserved, not replaced
    assert config.range_for("api") == DEFAULT_RANGES["api"]
    assert config.is_excluded(22) is True
    assert config.is_excluded(50050) is True
    assert config.is_excluded(9999) is False


def test_user_config_yaml_supported(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "ranges:\n  frontend:\n    start: 4000\n    end: 4099\nexclude:\n  - 3389\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [config_path])

    config = load_config()
    assert config.range_for("frontend").start == 4000
    assert config.range_for("frontend").end == 4099
    assert config.is_excluded(3389) is True


def test_malformed_config_file_falls_back_to_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [config_path])

    config = load_config()
    assert config.ranges == DEFAULT_RANGES  # never crashes, degrades to defaults


def test_malformed_individual_range_entry_is_skipped_not_fatal(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"ranges": {"broken": {"start": "not-a-number"}, "api": {"start": 9000, "end": 9099}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [config_path])

    config = load_config()
    assert "broken" not in config.ranges
    assert config.range_for("api").start == 9000


def test_malformed_exclusion_entries_are_skipped(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"exclude": ["not-a-port", "22", None]}), encoding="utf-8")
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [config_path])

    config = load_config()
    assert config.is_excluded(22) is True
    assert len(config.exclusions) == 1


def test_explicit_path_overrides_candidate_search(tmp_path):
    config_path = tmp_path / "custom.json"
    config_path.write_text(json.dumps({"exclude": ["9999"]}), encoding="utf-8")

    config = load_config(explicit_path=config_path)
    assert config.is_excluded(9999) is True


def test_first_existing_candidate_wins(tmp_path, monkeypatch):
    yml_path = tmp_path / "config.yml"
    json_path = tmp_path / "config.json"
    yml_path.write_text("exclude:\n  - 111\n", encoding="utf-8")
    json_path.write_text(json.dumps({"exclude": ["222"]}), encoding="utf-8")
    monkeypatch.setattr("portforge_agent.config.paths.config_path_candidates", lambda: [yml_path, json_path])

    config = load_config()
    assert config.is_excluded(111) is True
    assert config.is_excluded(222) is False


# ---------------------------------------------------------------------------
# Project config discovery (.portforge.json/.yml)
# ---------------------------------------------------------------------------


def test_find_project_config_json(tmp_path):
    (tmp_path / ".portforge.json").write_text(
        json.dumps({"project": "deeptrace", "ports": [{"port": 8003, "service": "api"}]}), encoding="utf-8"
    )
    result = find_project_config(str(tmp_path))
    assert result["project"] == "deeptrace"
    assert result["ports"][0]["port"] == 8003


def test_find_project_config_yaml_with_ports_list(tmp_path):
    (tmp_path / ".portforge.yml").write_text(
        "project: deeptrace\nports:\n  - port: 8003\n    service: api\n    purpose: api\n"
        "  - port: 3002\n    service: frontend\n",
        encoding="utf-8",
    )
    result = find_project_config(str(tmp_path))
    assert result["project"] == "deeptrace"
    assert len(result["ports"]) == 2
    assert result["ports"][0]["port"] == 8003
    assert result["ports"][1]["service"] == "frontend"


def test_find_project_config_walks_up_bounded(tmp_path):
    (tmp_path / ".portforge.json").write_text(json.dumps({"project": "root-project"}), encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    result = find_project_config(str(nested))
    assert result["project"] == "root-project"


def test_find_project_config_none_when_absent(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_project_config(str(empty)) is None


def test_unrelated_project_config_in_different_directory_is_not_used(tmp_path):
    other = tmp_path / "other-project"
    other.mkdir()
    (other / ".portforge.json").write_text(json.dumps({"project": "unrelated"}), encoding="utf-8")

    mine = tmp_path / "my-project"
    mine.mkdir()

    result = find_project_config(str(mine))
    assert result is None
