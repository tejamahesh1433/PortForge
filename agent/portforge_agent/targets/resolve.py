from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Union

from ..manifest import ProjectManifest
from .models import EnvironmentConfig, TargetRef, TargetsError

_MAX_ENVIRONMENTS = 20
_MAX_TARGETS_PER_ENV = 20
_TARGET_ENTRY_KEYS = {"host_id", "host"}


def _validate_uuid(value: str, context: str) -> str:
    text = (value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        raise TargetsError(
            "INVALID_HOST_ID",
            f"'{context}' must be a valid UUID, got {value!r}.",
            details=[{"field": context, "value": value}],
        )
    return str(parsed)


def _validate_target_entry(alias: str, entry: Any) -> TargetRef:
    context = f"targets.{alias}"
    if not isinstance(entry, dict):
        raise TargetsError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
    unknown = sorted(set(entry.keys()) - _TARGET_ENTRY_KEYS)
    if unknown:
        raise TargetsError(
            "MANIFEST_INVALID",
            f"Unknown field(s) in {context}: {', '.join(unknown)}.",
            details=[{"context": context, "fields": unknown}],
        )
    host_id = _validate_uuid(entry.get("host_id"), f"{context}.host_id")
    hostname: Optional[str] = None
    if "host" in entry:
        raw = entry["host"]
        if not isinstance(raw, str) or not raw.strip():
            raise TargetsError("MANIFEST_INVALID", f"'{context}.host' must be a non-empty string if present.")
        hostname = raw.strip()
    return TargetRef(alias=alias.strip(), host_id=host_id, hostname=hostname)


def load_environments_from_manifest_data(data: Dict[str, Any]) -> Dict[str, EnvironmentConfig]:
    raw = data.get("environments")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TargetsError("MANIFEST_INVALID", "'environments' must be a mapping.")
    if len(raw) > _MAX_ENVIRONMENTS:
        raise TargetsError(
            "MANIFEST_INVALID",
            f"'environments' has {len(raw)} entries, exceeding the {_MAX_ENVIRONMENTS}-entry limit.",
        )

    environments: Dict[str, EnvironmentConfig] = {}
    for env_name, env_entry in raw.items():
        if not isinstance(env_name, str) or not env_name.strip():
            raise TargetsError("MANIFEST_INVALID", "'environments' keys must be non-empty strings.")
        context = f"environments.{env_name}"
        if not isinstance(env_entry, dict):
            raise TargetsError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
        targets_raw = env_entry.get("targets")
        if not isinstance(targets_raw, dict) or not targets_raw:
            raise TargetsError("MANIFEST_INVALID", f"'{context}.targets' must be a non-empty mapping.")
        if len(targets_raw) > _MAX_TARGETS_PER_ENV:
            raise TargetsError(
                "MANIFEST_INVALID",
                f"'{context}.targets' has {len(targets_raw)} entries, exceeding the {_MAX_TARGETS_PER_ENV}-entry limit.",
            )
        targets = {alias: _validate_target_entry(alias, entry) for alias, entry in targets_raw.items()}
        environments[env_name.strip()] = EnvironmentConfig(name=env_name.strip(), targets=targets)
    return environments


def _environments_from_manifest(manifest: ProjectManifest) -> Dict[str, EnvironmentConfig]:
    if not manifest.environments:
        return {}
    return load_environments_from_manifest_data({"environments": manifest.environments})


def resolve_target(
    manifest_or_config: Union[ProjectManifest, Dict[str, EnvironmentConfig], None],
    environment: Optional[str],
    *,
    target: Optional[str] = None,
    target_host_id: Optional[str] = None,
) -> TargetRef:
    environments: Dict[str, EnvironmentConfig]
    if isinstance(manifest_or_config, ProjectManifest):
        environments = _environments_from_manifest(manifest_or_config)
    elif isinstance(manifest_or_config, dict):
        environments = manifest_or_config
    else:
        environments = {}

    env_name = (environment or "").strip()
    target_alias = (target or "").strip()
    host_id_arg = (target_host_id or "").strip()

    if host_id_arg:
        host_id = _validate_uuid(host_id_arg, "target_host_id")
        alias = target_alias or host_id
        if env_name and environments:
            env_config = environments.get(env_name)
            if env_config is None:
                raise TargetsError(
                    "ENVIRONMENT_NOT_FOUND",
                    f"Environment '{env_name}' is not declared in the manifest.",
                    details=[{"environment": env_name}],
                )
            if target_alias and target_alias not in env_config.targets:
                raise TargetsError(
                    "UNKNOWN_TARGET",
                    f"Target alias '{target_alias}' is not declared under environment '{env_name}'.",
                    details=[{"environment": env_name, "target": target_alias}],
                )
            declared = env_config.targets.get(target_alias) if target_alias else None
            if declared is not None and declared.host_id != host_id:
                raise TargetsError(
                    "UNKNOWN_TARGET",
                    f"Target alias '{target_alias}' host_id does not match target_host_id.",
                    details=[{"expected": declared.host_id, "provided": host_id}],
                )
            if declared is not None:
                return declared
        return TargetRef(alias=alias, host_id=host_id)

    if not env_name:
        raise TargetsError(
            "TARGET_REQUIRED",
            "environment is required when resolving a target alias.",
            details=[{"target": target_alias or None}],
        )

    env_config = environments.get(env_name)
    if env_config is None:
        raise TargetsError(
            "ENVIRONMENT_NOT_FOUND",
            f"Environment '{env_name}' is not declared in the manifest.",
            details=[{"environment": env_name}],
        )

    if not target_alias:
        raise TargetsError(
            "TARGET_REQUIRED",
            f"target alias or target_host_id is required for environment '{env_name}'.",
            details=[{"environment": env_name}],
        )

    if target_alias in env_config.targets:
        return env_config.targets[target_alias]

    try:
        uuid.UUID(target_alias)
    except ValueError:
        raise TargetsError(
            "UNKNOWN_TARGET",
            f"Target '{target_alias}' is not declared under environment '{env_name}'.",
            details=[{"environment": env_name, "target": target_alias}],
        )

    return TargetRef(alias=target_alias, host_id=target_alias)
