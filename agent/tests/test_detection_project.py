"""Tests for native (filesystem-based) project detection."""
import json

from portforge_agent.detection.evidence import DetectionCache
from portforge_agent.detection.models import Confidence
from portforge_agent.detection.project import detect_project_for_directory


def test_package_json_name_wins_at_high_confidence(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "my-dashboard"}), encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())

    assert result.project_name == "my-dashboard"
    assert result.confidence == Confidence.HIGH
    assert result.method == "package_json_name"
    assert any("package.json" in e for e in result.evidence)


def test_pyproject_toml_name_wins_at_high_confidence(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-api"\n', encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())

    assert result.project_name == "my-api"
    assert result.confidence == Confidence.HIGH
    assert result.method == "pyproject_toml_name"


def test_manifest_name_outranks_directory_name_even_when_both_present(tmp_path):
    # directory is named "frontend" but package.json declares a real name --
    # per the project brief, the declared name is stronger evidence.
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "package.json").write_text(json.dumps({"name": "my-dashboard"}), encoding="utf-8")

    result = detect_project_for_directory(str(frontend_dir), DetectionCache())
    assert result.project_name == "my-dashboard"


def test_git_root_fallback_when_no_manifest_name(tmp_path):
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "src"
    src.mkdir()

    result = detect_project_for_directory(str(src), DetectionCache())

    assert result.project_name == "my-repo"
    assert result.confidence == Confidence.MEDIUM
    assert result.method == "git_root"


def test_directory_name_fallback_for_generic_marker(tmp_path):
    project = tmp_path / "some-service"
    project.mkdir()
    (project / "requirements.txt").write_text("flask\n", encoding="utf-8")

    result = detect_project_for_directory(str(project), DetectionCache())

    assert result.project_name == "some-service"
    assert result.confidence == Confidence.LOW
    assert result.method == "directory_marker"


def test_unknown_when_no_marker_found(tmp_path):
    empty = tmp_path / "just-a-folder"
    empty.mkdir()

    result = detect_project_for_directory(str(empty), DetectionCache(max_depth=0))

    assert result.project_name is None
    assert result.confidence == Confidence.UNKNOWN
    assert result.method == "no_evidence"


def test_portforge_manifest_outranks_manifest_name(tmp_path):
    # .portforge.json two levels up should win over a package.json name one
    # level up -- tier 2 beats tier 3 regardless of which is closer.
    (tmp_path / ".portforge.json").write_text(json.dumps({"project": "explicit-name"}), encoding="utf-8")
    mid = tmp_path / "services"
    mid.mkdir()
    leaf = mid / "api"
    leaf.mkdir()
    (leaf / "package.json").write_text(json.dumps({"name": "package-name"}), encoding="utf-8")

    result = detect_project_for_directory(str(leaf), DetectionCache(max_depth=6))

    assert result.project_name == "explicit-name"
    assert result.method == "portforge_manifest"
    assert result.confidence == Confidence.HIGH


def test_portforge_yaml_manifest_supported(tmp_path):
    (tmp_path / ".portforge.yml").write_text("project: yaml-project\n", encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())
    assert result.project_name == "yaml-project"
    assert result.method == "portforge_manifest"


def test_bounded_traversal_does_not_find_marker_beyond_max_depth(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "too-far"}), encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)

    result = detect_project_for_directory(str(deep), DetectionCache(max_depth=2))

    assert result.project_name is None
    assert result.confidence == Confidence.UNKNOWN


def test_bounded_traversal_finds_marker_within_max_depth(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "just-far-enough"}), encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)

    result = detect_project_for_directory(str(deep), DetectionCache(max_depth=2))

    assert result.project_name == "just-far-enough"


def test_inaccessible_directory_does_not_raise(tmp_path, monkeypatch):
    target = tmp_path / "locked"
    target.mkdir()

    def _raise(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(type(target), "iterdir", _raise)

    result = detect_project_for_directory(str(target), DetectionCache())
    assert result.project_name is None
    assert result.confidence == Confidence.UNKNOWN


def test_none_start_dir_does_not_raise():
    result = detect_project_for_directory(None, DetectionCache())
    assert result.project_name is None
    assert result.confidence == Confidence.UNKNOWN


def test_malformed_package_json_falls_back_gracefully(tmp_path):
    (tmp_path / "package.json").write_text("{ this is not json", encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())

    # package.json exists but couldn't be parsed for a name -> falls through
    # to directory-marker tier using package.json's mere presence.
    assert result.project_name == tmp_path.name
    assert result.confidence == Confidence.LOW
    assert result.method == "directory_marker"


def test_malformed_pyproject_toml_falls_back_gracefully(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not [ valid toml =", encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())

    assert result.project_name == tmp_path.name
    assert result.confidence == Confidence.LOW
    assert result.method == "directory_marker"


def test_huge_package_json_is_not_parsed_and_falls_back(tmp_path):
    huge = {"name": "x" * 2_000_000}
    (tmp_path / "package.json").write_text(json.dumps(huge), encoding="utf-8")

    result = detect_project_for_directory(str(tmp_path), DetectionCache())

    # The file is real and present (still a valid marker for the fallback
    # tier) but far too large to safely parse for its declared name.
    assert result.project_name == tmp_path.name
    assert result.method == "directory_marker"


def test_manifest_dependencies_collected_regardless_of_which_tier_wins_naming(tmp_path):
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"vite": "^5.0.0"}}), encoding="utf-8"
    )
    # No "name" field, so naming falls through to git_root -- but the
    # dependency should still be collected for purpose detection to use.
    src = repo / "src"
    src.mkdir()

    result = detect_project_for_directory(str(src), DetectionCache())

    assert result.method == "git_root"
    assert "vite" in result.manifest_dependencies


def test_cargo_toml_and_go_mod_and_composer_and_pom_names(tmp_path):
    cargo_dir = tmp_path / "cargo"
    cargo_dir.mkdir()
    (cargo_dir / "Cargo.toml").write_text('[package]\nname = "rust-svc"\n', encoding="utf-8")
    assert detect_project_for_directory(str(cargo_dir), DetectionCache()).project_name == "rust-svc"

    go_dir = tmp_path / "go"
    go_dir.mkdir()
    (go_dir / "go.mod").write_text("module github.com/example/go-svc\n", encoding="utf-8")
    assert detect_project_for_directory(str(go_dir), DetectionCache()).project_name == "go-svc"

    composer_dir = tmp_path / "composer"
    composer_dir.mkdir()
    (composer_dir / "composer.json").write_text(json.dumps({"name": "php-svc"}), encoding="utf-8")
    assert detect_project_for_directory(str(composer_dir), DetectionCache()).project_name == "php-svc"

    pom_dir = tmp_path / "pom"
    pom_dir.mkdir()
    (pom_dir / "pom.xml").write_text("<project><artifactId>java-svc</artifactId></project>", encoding="utf-8")
    assert detect_project_for_directory(str(pom_dir), DetectionCache()).project_name == "java-svc"
