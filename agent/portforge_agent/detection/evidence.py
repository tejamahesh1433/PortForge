"""Shared, safety-bounded evidence gathering for project/purpose detection.

Safety model (see agent/README.md "Safety limits" for the user-facing
version of this):

- Never executes, imports, or evaluates anything it reads -- only
  ``json.loads`` / a read-only TOML parser / plain-text regex on files this
  process can already read.
- Never scans the whole disk: only a process's own working directory and a
  bounded number of parent directories (``max_depth``, configurable).
- Every manifest read is size-capped (``MAX_MANIFEST_BYTES``) *before*
  parsing, so a huge or hostile file is skipped, not slow-parsed.
- Every read is wrapped so permission errors, missing files, malformed
  JSON/TOML, and encoding errors all degrade to "no evidence here" --
  never an exception that reaches the caller.
- Never runs `npm`, `pip`, `git`, or any other tool -- project markers are
  recognized purely by filename.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger("portforge_agent.detection.evidence")

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - depends on interpreter version
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - tomli not installed either
        tomllib = None  # type: ignore[assignment]

# Every filename that marks "this directory is a project root", per the
# project brief. Detection only ever checks for presence of these names --
# it never opens or interprets most of them (see project.py for which ones
# additionally get their contents read for a declared name).
PROJECT_MARKERS: frozenset = frozenset(
    {
        ".git",
        "package.json",
        "pnpm-workspace.yaml",
        "yarn.lock",
        "package-lock.json",
        "pyproject.toml",
        "requirements.txt",
        "Pipfile",
        "poetry.lock",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "composer.json",
        "Gemfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }
)

PORTFORGE_JSON_MANIFEST = ".portforge.json"
PORTFORGE_YAML_MANIFESTS = (".portforge.yml", ".portforge.yaml")

DEFAULT_MAX_TRAVERSAL_DEPTH = 6
MAX_MANIFEST_BYTES = 512 * 1024  # 512 KiB: generous for any real manifest, bounded against abuse


class DetectionCache:
    """Per-scan cache so repeated directories/manifests are only touched once.

    A single scan can observe many processes that share a working directory
    (several services under one monorepo, or just several ports opened by
    the same process), so without caching the same package.json could be
    read and parsed dozens of times per scan. One cache is created per
    ``discover_all_ports()`` call and threaded through every port's
    detection (see detection/__init__.py).
    """

    def __init__(self, max_depth: Optional[int] = None):
        if max_depth is None:
            max_depth = _default_max_depth()
        self.max_depth = max_depth
        self._dir_entries: Dict[Path, Optional[frozenset]] = {}
        self._text_cache: Dict[Path, Optional[str]] = {}
        self._json_cache: Dict[Path, Optional[dict]] = {}
        self._toml_cache: Dict[Path, Optional[dict]] = {}
        self._candidates_cache: Dict[Path, List[Path]] = {}

    def list_dir(self, directory: Path) -> frozenset:
        """Names of entries directly inside `directory`, or an empty
        frozenset if it can't be listed (missing, permission denied, or not
        actually a directory) -- never raises.
        """
        if directory in self._dir_entries:
            cached = self._dir_entries[directory]
            return cached if cached is not None else frozenset()

        entries: Optional[frozenset]
        try:
            entries = frozenset(p.name for p in directory.iterdir())
        except (OSError, PermissionError):
            entries = None
        self._dir_entries[directory] = entries
        return entries if entries is not None else frozenset()

    def candidates_for(self, start_dir: Optional[str]) -> List[Path]:
        """Ordered list of directories to inspect: `start_dir` itself, then
        up to `max_depth` parent directories. Bounded and cached by
        resolved start path so repeated calls for the same directory (or a
        directory under a previously-resolved one) don't redo the work.

        The walk never includes, or goes above, the user's home directory.
        Many unrelated processes on a real dev machine (GUI apps launched
        from the shell, services, anything that doesn't set its own cwd)
        end up with a working directory of "the user's home folder" purely
        as a platform default -- not because they have anything to do with
        whatever happens to be sitting loose in it. Without this boundary,
        a single stray package.json/requirements.txt directly in $HOME (a
        real, observed case: pip/npm tooling and editors both do this)
        would get every such process misattributed to the same bogus
        "project" named after the home directory. Treating $HOME itself as
        outside project territory is a deliberate, conservative choice, not
        an oversight -- see agent/README.md "Known limitations" for the
        (rare, accepted) cost: a real project living directly in $HOME with
        no subdirectory won't be detected.
        """
        if not start_dir:
            return []
        try:
            resolved = Path(start_dir).resolve()
        except (OSError, ValueError, RuntimeError):
            return []

        if resolved in self._candidates_cache:
            return self._candidates_cache[resolved]

        home = _home_directory()

        result: List[Path] = []
        current = resolved
        for _ in range(self.max_depth + 1):
            if home is not None and current == home:
                break
            result.append(current)
            parent = current.parent
            if parent == current:  # filesystem root reached
                break
            current = parent

        self._candidates_cache[resolved] = result
        return result

    def _read_bounded_text(self, path: Path) -> Optional[str]:
        if path in self._text_cache:
            return self._text_cache[path]

        text: Optional[str] = None
        try:
            if path.is_file() and path.stat().st_size <= MAX_MANIFEST_BYTES:
                text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError, UnicodeDecodeError):
            text = None
        self._text_cache[path] = text
        return text

    def read_text(self, path: Path) -> Optional[str]:
        """Size-capped, error-swallowing text read. None if unreadable,
        too large, or the file doesn't exist.
        """
        return self._read_bounded_text(path)

    def read_json(self, path: Path) -> Optional[dict]:
        """Size-capped JSON read. None for anything unreadable, too large,
        not valid JSON, or not a JSON object at the top level.
        """
        if path in self._json_cache:
            return self._json_cache[path]

        data: Optional[dict] = None
        text = self._read_bounded_text(path)
        if text is not None:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    data = parsed
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                logger.debug("Malformed JSON manifest %s: %s", path, exc)
                data = None
        self._json_cache[path] = data
        return data

    def read_toml(self, path: Path) -> Optional[dict]:
        """Size-capped TOML read. None for anything unreadable, too large,
        not valid TOML, or if no TOML parser is available at all.
        """
        if path in self._toml_cache:
            return self._toml_cache[path]

        data: Optional[dict] = None
        if tomllib is not None:
            text = self._read_bounded_text(path)
            if text is not None:
                try:
                    parsed = tomllib.loads(text)
                    if isinstance(parsed, dict):
                        data = parsed
                except Exception as exc:  # tomllib's error type varies by backport; be defensive
                    logger.debug("Malformed TOML manifest %s: %s", path, exc)
                    data = None
        self._toml_cache[path] = data
        return data


_home_directory_cache: Dict[str, Optional[Path]] = {}


def _home_directory() -> Optional[Path]:
    if "value" not in _home_directory_cache:
        try:
            _home_directory_cache["value"] = Path.home().resolve()
        except (OSError, RuntimeError):
            _home_directory_cache["value"] = None
    return _home_directory_cache["value"]


def _default_max_depth() -> int:
    import os

    raw = os.environ.get("PORTFORGE_MAX_TRAVERSAL_DEPTH")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_TRAVERSAL_DEPTH


# ---------------------------------------------------------------------------
# Manifest content extraction. Each function takes already-parsed/read data
# (from DetectionCache) and returns plain values -- no I/O happens here.
# ---------------------------------------------------------------------------


def extract_json_name(data: Optional[dict]) -> Optional[str]:
    """Shared by package.json and composer.json: both use a top-level "name"."""
    if not data:
        return None
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def extract_package_json_dependencies(data: Optional[dict]) -> Set[str]:
    if not data:
        return set()
    deps: Set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(str(k).lower() for k in section.keys())
    return deps


def extract_pyproject_name(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    project = data.get("project")
    if isinstance(project, dict):
        name = project.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            name = poetry.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


_REQUIREMENT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+")


def _split_requirement_name(spec: object) -> Optional[str]:
    if not isinstance(spec, str):
        return None
    spec = spec.strip()
    if not spec or spec.startswith("#") or spec.startswith("-"):
        return None
    match = _REQUIREMENT_NAME_RE.match(spec)
    if not match:
        return None
    return match.group(0).lower().replace("_", "-")


def extract_pyproject_dependencies(data: Optional[dict]) -> Set[str]:
    if not data:
        return set()
    deps: Set[str] = set()

    project = data.get("project")
    if isinstance(project, dict):
        for item in project.get("dependencies") or []:
            name = _split_requirement_name(item)
            if name:
                deps.add(name)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    for item in group:
                        name = _split_requirement_name(item)
                        if name:
                            deps.add(name)

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            for section_name in ("dependencies", "dev-dependencies"):
                section = poetry.get(section_name)
                if isinstance(section, dict):
                    deps.update(
                        str(k).lower() for k in section.keys() if str(k).lower() != "python"
                    )

    return deps


def extract_requirements_txt_dependencies(text: Optional[str]) -> Set[str]:
    if not text:
        return set()
    deps: Set[str] = set()
    for line in text.splitlines():
        name = _split_requirement_name(line)
        if name:
            deps.add(name)
    return deps


def extract_cargo_toml_name(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    package = data.get("package")
    if isinstance(package, dict):
        name = package.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


_GO_MODULE_RE = re.compile(r"^module\s+(\S+)", re.MULTILINE)


def extract_go_mod_name(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = _GO_MODULE_RE.search(text)
    if not match:
        return None
    module_path = match.group(1).strip().strip('"')
    if not module_path:
        return None
    return module_path.rstrip("/").rsplit("/", 1)[-1] or None


_PARENT_BLOCK_RE = re.compile(r"<parent>.*?</parent>", re.DOTALL)
_ARTIFACT_ID_RE = re.compile(r"<artifactId>\s*([^<]+?)\s*</artifactId>")


def extract_pom_xml_name(text: Optional[str]) -> Optional[str]:
    """Best-effort artifactId extraction via plain regex -- deliberately not
    a real XML parser (avoids any entity-expansion attack surface). The
    <parent> block is stripped first so a parent POM's artifactId (which
    Maven convention places before the project's own) isn't picked instead.
    """
    if not text:
        return None
    stripped = _PARENT_BLOCK_RE.sub("", text, count=1)
    match = _ARTIFACT_ID_RE.search(stripped)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_portforge_json_project(data: Optional[dict]) -> Optional[str]:
    if not data:
        return None
    name = data.get("project")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


_PORTFORGE_YAML_PROJECT_RE = re.compile(r"^project:\s*[\"']?([^\"'\r\n#]+?)[\"']?\s*(?:#.*)?$", re.MULTILINE)


def extract_portforge_yaml_project(text: Optional[str]) -> Optional[str]:
    """Recognizes only a minimal top-level ``project: <name>`` key -- not a
    full YAML parser (avoids adding a YAML dependency for one field).
    """
    if not text:
        return None
    match = _PORTFORGE_YAML_PROJECT_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None
