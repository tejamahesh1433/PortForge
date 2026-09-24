from __future__ import annotations

from typing import Any, List

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from ..models import Evidence, PortRequirement, ServiceInfo

_PORT_KEYS = frozenset({"port", "nodeport", "hostport"})


def _walk_values(prefix: str, value: Any, source_path: str, requirements: List[PortRequirement]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            joined = f"{prefix}.{key}" if prefix else str(key)
            _walk_values(joined, nested, source_path, requirements)
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _walk_values(f"{prefix}[{index}]", nested, source_path, requirements)
        return
    leaf = prefix.rsplit(".", 1)[-1].lower()
    if leaf in _PORT_KEYS and isinstance(value, int) and 1 <= value <= 65535:
        classification = "AMBIGUOUS" if leaf == "port" else "UNSUPPORTED"
        requirements.append(
            PortRequirement(
                service="helm",
                port=value,
                protocol="tcp",
                role="host" if leaf in {"nodeport", "hostport"} else "unknown",
                classification=classification,
                mutable=False,
                confidence="medium",
                evidence=[
                    Evidence(
                        source_path=source_path,
                        kind="helm",
                        detail=f"values key {prefix}",
                        snippet_safe=f"{prefix}={value}",
                    )
                ],
            )
        )


def extract_helm_services(text: str, source_path: str = "values.yaml") -> List[ServiceInfo]:
    if yaml is None:
        return []
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return []
    requirements: List[PortRequirement] = []
    _walk_values("", data, source_path, requirements)
    if not requirements:
        return []
    return [
        ServiceInfo(
            name="helm",
            type="other",
            source_paths=[source_path],
            port_requirements=requirements,
            confidence="medium",
        )
    ]


def safe_extract_helm_services(text: str, source_path: str) -> tuple[List[ServiceInfo], dict | None]:
    try:
        return extract_helm_services(text, source_path), None
    except Exception as exc:
        return [], {"source_path": source_path, "kind": "helm", "code": "CONFIG_PARSE_ERROR", "message": str(exc)}
