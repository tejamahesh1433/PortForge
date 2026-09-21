"""v1.1-A: CLI-level tests for `portforge doctor` -- JSON purity, human
output, exit codes. Follows the same mocking pattern as
test_cli_allocation.py/test_cli_project.py.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from portforge_agent.cli import main


def _mock_client(healthy=True):
    instance = MagicMock()
    if healthy:
        instance.health.return_value = MagicMock(
            success=True,
            data={"status": "ok", "service": "portforge", "database": "connected", "version": "1.1.0", "protocol_version": 1},
        )
    else:
        instance.health.return_value = MagicMock(success=False, error="connection refused")
    return instance


def _patch_common(client):
    return [
        patch("portforge_agent.central_client.CentralClient", return_value=client),
        patch("portforge_agent.doctor.load_central_config", return_value=MagicMock(enabled=False)),
        patch("portforge_agent.doctor.load_credential", return_value=None),
        patch("portforge_agent.doctor.service_ops.status", return_value=MagicMock(success=True, message="running")),
        patch("portforge_agent.doctor.is_docker_available", return_value=True),
        patch("portforge_agent.doctor.discover_all_ports", return_value=[]),
    ]


def test_doctor_json_output_is_pure_json(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _mock_client(healthy=True)
    patches = _patch_common(client)
    for p in patches:
        p.start()
    try:
        result = main(["doctor", "--url", "http://central.example", "--json"])
    finally:
        for p in patches:
            p.stop()

    captured = capsys.readouterr()
    assert captured.err == ""
    parsed = json.loads(captured.out)  # must not raise -- pure JSON, no banners/progress text
    assert parsed["status"] in ("ok", "degraded", "error")
    assert result == 0


def test_doctor_healthy_exit_code_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _mock_client(healthy=True)
    patches = _patch_common(client)
    for p in patches:
        p.start()
    try:
        result = main(["doctor", "--url", "http://central.example", "--json"])
    finally:
        for p in patches:
            p.stop()
    assert result == 0


def test_doctor_central_unreachable_produces_error_and_exit_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _mock_client(healthy=False)
    patches = _patch_common(client)
    for p in patches:
        p.start()
    try:
        result = main(["doctor", "--url", "http://central.example", "--json"])
    finally:
        for p in patches:
            p.stop()
    assert result == 1


def test_doctor_human_output_has_plain_ascii_markers(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _mock_client(healthy=True)
    patches = _patch_common(client)
    for p in patches:
        p.start()
    try:
        main(["doctor", "--url", "http://central.example"])
    finally:
        for p in patches:
            p.stop()

    out = capsys.readouterr().out
    assert "PASS" in out or "WARN" in out
    assert "Overall:" in out
    # No non-ASCII check/cross marks (Windows console code page safety,
    # matching cli.py's existing convention for `next`'s validation steps).
    assert all(ord(ch) < 128 for ch in out)


def test_doctor_never_writes_to_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    client = _mock_client(healthy=True)
    patches = _patch_common(client)
    for p in patches:
        p.start()
    try:
        main(["doctor", "--url", "http://central.example", "--json"])
    finally:
        for p in patches:
            p.stop()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
