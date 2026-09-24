"""Workspace conflict detection kinds."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from portforge_agent.models import DiscoveredPort, Protocol, Source
from portforge_agent.reservations.models import Reservation
from portforge_agent.workspace.discover import discover_workspace

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


def test_internal_project_conflict(tmp_path):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  web:\n    ports:\n      - \"3000:3000\"\n"
        "  api:\n    ports:\n      - \"3000:8000\"\n",
        encoding="utf-8",
    )
    model = _discover(tmp_path)
    kinds = {conflict.kind for conflict in model.conflicts}
    assert "INTERNAL_PROJECT_CONFLICT" in kinds


def test_local_runtime_conflict():
    path = workspace_fixture_path("A")
    with patch("portforge_agent.workspace.discover.discover_all_ports", return_value=[_local_port(3000)]):
        model = discover_workspace(path, include_local_runtime=True)
    assert any(conflict.kind == "LOCAL_RUNTIME_CONFLICT" for conflict in model.conflicts)


def test_central_allocation_conflict():
    path = workspace_fixture_path("A")
    client = central_mock()
    client.list_allocations.return_value = MagicMock(
        success=True,
        data={
            "items": [
                {
                    "allocation_id": "other",
                    "project": "other-project",
                    "status": "active",
                    "allocations": [{"name": "web", "port": 3000, "protocol": "tcp"}],
                }
            ]
        },
    )
    with patch("portforge_agent.workspace.discover.CentralClient", return_value=client):
        model = discover_workspace(path, central_url="http://central.example", include_local_runtime=False)
    assert model.central_available is True
    assert any(conflict.kind == "CENTRAL_ALLOCATION_CONFLICT" for conflict in model.conflicts)


def test_reservation_conflict():
    path = workspace_fixture_path("G")
    reservation = Reservation.create(
        host_id="local",
        port=3000,
        project="other-project",
        protocol=Protocol.TCP,
        purpose="generic",
    )
    with patch("portforge_agent.workspace.discover.discover_all_ports", return_value=[]):
        with patch("portforge_agent.workspace.discover.ReservationStore") as store_cls:
            store_cls.return_value.load.return_value = [reservation]
            model = discover_workspace(path, include_local_runtime=True)
    assert any(conflict.kind == "RESERVATION_CONFLICT" for conflict in model.conflicts)


def test_configuration_conflict_fixture_h():
    model = _discover(workspace_fixture_path("H"))
    assert any(conflict.kind == "CONFIGURATION_CONFLICT" for conflict in model.conflicts)


def test_central_unavailable_still_returns_model():
    path = workspace_fixture_path("A")
    client = central_mock()
    client.list_allocations.return_value = MagicMock(success=False, error="connection refused", data=None)
    with patch("portforge_agent.workspace.discover.CentralClient", return_value=client):
        model = discover_workspace(path, central_url="http://central.example", include_local_runtime=False)
    assert model.central_available is False
    assert model.central_error
    assert model.services
