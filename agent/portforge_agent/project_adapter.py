"""Phase 8B: provider-neutral adapter boundary.

    Manifest (manifest.py)
      -> NormalizedProjectRequest (this module)
      -> Allocation adapter (this module: to_allocation_body)
      -> Phase 8A API/service (central_client.py -> POST /api/allocations)

This module knows NOTHING about any specific AI provider (no Claude,
Codex, Antigravity, Gemini, OpenAI, Anthropic, or Google references, and
none should ever be added here) and nothing about YAML -- it only deals in
already-validated data (`ManifestPortRequest`, plain strings/UUIDs). A
future adapter for a different manifest source (a different file format, a
different tool's own project config) only needs to produce a
`NormalizedProjectRequest`; everything downstream of that is unchanged.

`resolve_host_id` is the SINGLE host resolver for both the pre-existing
`allocate` CLI command and the new `project validate/plan/allocate`
commands -- see docs/phase8b_manifest_audit.md §3 for why this was moved
here (out of cli.py) rather than duplicated.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List, Optional

from .manifest import ManifestPortRequest, ProjectManifest


@dataclass(frozen=True)
class NormalizedHostRef:
    id: str
    hostname: str


@dataclass(frozen=True)
class NormalizedProjectRequest:
    """The provider-neutral, fully-resolved request a manifest normalizes
    into. `schema_version` is this normalized contract's own version (see
    docs/phase8b_project_manifest.md "Normalized contract") -- independent
    of the manifest file's own `version` field.
    """

    schema_version: int
    project: str
    host: NormalizedHostRef
    requests: List[ManifestPortRequest]


def resolve_host_ref(client, host_arg: str) -> "tuple[Optional[NormalizedHostRef], Optional[str], Optional[str]]":
    """Resolves a hostname or UUID to a `NormalizedHostRef` (id + hostname)
    via Central. Returns `(host, error_message, error_code)` -- exactly one
    of `host` or `(error_message, error_code)` is populated.

    THE single host resolver for `allocate`, `allocation get/release`
    (which only need the id), and `project validate/plan/allocate` (which
    also want the hostname for display) -- see
    docs/phase8b_manifest_audit.md §3 for why this replaced two prior,
    near-duplicate implementations.

    `--host`/`target.host` accepts either a UUID or a hostname (Phase 8A's
    conceptual examples and Phase 8B's manifest examples both use plain
    hostnames like "NTMKEYA"). The HTTP API itself only ever accepts a
    UUID, consistent with every other Central endpoint -- this resolves a
    hostname by listing `/api/hosts` and matching case-insensitively,
    exactly as the dashboard's own client-side host search already does.

    An ambiguous match (more than one host with the same name) fails with
    `HOST_AMBIGUOUS` rather than arbitrarily picking one. A UUID that is
    syntactically valid but unknown to Central also fails with
    `HOST_NOT_FOUND` (not silently accepted) so callers always get a real
    hostname back.
    """
    is_uuid = True
    try:
        uuid.UUID(host_arg)
    except ValueError:
        is_uuid = False

    result = client.list_hosts()
    if not result.success:
        return None, f"Could not reach Central to resolve host '{host_arg}': {result.error}", "HOST_NOT_FOUND"
    items = (result.data or {}).get("items", [])

    if is_uuid:
        match = next((h for h in items if h.get("id") == host_arg), None)
        if match is None:
            return None, f"No host with id '{host_arg}' is known to Central.", "HOST_NOT_FOUND"
        return NormalizedHostRef(id=match["id"], hostname=match["hostname"]), None, None

    matches = [h for h in items if h.get("hostname", "").lower() == host_arg.lower()]
    if not matches:
        return None, f"No host named '{host_arg}' is known to Central.", "HOST_NOT_FOUND"
    # Decommissioned hosts retain their hostname for UUID lookups but must not
    # block hostname resolution when an ACTIVE peer exists (Qual dispose-and-
    # re-enroll leaves DECOMMISSIONED tombstones with the same name).
    active = [
        h for h in matches
        if (h.get("lifecycle_state") or "ACTIVE").upper() != "DECOMMISSIONED"
    ]
    candidates = active if active else matches
    if len(candidates) > 1:
        return (
            None,
            f"Multiple hosts named '{host_arg}' are known to Central; use its UUID instead.",
            "HOST_AMBIGUOUS",
        )
    return NormalizedHostRef(id=candidates[0]["id"], hostname=candidates[0]["hostname"]), None, None


NORMALIZED_SCHEMA_VERSION = 1


def build_normalized_request(manifest: ProjectManifest, host: NormalizedHostRef) -> NormalizedProjectRequest:
    return NormalizedProjectRequest(
        schema_version=NORMALIZED_SCHEMA_VERSION,
        project=manifest.project,
        host=host,
        requests=manifest.requests,
    )


def build_candidate_preview(client, host: NormalizedHostRef, requests: List[ManifestPortRequest]) -> List[dict]:
    """Per-request advisory candidate info via the existing, unmodified
    `GET /api/recommendations` endpoint -- one call per request, exactly
    as `project plan` (Phase 8B) already does. Phase 8D's `workflow
    prepare` reuses this SAME function rather than re-querying Central
    itself, so there is exactly one "what would Central suggest" code
    path in the whole CLI (see docs/phase8d_agent_integration_audit.md §2).
    Never mutates anything -- purely a sequence of GET calls.
    """
    requests_out = []
    for item in requests:
        result = client.get_recommendation(host.id, item.purpose, item.protocol)
        candidate_port = None
        candidates_considered = None
        basis = None
        if result.success and isinstance(result.data, dict):
            candidate_port = result.data.get("recommended_port")
            candidates_considered = result.data.get("candidates_considered")
            basis = result.data.get("basis")
        requests_out.append(
            {
                "name": item.name,
                "purpose": item.purpose,
                "protocol": item.protocol,
                "preferred_port": item.preferred_port,
                "candidate_port": candidate_port,
                "candidates_considered": candidates_considered,
                "basis": basis,
            }
        )
    return requests_out


def to_allocation_body(normalized: NormalizedProjectRequest, request_id: Optional[str]) -> dict:
    """Translates a `NormalizedProjectRequest` into the exact JSON body
    `central_client.create_allocation()` sends to the existing, unmodified
    Phase 8A `POST /api/allocations` endpoint. This is the only place a
    manifest-derived request turns into a Phase 8A allocation call -- see
    cli.py's `_cmd_project_allocate`, which calls
    `client.create_allocation(**to_allocation_body(...))` and nothing else.
    """
    requests = [
        {
            "name": item.name,
            "purpose": item.purpose,
            "protocol": item.protocol,
            **({"preferred_port": item.preferred_port} if item.preferred_port is not None else {}),
            **({"requested_range": item.requested_range} if item.requested_range is not None else {}),
        }
        for item in normalized.requests
    ]
    return {
        "project": normalized.project,
        "host_id": normalized.host.id,
        "requests": requests,
        "request_id": request_id,
    }
