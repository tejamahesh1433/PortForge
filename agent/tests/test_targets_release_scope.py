"""Release scope: host-specific allocations are independent (Phase 17)."""
from __future__ import annotations

from unittest.mock import MagicMock

from portforge_agent.targets.request_id import namespace_request_id

from .mcp_helpers import invoke_tool


def test_namespaced_request_ids_differ_per_target_host():
    lenovo_id = namespace_request_id("production", "lenovo-prod", "release-scope-1")
    hp_id = namespace_request_id("production", "hp-prod", "release-scope-1")
    windows_id = namespace_request_id("development", "windows", "release-scope-1")

    assert lenovo_id != hp_id
    assert lenovo_id != windows_id
    assert hp_id != windows_id
    assert lenovo_id == "production:lenovo-prod:release-scope-1"
    assert hp_id == "production:hp-prod:release-scope-1"


def test_releasing_one_allocation_does_not_release_other_host(mock_central_client):
    allocation_lenovo = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    allocation_hp = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    mock_central_client.release_allocation.return_value = MagicMock(
        success=True,
        data={"allocation_id": allocation_lenovo, "status": "released"},
    )

    payload, is_error = invoke_tool(
        "portforge_allocation_release",
        {"allocation_id": allocation_lenovo, "confirm_mutate": True},
    )
    assert is_error is False
    assert payload["allocation_id"] == allocation_lenovo

    mock_central_client.release_allocation.assert_called_once_with(allocation_lenovo)
    assert allocation_hp not in [call.args[0] for call in mock_central_client.release_allocation.call_args_list]
