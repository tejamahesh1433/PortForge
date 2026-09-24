from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from .models import IngressBinding, IngressStatus, TargetsError

_INGRESS_ENTRY_KEYS = {
    "name",
    "scheme",
    "hostname",
    "public_port",
    "service",
    "environment",
    "target",
    "bound_host_port",
}
_MAX_INGRESS_ENTRIES = 50


def _require_string(entry: dict, key: str, context: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TargetsError("MANIFEST_INVALID", f"'{context}.{key}' must be a non-empty string.")
    return value.strip()


def parse_ingress_from_manifest(data: dict) -> List[IngressBinding]:
    raw = data.get("ingress")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TargetsError("MANIFEST_INVALID", "'ingress' must be a list.")
    if len(raw) > _MAX_INGRESS_ENTRIES:
        raise TargetsError(
            "MANIFEST_INVALID",
            f"'ingress' has {len(raw)} entries, exceeding the {_MAX_INGRESS_ENTRIES}-entry limit.",
        )

    bindings: List[IngressBinding] = []
    for index, entry in enumerate(raw):
        context = f"ingress[{index}]"
        if not isinstance(entry, dict):
            raise TargetsError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
        unknown = sorted(set(entry.keys()) - _INGRESS_ENTRY_KEYS)
        if unknown:
            raise TargetsError(
                "MANIFEST_INVALID",
                f"Unknown field(s) in {context}: {', '.join(unknown)}.",
                details=[{"context": context, "fields": unknown}],
            )

        public_port = entry.get("public_port")
        if isinstance(public_port, bool) or not isinstance(public_port, int) or not (1 <= public_port <= 65535):
            raise TargetsError(
                "MANIFEST_INVALID",
                f"'{context}.public_port' must be an integer between 1 and 65535.",
            )

        bound_host_port: Optional[int] = None
        if "bound_host_port" in entry:
            raw_bound = entry["bound_host_port"]
            if isinstance(raw_bound, bool) or not isinstance(raw_bound, int) or not (1 <= raw_bound <= 65535):
                raise TargetsError(
                    "MANIFEST_INVALID",
                    f"'{context}.bound_host_port' must be an integer between 1 and 65535.",
                )
            bound_host_port = raw_bound

        bindings.append(
            IngressBinding(
                name=_require_string(entry, "name", context),
                scheme=_require_string(entry, "scheme", context),
                hostname=_require_string(entry, "hostname", context),
                public_port=public_port,
                service=_require_string(entry, "service", context),
                environment=_require_string(entry, "environment", context),
                target=_require_string(entry, "target", context),
                bound_host_port=bound_host_port,
                status=IngressStatus.INGRESS_PLANNED,
            )
        )
    return bindings


def validate_ingress(binding: IngressBinding, services: List[str]) -> List[dict]:
    if binding.service not in services:
        return [
            {
                "code": "INVALID_INGRESS",
                "message": f"Ingress '{binding.name}' references unknown service '{binding.service}'.",
                "details": [{"ingress": binding.name, "service": binding.service}],
            }
        ]
    return []


def optional_static_proxy_scan(project_root: Path) -> List[dict]:
    evidence: List[dict] = []
    candidates = [
        ("nginx", project_root / "nginx.conf"),
        ("caddy", project_root / "Caddyfile"),
        ("traefik", project_root / "traefik.yml"),
        ("traefik", project_root / "traefik.yaml"),
    ]
    for proxy, path in candidates:
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 256 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text.splitlines()) > 500:
            continue
        evidence.append({"proxy": proxy, "file": str(path.relative_to(project_root)), "lines": len(text.splitlines())})
    return evidence
