"""Normalized data model for a discovered port.

Every collector (Windows, macOS, Linux) must return ``DiscoveredPort``
instances so the rest of PortForge never needs to know which OS the data
came from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    # Only used for type checking -- kept out of the runtime import graph so
    # portforge_agent.models (the raw discovery model) never has a runtime
    # dependency on portforge_agent.detection (the enrichment layer that
    # interprets it). See detection/__init__.py for the reasoning.
    from .detection.models import DetectionInfo


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class Source(str, Enum):
    """Where an observation of a port came from."""

    PROCESS = "process"
    DOCKER = "docker"
    SYSTEM = "system"
    RESERVATION = "reservation"


class PortState(str, Enum):
    """High-level lifecycle state of a port, as understood by PortForge.

    Phase 1 only performs live discovery, so every discovered port is
    reported as ACTIVE. FREE / RESERVED / CONFLICT require comparing a scan
    against a reservation store and other hosts, which is implemented in a
    later phase.
    """

    ACTIVE = "ACTIVE"
    FREE = "FREE"
    RESERVED = "RESERVED"
    CONFLICT = "CONFLICT"
    SYSTEM = "SYSTEM"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DiscoveredPort:
    """A single normalized observation of a port in use on a host."""

    hostname: str
    host_id: str
    operating_system: str
    port: int
    protocol: Protocol
    bind_address: str
    source: Source

    pid: Optional[int] = None
    process_name: Optional[str] = None
    process_path: Optional[str] = None
    working_directory: Optional[str] = None
    # RAW FACTS (Phase 3): command line and immediate parent, as reported by
    # the OS. Detection reads these as evidence; nothing ever writes them
    # back based on an inference.
    command_line: Optional[List[str]] = None
    parent_pid: Optional[int] = None
    parent_process_name: Optional[str] = None
    parent_working_directory: Optional[str] = None

    container_id: Optional[str] = None
    container_name: Optional[str] = None
    docker_compose_project: Optional[str] = None
    container_image: Optional[str] = None
    container_status: Optional[str] = None
    docker_networks: Optional[List[str]] = None
    docker_labels: Optional[Dict[str, str]] = None
    # RAW FACT (Phase 3): the container's actual resolved entrypoint+args
    # (docker inspect .Path + .Args), not the Dockerfile's declared CMD.
    container_command: Optional[List[str]] = None

    # host_port/container_port are Phase 2 additions for Docker awareness.
    # host_port defaults to `port` in __post_init__ so every DiscoveredPort
    # (including plain Phase 1 native observations) has it populated, and
    # nothing that only reads `.port` needs to change. container_port stays
    # None for non-Docker observations.
    host_port: Optional[int] = None
    container_port: Optional[int] = None

    # INFERRED (Phase 3): everything below this point is a conclusion, not a
    # fact -- produced by portforge_agent.detection from the raw facts
    # above, and always paired with `detection` explaining how/why. An
    # unrecognized process/container legitimately leaves these None/"unknown"
    # rather than guessing.
    project_name: Optional[str] = None
    service_name: Optional[str] = None
    purpose: Optional[str] = None
    category: Optional[str] = None
    detection: Optional["DetectionInfo"] = None

    state: PortState = PortState.ACTIVE
    raw_state: Optional[str] = None

    first_seen: datetime = field(default_factory=_utcnow)
    last_seen: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.host_port is None:
            self.host_port = self.port

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["protocol"] = self.protocol.value
        data["source"] = self.source.value
        data["state"] = self.state.value
        data["first_seen"] = self.first_seen.isoformat()
        data["last_seen"] = self.last_seen.isoformat()
        # `detection` is deliberately not left to asdict()'s generic nested-
        # dataclass recursion: DetectionInfo owns its own to_dict() (it has
        # an enum field that needs the same explicit .value handling as
        # protocol/source/state above), and we don't want a hard runtime
        # import of the detection package just to duplicate that logic here.
        data["detection"] = self.detection.to_dict() if self.detection is not None else None
        return data

    def dedupe_key(self) -> tuple:
        """Key identifying a unique socket observation for de-duplication."""
        return (self.protocol, self.bind_address, self.port, self.pid)
