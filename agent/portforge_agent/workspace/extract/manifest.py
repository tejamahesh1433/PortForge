from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...manifest import ManifestError, parse_manifest_yaml, validate_manifest
from ..models import Evidence, PortRequirement, ServiceInfo


def extract_manifest_summary(source_path: str, text: str) -> tuple[Optional[Dict[str, Any]], List[ServiceInfo], dict | None]:
    try:
        manifest = validate_manifest(parse_manifest_yaml(text))
    except ManifestError as exc:
        return None, [], {"source_path": source_path, "kind": "manifest", "code": exc.code, "message": exc.message}

    summary: Dict[str, Any] = {
        "path": source_path,
        "project": manifest.project,
        "host": manifest.host,
        "ports": {
            item.name: {
                "purpose": item.purpose,
                "protocol": item.protocol,
                "preferred": item.preferred_port,
            }
            for item in manifest.requests
        },
        "config_targets": [],
    }
    if manifest.config is not None:
        for mapping in manifest.config.dotenv:
            summary["config_targets"].append({"type": "dotenv", "file": mapping.file})
        for mapping in manifest.config.compose:
            summary["config_targets"].append({"type": "compose", "file": mapping.file})
        for mapping in manifest.config.kubernetes:
            summary["config_targets"].append({"type": "kubernetes", "file": mapping.file})

    services: List[ServiceInfo] = []
    for item in manifest.requests:
        requirements: List[PortRequirement] = []
        if item.preferred_port is not None:
            requirements.append(
                PortRequirement(
                    service=item.name,
                    port=item.preferred_port,
                    protocol=item.protocol,
                    role="host",
                    classification="EXPLICIT",
                    mutable=True,
                    confidence="high",
                    evidence=[
                        Evidence(
                            source_path=source_path,
                            kind="manifest",
                            detail="portforge.yml preferred port",
                            snippet_safe=f"{item.name}.preferred={item.preferred_port}",
                        )
                    ],
                )
            )
        services.append(
            ServiceInfo(
                name=item.name,
                type=item.purpose,
                source_paths=[source_path],
                port_requirements=requirements,
                confidence="high",
            )
        )
    return summary, services, None
