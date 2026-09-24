"""JSON allowlist validation for deployment persisted fields (Phase 18)."""
from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(password|secret|token|authorization|api[_-]?key|credential|private[_-]?key|env)",
    re.IGNORECASE,
)

_PORTS_SERVICE_KEYS = frozenset({"name", "internal_port", "host_port", "protocol", "reason_code"})
_INGRESS_BINDING_KEYS = frozenset(
    {"name", "scheme", "hostname", "public_port", "service", "status", "bound_host_port"}
)
_HEALTH_OVERALL = frozenset({"HEALTHY", "UNHEALTHY", "HEALTH_UNKNOWN", "RUNTIME_STARTED"})
_HEALTH_CHECK_KEYS = frozenset({"id", "kind", "status", "detail"})
_HEALTH_KINDS = frozenset({"compose", "container", "http", "tcp"})


class DeploymentJsonError(ValueError):
    pass


def _reject_secret_keys(obj: Any, *, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _SECRET_KEY_RE.search(str(key)):
                raise DeploymentJsonError(f"Disallowed key at {path}.{key!r}")
            _reject_secret_keys(value, path=f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            _reject_secret_keys(item, path=f"{path}[{idx}]")


def _require_dict(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise DeploymentJsonError(f"{label} must be an object")
    return value


def validate_ports_json(value: dict | None) -> dict | None:
    if value is None:
        return None
    data = _require_dict(value, "ports_json")
    _reject_secret_keys(data)
    unknown = set(data) - {"services"}
    if unknown:
        raise DeploymentJsonError(f"ports_json has unknown keys: {sorted(unknown)}")
    services = data.get("services")
    if services is None:
        raise DeploymentJsonError("ports_json.services is required")
    if not isinstance(services, list):
        raise DeploymentJsonError("ports_json.services must be a list")
    for idx, svc in enumerate(services):
        if not isinstance(svc, dict):
            raise DeploymentJsonError(f"ports_json.services[{idx}] must be an object")
        svc_unknown = set(svc) - _PORTS_SERVICE_KEYS
        if svc_unknown:
            raise DeploymentJsonError(f"ports_json.services[{idx}] unknown keys: {sorted(svc_unknown)}")
        if "name" not in svc or not isinstance(svc["name"], str) or not svc["name"]:
            raise DeploymentJsonError(f"ports_json.services[{idx}].name is required")
        for port_field in ("internal_port", "host_port"):
            if port_field in svc:
                port_val = svc[port_field]
                if not isinstance(port_val, int) or port_val < 0 or port_val > 65535:
                    raise DeploymentJsonError(
                        f"ports_json.services[{idx}].{port_field} must be 0-65535"
                    )
        if "protocol" in svc and svc["protocol"] not in ("tcp", "udp"):
            raise DeploymentJsonError(f"ports_json.services[{idx}].protocol must be tcp or udp")
    return data


def validate_ingress_json(value: dict | None) -> dict | None:
    if value is None:
        return None
    data = _require_dict(value, "ingress_json")
    _reject_secret_keys(data)
    unknown = set(data) - {"bindings"}
    if unknown:
        raise DeploymentJsonError(f"ingress_json has unknown keys: {sorted(unknown)}")
    bindings = data.get("bindings")
    if bindings is None:
        raise DeploymentJsonError("ingress_json.bindings is required")
    if not isinstance(bindings, list):
        raise DeploymentJsonError("ingress_json.bindings must be a list")
    for idx, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            raise DeploymentJsonError(f"ingress_json.bindings[{idx}] must be an object")
        binding_unknown = set(binding) - _INGRESS_BINDING_KEYS
        if binding_unknown:
            raise DeploymentJsonError(
                f"ingress_json.bindings[{idx}] unknown keys: {sorted(binding_unknown)}"
            )
        if "name" in binding and not isinstance(binding["name"], str):
            raise DeploymentJsonError(f"ingress_json.bindings[{idx}].name must be a string")
        if "public_port" in binding:
            port_val = binding["public_port"]
            if not isinstance(port_val, int) or port_val < 0 or port_val > 65535:
                raise DeploymentJsonError(
                    f"ingress_json.bindings[{idx}].public_port must be 0-65535"
                )
        if "bound_host_port" in binding:
            port_val = binding["bound_host_port"]
            if not isinstance(port_val, int) or port_val < 0 or port_val > 65535:
                raise DeploymentJsonError(
                    f"ingress_json.bindings[{idx}].bound_host_port must be 0-65535"
                )
    return data


def validate_health_json(value: dict | None) -> dict | None:
    if value is None:
        return None
    data = _require_dict(value, "health_json")
    _reject_secret_keys(data)
    unknown = set(data) - {"overall", "checks"}
    if unknown:
        raise DeploymentJsonError(f"health_json has unknown keys: {sorted(unknown)}")
    overall = data.get("overall")
    if overall is None:
        raise DeploymentJsonError("health_json.overall is required")
    if overall not in _HEALTH_OVERALL:
        raise DeploymentJsonError(f"health_json.overall must be one of {sorted(_HEALTH_OVERALL)}")
    checks = data.get("checks")
    if checks is None:
        raise DeploymentJsonError("health_json.checks is required")
    if not isinstance(checks, list):
        raise DeploymentJsonError("health_json.checks must be a list")
    for idx, check in enumerate(checks):
        if not isinstance(check, dict):
            raise DeploymentJsonError(f"health_json.checks[{idx}] must be an object")
        check_unknown = set(check) - _HEALTH_CHECK_KEYS
        if check_unknown:
            raise DeploymentJsonError(
                f"health_json.checks[{idx}] unknown keys: {sorted(check_unknown)}"
            )
        if "id" not in check or not isinstance(check["id"], str) or not check["id"]:
            raise DeploymentJsonError(f"health_json.checks[{idx}].id is required")
        if "kind" not in check or check["kind"] not in _HEALTH_KINDS:
            raise DeploymentJsonError(
                f"health_json.checks[{idx}].kind must be one of {sorted(_HEALTH_KINDS)}"
            )
        if "status" not in check or not isinstance(check["status"], str):
            raise DeploymentJsonError(f"health_json.checks[{idx}].status is required")
        if "detail" in check:
            detail = check["detail"]
            if not isinstance(detail, str) or len(detail) > 512:
                raise DeploymentJsonError(
                    f"health_json.checks[{idx}].detail must be a short string (max 512)"
                )
    return data
