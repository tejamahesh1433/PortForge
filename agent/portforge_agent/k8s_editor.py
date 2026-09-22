"""v1.1-C: safe, style-preserving Kubernetes YAML port editing, for LOCAL
single-node development clusters only (kind, Docker Desktop Kubernetes).

See docs/v1.1/kubernetes-design.md for the full rationale. The short
version: Kubernetes has five things people call "a port," and only two are
ever real host-network resources PortForge's allocation model can own:

  - `hostPort` (container port bound directly on the node's own network
    namespace) -- functionally identical to a Compose `"HOST:CONTAINER"`
    mapping's host side.
  - Service `nodePort` (opens on every node -- meaningless to "reserve on
    a specific host" in a real multi-node cluster, but in a local
    single-node cluster "every node" reduces to "the one dev machine").

`containerPort`, Service `port`, and Service `targetPort` are cluster-
internal routing values, never host-network resources -- this module never
reads, matches on as a mutation target, or writes any of them except as an
immutable match key (`containerPort`/`servicePort` locate the entry to
mutate; their own values are never changed).

Uses `ruamel.yaml`'s round-trip mode in MULTI-DOCUMENT form
(`load_all`/`dump_all`), the same round-trip settings already proven for
Compose (`compose_editor.py`), since a real Kubernetes manifest file
commonly holds a Deployment and a Service (and other resources) together,
`---`-separated. Round-trip mode (`typ="rt"`) never constructs arbitrary
Python objects from YAML tags -- this is the same safety property
`compose_editor.py` and `manifest.py`'s `yaml.safe_load()` already rely on,
just via a different (but equally safe) loader.

Unlike Compose's `apply_port_mapping()` (which appends a brand-new port
entry when nothing matches), this module's mutators FAIL when the
referenced `containerPort`/`servicePort` entry does not already exist in
the YAML, rather than inventing Kubernetes structure. A Compose short-
syntax string ("8000:8000") is trivially safe to synthesize; a Kubernetes
container port entry has more shape (name, protocol casing, sibling
fields) that would require guessing. See docs/v1.1/v1.1-c-implementation.md
for why this is a deliberate divergence from the original design sketch's
"mirror Compose's zero-match-appends behavior" and from
docs/v1.1/kubernetes-design.md's own original wording (updated alongside
this module).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

# A real multi-resource manifest (Deployment + Service + ConfigMap, etc.)
# is a handful of documents -- this bounds pathological/malicious input,
# not real usage (see docs/v1.1/kubernetes-design.md's scope note and task
# §11's "bound file size / document count / mapping count").
MAX_KUBERNETES_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_KUBERNETES_DOCUMENTS = 50

# Kept deliberately small and explicit -- v1.1-C only needs to locate a Pod
# template to reach `spec.template.spec.containers[]` for a hostPort
# mapping. DaemonSet/Pod were mentioned in the original design sketch but
# are out of scope for this increment (see
# docs/v1.1/v1.1-c-implementation.md "known limitations").
SUPPORTED_WORKLOAD_KINDS = ("Deployment", "StatefulSet")

# Kubernetes' own default NodePort range for a local single-node cluster
# (kind, Docker Desktop Kubernetes). v1.1-C has no authoritative way to
# discover a cluster's actual configured range (that would require live
# cluster access, which config plan/apply deliberately never has -- see
# task §21 "no cluster mutation during config apply"), so this is the
# supported-local-cluster assumption from docs/v1.1/kubernetes-design.md,
# validated explicitly rather than silently trusted.
DEFAULT_NODEPORT_RANGE = (30000, 32767)

_SUPPORTED_SERVICE_TYPES_FOR_NODEPORT = ("NodePort", "LoadBalancer")


class KubernetesError(Exception):
    def __init__(self, code: str, message: str, details: Optional[List[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class KubernetesChange:
    kind: str  # "Deployment" | "StatefulSet" | "Service"
    name: str
    field: str  # "hostPort" | "nodePort"
    container: Optional[str]  # set for hostPort changes, None for nodePort
    match_port: int  # the containerPort or servicePort used to locate the entry (never itself changed)
    before: Optional[int]
    after: int
    action: str  # always "update" here -- v1.1-C never invents a new port entry (see module docstring)


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_kubernetes_documents(text: str) -> List[Any]:
    """Parses a `---`-separated multi-document Kubernetes YAML file.
    Returns the list of parsed documents in file order (a document that
    was entirely blank/comments-only round-trips as `None` and is kept in
    place -- callers skip non-dict documents when matching resources, but
    `dump_kubernetes_documents` must still re-emit it to preserve document
    boundaries).
    """
    if len(text.encode("utf-8")) > MAX_KUBERNETES_FILE_BYTES:
        raise KubernetesError(
            "CONFIG_TOO_LARGE",
            f"Kubernetes file is larger than the {MAX_KUBERNETES_FILE_BYTES}-byte limit.",
        )
    try:
        docs = list(_yaml().load_all(text))
    except Exception as exc:  # ruamel raises its own YAMLError subclasses
        raise KubernetesError("CONFIG_PARSE_ERROR", f"Kubernetes file is not valid YAML: {exc}")

    if len(docs) > MAX_KUBERNETES_DOCUMENTS:
        raise KubernetesError(
            "CONFIG_TOO_LARGE",
            f"Kubernetes file has {len(docs)} YAML documents, exceeding the "
            f"{MAX_KUBERNETES_DOCUMENTS}-document limit.",
        )
    return docs


def dump_kubernetes_documents(docs: List[Any]) -> str:
    stream = io.StringIO()
    _yaml().dump_all(docs, stream)
    return stream.getvalue()


def _find_resource(docs: List[Any], kind: str, name: str, namespace: Optional[str]) -> Tuple[int, dict]:
    """Locates exactly one resource matching `(kind, metadata.name)`, and
    `metadata.namespace` too if `namespace` was given explicitly in the
    manifest mapping. Never falls back to "the first thing that looks
    close" -- zero or multiple matches both fail safely, no mutation.
    """
    matches: List[int] = []
    for i, doc in enumerate(docs):
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") != kind:
            continue
        metadata = doc.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            continue
        if namespace is not None and metadata.get("namespace") != namespace:
            continue
        matches.append(i)

    if len(matches) == 0:
        raise KubernetesError(
            "KUBERNETES_RESOURCE_NOT_FOUND",
            f"No {kind} named '{name}'" + (f" in namespace '{namespace}'" if namespace else "") + " found in file.",
            details=[{"kind": kind, "name": name, "namespace": namespace}],
        )
    if len(matches) > 1:
        raise KubernetesError(
            "KUBERNETES_RESOURCE_AMBIGUOUS",
            f"{len(matches)} {kind} resources named '{name}' found in file -- PortForge cannot safely "
            "determine which one to update. Add an explicit 'namespace' to the manifest mapping to disambiguate.",
            details=[{"kind": kind, "name": name}],
        )
    return matches[0], docs[matches[0]]


def apply_host_port(
    docs: List[Any],
    kind: str,
    name: str,
    namespace: Optional[str],
    container: str,
    container_port: int,
    protocol: str,
    new_host_port: int,
) -> Tuple[List[Any], KubernetesChange]:
    """Mutates `docs` IN PLACE. Locates `kind`/`name` (optionally
    `namespace`) -> `spec.template.spec.containers[]` entry named
    `container` -> its `ports[]` entry matching `(container_port,
    protocol)` -> sets ONLY that entry's `hostPort`. Never touches
    `containerPort`, `name`, or `protocol` on that entry, and never adds a
    new ports entry (see module docstring).
    """
    if kind not in SUPPORTED_WORKLOAD_KINDS:
        raise KubernetesError(
            "KUBERNETES_KIND_UNSUPPORTED",
            f"'{kind}' is not a supported hostPort workload kind for v1.1-C. Supported: "
            f"{', '.join(SUPPORTED_WORKLOAD_KINDS)}.",
            details=[{"kind": kind}],
        )

    _, doc = _find_resource(docs, kind, name, namespace)

    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise KubernetesError("CONFIG_PARSE_ERROR", f"{kind} '{name}' has no 'spec'.")
    template = spec.get("template")
    if not isinstance(template, dict):
        raise KubernetesError("CONFIG_PARSE_ERROR", f"{kind} '{name}' has no 'spec.template'.")
    pod_spec = template.get("spec")
    if not isinstance(pod_spec, dict):
        raise KubernetesError("CONFIG_PARSE_ERROR", f"{kind} '{name}' has no 'spec.template.spec'.")
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        raise KubernetesError("CONFIG_PARSE_ERROR", f"{kind} '{name}' has no 'spec.template.spec.containers'.")

    container_matches = [c for c in containers if isinstance(c, dict) and c.get("name") == container]
    if len(container_matches) == 0:
        raise KubernetesError(
            "KUBERNETES_CONTAINER_NOT_FOUND",
            f"Container '{container}' not found in {kind} '{name}'.",
            details=[{"kind": kind, "name": name, "container": container}],
        )
    if len(container_matches) > 1:
        raise KubernetesError(
            "KUBERNETES_CONTAINER_AMBIGUOUS",
            f"Multiple containers named '{container}' found in {kind} '{name}'.",
            details=[{"kind": kind, "name": name, "container": container}],
        )
    container_map = container_matches[0]

    ports_list = container_map.get("ports")
    if not isinstance(ports_list, list):
        ports_list = []

    matches = []
    for i, entry in enumerate(ports_list):
        if not isinstance(entry, dict):
            continue
        if entry.get("containerPort") != container_port:
            continue
        entry_protocol = str(entry.get("protocol", "TCP")).upper()
        if entry_protocol == protocol.upper():
            matches.append(i)

    if len(matches) > 1:
        raise KubernetesError(
            "KUBERNETES_PORT_AMBIGUOUS",
            f"Container '{container}' in {kind} '{name}' has {len(matches)} port entries for "
            f"containerPort {container_port}/{protocol} -- PortForge cannot safely determine which one to update.",
            details=[{"kind": kind, "name": name, "container": container, "container_port": container_port}],
        )
    if len(matches) == 0:
        raise KubernetesError(
            "KUBERNETES_PORT_NOT_FOUND",
            f"No containerPort {container_port}/{protocol} entry found on container '{container}' in "
            f"{kind} '{name}'. PortForge will not invent a new port entry -- add it to the YAML first.",
            details=[{"kind": kind, "name": name, "container": container, "container_port": container_port}],
        )

    entry = ports_list[matches[0]]
    before = entry.get("hostPort")
    entry["hostPort"] = new_host_port

    change = KubernetesChange(
        kind=kind,
        name=name,
        field="hostPort",
        container=container,
        match_port=container_port,
        before=before,
        after=new_host_port,
        action="update",
    )
    return docs, change


def apply_node_port(
    docs: List[Any],
    name: str,
    namespace: Optional[str],
    service_port: int,
    protocol: str,
    new_node_port: int,
) -> Tuple[List[Any], KubernetesChange]:
    """Mutates `docs` IN PLACE. Locates the `Service` named `name` ->
    validates its `spec.type` actually supports `nodePort` -> its
    `spec.ports[]` entry matching `(service_port, protocol)` (`port`, the
    Service's own cluster-facing port -- NOT `targetPort`) -> sets ONLY
    that entry's `nodePort`. Never changes `spec.type`, `port`, or
    `targetPort`.
    """
    _, doc = _find_resource(docs, "Service", name, namespace)

    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise KubernetesError("CONFIG_PARSE_ERROR", f"Service '{name}' has no 'spec'.")

    service_type = spec.get("type", "ClusterIP")
    if service_type not in _SUPPORTED_SERVICE_TYPES_FOR_NODEPORT:
        raise KubernetesError(
            "KUBERNETES_SERVICE_TYPE_UNSUPPORTED",
            f"Service '{name}' has type '{service_type}', which does not support nodePort. PortForge will "
            "not change a Service's type -- set 'spec.type: NodePort' in the YAML first.",
            details=[{"name": name, "type": service_type}],
        )

    ports_list = spec.get("ports")
    if not isinstance(ports_list, list):
        raise KubernetesError(
            "KUBERNETES_PORT_NOT_FOUND", f"Service '{name}' has no 'spec.ports'.", details=[{"name": name}]
        )

    matches = []
    for i, entry in enumerate(ports_list):
        if not isinstance(entry, dict):
            continue
        if entry.get("port") != service_port:
            continue
        entry_protocol = str(entry.get("protocol", "TCP")).upper()
        if entry_protocol == protocol.upper():
            matches.append(i)

    if len(matches) > 1:
        raise KubernetesError(
            "KUBERNETES_PORT_AMBIGUOUS",
            f"Service '{name}' has {len(matches)} port entries for port {service_port}/{protocol} -- "
            "PortForge cannot safely determine which one to update.",
            details=[{"name": name, "service_port": service_port}],
        )
    if len(matches) == 0:
        raise KubernetesError(
            "KUBERNETES_PORT_NOT_FOUND",
            f"No Service port {service_port}/{protocol} entry found on Service '{name}'. PortForge will not "
            "invent a new port entry -- add it to the YAML first.",
            details=[{"name": name, "service_port": service_port}],
        )

    entry = ports_list[matches[0]]
    before = entry.get("nodePort")
    entry["nodePort"] = new_node_port

    change = KubernetesChange(
        kind="Service",
        name=name,
        field="nodePort",
        container=None,
        match_port=service_port,
        before=before,
        after=new_node_port,
        action="update",
    )
    return docs, change
