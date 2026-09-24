from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PortScope(str, Enum):
    INTERNAL = "INTERNAL"
    HOST = "HOST"
    INGRESS = "INGRESS"


class IngressStatus(str, Enum):
    INGRESS_PLANNED = "INGRESS_PLANNED"
    TARGET_BOUND = "TARGET_BOUND"
    EXTERNAL_NETWORK_UNVERIFIED = "EXTERNAL_NETWORK_UNVERIFIED"


class TargetsError(Exception):
    def __init__(self, code: str, message: str, details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class TargetRef:
    alias: str
    host_id: str
    hostname: Optional[str] = None


@dataclass(frozen=True)
class EnvironmentConfig:
    name: str
    targets: Dict[str, TargetRef]


@dataclass(frozen=True)
class ServicePortMapping:
    service: str
    internal_port: int
    host_port: Optional[int]
    protocol: str
    reason_code: str
    reason_message: str

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "internal_port": self.internal_port,
            "host_port": self.host_port,
            "protocol": self.protocol,
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
        }


@dataclass(frozen=True)
class IngressBinding:
    name: str
    scheme: str
    hostname: str
    public_port: int
    service: str
    environment: str
    target: str
    bound_host_port: Optional[int] = None
    status: IngressStatus = IngressStatus.INGRESS_PLANNED

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "scheme": self.scheme,
            "hostname": self.hostname,
            "public_port": self.public_port,
            "service": self.service,
            "environment": self.environment,
            "target": self.target,
            "bound_host_port": self.bound_host_port,
            "status": self.status.value,
        }


@dataclass
class TargetPlan:
    plan_id: Optional[str]
    environment: str
    target: str
    host_id: str
    services: List[ServicePortMapping] = field(default_factory=list)
    ingress: List[IngressBinding] = field(default_factory=list)
    namespaced_request_id_hint: Optional[str] = None
    committed: bool = False
    config_overrides: Optional[dict] = None
    proxy_evidence: Optional[list] = None

    def to_dict(self) -> dict:
        payload: Dict[str, Any] = {
            "plan_id": self.plan_id,
            "environment": self.environment,
            "target": self.target,
            "host_id": self.host_id,
            "services": [item.to_dict() for item in self.services],
            "ingress": [item.to_dict() for item in self.ingress],
            "committed": self.committed,
        }
        if self.namespaced_request_id_hint is not None:
            payload["namespaced_request_id_hint"] = self.namespaced_request_id_hint
        if self.config_overrides is not None:
            payload["config_overrides"] = self.config_overrides
        if self.proxy_evidence is not None:
            payload["proxy_evidence"] = self.proxy_evidence
        return payload
