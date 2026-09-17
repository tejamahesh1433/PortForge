"""SQLAlchemy ORM models for the PortForge central registry.

Every table's primary identity fields (host_id, port, protocol, ...)
deliberately mirror the vocabulary the agent already uses (see
agent/portforge_agent/models.py and reservations/models.py) -- but these
are genuinely separate representations for a separate bounded context (a
persisted, relational, multi-host store), not copies pretending to be the
same object. Where a value has a fixed, meaningful vocabulary shared with
the agent (Protocol, PortState, Confidence), the *same enum classes* are
imported directly from the already-installed `portforge_agent` package
(see base.py) rather than redefined here incompatibly -- see
docs/architecture.md "Shared domain types" for the full reasoning.
"""
from .agent_credential import AgentCredential, EnrollmentToken
from .base import Base
from .host import Host
from .port_observation import CurrentPortObservation, PortObservationEvent
from .reservation import CentralReservation
from .scan import Scan

__all__ = [
    "Base",
    "Host",
    "AgentCredential",
    "EnrollmentToken",
    "CurrentPortObservation",
    "PortObservationEvent",
    "CentralReservation",
    "Scan",
]
