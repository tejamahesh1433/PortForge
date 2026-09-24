from __future__ import annotations

import json
import re
from typing import Any, List

from ..models import Evidence, PortRequirement, ServiceInfo

_SHELL_LIKE = re.compile(r"[&|$`|]")
_NEXT_PORT_RE = re.compile(r"(?:^|\s)-p\s+(\d{1,5})(?:\s|$)")
_VITE_PORT_RE = re.compile(r"(?:^|\s)--port(?:=|\s+)(\d{1,5})(?:\s|$)")
_REACT_PORT_RE = re.compile(r"(?:^|\s)PORT=(\d{1,5})(?:\s|$)")


def _service_name_from_path(source_path: str) -> str:
    parts = source_path.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[-1] == "package.json":
        return parts[-2]
    return "app"


def _port_from_script(script: str) -> tuple[int | None, str, str]:
    stripped = script.strip()
    if stripped.isdigit():
        port = int(stripped)
        if 1 <= port <= 65535:
            return port, "INFERRED", "high"
    if _SHELL_LIKE.search(stripped):
        return None, "AMBIGUOUS", "low"
    for pattern, classification, confidence in (
        (_NEXT_PORT_RE, "INFERRED", "medium"),
        (_VITE_PORT_RE, "INFERRED", "medium"),
        (_REACT_PORT_RE, "INFERRED", "medium"),
    ):
        match = pattern.search(stripped)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port, classification, confidence
    if "react-scripts" in stripped and "PORT=" not in stripped:
        return None, "AMBIGUOUS", "low"
    return None, "AMBIGUOUS", "low"


def extract_package_json_services(text: str, source_path: str = "package.json") -> List[ServiceInfo]:
    data = json.loads(text)
    if not isinstance(data, dict):
        return []

    service_name = _service_name_from_path(source_path)
    requirements: List[PortRequirement] = []

    config = data.get("config")
    if isinstance(config, dict):
        port_value = config.get("port")
        if isinstance(port_value, int) and 1 <= port_value <= 65535:
            requirements.append(
                PortRequirement(
                    service=service_name,
                    port=port_value,
                    protocol="tcp",
                    role="host",
                    classification="EXPLICIT",
                    mutable=True,
                    confidence="high",
                    evidence=[
                        Evidence(
                            source_path=source_path,
                            kind="package_json",
                            detail="config.port",
                            snippet_safe=f"config.port={port_value}",
                        )
                    ],
                )
            )

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for script_name, script_value in scripts.items():
            if not isinstance(script_value, str):
                continue
            port, classification, confidence = _port_from_script(script_value)
            if port is None and classification != "AMBIGUOUS":
                continue
            requirements.append(
                PortRequirement(
                    service=service_name,
                    port=port,
                    protocol="tcp",
                    role="host",
                    classification=classification,
                    mutable=classification in {"EXPLICIT", "INFERRED"},
                    confidence=confidence,
                    evidence=[
                        Evidence(
                            source_path=source_path,
                            kind="package_json",
                            detail=f"scripts.{script_name}",
                            snippet_safe=script_value[:120],
                        )
                    ],
                )
            )

    if not requirements:
        return []
    return [
        ServiceInfo(
            name=service_name,
            type="frontend" if service_name in {"web", "frontend", "app"} else "other",
            source_paths=[source_path],
            port_requirements=requirements,
            confidence="high" if any(r.classification == "EXPLICIT" for r in requirements) else "medium",
        )
    ]


def safe_extract_package_json_services(text: str, source_path: str) -> tuple[List[ServiceInfo], dict | None]:
    try:
        return extract_package_json_services(text, source_path), None
    except json.JSONDecodeError as exc:
        return [], {"source_path": source_path, "kind": "package_json", "code": "CONFIG_PARSE_ERROR", "message": str(exc)}
