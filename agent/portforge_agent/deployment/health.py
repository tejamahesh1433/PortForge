"""Deployment health evaluation — declared checks only, no URL crawling."""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class HealthOverall(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    HEALTH_UNKNOWN = "HEALTH_UNKNOWN"
    RUNTIME_STARTED = "RUNTIME_STARTED"


def _service_states(compose_status: Dict[str, Any]) -> List[str]:
    states: List[str] = []
    for item in compose_status.get("services") or []:
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        if isinstance(state, str):
            states.append(state.lower())
    return states


def _runtime_started(compose_status: Dict[str, Any]) -> bool:
    states = _service_states(compose_status)
    if not states:
        return False
    return any("running" in state or "up" in state for state in states)


def _check_http(check: Dict[str, Any]) -> Dict[str, Any]:
    check_id = str(check.get("id") or "http")
    url = check.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return {"id": check_id, "kind": "http", "status": "HEALTH_UNKNOWN", "detail": "missing url"}
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "PortForge-Agent-Deployment/1"})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as response:
            ok = 200 <= response.status < 300
            return {
                "id": check_id,
                "kind": "http",
                "status": "HEALTHY" if ok else "UNHEALTHY",
                "detail": f"HTTP {response.status}",
            }
    except urllib.error.HTTPError as exc:
        return {
            "id": check_id,
            "kind": "http",
            "status": "HEALTHY" if 200 <= exc.code < 300 else "UNHEALTHY",
            "detail": f"HTTP {exc.code}",
        }
    except Exception as exc:
        return {"id": check_id, "kind": "http", "status": "UNHEALTHY", "detail": str(exc)[:200]}


def _check_tcp(check: Dict[str, Any]) -> Dict[str, Any]:
    check_id = str(check.get("id") or "tcp")
    host = check.get("host")
    port = check.get("port")
    if not isinstance(host, str) or not host.strip():
        return {"id": check_id, "kind": "tcp", "status": "HEALTH_UNKNOWN", "detail": "missing host"}
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return {"id": check_id, "kind": "tcp", "status": "HEALTH_UNKNOWN", "detail": "missing port"}
    if not (1 <= port_int <= 65535):
        return {"id": check_id, "kind": "tcp", "status": "HEALTH_UNKNOWN", "detail": "invalid port"}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((host, port_int))
        return {"id": check_id, "kind": "tcp", "status": "HEALTHY", "detail": "connected"}
    except OSError as exc:
        return {"id": check_id, "kind": "tcp", "status": "UNHEALTHY", "detail": str(exc)[:200]}
    finally:
        sock.close()


def evaluate_health(
    compose_status: Dict[str, Any],
    declared_checks: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Evaluate deployment health from compose status and explicit declared checks."""
    checks_out: List[Dict[str, Any]] = []
    declared = list(declared_checks or ())

    for check in declared:
        if not isinstance(check, dict):
            continue
        kind = str(check.get("kind") or "").lower()
        if kind == "http":
            checks_out.append(_check_http(check))
        elif kind == "tcp":
            checks_out.append(_check_tcp(check))
        elif kind == "compose":
            state = "HEALTHY" if compose_status.get("available") else "UNHEALTHY"
            checks_out.append(
                {
                    "id": str(check.get("id") or "compose"),
                    "kind": "compose",
                    "status": state,
                    "detail": compose_status.get("detail"),
                }
            )
        elif kind == "container":
            running = _runtime_started(compose_status)
            checks_out.append(
                {
                    "id": str(check.get("id") or "container"),
                    "kind": "container",
                    "status": "HEALTHY" if running else "UNHEALTHY",
                    "detail": None,
                }
            )

    if checks_out:
        statuses = {item["status"] for item in checks_out}
        if "UNHEALTHY" in statuses:
            overall = HealthOverall.UNHEALTHY
        elif statuses == {"HEALTHY"}:
            overall = HealthOverall.HEALTHY
        elif "HEALTH_UNKNOWN" in statuses:
            overall = HealthOverall.HEALTH_UNKNOWN
        else:
            overall = HealthOverall.HEALTH_UNKNOWN
    elif _runtime_started(compose_status):
        overall = HealthOverall.RUNTIME_STARTED
    elif compose_status.get("available"):
        overall = HealthOverall.HEALTH_UNKNOWN
    else:
        overall = HealthOverall.UNHEALTHY

    return {"overall": overall.value, "checks": checks_out}
