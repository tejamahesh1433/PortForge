"""Public entry point for the detection/enrichment layer.

Collectors and discovery.py only gather and merge RAW FACTS (process
metadata, Docker container metadata). This package interprets those facts
into project/service/purpose/category, each explainable via a
``DetectionInfo`` (confidence, method, evidence) -- and never overwrites a
raw fact to do so; see :func:`enrich_port`.

Note: this module imports ``portforge_agent.models`` at call time inside
:func:`enrich_port`/:func:`enrich_ports`, not at module load time. That's
deliberate, not an oversight: ``portforge_agent.models`` only references
this package's ``DetectionInfo`` under ``TYPE_CHECKING`` (never at runtime),
so there is no real import cycle -- but keeping the import local here makes
that one-directional dependency (detection depends on models, never the
reverse) explicit and independent of import order.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from .evidence import DetectionCache
from .models import Confidence, DetectionFacts, DetectionInfo
from .project import detect_project_for_directory
from .purpose import detect_purpose

__all__ = ["enrich_port", "enrich_ports", "DetectionCache"]

_CONFIDENCE_RANK = {
    Confidence.UNKNOWN: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
}


def _facts_from_port(port) -> DetectionFacts:
    return DetectionFacts(
        source=port.source.value,
        process_name=port.process_name,
        process_path=port.process_path,
        command_line=port.command_line,
        working_directory=port.working_directory,
        parent_process_name=port.parent_process_name,
        parent_working_directory=port.parent_working_directory,
        container_name=port.container_name,
        container_image=port.container_image,
        container_command=port.container_command,
        compose_project=port.docker_compose_project,
        compose_service=port.service_name,
    )


def _combine_confidence(a: Confidence, b: Confidence) -> Confidence:
    """The weaker of the two -- excluding UNKNOWN, since an explicitly
    unknown field (e.g. no project detected) isn't a "weak claim" that
    should drag down confidence in a field we *did* determine; it's an
    honest absence, already visible as `project_name: null`.
    """
    candidates = [c for c in (a, b) if c != Confidence.UNKNOWN]
    if not candidates:
        return Confidence.UNKNOWN
    return min(candidates, key=lambda c: _CONFIDENCE_RANK[c])


def _combine_method(a: str, b: str) -> str:
    parts = [m for m in (a, b) if m and m != "no_evidence"]
    if not parts:
        return "no_evidence"
    ordered_unique: List[str] = []
    for m in parts:
        if m not in ordered_unique:
            ordered_unique.append(m)
    return "+".join(ordered_unique)


def enrich_port(port, cache: Optional[DetectionCache] = None):
    """Return a new DiscoveredPort with project/purpose/category/detection filled in.

    Never mutates `port`. Docker Compose project metadata, when present, is
    always authoritative for project naming and short-circuits native
    filesystem detection entirely (a container without Compose labels is
    never assigned a project name from its container name -- see
    agent/README.md "Docker project detection").
    """
    from ..models import Source  # local import: see module docstring

    cache = cache if cache is not None else DetectionCache()
    facts = _facts_from_port(port)

    if port.docker_compose_project:
        project_name = port.docker_compose_project
        project_confidence = Confidence.HIGH
        project_method = "docker_compose"
        project_evidence = [f"com.docker.compose.project={port.docker_compose_project}"]
        if port.service_name:
            project_evidence.append(f"com.docker.compose.service={port.service_name}")
        manifest_dependencies = set()
    elif port.source == Source.DOCKER:
        # A Docker container with no Compose labels: the container name is
        # NOT automatically a project name (see project.py's docstring).
        project_name = None
        project_confidence = Confidence.UNKNOWN
        project_method = "no_evidence"
        project_evidence = []
        manifest_dependencies = set()
    else:
        start_dir = port.working_directory or port.parent_working_directory
        project_result = detect_project_for_directory(start_dir, cache)
        project_name = project_result.project_name
        project_confidence = project_result.confidence
        project_method = project_result.method
        project_evidence = project_result.evidence
        manifest_dependencies = project_result.manifest_dependencies

    purpose_result = detect_purpose(facts, manifest_dependencies)

    overall_confidence = _combine_confidence(project_confidence, purpose_result.confidence)
    combined_evidence = list(project_evidence) + list(purpose_result.evidence)
    combined_method = _combine_method(project_method, purpose_result.method)

    detection = DetectionInfo(
        confidence=overall_confidence,
        method=combined_method,
        evidence=combined_evidence,
    )

    return dataclasses.replace(
        port,
        project_name=project_name,
        purpose=purpose_result.purpose,
        category=purpose_result.category,
        detection=detection,
    )


def enrich_ports(ports: List) -> List:
    """Enrich a whole scan's worth of ports, sharing one DetectionCache so
    repeated manifest reads and project-root lookups across ports are only
    done once (see evidence.DetectionCache).
    """
    cache = DetectionCache()
    return [enrich_port(port, cache) for port in ports]
