"""Tests for the safety-bounded manifest reading / traversal helpers."""
import json

import pytest

from portforge_agent.detection.evidence import (
    DetectionCache,
    MAX_MANIFEST_BYTES,
    extract_cargo_toml_name,
    extract_go_mod_name,
    extract_json_name,
    extract_package_json_dependencies,
    extract_pom_xml_name,
    extract_portforge_json_project,
    extract_portforge_yaml_project,
    extract_pyproject_dependencies,
    extract_pyproject_name,
    extract_requirements_txt_dependencies,
)


@pytest.fixture
def project_tree(tmp_path):
    """
    tmp_path/
      repo/                 (.git here)
        backend/
          app/               (deepest: this is our "start_dir")
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    backend = repo / "backend"
    backend.mkdir()
    app_dir = backend / "app"
    app_dir.mkdir()
    return {"tmp": tmp_path, "repo": repo, "backend": backend, "app": app_dir}


def test_candidates_for_includes_start_dir_and_ancestors(project_tree):
    cache = DetectionCache(max_depth=6)
    candidates = cache.candidates_for(str(project_tree["app"]))
    assert candidates[0] == project_tree["app"].resolve()
    assert project_tree["backend"].resolve() in candidates
    assert project_tree["repo"].resolve() in candidates


def test_candidates_for_is_bounded_by_max_depth(project_tree):
    cache = DetectionCache(max_depth=1)
    candidates = cache.candidates_for(str(project_tree["app"]))
    # depth 0 (app) + depth 1 (backend) = 2 entries, repo (depth 2) excluded
    assert len(candidates) == 2
    assert project_tree["repo"].resolve() not in candidates


def test_candidates_for_missing_directory_returns_empty(tmp_path):
    cache = DetectionCache()
    missing = tmp_path / "does" / "not" / "exist"
    # .resolve() still works for a non-existent path; list_dir() will just
    # come back empty for it -- candidates_for itself doesn't require
    # existence, only a resolvable path.
    candidates = cache.candidates_for(str(missing))
    assert candidates[0] == missing.resolve()


def test_candidates_for_empty_start_dir_returns_empty():
    cache = DetectionCache()
    assert cache.candidates_for(None) == []
    assert cache.candidates_for("") == []


def test_candidates_for_stops_at_home_directory(monkeypatch, tmp_path):
    """Real bug found during Phase 3 validation: a process whose cwd falls
    under $HOME must never have the walk reach (or cross) $HOME itself,
    since incidental files sitting loose there (observed in practice: a
    stray package.json/requirements.txt in a real user's home directory)
    would otherwise misattribute unrelated processes to a bogus project
    named after the home directory.
    """
    fake_home = tmp_path / "home" / "someuser"
    fake_home.mkdir(parents=True)
    (fake_home / "package.json").write_text('{"name": "should-never-be-used"}', encoding="utf-8")
    project_dir = fake_home / "projects" / "real-app"
    project_dir.mkdir(parents=True)

    import portforge_agent.detection.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "_home_directory_cache", {})
    monkeypatch.setattr(evidence_module.Path, "home", classmethod(lambda cls: fake_home))

    cache = DetectionCache(max_depth=10)
    candidates = cache.candidates_for(str(project_dir))

    assert fake_home.resolve() not in candidates
    assert candidates == [project_dir.resolve(), (fake_home / "projects").resolve()]


def test_list_dir_missing_directory_returns_empty_frozenset(tmp_path):
    cache = DetectionCache()
    assert cache.list_dir(tmp_path / "nope") == frozenset()


def test_list_dir_is_cached(tmp_path, monkeypatch):
    cache = DetectionCache()
    real_dir = tmp_path / "a"
    real_dir.mkdir()

    calls = []
    original_iterdir = type(real_dir).iterdir

    def counting_iterdir(self):
        calls.append(1)
        return original_iterdir(self)

    monkeypatch.setattr(type(real_dir), "iterdir", counting_iterdir)

    cache.list_dir(real_dir)
    cache.list_dir(real_dir)
    assert len(calls) == 1


def test_read_json_valid(tmp_path):
    path = tmp_path / "package.json"
    path.write_text(json.dumps({"name": "my-app"}), encoding="utf-8")

    cache = DetectionCache()
    data = cache.read_json(path)
    assert data == {"name": "my-app"}


def test_read_json_malformed_does_not_raise(tmp_path):
    path = tmp_path / "package.json"
    path.write_text("{not valid json!!", encoding="utf-8")

    cache = DetectionCache()
    assert cache.read_json(path) is None


def test_read_json_non_object_top_level_returns_none(tmp_path):
    path = tmp_path / "package.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    cache = DetectionCache()
    assert cache.read_json(path) is None


def test_read_json_missing_file_returns_none(tmp_path):
    cache = DetectionCache()
    assert cache.read_json(tmp_path / "missing.json") is None


def test_read_json_huge_file_is_skipped_not_parsed(tmp_path):
    path = tmp_path / "package.json"
    huge_name = "x" * (MAX_MANIFEST_BYTES + 1024)
    path.write_text(json.dumps({"name": huge_name}), encoding="utf-8")

    cache = DetectionCache()
    assert cache.read_json(path) is None


def test_read_toml_valid(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "my-service"\n', encoding="utf-8")

    cache = DetectionCache()
    data = cache.read_toml(path)
    assert data["project"]["name"] == "my-service"


def test_read_toml_malformed_does_not_raise(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text("this is not [valid toml", encoding="utf-8")

    cache = DetectionCache()
    assert cache.read_toml(path) is None


def test_read_toml_huge_file_is_skipped(tmp_path):
    path = tmp_path / "pyproject.toml"
    huge_value = "x" * (MAX_MANIFEST_BYTES + 1024)
    path.write_text(f'[project]\nname = "{huge_value}"\n', encoding="utf-8")

    cache = DetectionCache()
    assert cache.read_toml(path) is None


def test_read_json_caches_result(tmp_path, monkeypatch):
    path = tmp_path / "package.json"
    path.write_text('{"name": "a"}', encoding="utf-8")
    cache = DetectionCache()

    calls = []
    original_loads = json.loads

    def counting_loads(*args, **kwargs):
        calls.append(1)
        return original_loads(*args, **kwargs)

    monkeypatch.setattr("portforge_agent.detection.evidence.json.loads", counting_loads)

    cache.read_json(path)
    cache.read_json(path)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Manifest content extraction
# ---------------------------------------------------------------------------


def test_extract_json_name():
    assert extract_json_name({"name": " my-app "}) == "my-app"
    assert extract_json_name({"name": ""}) is None
    assert extract_json_name({}) is None
    assert extract_json_name(None) is None
    assert extract_json_name({"name": 123}) is None


def test_extract_package_json_dependencies():
    data = {
        "dependencies": {"react": "^18.0.0", "vite": "^5.0.0"},
        "devDependencies": {"eslint": "^9.0.0"},
    }
    deps = extract_package_json_dependencies(data)
    assert deps == {"react", "vite", "eslint"}


def test_extract_pyproject_name_from_project_table():
    assert extract_pyproject_name({"project": {"name": "my-service"}}) == "my-service"


def test_extract_pyproject_name_from_poetry_table():
    assert extract_pyproject_name({"tool": {"poetry": {"name": "poetry-app"}}}) == "poetry-app"


def test_extract_pyproject_name_missing():
    assert extract_pyproject_name({}) is None
    assert extract_pyproject_name(None) is None


def test_extract_pyproject_dependencies_pep621():
    data = {"project": {"dependencies": ["fastapi>=0.100", "uvicorn[standard]==0.30.0"]}}
    deps = extract_pyproject_dependencies(data)
    assert "fastapi" in deps
    assert "uvicorn" in deps


def test_extract_pyproject_dependencies_poetry():
    data = {"tool": {"poetry": {"dependencies": {"python": "^3.11", "flask": "^3.0"}}}}
    deps = extract_pyproject_dependencies(data)
    assert deps == {"flask"}  # "python" itself is excluded


def test_extract_requirements_txt_dependencies():
    text = "fastapi==0.110.0\n# a comment\n\nuvicorn[standard]>=0.30\n-r other.txt\nDjango~=5.0\n"
    deps = extract_requirements_txt_dependencies(text)
    assert deps == {"fastapi", "uvicorn", "django"}


def test_extract_requirements_txt_dependencies_none_text():
    assert extract_requirements_txt_dependencies(None) == set()


def test_extract_cargo_toml_name():
    assert extract_cargo_toml_name({"package": {"name": "my-crate"}}) == "my-crate"
    assert extract_cargo_toml_name({}) is None


def test_extract_go_mod_name():
    text = "module github.com/example/my-service\n\ngo 1.22\n"
    assert extract_go_mod_name(text) == "my-service"


def test_extract_go_mod_name_missing():
    assert extract_go_mod_name("go 1.22\n") is None
    assert extract_go_mod_name(None) is None


def test_extract_pom_xml_name_skips_parent_artifact_id():
    text = """
    <project>
      <parent>
        <artifactId>parent-pom</artifactId>
      </parent>
      <artifactId>my-service</artifactId>
    </project>
    """
    assert extract_pom_xml_name(text) == "my-service"


def test_extract_pom_xml_name_missing():
    assert extract_pom_xml_name("<project></project>") is None
    assert extract_pom_xml_name(None) is None


def test_extract_portforge_json_project():
    assert extract_portforge_json_project({"project": "my-explicit-name"}) == "my-explicit-name"
    assert extract_portforge_json_project({}) is None
    assert extract_portforge_json_project(None) is None


def test_extract_portforge_yaml_project():
    assert extract_portforge_yaml_project("project: my-app\nother: value\n") == "my-app"
    assert extract_portforge_yaml_project('project: "quoted-name"\n') == "quoted-name"
    assert extract_portforge_yaml_project("other: value\n") is None
    assert extract_portforge_yaml_project(None) is None
