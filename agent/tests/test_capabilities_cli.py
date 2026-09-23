"""Phase 11+13: capabilities CLI alias and additive contract fields."""
from __future__ import annotations

import json

from portforge_agent.agent_contract import CONTRACT_VERSION, build_contract
from portforge_agent.cli import main


def test_capabilities_matches_build_contract(capsys):
    expected = build_contract()
    assert main(["capabilities", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_capabilities_alias_matches_agent_contract(capsys):
    assert main(["capabilities", "--json"]) == 0
    cap_out = capsys.readouterr().out
    assert main(["agent-contract", "--json"]) == 0
    assert json.loads(cap_out) == json.loads(capsys.readouterr().out)


def test_additive_capabilities_present():
    caps = build_contract()["capabilities"]
    assert caps["batch_allocation"] is True
    assert caps["dry_run"] is True
    assert caps["project_inspect"] is True
    assert caps["project_provision"] is True
    assert caps["kubernetes_hostPort"] is True
    assert caps["kubernetes_nodePort"] is True
    assert caps["kubernetes_containerPort_auto"] is False
    assert caps["kubernetes_service_port_auto"] is False
    assert caps["kubernetes_targetPort_auto"] is False
    assert caps["mcp"] is False


def test_machine_interface_contract_version_unchanged():
    contract = build_contract()
    assert contract["contract_version"] == CONTRACT_VERSION == 1
    assert contract["machine_interface"]["contract_version"] == 1
