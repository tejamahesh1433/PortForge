# v1.1 Design: Kubernetes Configuration Integration

**Design only — not implemented in this task.** This is the previously
deferred Phase 8C.1 concept (`docs/phase8c_safe_config.md`'s "Kubernetes
decision"). Scope is deliberately narrow: local development clusters, not
production multi-node clusters.

## The core distinction PortForge must respect

Kubernetes has (at least) five things people casually call "a port," and
only two of them are ever real host-network resources the way a bare
process's `bind()` or a Compose `hostPort:` mapping is:

| Field | Where it lives | Is it a real host port? |
|---|---|---|
| `containerPort` | `spec.containers[].ports[]` on a Pod/Deployment/StatefulSet/DaemonSet | **No.** Only exists inside the pod's own network namespace. Never touches the node's host network. |
| Service `port` | `spec.ports[]` on a `Service` | **No.** Cluster-internal (ClusterIP) virtual IP:port, resolved via kube-proxy/CNI, not bound on any single host. |
| Service `targetPort` | `spec.ports[]` on a `Service` | **No.** Just says which `containerPort` the Service forwards to (defaults to `port` if omitted). Still cluster-internal. |
| `hostPort` | `spec.containers[].ports[].hostPort` | **Yes.** Explicitly binds the pod's port directly on the *node's* host network namespace, bypassing the overlay entirely. This is functionally identical to a Compose `"HOST:CONTAINER"` mapping's host side. |
| Service `nodePort` | `spec.ports[].nodePort` on a `Service` of `type: NodePort` (or `LoadBalancer`) | **Yes, but scheduler-mediated.** Opens that port on *every* node in the cluster, not a specific one PortForge can name in advance the way it names a host for a bare-process allocation. |

**PortForge must only ever treat `hostPort` and `nodePort` as candidates
for host-port allocation.** `containerPort`, Service `port`, and
`targetPort` must never be written by PortForge, never validated against
Central's reservation table, and never appear as a "conflict" — doing so
would be actively misleading, since a collision on those fields literally
cannot happen at the host-network level the rest of PortForge protects.

## Why "local development" is the right scope boundary

For `hostPort`: the node a given pod lands on is decided by the scheduler
at admission time in a real multi-node cluster — PortForge's whole model
(`target: {host: NTMKEYA}`, "reserve this exact host") has no meaning
there, since PortForge can't know or control which node will actually run
the pod. In a **local single-node cluster** (`kind`, Docker Desktop's
built-in Kubernetes, `minikube` with the docker/none driver), there is
exactly one node — the dev machine itself — so "reserve a host port on
this host" is well-defined again, identically to how it already is for a
bare process or a Compose container.

For `nodePort`: same reasoning. A `NodePort` Service opens its port on
*every* node — meaningless to reserve against a specific host in a real
cluster, but in a local single-node cluster "every node" reduces to "the
one dev machine," so it becomes a legitimate, ownable host-port resource
again.

**Conclusion: v1.1's Kubernetes integration should explicitly target
single-node local development clusters only, and should say so in its own
error messages/docs — not attempt to be a general-purpose Kubernetes port
manager for production clusters.** Multi-node awareness (if ever wanted)
is a materially different, much larger feature and is explicitly OUT OF
SCOPE here.

## IN SCOPE for v1.1

- A new manifest config section (naming TBD at implementation time —
  either a `kubernetes:` list alongside `config.dotenv`/`config.compose`,
  or a third entry under `config:` itself; both are additive, no existing
  `config:` shape changes either way) that maps an allocation request name
  to:
  - a specific **container's `hostPort`**, identified by
    `{file, kind (Deployment/StatefulSet/DaemonSet/Pod), name, container,
    containerPort}` — `containerPort` is the match key (mirrors Compose's
    `container:` field exactly), never inferred.
  - a specific **Service's `nodePort`**, identified by `{file, name,
    servicePort}` — `servicePort` (the Service's own `port` field, not
    `targetPort`) is the match key.
- Reading and writing `hostPort`/`nodePort` values only. Nothing else in
  the manifest is ever touched.
- **Multi-document YAML** (`---`-separated), since a real K8s manifest
  file commonly holds a Deployment and a Service together. `ruamel.yaml`
  already supports this via `YAML().load_all()` / `YAML().dump_all()` in
  round-trip mode — the same round-trip settings already proven for
  Compose (`indent(mapping=2, sequence=4, offset=2)`, `preserve_quotes`)
  apply per-document without change. **Evaluated and confirmed
  sufficient**: no new YAML dependency needed beyond what Phase 8C already
  added.
- Reusing the exact same match-then-update-host-side-only algorithm
  `compose_editor.py::apply_port_mapping()` already implements: zero
  matches → append a new `hostPort`/`nodePort` field; exactly one match →
  update only that field, preserving every other field and the document's
  own style; more than one match → refuse
  (`KUBERNETES_PORT_AMBIGUOUS`, mirroring `COMPOSE_PORT_AMBIGUOUS`),
  zero mutation.
- Reusing Phase 8C's entire safety architecture unchanged: project-root
  containment (`resolve_within_root`), precondition content hashing
  (`CONFIG_CHANGED_SINCE_PLAN`/`_APPLY`), atomic multi-file apply, and
  backup/rollback via `.portforge/mutations/`. A Kubernetes file is just a
  third `kind` of target file alongside dotenv and Compose — the
  orchestration in `config_manager.py` (`build_plan`/`apply_mutation`/
  `rollback_mutation`) needs a third per-kind branch, not a new engine.
- `kubectl` validation *if available* (mirroring Phase 8C's optional
  `docker compose config --quiet` check) — e.g. `kubectl apply --dry-run=client
  -f <file>` against the proposed content, informational only, never
  required, never starting or applying anything against a real cluster.

## OUT OF SCOPE for v1.1

- Multi-node / production cluster awareness of any kind.
- `containerPort`, Service `port`, `targetPort` — never written, never
  validated, never treated as a conflict source.
- `Ingress`, `Gateway API`, `LoadBalancer` external IPs, or any other
  Kubernetes networking object. `hostPort` and Service `nodePort` are the
  entire scope.
- Actually applying anything to a live cluster (`kubectl apply` for real,
  starting pods) — PortForge writes files; a human or CI pipeline is
  still the one who runs `kubectl apply` for real, exactly as Phase 8C
  never starts `docker compose up`.
- Helm charts, Kustomize overlays, or any templating layer — raw K8s YAML
  manifests only, matching Compose's own "only files explicitly referenced
  by the manifest, never templated/generated" posture.
- Any change to `containerPort` inference from an allocated port — the
  container port is always given explicitly in the mapping, exactly like
  Compose's `container:` field.

## Physical validation this would need before shipping

A real `kind` cluster (or Docker Desktop Kubernetes) on NTMKEYA, a real
Deployment+Service manifest, a real allocation, `config plan`/`apply`
against it, and `kubectl apply --dry-run=client` (or a real `kubectl
apply` into a throwaway `kind` cluster, then `kubectl delete`) confirming
the written `hostPort`/`nodePort` values are both syntactically valid and
semantically what was intended — the same rigor Phase 8C applied to
Compose, not a lighter bar just because it's a new file kind.
