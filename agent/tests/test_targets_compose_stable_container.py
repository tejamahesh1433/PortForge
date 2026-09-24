"""Compose published-port mutation keeps container port stable (Phase 17)."""
from __future__ import annotations

from portforge_agent.compose_editor import apply_port_mapping, dump_compose, load_compose

from .targets_helpers import FIXTURE_ROOT


def test_apply_port_mapping_changes_published_not_container():
    compose_path = FIXTURE_ROOT / "docker-compose.yml"
    data = load_compose(compose_path.read_text(encoding="utf-8"))

    data, change = apply_port_mapping(data, "api", 8000, "tcp", 18000)
    out = dump_compose(data)

    assert change.before_host == "8000"
    assert change.after_host == "18000"
    assert change.container_port == 8000
    api_section = out.split("  api:")[1].split("\n  postgres:")[0]
    assert '"18000:8000"' in api_section
    assert '"8000:8000"' not in api_section

    # Windows dev: 8000:8000 — Lenovo prod: 18000:8000 (container stays 8000)
    assert change.container_port == 8000, (
        "Container port must remain 8000 when published host port changes "
        "(Windows 8000:8000 vs Lenovo 18000:8000)"
    )
