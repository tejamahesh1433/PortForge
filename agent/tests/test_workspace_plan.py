"""Coordinated workspace planning."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from portforge_agent.models import DiscoveredPort, Protocol, Source
from portforge_agent.project_adapter import NormalizedHostRef
from portforge_agent.workspace.discover import discover_workspace
from portforge_agent.workspace.plan import plan_workspace

from .mcp_helpers import central_mock, workspace_fixture_path


def _discover(path, **kwargs):
    defaults = {"include_local_runtime": False}
    defaults.update(kwargs)
    return discover_workspace(path, **defaults)


def _local_port(port: int) -> DiscoveredPort:
    return DiscoveredPort(
        hostname="local",
        host_id="local",
        operating_system="test",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        process_name="test-proc",
    )


def test_plan_preserves_free_ports():
    model = _discover(workspace_fixture_path("A"))
    plan = plan_workspace(model)
    assert plan["ready"] is True
    for row in plan["services"]:
        assert row["current_port"] == row["proposed_port"]
        assert row["reason"]["code"] == "PRESERVE"


def test_plan_conflict_proposes_replacement_via_central():
    path = workspace_fixture_path("A")
    with patch("portforge_agent.workspace.discover.discover_all_ports", return_value=[_local_port(3000)]):
        model = discover_workspace(path, include_local_runtime=True)
    client = central_mock()
    client.get_recommendation.side_effect = lambda _host_id, _purpose, _protocol: MagicMock(
        success=True,
        data={"recommended_port": 3100},
    )
    host = NormalizedHostRef(id="22222222-2222-2222-2222-222222222222", hostname="workstation")
    plan = plan_workspace(model, client=client, host=host)
    conflict_rows = [row for row in plan["services"] if row["reason"]["conflicts"]]
    assert conflict_rows
    assert any(row["proposed_port"] == 3100 for row in conflict_rows)
    client.get_recommendation.assert_called()


def test_plan_conflict_without_central_proposed_null_with_reason():
    path = workspace_fixture_path("A")
    with patch("portforge_agent.workspace.discover.discover_all_ports", return_value=[_local_port(3000)]):
        model = discover_workspace(path, include_local_runtime=True)
    plan = plan_workspace(model, client=None)
    conflict_rows = [row for row in plan["services"] if row["reason"]["conflicts"]]
    assert conflict_rows
    assert any(row["proposed_port"] is None for row in conflict_rows)
    assert any(reason.get("code") == "NO_ALTERNATIVE" for reason in plan.get("reasons") or [])


def test_workspace_fingerprint_stable_for_same_tree():
    path = workspace_fixture_path("A")
    first = _discover(path)
    second = _discover(path)
    assert first.workspace_fingerprint == second.workspace_fingerprint


def test_concurrent_plan_workspace_no_crash():
    path = workspace_fixture_path("B")
    model = _discover(path)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            plan_workspace(model)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert not errors
