"""Phase 8D: the stable, versioned machine contract a coding agent reads
via `portforge agent-contract --json` to discover what PortForge can do
without needing to read this codebase. `CONTRACT_VERSION` is independent
of `manifest.SUPPORTED_MANIFEST_VERSION` (the portforge.yml schema
version) and the package's own `portforge_version` -- three separate
numbers for three separate things, never conflated (see
docs/phase8d_agent_integration_audit.md's decisions table). Bumping
`CONTRACT_VERSION` is how a future incompatible change to THIS contract's
shape would be introduced, without silently changing what v1 promised.
"""
from __future__ import annotations

from .manifest import MAX_PORTS_PER_MANIFEST, SUPPORTED_MANIFEST_VERSION
from .version import PROTOCOL_VERSION, get_portforge_version

CONTRACT_VERSION = 1

_KNOWN_PURPOSES = ("frontend", "api", "postgres", "mysql", "redis", "generic")
_SUPPORTED_PROTOCOLS = ("tcp", "udp")


def build_contract() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "portforge_version": get_portforge_version(),
        "protocol_version": PROTOCOL_VERSION,
        "manifest_versions": [SUPPORTED_MANIFEST_VERSION],
        "max_ports_per_manifest": MAX_PORTS_PER_MANIFEST,
        "capabilities": {
            "project_init": True,
            "validate": True,
            "plan": True,
            "allocate": True,
            "config_plan": True,
            "config_apply": True,
            "config_status": True,
            "config_rollback": True,
            "workflow_prepare": True,
            "workflow_apply": True,
            "workflow_status": True,
            "doctor": True,
        },
        # Informational only -- NOT authoritative. Central's own
        # INVALID_REQUEST error at allocation time is the real source of
        # truth for known purposes (see docs/phase8b_manifest_audit.md §2's
        # identical reasoning); this list just gives an agent a sane
        # starting guess without a network round-trip.
        "known_purposes": list(_KNOWN_PURPOSES),
        "supported_protocols": list(_SUPPORTED_PROTOCOLS),
        "recommended_workflow": [
            "portforge project init --project <name> --host <host> --port <name>:<purpose>[:protocol]  # only if no portforge.yml exists yet",
            "portforge project validate [<manifest>] --json",
            "portforge workflow prepare [<manifest>] --json",
            "portforge workflow apply [<manifest>] --request-id <stable-id> --json",
            "portforge workflow status --request-id <stable-id> --project-root <dir> --json",
            "portforge config rollback <mutation-id> --project-root <dir> --json   # only if config was applied and needs undoing",
            "portforge allocation release <allocation-id> --json   # when the allocation is no longer needed",
        ],
        "request_id_guidance": (
            "Always pass a stable, caller-chosen --request-id to 'workflow apply'. Retrying the same "
            "request-id with an unchanged manifest returns the same outcome idempotently (no new ports, "
            "no duplicate reservations, no duplicate file rewrites). Retrying the same request-id with a "
            "materially different manifest or config mapping fails with WORKFLOW_IDEMPOTENCY_CONFLICT "
            "rather than silently reinterpreting the request."
        ),
        "cleanup_guidance": (
            "'workflow apply's JSON result includes recovery.rollback_command and recovery.release_command. "
            "If config was applied and needs undoing, roll it back BEFORE releasing the allocation."
        ),
    }
