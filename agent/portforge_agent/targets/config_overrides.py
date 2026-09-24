from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..manifest import ProjectManifest


def select_config_files_for_environment(manifest: ProjectManifest, environment: str) -> Dict[str, Any]:
    base_files: List[str] = []
    if manifest.config is not None:
        for mapping in manifest.config.dotenv:
            base_files.append(mapping.file)
        for mapping in manifest.config.compose:
            base_files.append(mapping.file)
        for mapping in manifest.config.kubernetes:
            base_files.append(mapping.file)

    override_candidates: List[str] = []
    env_suffix = f".{environment.strip()}" if environment else ""
    if env_suffix:
        dotenv_override = f".env{env_suffix}"
        compose_override = f"docker-compose{env_suffix}.yml"
        for candidate in (dotenv_override, compose_override, f"docker-compose{env_suffix}.yaml"):
            override_candidates.append(candidate)

    return {
        "base_config_files": sorted(set(base_files)),
        "override_candidates": override_candidates,
        "note": "Only existing override files are reported; unsupported keys are never mutated automatically.",
    }


def existing_override_files(project_root: Path, environment: str) -> List[str]:
    env_suffix = f".{environment.strip()}" if environment else ""
    if not env_suffix:
        return []
    candidates = [
        f".env{env_suffix}",
        f"docker-compose{env_suffix}.yml",
        f"docker-compose{env_suffix}.yaml",
    ]
    return [candidate for candidate in candidates if (project_root / candidate).is_file()]
