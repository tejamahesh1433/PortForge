"""Shared helpers for Phase 17 environment-target pytest suite."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

PLAN_CENTRAL_PATCH_TARGET = "portforge_agent.targets.plan.CentralClient"

_REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = _REPO_ROOT / "fixtures" / "targets" / "multi-host-app"

HOST_WINDOWS = "11111111-1111-1111-1111-111111111111"
HOST_MAC = "22222222-2222-2222-2222-222222222222"
HOST_LENOVO = "33333333-3333-3333-3333-333333333333"
HOST_HP = "44444444-4444-4444-4444-444444444444"

INTERNAL_PORTS = {"frontend": 3000, "api": 8000, "postgres": 5432, "redis": 6379}

LENOVO_CONFLICT_PORTS = [3000, 8000, 5432, 6379]
LENOVO_RECOMMENDATIONS = {
    "frontend": 13000,
    "api": 18000,
    "postgres": 15432,
    "redis": 16379,
}

HP_CONFLICT_PORTS = [3000, 8000]
HP_RECOMMENDATIONS = {
    "frontend": 23000,
    "api": 28000,
    "postgres": 5432,
    "redis": 6379,
}


def copy_targets_fixture(tmp_path: Path) -> Path:
    dst = tmp_path / "multi-host-app"
    shutil.copytree(FIXTURE_ROOT, dst, ignore=shutil.ignore_patterns(".portforge"))
    return dst


def _host_items(*, decommissioned_host_id: Optional[str] = None) -> list[dict]:
    items = [
        {"id": HOST_WINDOWS, "hostname": "windows-dev"},
        {"id": HOST_MAC, "hostname": "macbook-dev"},
        {"id": HOST_LENOVO, "hostname": "lenovo-prod"},
        {"id": HOST_HP, "hostname": "hp-prod"},
    ]
    if decommissioned_host_id:
        for item in items:
            if item["id"] == decommissioned_host_id:
                item["decommissioned_at"] = "2026-01-01T00:00:00+00:00"
    return items


def _allocation_item(project: str, host_id: str, ports: list[int], protocol: str = "tcp") -> dict:
    return {
        "project": project,
        "host": {"id": host_id},
        "status": "active",
        "allocations": [{"port": port, "protocol": protocol} for port in ports],
    }


def targets_central_mock(
    *,
    allocation_items: Optional[list[dict]] = None,
    list_allocations_success: bool = True,
    list_hosts_success: bool = True,
    recommendation_map: Optional[dict[str, int]] = None,
    get_recommendation_success: bool = True,
    decommissioned_host_id: Optional[str] = None,
    list_allocations_error: Optional[str] = None,
) -> MagicMock:
    instance = MagicMock()
    instance.health.return_value = MagicMock(success=True, error=None)

    if list_hosts_success:
        instance.list_hosts.return_value = MagicMock(
            success=True,
            data={"items": _host_items(decommissioned_host_id=decommissioned_host_id)},
        )
    else:
        instance.list_hosts.return_value = MagicMock(success=False, error="Central unreachable")

    items = allocation_items if allocation_items is not None else []
    if list_allocations_success:
        instance.list_allocations.return_value = MagicMock(success=True, data={"items": items})
    else:
        instance.list_allocations.return_value = MagicMock(
            success=False,
            error=list_allocations_error or "Central unavailable",
        )

    rec_map = recommendation_map or {}

    def _recommend(_host_id, purpose, _protocol):
        if not get_recommendation_success:
            return MagicMock(success=False, error="recommendation unavailable")
        port = rec_map.get(purpose, 8100)
        return MagicMock(success=True, data={"recommended_port": port})

    instance.get_recommendation.side_effect = _recommend
    return instance


def lenovo_conflict_allocations() -> list[dict]:
    return [_allocation_item("other-project", HOST_LENOVO, LENOVO_CONFLICT_PORTS)]


def hp_conflict_allocations() -> list[dict]:
    return [_allocation_item("other-project", HOST_HP, HP_CONFLICT_PORTS)]


def combined_conflict_allocations() -> list[dict]:
    return lenovo_conflict_allocations() + hp_conflict_allocations()


def service_map(payload: dict) -> dict[str, dict]:
    return {row["service"]: row for row in payload["services"]}
