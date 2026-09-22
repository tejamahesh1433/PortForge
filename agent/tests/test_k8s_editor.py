"""v1.1-C: Kubernetes YAML editor tests (multi-document round-trip,
hostPort/nodePort mutation, resource identification, safety bounds)."""
from __future__ import annotations

import pytest

from portforge_agent.k8s_editor import (
    MAX_KUBERNETES_DOCUMENTS,
    MAX_KUBERNETES_FILE_BYTES,
    KubernetesError,
    apply_host_port,
    apply_node_port,
    dump_kubernetes_documents,
    load_kubernetes_documents,
)

DEPLOYMENT_AND_SERVICE = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  template:
    spec:
      containers:
        - name: web
          image: nginx
          ports:
            - containerPort: 3000
              protocol: TCP
---
# a comment on the service, must survive round-trip
apiVersion: v1
kind: Service
metadata:
  name: api-svc
spec:
  type: NodePort
  ports:
    - port: 8080
      targetPort: 8080
      protocol: TCP
"""


# --- multi-document round-trip -----------------------------------------------


def test_multi_document_load_preserves_document_count():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    assert len(docs) == 2


def test_round_trip_with_no_mutation_is_byte_identical():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    assert dump_kubernetes_documents(docs) == DEPLOYMENT_AND_SERVICE


def test_comments_and_unrelated_document_preserved_after_mutation():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, _ = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    out = dump_kubernetes_documents(docs)
    assert "# a comment on the service, must survive round-trip" in out
    assert "targetPort: 8080" in out  # untouched Service field


def test_unrelated_third_document_untouched():
    text = DEPLOYMENT_AND_SERVICE + "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unrelated\ndata:\n  FOO: bar\n"
    docs = load_kubernetes_documents(text)
    docs, _ = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    out = dump_kubernetes_documents(docs)
    assert "kind: ConfigMap" in out
    assert "FOO: bar" in out


def test_invalid_yaml_fails():
    with pytest.raises(KubernetesError) as exc_info:
        load_kubernetes_documents("kind: [unterminated")
    assert exc_info.value.code == "CONFIG_PARSE_ERROR"


def test_file_size_bound_enforced():
    huge = "a: " + "x" * (MAX_KUBERNETES_FILE_BYTES + 1)
    with pytest.raises(KubernetesError) as exc_info:
        load_kubernetes_documents(huge)
    assert exc_info.value.code == "CONFIG_TOO_LARGE"


def test_document_count_bound_enforced():
    text = "---\n".join(["apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c\n"] * (MAX_KUBERNETES_DOCUMENTS + 1))
    with pytest.raises(KubernetesError) as exc_info:
        load_kubernetes_documents(text)
    assert exc_info.value.code == "CONFIG_TOO_LARGE"


# --- hostPort mutation --------------------------------------------------------


def test_hostport_update_only_touches_matched_entry():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, change = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    out = dump_kubernetes_documents(docs)
    assert "hostPort: 31234" in out
    assert "containerPort: 3000" in out  # match key itself never changed
    assert change.before is None
    assert change.after == 31234
    assert change.action == "update"


def test_hostport_second_apply_updates_existing_hostport():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, _ = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    docs, change = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31999)
    out = dump_kubernetes_documents(docs)
    assert "hostPort: 31999" in out
    assert "hostPort: 31234" not in out
    assert change.before == 31234


def test_statefulset_kind_supported():
    text = DEPLOYMENT_AND_SERVICE.replace("kind: Deployment", "kind: StatefulSet")
    docs = load_kubernetes_documents(text)
    docs, change = apply_host_port(docs, "StatefulSet", "frontend", None, "web", 3000, "tcp", 31234)
    assert change.kind == "StatefulSet"


def test_unsupported_kind_rejected():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "DaemonSet", "frontend", None, "web", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_KIND_UNSUPPORTED"


def test_resource_not_found_fails_safely_zero_mutation():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "ghost", None, "web", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_RESOURCE_NOT_FOUND"
    assert dump_kubernetes_documents(docs) == DEPLOYMENT_AND_SERVICE


def test_ambiguous_resource_name_fails_safely():
    text = DEPLOYMENT_AND_SERVICE + "---\n" + DEPLOYMENT_AND_SERVICE.split("---")[0]
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_RESOURCE_AMBIGUOUS"


def test_namespace_disambiguates_duplicate_names():
    text = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n  namespace: dev\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n"
        "---\n"
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n  namespace: staging\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n"
    )
    docs = load_kubernetes_documents(text)
    docs, change = apply_host_port(docs, "Deployment", "frontend", "dev", "web", 3000, "tcp", 31234)
    out = dump_kubernetes_documents(docs)
    assert out.count("hostPort: 31234") == 1
    # Confirm it landed in the "dev" document, not "staging".
    dev_section, staging_section = out.split("namespace: staging")
    assert "hostPort: 31234" in dev_section


def test_missing_namespace_with_duplicate_names_is_ambiguous():
    text = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n  namespace: dev\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n"
        "---\n"
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n  namespace: staging\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n"
    )
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_RESOURCE_AMBIGUOUS"


def test_container_not_found_fails():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "frontend", None, "ghost-container", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_CONTAINER_NOT_FOUND"


def test_missing_container_port_entry_fails_does_not_invent_structure():
    """v1.1-C deliberately does NOT append a new port entry the way
    Compose's apply_port_mapping does -- see module docstring."""
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "frontend", None, "web", 9999, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_PORT_NOT_FOUND"
    assert dump_kubernetes_documents(docs) == DEPLOYMENT_AND_SERVICE  # zero mutation


