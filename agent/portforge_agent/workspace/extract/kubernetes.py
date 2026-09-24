from __future__ import annotations

from typing import List

from ...k8s_editor import KubernetesError, load_kubernetes_documents
from ..models import Evidence, PortRequirement, ServiceInfo


def _append_requirement(
    requirements: List[PortRequirement],
    *,
    service: str,
    source_path: str,
    port: int,
    protocol: str,
    role: str,
    classification: str,
    mutable: bool,
    confidence: str,
    detail: str,
    snippet: str,
) -> None:
    requirements.append(
        PortRequirement(
            service=service,
            port=port,
            protocol=protocol,
            role=role,
            classification=classification,
            mutable=mutable,
            confidence=confidence,
            evidence=[
                Evidence(
                    source_path=source_path,
                    kind="kubernetes",
                    detail=detail,
                    snippet_safe=snippet[:120],
                )
            ],
        )
    )


def _resource_name(doc: dict) -> str:
    metadata = doc.get("metadata") or {}
    return str(metadata.get("name") or doc.get("kind") or "resource")


def extract_kubernetes_services(text: str, source_path: str = "k8s.yaml") -> List[ServiceInfo]:
    docs = load_kubernetes_documents(text)
    by_service: dict[str, ServiceInfo] = {}

    def ensure(name: str) -> ServiceInfo:
        if name not in by_service:
            by_service[name] = ServiceInfo(name=name, source_paths=[source_path], confidence="high")
        elif source_path not in by_service[name].source_paths:
            by_service[name].source_paths.append(source_path)
        return by_service[name]

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        name = _resource_name(doc)

        if kind in {"Deployment", "StatefulSet", "Pod", "DaemonSet"}:
            template = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
            for container in template.get("containers") or []:
                if not isinstance(container, dict):
                    continue
                container_name = str(container.get("name") or name)
                service = ensure(container_name)
                for port_entry in container.get("ports") or []:
                    if not isinstance(port_entry, dict):
                        continue
                    container_port = port_entry.get("containerPort")
                    host_port = port_entry.get("hostPort")
                    protocol = str(port_entry.get("protocol", "TCP")).lower()
                    if isinstance(host_port, int):
                        _append_requirement(
                            service.port_requirements,
                            service=container_name,
                            source_path=source_path,
                            port=host_port,
                            protocol=protocol,
                            role="host",
                            classification="EXPLICIT",
                            mutable=True,
                            confidence="high",
                            detail="kubernetes hostPort",
                            snippet=f"hostPort={host_port}",
                        )
                    if isinstance(container_port, int):
                        _append_requirement(
                            service.port_requirements,
                            service=container_name,
                            source_path=source_path,
                            port=container_port,
                            protocol=protocol,
                            role="container",
                            classification="UNSUPPORTED",
                            mutable=False,
                            confidence="high",
                            detail="kubernetes containerPort",
                            snippet=f"containerPort={container_port}",
                        )

        if kind == "Service":
            service = ensure(name)
            for port_entry in (doc.get("spec") or {}).get("ports") or []:
                if not isinstance(port_entry, dict):
                    continue
                protocol = str(port_entry.get("protocol", "TCP")).lower()
                node_port = port_entry.get("nodePort")
                service_port = port_entry.get("port")
                target_port = port_entry.get("targetPort")
                if isinstance(node_port, int):
                    _append_requirement(
                        service.port_requirements,
                        service=name,
                        source_path=source_path,
                        port=node_port,
                        protocol=protocol,
                        role="host",
                        classification="EXPLICIT",
                        mutable=True,
                        confidence="high",
                        detail="kubernetes Service nodePort",
                        snippet=f"nodePort={node_port}",
                    )
                if isinstance(service_port, int):
                    _append_requirement(
                        service.port_requirements,
                        service=name,
                        source_path=source_path,
                        port=service_port,
                        protocol=protocol,
                        role="container",
                        classification="UNSUPPORTED",
                        mutable=False,
                        confidence="high",
                        detail="kubernetes Service port",
                        snippet=f"port={service_port}",
                    )
                if isinstance(target_port, int):
                    _append_requirement(
                        service.port_requirements,
                        service=name,
                        source_path=source_path,
                        port=target_port,
                        protocol=protocol,
                        role="container",
                        classification="UNSUPPORTED",
                        mutable=False,
                        confidence="high",
                        detail="kubernetes Service targetPort",
                        snippet=f"targetPort={target_port}",
                    )

    return [service for service in by_service.values() if service.port_requirements]


def safe_extract_kubernetes_services(text: str, source_path: str) -> tuple[List[ServiceInfo], dict | None]:
    try:
        services = extract_kubernetes_services(text, source_path)
        if not services:
            return [], None
        return services, None
    except KubernetesError as exc:
        return [], {"source_path": source_path, "kind": "kubernetes", "code": exc.code, "message": exc.message}
