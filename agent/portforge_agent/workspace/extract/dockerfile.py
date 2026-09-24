from __future__ import annotations

import re
from typing import List

from ..models import Evidence, PortRequirement, ServiceInfo

_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(?P<ports>.+)$", re.IGNORECASE | re.MULTILINE)


def _parse_expose_tokens(raw: str) -> List[tuple[int, str]]:
    results: List[tuple[int, str]] = []
    for token in raw.split():
        token = token.strip()
        if not token:
            continue
        protocol = "tcp"
        body = token
        if "/" in body:
            body, protocol = body.rsplit("/", 1)
            protocol = protocol.lower()
        if body.isdigit():
            port = int(body)
            if 1 <= port <= 65535:
                results.append((port, protocol))
    return results


def extract_dockerfile_services(text: str, source_path: str = "Dockerfile") -> List[ServiceInfo]:
    service_name = Path_stem(source_path)
    requirements: List[PortRequirement] = []
    for match in _EXPOSE_RE.finditer(text):
        for port, protocol in _parse_expose_tokens(match.group("ports")):
            requirements.append(
                PortRequirement(
                    service=service_name,
                    port=port,
                    protocol=protocol,
                    role="container",
                    classification="EXPLICIT",
                    mutable=False,
                    confidence="high",
                    evidence=[
                        Evidence(
                            source_path=source_path,
                            kind="dockerfile",
                            detail="EXPOSE directive",
                            snippet_safe=f"EXPOSE {port}/{protocol}",
                        )
                    ],
                )
            )
    if not requirements:
        return []
    return [
        ServiceInfo(
            name=service_name,
            type="other",
            source_paths=[source_path],
            port_requirements=requirements,
            confidence="high",
        )
    ]


def Path_stem(path: str) -> str:
    from pathlib import Path

    name = Path(path).name
    if name.lower().startswith("dockerfile"):
        return "app"
    return name
