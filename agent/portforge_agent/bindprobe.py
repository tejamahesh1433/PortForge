"""Real socket bind-availability probe -- the final, most authoritative of
PortForge's three validation layers (see recommend.py).

Process/Docker discovery only tells us what PortForge *observed* -- it can
miss things (a permission gap, a timing race, a listener from a tool that
doesn't show up cleanly in `ss`/`lsof`/`psutil`). Before ever telling a
user a port is actually available, PortForge additionally attempts a real,
temporary socket bind.

Deliberately does **not** set SO_REUSEADDR or SO_REUSEPORT at all, on any
platform. Their semantics differ enough between Windows and POSIX that
using them here would work against the whole point of this probe: on
Windows in particular, SO_REUSEADDR can let a socket bind over an address
that already has an active listener, which would make an occupied port
look free -- exactly the failure mode this probe exists to prevent. The
accepted tradeoff: a genuinely free port that was very recently in
TIME_WAIT may occasionally probe as unavailable on POSIX without
SO_REUSEADDR. That's a deliberate conservative bias -- PortForge would
rather under-recommend than hand out a port that's still in use.

Never calls listen(): bind() alone is sufficient to trigger EADDRINUSE for
TCP, and UDP has no listen() concept at all. The socket is always closed
immediately, whether the bind succeeded or failed.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

from .models import Protocol


@dataclass
class BindProbeResult:
    available: bool
    reason: Optional[str] = None  # human-readable; None when available


def _is_ipv6(address: str) -> bool:
    return ":" in address


def probe_bind(port: int, protocol: Protocol, address: str = "0.0.0.0") -> BindProbeResult:
    """Attempt a real, temporary bind to (address, port).

    `address` must be a literal IP (IPv4 or IPv6) -- no hostname resolution
    is performed. IPv6 addresses (containing ":") select AF_INET6
    automatically; no extra socket options are set for dual-stack behavior,
    matching what a normal application binding that address would get from
    the OS's own defaults.
    """
    family = socket.AF_INET6 if _is_ipv6(address) else socket.AF_INET
    sock_type = socket.SOCK_STREAM if protocol == Protocol.TCP else socket.SOCK_DGRAM

    try:
        sock = socket.socket(family, sock_type)
    except OSError as exc:
        return BindProbeResult(available=False, reason=f"could not create probe socket: {exc}")

    try:
        sock.bind((address, port))
    except PermissionError as exc:
        return BindProbeResult(
            available=False,
            reason=f"permission denied binding {address}:{port} (privileged port, or restricted policy): {exc}",
        )
    except OSError as exc:
        return BindProbeResult(available=False, reason=f"bind failed: {exc}")
    else:
        return BindProbeResult(available=True, reason=None)
    finally:
        try:
            sock.close()
        except OSError:
            pass