def test_ambiguous_containerport_entries_fail_safely():
    text = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n              protocol: TCP\n"
        "            - containerPort: 3000\n              protocol: TCP\n"
    )
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    assert exc_info.value.code == "KUBERNETES_PORT_AMBIGUOUS"


def test_udp_protocol_matched_independently_of_tcp():
    text = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\n"
        "spec:\n  template:\n    spec:\n      containers:\n        - name: web\n          ports:\n"
        "            - containerPort: 3000\n              protocol: TCP\n"
        "            - containerPort: 3000\n              protocol: UDP\n"
    )
    docs = load_kubernetes_documents(text)
    docs, change = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "udp", 31234)
    out = dump_kubernetes_documents(docs)
    assert change.action == "update"
    # Only the UDP entry should carry the new hostPort.
    assert out.count("hostPort: 31234") == 1


def test_containerport_and_container_name_never_mutated():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, _ = apply_host_port(docs, "Deployment", "frontend", None, "web", 3000, "tcp", 31234)
    out = dump_kubernetes_documents(docs)
    assert "name: web" in out
    assert "containerPort: 3000" in out


# --- nodePort mutation ---------------------------------------------------------


def test_nodeport_update_only_touches_matched_entry():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, change = apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    out = dump_kubernetes_documents(docs)
    assert "nodePort: 30555" in out
    assert "port: 8080" in out
    assert "targetPort: 8080" in out  # never changed
    assert change.field == "nodePort"
    assert change.container is None


def test_nodeport_service_not_found():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_node_port(docs, "ghost-svc", None, 8080, "tcp", 30555)
    assert exc_info.value.code == "KUBERNETES_RESOURCE_NOT_FOUND"


def test_nodeport_missing_service_port_entry_fails():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    with pytest.raises(KubernetesError) as exc_info:
        apply_node_port(docs, "api-svc", None, 9999, "tcp", 30555)
    assert exc_info.value.code == "KUBERNETES_PORT_NOT_FOUND"
    assert dump_kubernetes_documents(docs) == DEPLOYMENT_AND_SERVICE


def test_nodeport_clusterip_service_type_rejected():
    text = DEPLOYMENT_AND_SERVICE.replace("type: NodePort", "type: ClusterIP")
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    assert exc_info.value.code == "KUBERNETES_SERVICE_TYPE_UNSUPPORTED"


def test_nodeport_missing_type_defaults_to_clusterip_and_is_rejected():
    text = DEPLOYMENT_AND_SERVICE.replace("  type: NodePort\n", "")
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    assert exc_info.value.code == "KUBERNETES_SERVICE_TYPE_UNSUPPORTED"


def test_nodeport_loadbalancer_type_accepted():
    text = DEPLOYMENT_AND_SERVICE.replace("type: NodePort", "type: LoadBalancer")
    docs = load_kubernetes_documents(text)
    docs, change = apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    assert change.after == 30555


def test_nodeport_ambiguous_service_ports_fail_safely():
    text = DEPLOYMENT_AND_SERVICE.replace(
        "  ports:\n    - port: 8080\n      targetPort: 8080\n      protocol: TCP\n",
        "  ports:\n    - port: 8080\n      targetPort: 8080\n      protocol: TCP\n"
        "    - port: 8080\n      targetPort: 9090\n      protocol: TCP\n",
    )
    docs = load_kubernetes_documents(text)
    with pytest.raises(KubernetesError) as exc_info:
        apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    assert exc_info.value.code == "KUBERNETES_PORT_AMBIGUOUS"


def test_service_port_and_target_port_never_mutated():
    docs = load_kubernetes_documents(DEPLOYMENT_AND_SERVICE)
    docs, _ = apply_node_port(docs, "api-svc", None, 8080, "tcp", 30555)
    out = dump_kubernetes_documents(docs)
    assert "port: 8080" in out
    assert "targetPort: 8080" in out
