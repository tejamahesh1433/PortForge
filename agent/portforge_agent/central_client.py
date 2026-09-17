"""Thin HTTP client for the optional central server.

Uses only the standard library (`urllib.request`) -- deliberately no new
dependency: this client needs simple JSON POST/GET with a bearer header
and a timeout, nothing `requests` would meaningfully simplify, and the
project's existing discipline has been to add a dependency only when it
buys something stdlib genuinely can't (see e.g. PyYAML in Phase 4 for
structured config parsing).

Every method here catches network/HTTP errors and returns a `CentralResult`
instead of raising -- central connectivity failing must never propagate
into a command that has nothing to do with the central server (see
agent/README.md "Offline behavior"). The token is never included in any
exception message, log line, or printed output this module produces.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 5.0


@dataclass
class CentralResult:
    success: bool
    status_code: Optional[int] = None
    data: Any = None
    error: Optional[str] = None


class CentralClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None, authenticated: bool = True) -> CentralResult:
        url = f"{self.base_url}{path}"
        data_bytes = json.dumps(body).encode("utf-8") if body is not None else None

        headers = {"Content-Type": "application/json"}
        if authenticated:
            if not self.token:
                return CentralResult(success=False, error="No central token configured.")
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status_code = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except Exception:
                detail = None
            return CentralResult(success=False, status_code=exc.code, error=detail or f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return CentralResult(success=False, error=f"Could not reach central server: {exc.reason}")
        except (TimeoutError, OSError) as exc:
            return CentralResult(success=False, error=f"Central server request failed: {exc}")

        parsed = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                parsed = None

        return CentralResult(success=200 <= status_code < 300, status_code=status_code, data=parsed)

    def health(self) -> CentralResult:
        return self._request("GET", "/api/health", authenticated=False)

    def enroll(
        self,
        enrollment_token: str,
        host_id: str,
        hostname: str,
        operating_system: str,
        os_version: Optional[str],
        architecture: Optional[str],
        agent_version: Optional[str],
        docker_available: bool,
    ) -> CentralResult:
        body = {
            "enrollment_token": enrollment_token,
            "host_id": host_id,
            "hostname": hostname,
            "operating_system": operating_system,
            "os_version": os_version,
            "architecture": architecture,
            "agent_version": agent_version,
            "docker_available": docker_available,
        }
        return self._request("POST", "/api/agent/enroll", body=body, authenticated=False)

    def heartbeat(
        self,
        host_id: str,
        hostname: str,
        operating_system: str,
        os_version: Optional[str],
        architecture: Optional[str],
        agent_version: Optional[str],
        docker_available: bool,
        timestamp: str,
    ) -> CentralResult:
        body = {
            "host_id": host_id,
            "hostname": hostname,
            "operating_system": operating_system,
            "os_version": os_version,
            "architecture": architecture,
            "agent_version": agent_version,
            "docker_available": docker_available,
            "timestamp": timestamp,
        }
        return self._request("POST", "/api/agent/heartbeat", body=body)

    def submit_observations(
        self, scan_id: str, host_id: str, observed_at: str, observations: List[Dict[str, Any]]
    ) -> CentralResult:
        body = {"scan_id": scan_id, "host_id": host_id, "observed_at": observed_at, "observations": observations}
        return self._request("POST", "/api/agent/observations", body=body)

    def sync_reservation(self, reservation: Dict[str, Any]) -> CentralResult:
        return self._request("POST", "/api/reservations", body=reservation)
