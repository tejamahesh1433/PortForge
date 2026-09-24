from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from ..config_files import resolve_within_root
from .ignore import is_ignored_dir


@dataclass
class WalkStats:
    considered_files: List[str] = field(default_factory=list)
    candidates: Dict[str, List[str]] = field(default_factory=dict)
    ignored_dir_count: int = 0
    files_considered: int = 0


def _classify_candidate(relative: str) -> str | None:
    name = Path(relative).name
    lower = name.lower()
    suffix = Path(relative).suffix.lower()

    if lower == "portforge.yml" or lower == "portforge.yaml":
        return "manifest"
    if lower in {".env", ".env.example", ".env.local"} or lower.startswith(".env."):
        return "dotenv"
    if lower in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return "compose"
    if lower == "dockerfile" or lower.startswith("dockerfile."):
        return "dockerfile"
    if lower == "package.json":
        return "package_json"
    if lower == "values.yaml" or (lower.startswith("values") and lower.endswith((".yaml", ".yml"))):
        return "helm"
    if suffix in {".yaml", ".yml"}:
        return "kubernetes"
    return None


def _safe_relative(root: Path, path: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def walk_workspace(project_root: Path, max_depth: int = 8, max_files: int = 2000) -> WalkStats:
    root = project_root.resolve(strict=True)
    stats = WalkStats(
        candidates={
            "dotenv": [],
            "compose": [],
            "dockerfile": [],
            "package_json": [],
            "kubernetes": [],
            "helm": [],
            "manifest": [],
        }
    )

    def walk_dir(current: Path, depth: int) -> None:
        if depth > max_depth or stats.files_considered >= max_files:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for entry in entries:
            if stats.files_considered >= max_files:
                return
            if entry.is_dir(follow_symlinks=False):
                if is_ignored_dir(entry.name):
                    stats.ignored_dir_count += 1
                    continue
                walk_dir(entry, depth + 1)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue

            relative = _safe_relative(root, entry)
            if relative is None:
                continue

            try:
                resolved = resolve_within_root(root, relative)
            except Exception:
                continue
            if not resolved.is_file():
                continue

            stats.files_considered += 1
            stats.considered_files.append(relative)
            kind = _classify_candidate(relative)
            if kind is not None:
                stats.candidates[kind].append(relative)

    walk_dir(root, 0)
    stats.considered_files.sort()
    for kind in stats.candidates:
        stats.candidates[kind].sort()
    return stats
