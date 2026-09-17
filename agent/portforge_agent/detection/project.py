"""Native (filesystem-based) project detection.

This module is only ever consulted for native processes. Docker Compose
project metadata is authoritative on its own and is handled directly by
detection/__init__.py without touching the filesystem at all -- see its
module docstring, and section 3 of the Phase 2/3 design in
agent/README.md ("Do not infer the project solely from a container name if
authoritative Compose metadata exists").

Priority order (highest first) -- see agent/README.md "Evidence hierarchy":

  1. Docker Compose project label            (handled by the caller, not here)
  2. an explicit ``.portforge.json``/``.portforge.yml`` project override
  3. a project manifest's own declared name (package.json, pyproject.toml,
     Cargo.toml, go.mod, composer.json, pom.xml)
  4. the name of the nearest ancestor directory containing a ``.git`` folder
  5. the name of the nearest ancestor directory containing any other
     recognized project marker
  6. unknown

Each tier is evaluated across the *entire* bounded ancestor chain before
falling through to the next tier, so e.g. an explicit ``.portforge.json``
two levels up always outranks a ``package.json`` name one level up --
depth only breaks ties *within* a tier.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set, Tuple

from .evidence import (
    DetectionCache,
    PORTFORGE_JSON_MANIFEST,
    PORTFORGE_YAML_MANIFESTS,
    PROJECT_MARKERS,
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
from .models import Confidence, ProjectDetectionResult

_Candidate = Tuple[Path, frozenset]


def detect_project_for_directory(
    start_dir: Optional[str], cache: DetectionCache
) -> ProjectDetectionResult:
    candidates = cache.candidates_for(start_dir)
    usable: List[_Candidate] = [
        (directory, cache.list_dir(directory)) for directory in candidates
    ]
    usable = [(d, e) for d, e in usable if e]

    if not usable:
        return ProjectDetectionResult()

    result = _check_portforge_manifest(usable, cache)
    if result is None:
        result = _check_declared_manifest_name(usable, cache)
    if result is None:
        result = _check_git_root(usable)
    if result is None:
        result = _check_directory_marker(usable)

    dependencies = _collect_manifest_dependencies(usable, cache)

    if result is None:
        return ProjectDetectionResult(manifest_dependencies=dependencies)

    project_name, confidence, method, evidence = result
    return ProjectDetectionResult(
        project_name=project_name,
        confidence=confidence,
        method=method,
        evidence=evidence,
        manifest_dependencies=dependencies,
    )


def _check_portforge_manifest(
    usable: List[_Candidate], cache: DetectionCache
) -> Optional[Tuple[str, Confidence, str, List[str]]]:
    for directory, entries in usable:
        if PORTFORGE_JSON_MANIFEST in entries:
            data = cache.read_json(directory / PORTFORGE_JSON_MANIFEST)
            name = extract_portforge_json_project(data)
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "portforge_manifest",
                    [f"{PORTFORGE_JSON_MANIFEST} at {directory} declares project={name}"],
                )
        for yaml_name in PORTFORGE_YAML_MANIFESTS:
            if yaml_name in entries:
                text = cache.read_text(directory / yaml_name)
                name = extract_portforge_yaml_project(text)
                if name:
                    return (
                        name,
                        Confidence.HIGH,
                        "portforge_manifest",
                        [f"{yaml_name} at {directory} declares project={name}"],
                    )
    return None


def _check_declared_manifest_name(
    usable: List[_Candidate], cache: DetectionCache
) -> Optional[Tuple[str, Confidence, str, List[str]]]:
    for directory, entries in usable:
        if "package.json" in entries:
            name = extract_json_name(cache.read_json(directory / "package.json"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "package_json_name",
                    [f"package.json at {directory} declares name={name}"],
                )
        if "pyproject.toml" in entries:
            name = extract_pyproject_name(cache.read_toml(directory / "pyproject.toml"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "pyproject_toml_name",
                    [f"pyproject.toml at {directory} declares name={name}"],
                )
        if "Cargo.toml" in entries:
            name = extract_cargo_toml_name(cache.read_toml(directory / "Cargo.toml"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "cargo_toml_name",
                    [f"Cargo.toml at {directory} declares name={name}"],
                )
        if "go.mod" in entries:
            name = extract_go_mod_name(cache.read_text(directory / "go.mod"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "go_mod_name",
                    [f"go.mod at {directory} declares a module ending in {name}"],
                )
        if "composer.json" in entries:
            name = extract_json_name(cache.read_json(directory / "composer.json"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "composer_json_name",
                    [f"composer.json at {directory} declares name={name}"],
                )
        if "pom.xml" in entries:
            name = extract_pom_xml_name(cache.read_text(directory / "pom.xml"))
            if name:
                return (
                    name,
                    Confidence.HIGH,
                    "pom_xml_name",
                    [f"pom.xml at {directory} declares artifactId={name}"],
                )
    return None


def _check_git_root(
    usable: List[_Candidate],
) -> Optional[Tuple[str, Confidence, str, List[str]]]:
    for directory, entries in usable:
        if ".git" in entries:
            return (
                directory.name,
                Confidence.MEDIUM,
                "git_root",
                [f".git directory found at {directory}"],
            )
    return None


def _check_directory_marker(
    usable: List[_Candidate],
) -> Optional[Tuple[str, Confidence, str, List[str]]]:
    for directory, entries in usable:
        matched = sorted(entries & PROJECT_MARKERS)
        if matched:
            return (
                directory.name,
                Confidence.LOW,
                "directory_marker",
                [f"project marker '{matched[0]}' found at {directory}"],
            )
    return None


def _collect_manifest_dependencies(usable: List[_Candidate], cache: DetectionCache) -> Set[str]:
    deps: Set[str] = set()
    for directory, entries in usable:
        if "package.json" in entries:
            deps |= extract_package_json_dependencies(cache.read_json(directory / "package.json"))
        if "pyproject.toml" in entries:
            deps |= extract_pyproject_dependencies(cache.read_toml(directory / "pyproject.toml"))
        if "requirements.txt" in entries:
            deps |= extract_requirements_txt_dependencies(
                cache.read_text(directory / "requirements.txt")
            )
    return deps
