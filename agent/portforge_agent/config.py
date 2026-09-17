"""Central configuration: recommendation ranges and exclusions.

Single source of truth for the numeric ranges used by `portforge next` --
see DEFAULT_RANGES below. Nothing else in the codebase hardcodes a port
range; everything goes through a PortForgeConfig instance.

Precedence (highest wins), see agent/README.md "Configuration" for the
full explanation:

    CLI arguments  >  project config (.portforge.yml/.json)  >  user config  >  built-in defaults

This module resolves the bottom two layers (built-in defaults merged with
the user-level config file). Project config and CLI arguments are layered
on top of the result by the CLI itself (project config supplies
project/service defaults and reservation-sync data; it does not currently
override ranges/exclusions -- see find_project_config()).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

logger = logging.getLogger("portforge_agent.config")

try:
    import yaml
except ImportError:  # pragma: no cover - optional at runtime, see requirements.txt
    yaml = None


@dataclass(frozen=True)
class PortRange:
    name: str
    start: int
    end: int

    def contains(self, port: int) -> bool:
        return self.start <= port <= self.end


# The one place service-type ranges are defined. Extend/override via a user
# config file (see paths.config_path_candidates()) -- never add a new range
# by editing source elsewhere.
DEFAULT_RANGES: Dict[str, PortRange] = {
    "frontend": PortRange("frontend", 3000, 3999),
    "api": PortRange("api", 8000, 8999),
    "postgres": PortRange("postgres", 5432, 5499),
    "mysql": PortRange("mysql", 3306, 3399),
    "redis": PortRange("redis", 6379, 6399),
    "generic": PortRange("generic", 10000, 19999),
}


@dataclass(frozen=True)
class Exclusion:
    """A single excluded port, or an inclusive port range."""

    start: int
    end: int

    def contains(self, port: int) -> bool:
        return self.start <= port <= self.end

    def __str__(self) -> str:
        return str(self.start) if self.start == self.end else f"{self.start}-{self.end}"


@dataclass
class PortForgeConfig:
    ranges: Dict[str, PortRange] = field(default_factory=lambda: dict(DEFAULT_RANGES))
    exclusions: List[Exclusion] = field(default_factory=list)

    def is_excluded(self, port: int) -> bool:
        return any(e.contains(port) for e in self.exclusions)

    def range_for(self, service_type: str) -> Optional[PortRange]:
        return self.ranges.get(service_type)


def _parse_exclusion(item: Any) -> Optional[Exclusion]:
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return Exclusion(item, item)
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        if "-" in text:
            start_s, _, end_s = text.partition("-")
            try:
                start, end = int(start_s.strip()), int(end_s.strip())
            except ValueError:
                return None
            if start > end:
                start, end = end, start
            return Exclusion(start, end)
        try:
            value = int(text)
        except ValueError:
            return None
        return Exclusion(value, value)
    return None


def _parse_range(name: str, item: Any) -> Optional[PortRange]:
    if not isinstance(item, dict):
        return None
    try:
        start = int(item["start"])
        end = int(item["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start > end:
        start, end = end, start
    return PortRange(name, start, end)


def _merge_raw_config(config: PortForgeConfig, raw: Dict[str, Any], source: str) -> None:
    ranges = raw.get("ranges")
    if isinstance(ranges, dict):
        for name, item in ranges.items():
            parsed = _parse_range(str(name), item)
            if parsed is None:
                logger.warning("Ignoring malformed range '%s' in %s", name, source)
                continue
            config.ranges[str(name)] = parsed

    exclude = raw.get("exclude")
    if isinstance(exclude, list):
        for item in exclude:
            parsed = _parse_exclusion(item)
            if parsed is None:
                logger.warning("Ignoring malformed exclusion entry %r in %s", item, source)
                continue
            config.exclusions.append(parsed)


def _read_structured_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read config file %s: %s", path, exc)
        return None

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in (".yml", ".yaml"):
            if yaml is None:
                logger.warning("PyYAML not available; cannot read %s", path)
                return None
            data = yaml.safe_load(text)
        else:
            return None
    except Exception as exc:  # malformed config must never crash PortForge
        logger.warning("Ignoring malformed config file %s: %s", path, exc)
        return None

    return data if isinstance(data, dict) else None


def load_config(explicit_path: Optional[Path] = None) -> PortForgeConfig:
    """Built-in defaults, with the first user config file found merged on top.

    A malformed user config file is logged and skipped (falling back to
    defaults for whatever it would have overridden) rather than raising --
    unlike reservation storage, a bad config file isn't user data that
    could be silently lost, so the friendlier degrade-to-defaults behavior
    is appropriate here.
    """
    config = PortForgeConfig()

    candidates = [Path(explicit_path)] if explicit_path else paths.config_path_candidates()
    for candidate in candidates:
        if candidate.exists():
            raw = _read_structured_file(candidate)
            if raw is not None:
                _merge_raw_config(config, raw, str(candidate))
            break  # first existing candidate wins, whether or not it parsed

    return config


def find_project_config(start_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look for `.portforge.json`/`.yml`/`.yaml` starting at `start_dir`
    (default: the current working directory) and walking upward with the
    same bounded, home-directory-safe traversal used by Phase 3 project
    detection (see detection/evidence.py) -- so a `.portforge.yml`
    belonging to an unrelated project (or one sitting loose in $HOME) is
    never picked up by accident. Returns the parsed content, or None if no
    project config is found in that bounded context.
    """
    # Local import: keeps detection's dependency direction one-way
    # (detection never needs to know about reservations/config) while
    # letting config.py reuse its safety-bounded traversal instead of
    # duplicating it.
    from .detection.evidence import (
        DetectionCache,
        PORTFORGE_JSON_MANIFEST,
        PORTFORGE_YAML_MANIFESTS,
    )

    start_dir = start_dir or os.getcwd()
    cache = DetectionCache()

    for directory in cache.candidates_for(start_dir):
        entries = cache.list_dir(directory)

        if PORTFORGE_JSON_MANIFEST in entries:
            data = cache.read_json(directory / PORTFORGE_JSON_MANIFEST)
            if isinstance(data, dict):
                return data

        for yaml_name in PORTFORGE_YAML_MANIFESTS:
            if yaml_name in entries:
                if yaml is None:
                    continue
                text = cache.read_text(directory / yaml_name)
                if text is None:
                    continue
                try:
                    data = yaml.safe_load(text)
                except Exception:
                    continue
                if isinstance(data, dict):
                    return data

    return None
