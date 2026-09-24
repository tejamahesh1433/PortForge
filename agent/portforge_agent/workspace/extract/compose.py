from __future__ import annotations

from typing import Any, Dict, List

from ...compose_editor import ComposeError, extract_port_entries, load_compose
from ..models import Evidence, PortRequirement, ServiceInfo


def _depends_on_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int))]
    if isinstance(raw, dict):
        return sorted(str(key) for key in raw.keys())
    return []


def _port_requirement_from_entry(
    service: str,
    source_path: str,
    entry: Any,
    *,
    role: str,
    classification: str,
    mutable: bool,
    confidence: str,
    detail: str,
    snippet: str,
) -> PortRequirement | None:
    if isinstance(entry, str):
        parsed = extract_port_entries(entry)
        if parsed is None:
            return None
        _ip, host_port, container_port, protocol = parsed
        port = host_port if role == "host" else container_port
        if port is None:
            return None
        return PortRequirement(
            service=service,
            port=int(port) if isinstance(port, str) else port,
            protocol=protocol,
            role=role,
            classification=classification,
            mutable=mutable,
            confidence=confidence,
            evidence=[
                Evidence(
                    source_path=source_path,
                    kind="compose",
                    detail=detail,
                    snippet_safe=snippet,
                )
            ],
        )
    if isinstance(entry, dict):
        protocol = str(entry.get("protocol", "tcp"))
        if role == "host":
            published = entry.get("published")
            if published is None:
                return None
            port = int(str(published))
        else:
            target = entry.get("target")
            if not isinstance(target, int):
                return None
            port = target
        return PortRequirement(
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
                    kind="compose",
                    detail=detail,
                    snippet_safe=snippet,
                )
            ],
        )
    return None


def extract_compose_services(text: str, source_path: str = "docker-compose.yml") -> List[ServiceInfo]:
    data = load_compose(text)
    services_raw = data.get("services")
    if not isinstance(services_raw, dict):
        return []

    services: List[ServiceInfo] = []
    for service_name, service_map in services_raw.items():
        if not isinstance(service_map, dict):
            continue
        requirements: List[PortRequirement] = []
        for entry in service_map.get("ports") or []:
            snippet = str(entry) if not isinstance(entry, dict) else f"published={entry.get('published')}"
            req = _port_requirement_from_entry(
                service_name,
                source_path,
                entry,
                role="host",
                classification="EXPLICIT",
                mutable=True,
                confidence="high",
                detail="compose ports mapping",
                snippet=snippet[:120],
            )
            if req is not None:
                requirements.append(req)
        for entry in service_map.get("expose") or []:
            snippet = str(entry)
            req = _port_requirement_from_entry(
                service_name,
                source_path,
                entry if isinstance(entry, str) else str(entry),
                role="container",
                classification="UNSUPPORTED",
                mutable=False,
                confidence="high",
                detail="compose expose entry",
                snippet=snippet[:120],
            )
            if req is not None:
                requirements.append(req)
            elif isinstance(entry, (str, int)) and str(entry).isdigit():
                requirements.append(
                    PortRequirement(
                        service=service_name,
                        port=int(entry),
                        protocol="tcp",
                        role="container",
                        classification="UNSUPPORTED",
                        mutable=False,
                        confidence="high",
                        evidence=[
                            Evidence(
                                source_path=source_path,
                                kind="compose",
                                detail="compose expose entry",
                                snippet_safe=str(entry)[:120],
                            )
                        ],
                    )
                )
        services.append(
            ServiceInfo(
                name=service_name,
                source_paths=[source_path],
                port_requirements=requirements,
                dependencies=_depends_on_list(service_map.get("depends_on")),
                confidence="high" if requirements else "medium",
            )
        )
    return services


def safe_extract_compose_services(text: str, source_path: str) -> tuple[List[ServiceInfo], dict | None]:
    try:
        return extract_compose_services(text, source_path), None
    except ComposeError as exc:
        return [], {"source_path": source_path, "kind": "compose", "code": exc.code, "message": exc.message}
