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
            # Parse the FULL error body, not just a `detail` string --
            # Phase 8A's allocation errors use `{"error": {"code": ...,
            # "message": ..., "details": [...]}}` (see
            # docs/phase8a_agent_allocation.md "Error contract"), distinct
            # from every other endpoint's plain `{"detail": "..."}`. Both
            # shapes are exposed: `.error` stays a human-readable string
            # (existing callers -- central_enroll/status/sync -- only ever
            # read this), `.data` now carries the full parsed body so
            # allocation-aware callers can read `.data["error"]["code"]`.
            body: Any = None
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                body = None

            error_message: Optional[str] = None
            if isinstance(body, dict):
                if isinstance(body.get("detail"), str):
                    error_message = body["detail"]
                elif isinstance(body.get("error"), dict) and isinstance(body["error"].get("message"), str):
                    error_message = body["error"]["message"]

            return CentralResult(
                success=False, status_code=exc.code, data=body, error=error_message or f"HTTP {exc.code}"
            )
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

    # --- Phase 8A: agent allocation ------------------------------------------
    # Unauthenticated by design -- see docs/phase8a_agent_allocation.md
    # "Error contract" and the audit's §9: coding-agent allocation has no
    # authentication in Phase 8A, matching the dashboard's own
    # already-unauthenticated write endpoints (Phase 7C.5).

    def create_allocation(
        self, project: str, host_id: str, requests: List[Dict[str, Any]], request_id: Optional[str] = None
    ) -> CentralResult:
        body: Dict[str, Any] = {"project": project, "host_id": host_id, "requests": requests}
        if request_id:
            body["request_id"] = request_id
        return self._request("POST", "/api/allocations", body=body, authenticated=False)

    def get_allocation(self, allocation_id: str) -> CentralResult:
        return self._request("GET", f"/api/allocations/{allocation_id}", authenticated=False)

    def release_allocation(self, allocation_id: str) -> CentralResult:
        return self._request("DELETE", f"/api/allocations/{allocation_id}", authenticated=False)

    def list_hosts(self, limit: int = 500) -> CentralResult:
        return self._request("GET", f"/api/hosts?limit={limit}", authenticated=False)

    # --- Phase 8B: project manifest ("plan" candidate info) ------------------
    # `GET /api/recommendations` is an existing, UNMODIFIED Phase 5 endpoint
    # (backend/app/api/recommendations.py) -- this is the only new client
    # method Phase 8B needed for `project plan`. It is always a
    # "central_suggestion", never a reservation. See
    # docs/phase8b_manifest_audit.md §4.

    def get_recommendation(self, host_id: str, service_type: str, protocol: str = "tcp") -> CentralResult:
        from urllib.parse import quote

        query = f"host_id={quote(host_id)}&service_type={quote(service_type)}&protocol={quote(protocol)}"
        return self._request("GET", f"/api/recommendations?{query}", authenticated=False)
