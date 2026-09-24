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
        protocol_version: Optional[int] = None,
        contract_version: Optional[int] = None,
        python_version: Optional[str] = None,
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
        # v1.1-A: additive only -- omitted entirely (not sent as null) when
        # unset, so an EnrollmentRequest built against the v1.0 schema
        # still validates identically. See docs/v1.1/version-compatibility.md.
        if protocol_version is not None:
            body["protocol_version"] = protocol_version
        # Phase 9/10: fleet inventory fields -- additive, omitted when absent.
        if contract_version is not None:
            body["contract_version"] = contract_version
        if python_version is not None:
            body["python_version"] = python_version
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
        protocol_version: Optional[int] = None,
        contract_version: Optional[int] = None,
        python_version: Optional[str] = None,
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
        if protocol_version is not None:
            body["protocol_version"] = protocol_version
        # Phase 9/10: fleet inventory fields -- additive, omitted when absent.
        if contract_version is not None:
            body["contract_version"] = contract_version
        if python_version is not None:
            body["python_version"] = python_version
        return self._request("POST", "/api/agent/heartbeat", body=body)

    def submit_observations(
        self, scan_id: str, host_id: str, observed_at: str, observations: List[Dict[str, Any]]
    ) -> CentralResult:
        body = {"scan_id": scan_id, "host_id": host_id, "observed_at": observed_at, "observations": observations}
        return self._request("POST", "/api/agent/observations", body=body)

    def sync_reservation(self, reservation: Dict[str, Any]) -> CentralResult:
        return self._request("POST", "/api/reservations", body=reservation)

    def generate_enrollment_token(self, label: Optional[str] = None, ttl_hours: int = 24) -> CentralResult:
        """Admin-only -- mints a new enrollment token via the backend's own
        `POST /agent/enrollment-tokens` (mirrors its "returned exactly once,
        never stored in raw form" semantics). `self.token` here must be the
        server's PORTFORGE_ADMIN_BOOTSTRAP_TOKEN, not a per-host agent
        credential -- see security/auth.py::require_admin.
        """
        from urllib.parse import urlencode

        params: Dict[str, Any] = {"ttl_hours": ttl_hours}
        if label:
            params["label"] = label
        return self._request("POST", f"/api/agent/enrollment-tokens?{urlencode(params)}")

    # --- v1.1-B: remote bind-probe result submission --------------------------
    # Authenticated (require_agent) -- see docs/v1.1/remote-probe-design.md.
    # Pending probes themselves are delivered inside the existing heartbeat
    # response (see `heartbeat()` above), not a separate poll call.

    def submit_probe_result(
        self, host_id: str, probe_id: str, available: Optional[bool], reason: Optional[str] = None
    ) -> CentralResult:
        body: Dict[str, Any] = {"host_id": host_id, "probe_id": probe_id, "available": available}
        if reason is not None:
            body["reason"] = reason
        return self._request("POST", "/api/agent/probes/result", body=body)

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
        
    def verify_allocation(self, allocation_id: str) -> CentralResult:
        return self._request("POST", f"/api/allocations/{allocation_id}/verify", authenticated=False)
        
    def list_allocations(self) -> CentralResult:
        return self._request("GET", "/api/allocations?limit=100", authenticated=False)

    # --- Phase 10: agent upgrade status reporting ----------------------------
    # Agent credential required -- see backend/app/api/agents.py
    # POST /api/agent/upgrades/{upgrade_id}/status

    def report_upgrade_status(
        self,
        upgrade_id: str,
        state: str,
        failure_reason: Optional[str] = None,
        reported_version: Optional[str] = None,
    ) -> CentralResult:
        """Report a status transition for an in-progress upgrade.

        Called at each phase: DOWNLOADING, VERIFYING, INSTALLING, RESTARTING,
        and FAILED (with failure_reason). The new process after restart reports
        SUCCEEDED via heartbeat (Central infers it from the matching version).
        """
        body: Dict[str, Any] = {"state": state}
        if failure_reason is not None:
            body["failure_reason"] = failure_reason
        if reported_version is not None:
            body["reported_version"] = reported_version
        return self._request("POST", f"/api/agent/upgrades/{upgrade_id}/status", body=body)

    def list_hosts(self, limit: int = 500) -> CentralResult:
        return self._request("GET", f"/api/hosts?limit={limit}", authenticated=False)

    # --- Phase 18: agent deployment status reporting -------------------------
    # Agent credential required -- see docs/design/deployment-schema-proposal.md

    def claim_deployment(self, deployment_id: str) -> CentralResult:
        """Obtain or renew the deployment claim lease and claim_token."""
        return self._request("POST", f"/api/agent/deployments/{deployment_id}/claim", body={})

    def deployment_status(
        self,
        deployment_id: str,
        *,
        claim_token: str,
        state: str,
        failure_code: Optional[str] = None,
        failure_reason: Optional[str] = None,
        revision_id: Optional[str] = None,
    ) -> CentralResult:
        """Report a deployment lifecycle transition (token required)."""
        body: Dict[str, Any] = {"claim_token": claim_token, "state": state}
        if failure_code is not None:
            body["failure_code"] = failure_code
        if failure_reason is not None:
            body["failure_reason"] = failure_reason
        if revision_id is not None:
            body["revision_id"] = revision_id
        return self._request("POST", f"/api/agent/deployments/{deployment_id}/status", body=body)

    def deployment_health(
        self,
        deployment_id: str,
        *,
        claim_token: str,
        health: Dict[str, Any],
    ) -> CentralResult:
        """Submit an allowlisted health snapshot for a deployment attempt."""
        body = {"claim_token": claim_token, "health": health}
        return self._request("POST", f"/api/agent/deployments/{deployment_id}/health", body=body)

    # --- Phase 18: client deployment orchestration ---------------------------
    # Unauthenticated by design — same posture as Phase 8A allocations.

    def plan_deployment(self, body: Dict[str, Any]) -> CentralResult:
        return self._request("POST", "/api/deployments/plan", body=body, authenticated=False)

    def create_deployment(self, body: Dict[str, Any]) -> CentralResult:
        return self._request("POST", "/api/deployments", body=body, authenticated=False)

    def get_deployment(self, deployment_id: str) -> CentralResult:
        return self._request("GET", f"/api/deployments/{deployment_id}", authenticated=False)

    def rollback_deployment(self, deployment_id: str, request_id: str) -> CentralResult:
        body = {"request_id": request_id}
        return self._request(
            "POST",
            f"/api/deployments/{deployment_id}/rollback",
            body=body,
            authenticated=False,
        )

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

    # --- Phase 22: admin upgrade observability --------------------------------
    # All four methods require PORTFORGE_ADMIN_BOOTSTRAP_TOKEN (authenticated=True,
    # self.token must be the admin bootstrap token -- see require_admin in
    # backend/app/security/auth.py, same posture as generate_enrollment_token).

    def get_upgrade_status(self, upgrade_id: str) -> CentralResult:
        """GET /api/upgrades/{id}/status -- typed UpgradeStatusOut."""
        return self._request("GET", f"/api/upgrades/{upgrade_id}/status")

    def get_host_upgrade_status(self, host_id: str) -> CentralResult:
        """GET /api/hosts/{host_id}/upgrade-status."""
        return self._request("GET", f"/api/hosts/{host_id}/upgrade-status")

    def retry_upgrade(self, upgrade_id: str) -> CentralResult:
        """POST /api/upgrades/{id}/retry -- returns 201 on success."""
        return self._request("POST", f"/api/upgrades/{upgrade_id}/retry", body={})

    def cancel_upgrade(self, upgrade_id: str) -> CentralResult:
        """POST /api/upgrades/{id}/cancel."""
        return self._request("POST", f"/api/upgrades/{upgrade_id}/cancel", body={})

    def get_upgrade_rollout(self, request_id: str) -> CentralResult:
        """GET /api/upgrade-rollouts/{request_id}."""
        return self._request("GET", f"/api/upgrade-rollouts/{request_id}")
