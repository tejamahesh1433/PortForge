"""Simulate an external coding agent using ONLY the CLI entry point.

No imports of allocation_service, no DB — Central is mocked at the client boundary.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO_ROOT / "fixtures" / "sample-stack"

_HOSTS = {"items": [{"id": "22222222-2222-2222-2222-222222222222", "hostname": "workstation"}]}  # matches fixture target.host

_ALLOCATION = {
    "allocation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "project": "sample-stack",
    "host": {"id": "22222222-2222-2222-2222-222222222222", "hostname": "workstation"},
    "status": "active",
    "allocations": [
        {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "port": 3100, "reservation_id": "r1"},
        {"name": "api", "purpose": "api", "protocol": "tcp", "port": 30080, "reservation_id": "r2"},
        {"name": "postgres", "purpose": "postgres", "protocol": "tcp", "port": 5433, "reservation_id": "r3"},
        {"name": "redis", "purpose": "redis", "protocol": "tcp", "port": 6380, "reservation_id": "r4"},
        {"name": "metrics", "purpose": "generic", "protocol": "tcp", "port": 9100, "reservation_id": "r5"},
    ],
}


def _copy_fixture(tmp_path: Path) -> Path:
    shutil.copytree(_FIXTURE, tmp_path / "stack")
    return tmp_path / "stack" / "portforge.yml"


def _central_mock():
    instance = MagicMock()
    instance.list_hosts.return_value = MagicMock(success=True, data=_HOSTS)
    ports_by_purpose = {
        "frontend": 3100,
        "api": 30080,
        "postgres": 5433,
        "redis": 6380,
        "generic": 9100,
    }  # postgres/redis purposes match manifest ports.*.purpose
    def _recommend(_host_id, purpose, _protocol):
        return MagicMock(success=True, data={"recommended_port": ports_by_purpose.get(purpose, 8100)})

    instance.get_recommendation.side_effect = _recommend
    create_result = MagicMock(success=True, status_code=201, data=_ALLOCATION)
    instance.create_allocation.return_value = create_result
    get_result = MagicMock(success=True, data=_ALLOCATION)
    instance.get_allocation.return_value = get_result
    instance.list_allocations.return_value = MagicMock(success=True, data={"items": [_ALLOCATION]})
    instance.release_allocation.return_value = MagicMock(
        success=True,
        data={**_ALLOCATION, "status": "released", "allocations": []},
    )
    return instance


def test_external_agent_cli_only_workflow(tmp_path, capsys):
    manifest = _copy_fixture(tmp_path)
    url_args = ["--url", "http://central.example"]
    json_args = ["--json"]
    request_id = "external-agent-run-1"

    with patch("portforge_agent.central_client.CentralClient") as mock_cls:
        client = _central_mock()
        mock_cls.return_value = client

        assert main(["capabilities", *json_args]) == 0
        caps = json.loads(capsys.readouterr().out)
        assert caps["capabilities"]["project_provision"] is True

        assert main(["project", "inspect", str(manifest), *url_args, *json_args]) == 0
        inspect = json.loads(capsys.readouterr().out)
        assert inspect["project"] == "sample-stack"
        assert len(inspect["config_targets"]) >= 3

        assert (
            main(
                [
                    "project",
                    "provision",
                    str(manifest),
                    "--request-id",
                    request_id,
                    "--dry-run",
                    *url_args,
                    *json_args,
                ]
            )
            == 0
        )
        dry = json.loads(capsys.readouterr().out)
        assert dry["dry_run"] is True
        assert client.create_allocation.call_count == 0

        assert (
            main(
                [
                    "project",
                    "provision",
                    str(manifest),
                    "--request-id",
                    request_id,
                    "--project-root",
                    str(manifest.parent),
                    *url_args,
                    *json_args,
                ]
            )
            == 0
        )
        applied = json.loads(capsys.readouterr().out)
        assert applied["status"] == "APPLIED"
        allocation_id = applied["allocation"]["id"]
        mutation_id = applied["config"]["mutation_id"]
        assert client.create_allocation.call_count == 1

        assert main(["allocation", "get", allocation_id, *url_args, *json_args]) == 0
        fetched = json.loads(capsys.readouterr().out)
        assert fetched["status"] == "active"

        assert (
            main(
                [
                    "config",
                    "rollback",
                    mutation_id,
                    "--project-root",
                    str(manifest.parent),
                    *json_args,
                ]
            )
            == 0
        )
        rollback = json.loads(capsys.readouterr().out)
        assert rollback["status"] == "ROLLED_BACK"
        assert rollback["allocation_id"] == allocation_id

        assert main(["allocation", "get", allocation_id, *url_args, *json_args]) == 0
        still_active = json.loads(capsys.readouterr().out)
        assert still_active["status"] == "active"

        assert main(["allocation", "release", allocation_id, *url_args, *json_args]) == 0
        client.release_allocation.assert_called_once_with(allocation_id)
