"""The Reservation model.

A reservation belongs to a single host (`host_id`) -- port availability is
always host-specific in PortForge, and this remains true for reservations:
Phase 4 only manages reservations for the *current* host. A future central
server aggregates reservations reported by multiple agents; `host_id` is
carried on every reservation today specifically so that migration doesn't
require a schema change later -- see storage.py's SCHEMA_VERSION handling.

`host_id` itself is still the Phase 1 placeholder (the hostname) -- a
persisted, stable host UUID is explicitly a later-phase concern, not
redesigned here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..models import Protocol


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_reservation_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Reservation:
    """A durable claim on a (host, protocol, port) -- optionally scoped to a
    specific bind address -- on behalf of a project/service.

    TCP and UDP on the same port number are distinct resources: a
    reservation for 8000/tcp says nothing about 8000/udp. A reservation
    with `bind_address=None` means "any address" (the common case: a
    project reserving "port 8003" doesn't usually care which interface).
    """

    reservation_id: str
    host_id: str
    port: int
    protocol: Protocol
    project: str
    service: Optional[str] = None
    purpose: Optional[str] = None
    bind_address: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        host_id: str,
        port: int,
        project: str,
        protocol: Protocol = Protocol.TCP,
        service: Optional[str] = None,
        purpose: Optional[str] = None,
        bind_address: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> "Reservation":
        now = _utcnow()
        return cls(
            reservation_id=new_reservation_id(),
            host_id=host_id,
            port=port,
            protocol=protocol,
            project=project,
            service=service,
            purpose=purpose,
            bind_address=bind_address,
            notes=notes,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "host_id": self.host_id,
            "port": self.port,
            "protocol": self.protocol.value,
            "project": self.project,
            "service": self.service,
            "purpose": self.purpose,
            "bind_address": self.bind_address,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Reservation":
        """Strict by design: raises on missing/malformed required fields
        rather than silently dropping the entry. See storage.py's
        docstring for why a single bad entry fails the whole load instead
        of quietly disappearing on the next save.
        """
        try:
            reservation_id = str(data["reservation_id"])
            host_id = str(data["host_id"])
            port = int(data["port"])
            protocol = Protocol(str(data["protocol"]))
            project = str(data["project"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed reservation entry: {exc}") from exc

        def _opt_str(key: str) -> Optional[str]:
            value = data.get(key)
            return str(value) if value is not None else None

        created_at = _parse_datetime(data.get("created_at"))
        updated_at = _parse_datetime(data.get("updated_at"))

        return cls(
            reservation_id=reservation_id,
            host_id=host_id,
            port=port,
            protocol=protocol,
            project=project,
            service=_opt_str("service"),
            purpose=_opt_str("purpose"),
            bind_address=_opt_str("bind_address"),
            notes=_opt_str("notes"),
            created_at=created_at,
            updated_at=updated_at,
        )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _utcnow()
