"""Phase 18: v1.4 agent ↔ new Central compatibility for pending_deployment.

Old agents must continue heartbeat/sync and simply never claim deployment work
when Central returns the additive pending_deployment field.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from portforge_agent.remote_probe import process_pending_probes


def _legacy_v14_heartbeat_cycle(client, heartbeat_data: dict) -> None:
    """Simulate a v1.4 agent main-loop slice that only knew probes + upgrades.

    Intentionally does *not* call process_pending_deployment — that is the
    Phase 18 addition. Unknown fields must be ignored without claiming.
    """
    process_pending_probes(client, heartbeat_data)
    if isinstance(heartbeat_data, dict) and heartbeat_data.get("pending_upgrade"):
        # Upgrade path existed in v1.4; deployments did not.
        pass


def test_v14_agent_ignores_pending_deployment_and_never_claims():
    client = MagicMock()
    client.claim_deployment = MagicMock()
    client.deployment_status = MagicMock()

    heartbeat = {
        "host_id": "host-legacy",
        "status": "online",
        "pending_probes": [],
        "pending_deployment": {
            "deployment_id": "11111111-1111-1111-1111-111111111111",
            "project": "demo",
            "environment": "production",
            "request_id": "req-1",
            "package_uri": "https://artifacts.example.com/pkg.zip",
            "package_sha256": "a" * 64,
            "package_manifest_sha256": "b" * 64,
            "state": "APPROVED",
        },
    }

    _legacy_v14_heartbeat_cycle(client, heartbeat)

    client.claim_deployment.assert_not_called()
    client.deployment_status.assert_not_called()


def test_missing_pending_deployment_field_is_legacy_compatible_no_op():
    """New agent code must treat absent pending_deployment like probes do."""
    from portforge_agent.deployment.handler import process_pending_deployment

    client = MagicMock()
    # Heartbeat without the field — handler is only invoked when present.
    heartbeat = {"host_id": "host-1", "status": "online", "pending_probes": []}
    assert heartbeat.get("pending_deployment") is None
    # Calling with None/empty must not raise or claim (guard for callers).
    assert process_pending_deployment({}, client) is False
    client.claim_deployment.assert_not_called()
