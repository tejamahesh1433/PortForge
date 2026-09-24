from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


Classification = Literal["EXPLICIT", "INFERRED", "AMBIGUOUS", "UNSUPPORTED"]
Confidence = Literal["high", "medium", "low"]
PortRole = Literal["host", "container", "unknown"]
ConflictKind = Literal[
    "INTERNAL_PROJECT_CONFLICT",
    "LOCAL_RUNTIME_CONFLICT",
    "CENTRAL_ALLOCATION_CONFLICT",
    "RESERVATION_CONFLICT",
    "CONFIGURATION_CONFLICT",
]


def _sorted_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _normalize(value) for key, value in sorted(data.items())}


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return _sorted_dict(value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


@dataclass(frozen=True)
class Evidence:
    source_path: str
    kind: str
    detail: str
    snippet_safe: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "source_path": self.source_path,
                "kind": self.kind,
                "detail": self.detail,
                "snippet_safe": self.snippet_safe,
            }
        )


@dataclass(frozen=True)
class PortRequirement:
    service: str
    port: Optional[int]
    protocol: str
    role: PortRole
    classification: Classification
    mutable: bool
    confidence: Confidence
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "service": self.service,
                "port": self.port,
                "protocol": self.protocol,
                "role": self.role,
                "classification": self.classification,
                "mutable": self.mutable,
                "confidence": self.confidence,
                "evidence": [item.to_dict() for item in self.evidence],
            }
        )


@dataclass
class ServiceInfo:
    name: str
    type: Optional[str] = None
    source_paths: List[str] = field(default_factory=list)
    port_requirements: List[PortRequirement] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    confidence: Confidence = "medium"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "name": self.name,
                "type": self.type,
                "source_paths": sorted(self.source_paths),
                "port_requirements": [item.to_dict() for item in self.port_requirements],
                "dependencies": sorted(self.dependencies),
                "confidence": self.confidence,
                "evidence": [item.to_dict() for item in self.evidence],
            }
        )


@dataclass(frozen=True)
class Conflict:
    kind: ConflictKind
    severity: str
    parties: List[str]
    port: Optional[int]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "kind": self.kind,
                "severity": self.severity,
                "parties": sorted(self.parties),
                "port": self.port,
                "message": self.message,
                "details": _normalize(self.details),
            }
        )


@dataclass
class WorkspaceModel:
    project_root: str
    central_available: bool = False
    central_error: Optional[str] = None
    services: List[ServiceInfo] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    files_considered: int = 0
    files_parsed: int = 0
    ignored_directories: int = 0
    duration_ms: int = 0
    fingerprint_inputs: List[str] = field(default_factory=list)
    existing_manifest: Optional[Dict[str, Any]] = None
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    workspace_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "project_root": self.project_root,
                "central_available": self.central_available,
                "central_error": self.central_error,
                "services": [service.to_dict() for service in sorted(self.services, key=lambda s: s.name)],
                "conflicts": [item.to_dict() for item in self.conflicts],
                "files_considered": self.files_considered,
                "files_parsed": self.files_parsed,
                "ignored_directories": self.ignored_directories,
                "duration_ms": self.duration_ms,
                "fingerprint_inputs": sorted(self.fingerprint_inputs),
                "existing_manifest": _normalize(self.existing_manifest) if self.existing_manifest else None,
                "warnings": [_normalize(item) for item in self.warnings],
                "workspace_fingerprint": self.workspace_fingerprint,
            }
        )


@dataclass
class CoordinatedPlanProposal:
    workspace_fingerprint: str
    services: List[Dict[str, Any]]
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    ready: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return _sorted_dict(
            {
                "workspace_fingerprint": self.workspace_fingerprint,
                "services": [_normalize(item) for item in self.services],
                "reasons": [_normalize(item) for item in self.reasons],
                "ready": self.ready,
            }
        )
