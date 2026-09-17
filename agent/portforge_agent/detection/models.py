"""Shared types for the detection/enrichment layer.

Kept separate from ``portforge_agent.models`` (the raw discovery model) so
the distinction between RAW FACTS (what a collector actually observed) and
INFERRED metadata (what detection concluded, and why) is structural, not
just a naming convention. Nothing in this module -- or anywhere in this
package -- reads from disk, the network, or a subprocess; it only reasons
over facts handed to it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set


class Confidence(str, Enum):
    """How much to trust an inferred field.

    Not a numeric score: there is no documented model that would make e.g.
    0.73 vs 0.68 meaningful here, so we use four understandable buckets
    instead (see agent/README.md "Confidence meanings" for what each one
    is supposed to promise the caller).
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class DetectionInfo:
    """Explains an inferred field: how confident, by what method, from what evidence."""

    confidence: Confidence = Confidence.UNKNOWN
    method: str = "no_evidence"
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence.value,
            "method": self.method,
            "evidence": list(self.evidence),
        }


@dataclass
class DetectionFacts:
    """Normalized view of the raw facts detection reasons over.

    Built once per DiscoveredPort (see detection/__init__.py) from the raw
    fact fields already on it -- this never invents a fact, only reshapes
    what already exists for convenient rule matching.
    """

    source: str  # "process" / "docker" / "system"

    process_name: Optional[str] = None
    process_path: Optional[str] = None
    command_line: Optional[List[str]] = None
    working_directory: Optional[str] = None
    parent_process_name: Optional[str] = None
    parent_working_directory: Optional[str] = None

    container_name: Optional[str] = None
    container_image: Optional[str] = None
    container_command: Optional[List[str]] = None
    compose_project: Optional[str] = None
    compose_service: Optional[str] = None


@dataclass
class ProjectDetectionResult:
    project_name: Optional[str] = None
    confidence: Confidence = Confidence.UNKNOWN
    method: str = "no_evidence"
    evidence: List[str] = field(default_factory=list)
    # Dependency names discovered while looking for a project name (e.g.
    # package.json "dependencies" keys), handed to purpose detection so a
    # manifest already read for one question doesn't get re-read for
    # another. Populated across the whole bounded ancestor chain, not just
    # the directory that ultimately won project naming.
    manifest_dependencies: Set[str] = field(default_factory=set)


@dataclass
class PurposeDetectionResult:
    purpose: Optional[str] = None
    category: str = "unknown"
    confidence: Confidence = Confidence.UNKNOWN
    method: str = "no_evidence"
    evidence: List[str] = field(default_factory=list)
