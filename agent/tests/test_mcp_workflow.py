"""MCP workflow integration on sample-stack with mocked Central."""
from __future__ import annotations

from portforge_agent.mcp.tools import TOOL_SPECS

from .mcp_helpers import copy_sample_stack, file_hashes, invoke_tool, tool_error_code


def _stack_args(root, manifest):
    return {
        "project_root": str(root),
        "manifest_path": str(manifest),
        "central_url": "http://central.example",
    }


def test_full_workflow_capabilities_to_release(tmp_path, mock_central_client):
    root, manifest = copy_sample_stack(tmp_path)
    config_files = [".env", "docker-compose.yml", "k8s/stack.yaml"]

    caps, err = invoke_tool("portforge_capabilities", {"central_url": "http://central.example"})
    assert err is False
    assert caps["capabilities"]["mcp"] is True
    assert "kubernetes-hostPort" in caps["supported_mutation_types"]
    assert "containerPort" in caps["unsupported_automatic_mutations"]

    inspect, err = invoke_tool("portforge_project_inspect", _stack_args(root, manifest))
    assert err is False
    assert inspect["project"] == "sample-stack"
    assert len(inspect["services"]) == 5
    assert len(inspect["config_targets"]) >= 3

    plan, err = invoke_tool("portforge_project_plan", _stack_args(root, manifest))
    assert err is False
    assert plan["committed"] is False
    assert plan["plan_id"]
    assert plan["plan_hash"]
    plan_id = plan["plan_id"]

    provision, err = invoke_tool(
        "portforge_project_provision",
        {
            **_stack_args(root, manifest),
            "request_id": "mcp-workflow-1",
            "confirm_mutate": True,
            "plan_id": plan_id,
        },
    )
    assert err is False
    assert provision["status"] == "APPLIED"
    mutation_id = provision["config"]["mutation_id"]
    allocation_id = provision["allocation"]["id"]
    assert mock_central_client.create_allocation.call_count == 1

    verify, err = invoke_tool(
        "portforge_project_verify",
        {
            **_stack_args(root, manifest),
            "request_id": "mcp-workflow-1",
            "allocation_id": allocation_id,
        },
    )
    assert err is False
    assert verify["workflow"]["status"] == "APPLIED"

    rollback, err = invoke_tool(
        "portforge_project_rollback",
        {
            "project_root": str(root),
            "mutation_id": mutation_id,
            "confirm_mutate": True,
        },
    )
    assert err is False
    assert rollback["status"] == "ROLLED_BACK"

    hashes_before_release = file_hashes(root, config_files)

    release, err = invoke_tool(
        "portforge_allocation_release",
        {
            "allocation_id": allocation_id,
            "confirm_mutate": True,
            "central_url": "http://central.example",
        },
    )
    assert err is False
    hashes_after_release = file_hashes(root, config_files)
    assert hashes_after_release == hashes_before_release
    mock_central_client.release_allocation.assert_called_once_with(allocation_id)


def test_stale_plan_config_changed_since_plan(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    plan, err = invoke_tool("portforge_project_plan", _stack_args(root, manifest))
    assert err is False
    plan_id = plan["plan_id"]

    env_path = root / ".env"
    env_path.write_text(env_path.read_text() + "# stale marker\n", encoding="utf-8")

    payload, is_error = invoke_tool(
        "portforge_project_provision",
        {
            **_stack_args(root, manifest),
            "request_id": "stale-plan-1",
            "confirm_mutate": True,
            "plan_id": plan_id,
        },
    )
    assert is_error is True
    assert tool_error_code(payload) == "CONFIG_CHANGED_SINCE_PLAN"


def test_kubernetes_capabilities_and_no_container_port_tools():
    caps, err = invoke_tool("portforge_capabilities", {})
    assert err is False
    assert "kubernetes-hostPort" in caps["supported_mutation_types"]
    assert "kubernetes-nodePort" in caps["supported_mutation_types"]
    unsupported = caps["unsupported_automatic_mutations"]
    assert "containerPort" in unsupported
    assert "Service port" in unsupported
    assert "targetPort" in unsupported

    for spec in TOOL_SPECS:
        blob = f"{spec['name']} {spec['description']}".lower()
        assert "containerport" not in blob.replace("_", "")
        assert "rewrite_container_port" not in blob


def test_plan_kubernetes_fields_affected_hostport_nodeport_only(tmp_path):
    root, manifest = copy_sample_stack(tmp_path)
    plan, err = invoke_tool("portforge_project_plan", _stack_args(root, manifest))
    assert err is False
    k8s_fields = [f for f in plan["fields_affected"] if f["type"] == "kubernetes"]
    assert len(k8s_fields) == 1
    assert k8s_fields[0]["host_ports"] >= 1
    assert k8s_fields[0]["node_ports"] >= 1
