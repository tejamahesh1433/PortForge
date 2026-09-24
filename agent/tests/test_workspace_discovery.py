"""Static workspace discovery against fixtures/workspace A–J."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from portforge_agent.config_files import ConfigPathError, resolve_within_root
from portforge_agent.workspace.discover import discover_workspace

from .mcp_helpers import WORKSPACE_FIXTURE_DIRS, WORKSPACE_FIXTURE_SECRETS, workspace_fixture_path


def _discover(path: Path, **kwargs):
    defaults = {"include_local_runtime": False}
    defaults.update(kwargs)
    return discover_workspace(path, **defaults)


def _serialized(model) -> str:
    return json.dumps(model.to_dict())


def _host_ports(model, service: str) -> set[int]:
    for svc in model.services:
        if svc.name == service:
            return {
                req.port
                for req in svc.port_requirements
                if req.role == "host" and req.port is not None
            }
    return set()


def _all_host_ports(model) -> set[int]:
    ports: set[int] = set()
    for svc in model.services:
        ports.update(_host_ports(model, svc.name))
    return ports


def _conflict_kinds(model) -> set[str]:
    return {conflict.kind for conflict in model.conflicts}


def _requirements(model, *, role: str | None = None, classification: str | None = None):
    for svc in model.services:
        for req in svc.port_requirements:
            if role is not None and req.role != role:
                continue
            if classification is not None and req.classification != classification:
                continue
            yield req


@pytest.mark.parametrize("fixture_id", list(WORKSPACE_FIXTURE_DIRS))
def test_discover_fixture_smoke(fixture_id: str):
    path = workspace_fixture_path(fixture_id)
    model = _discover(path)
    assert model.project_root == str(path.resolve())
    if fixture_id == "I":
        assert model.warnings
    else:
        assert model.files_parsed >= 1
    assert model.workspace_fingerprint


def test_fixture_a_ports_package_json_and_no_secrets():
    path = workspace_fixture_path("A")
    model = _discover(path)
    serialized = _serialized(model)

    assert _host_ports(model, "app") == {3000}
    assert _host_ports(model, "api") == {4000}
    assert 3000 in _host_ports(model, "A-simple-web") or any(
        req.port == 3000 for req in _requirements(model)
    )
    assert "package.json" in model.fingerprint_inputs

    for secret in WORKSPACE_FIXTURE_SECRETS["A"]:
        assert secret not in serialized


def test_fixture_b_multi_service_compose_dotenv():
    path = workspace_fixture_path("B")
    model = _discover(path)
    names = {svc.name for svc in model.services}
    assert {"frontend", "api", "postgres", "redis"}.issubset(names)
    assert _host_ports(model, "frontend") == {3000}
    assert _host_ports(model, "api") == {8000}
    assert _host_ports(model, "postgres") == {5432}
    assert _host_ports(model, "redis") == {6379}


def test_fixture_c_compose_host_port_mapping():
    path = workspace_fixture_path("C")
    model = _discover(path)
    assert _host_ports(model, "web") == {8080}


def test_compose_expose_is_container_not_host(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  cache:\n    image: redis:7\n    expose:\n      - \"6379\"\n",
        encoding="utf-8",
    )
    model = _discover(tmp_path)
    host = [req for req in _requirements(model, role="host")]
    container = [req for req in _requirements(model, role="container")]
    assert not host
    assert any(req.port == 6379 and req.classification == "UNSUPPORTED" for req in container)


def test_fixture_d_dotenv_only_no_secrets():
    path = workspace_fixture_path("D")
    model = _discover(path)
    serialized = _serialized(model)
    assert _host_ports(model, "app") == {5000}
    assert _host_ports(model, "worker") == {5001}
    for secret in WORKSPACE_FIXTURE_SECRETS["D"]:
        assert secret not in serialized


def test_fixture_e_kubernetes_hostport_mutable_containerport_not():
    path = workspace_fixture_path("E")
    model = _discover(path)
    host_reqs = list(_requirements(model, role="host"))
    container_reqs = list(_requirements(model, role="container", classification="UNSUPPORTED"))
    assert any(req.port == 18080 and req.mutable for req in host_reqs)
    assert any(req.port == 8080 and not req.mutable for req in container_reqs)
    assert any(req.port == 32080 and req.mutable for req in host_reqs)


def test_fixture_f_mixed_compose_dotenv():
    path = workspace_fixture_path("F")
    model = _discover(path)
    assert _host_ports(model, "app") == {9000}


def test_fixture_g_existing_manifest_authoritative():
    path = workspace_fixture_path("G")
    model = _discover(path)
    assert model.existing_manifest is not None
    assert model.existing_manifest["project"] == "sample-stack"
    assert "portforge.yml" in model.fingerprint_inputs
    assert _host_ports(model, "frontend") == {3000}
    assert _host_ports(model, "api") == {8000}


def test_fixture_h_configuration_conflict():
    path = workspace_fixture_path("H")
    model = _discover(path)
    assert "CONFIGURATION_CONFLICT" in _conflict_kinds(model)


def test_fixture_i_malformed_warning_no_crash():
    path = workspace_fixture_path("I")
    model = _discover(path)
    assert model.warnings
    assert any(w.get("kind") == "compose" for w in model.warnings)


def test_fixture_j_monorepo_apps():
    path = workspace_fixture_path("J")
    model = _discover(path)
    assert _host_ports(model, "web") == {5173}
    assert _host_ports(model, "api") == {8080}


def test_empty_directory_returns_no_services(tmp_path):
    model = _discover(tmp_path)
    assert model.services == []
    assert model.conflicts == []


def test_node_modules_ignored_for_fake_ports(tmp_path):
    (tmp_path / "node_modules" / "fake").mkdir(parents=True)
    (tmp_path / "node_modules" / "fake" / ".env").write_text("PORT=9999\n", encoding="utf-8")
    (tmp_path / "node_modules" / "fake" / "docker-compose.yml").write_text(
        'services:\n  x:\n    ports:\n      - "8888:8888"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("PORT=4000\n", encoding="utf-8")
    model = _discover(tmp_path)
    assert _all_host_ports(model) == {4000}


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require elevation on Windows")
def test_path_traversal_resolve_within_root_rejects(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.env").write_text("PORT=7777\n", encoding="utf-8")
    os.symlink(str(outside / "secret.env"), str(project_dir / ".env"))
    with pytest.raises(ConfigPathError):
        resolve_within_root(project_dir, ".env")
    model = _discover(project_dir)
    assert 7777 not in _all_host_ports(model)


def test_dockerfile_expose_not_host_allocation(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM node:20\nEXPOSE 8000\n", encoding="utf-8")
    model = _discover(tmp_path)
    host = list(_requirements(model, role="host"))
    container = list(_requirements(model, role="container"))
    assert not host
    assert any(req.port == 8000 and not req.mutable for req in container)


def test_helm_values_read_only_ambiguous_or_unsupported(tmp_path):
    (tmp_path / "values.yaml").write_text(
        "service:\n  port: 8080\n  nodePort: 32000\n",
        encoding="utf-8",
    )
    model = _discover(tmp_path)
    helm_reqs = [req for req in _requirements(model) if any(ev.kind == "helm" for ev in req.evidence)]
    assert helm_reqs
    assert all(not req.mutable for req in helm_reqs)
    classifications = {req.classification for req in helm_reqs}
    assert classifications <= {"AMBIGUOUS", "UNSUPPORTED"}
