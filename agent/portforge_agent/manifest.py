"""Phase 8B: project manifest parsing and strict validation.

Purely local and network-free -- this module never talks to Central. It
turns a `portforge.yml` file into a validated `ProjectManifest`. Host
resolution and Central communication live in `project_adapter.py`; the CLI
commands in `cli.py` wire the two together. See
docs/phase8b_manifest_audit.md for why this is a NEW filename
(`portforge.yml`/`.yaml`, no leading dot) rather than a reuse of Phase 4's
existing `.portforge.yml` (which has an incompatible, already-shipped
`ports:` LIST schema for local-only reservation sync -- see that audit's
§1) and docs/phase8b_project_manifest.md for the full schema reference.

Safety model (mirrors detection/evidence.py's, see that module's
docstring): `yaml.safe_load()` only -- never the default/full loader, so no
manifest can construct an arbitrary Python object or run a custom tag.
Every manifest is size-capped (`MAX_MANIFEST_BYTES`) before parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a required dependency; see pyproject.toml
    yaml = None

# Matches detection/evidence.py's MAX_MANIFEST_BYTES exactly -- same class
# of file (a small, trusted, project-local text file), same bound.
MAX_MANIFEST_BYTES = 512 * 1024  # 512 KiB

# No leading dot, deliberately distinct from Phase 4's `.portforge.yml`
# (see module docstring). Checked in this order.
MANIFEST_FILENAMES = ("portforge.yml", "portforge.yaml")

SUPPORTED_MANIFEST_VERSION = 1

# Mirrors backend/app/schemas/allocation.py's MAX_BUNDLE_SIZE. True
# cross-process code reuse isn't possible (agent and backend are separate
# deployables with separate dependencies -- see docs/phase8b_manifest_audit.md
# §2); kept in sync manually, this comment is the tripwire.
MAX_PORTS_PER_MANIFEST = 20

_PROTOCOLS = ("tcp", "udp")

# Phase 8C: `config:` is a new, OPTIONAL top-level key -- a manifest
# without it validates exactly as it did in Phase 8B (backward compatible).
_TOP_LEVEL_KEYS = {"version", "project", "target", "ports", "request_id", "config"}
_TARGET_KEYS = {"host"}
_PORT_ENTRY_KEYS = {"purpose", "protocol", "preferred"}

_CONFIG_KEYS = {"dotenv", "compose"}
_DOTENV_ENTRY_KEYS = {"file", "values"}
_COMPOSE_ENTRY_KEYS = {"file", "services"}
_COMPOSE_PORT_ENTRY_KEYS = {"allocation", "container", "protocol"}

MAX_CONFIG_FILES_PER_KIND = 10
MAX_VALUES_PER_DOTENV_FILE = 50
MAX_SERVICES_PER_COMPOSE_FILE = 50
MAX_PORTS_PER_COMPOSE_SERVICE = 20

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ManifestError(Exception):
    """Machine-readable manifest failure. Mirrors
    services/allocation_service.py::AllocationError's shape on the backend
    side (code/message/details) so CLI error rendering can treat both
    uniformly -- see cli.py's project command handlers.
    """

    def __init__(self, code: str, message: str, details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class ManifestPortRequest:
    name: str
    purpose: str
    protocol: str
    preferred_port: Optional[int]


@dataclass(frozen=True)
class DotenvFileMapping:
    file: str  # project-root-relative path, e.g. ".env" -- NOT yet resolved/verified safe
    values: Dict[str, str]  # env var name -> allocation request name (a `ports.<name>` key)


@dataclass(frozen=True)
class ComposePortEntry:
    allocation: str  # allocation request name (a `ports.<name>` key)
    container: int
    protocol: str  # default "tcp"


@dataclass(frozen=True)
class ComposeFileMapping:
    file: str
    services: Dict[str, List[ComposePortEntry]]  # service name -> port entries


@dataclass(frozen=True)
class ConfigMappings:
    dotenv: List[DotenvFileMapping]
    compose: List[ComposeFileMapping]


@dataclass(frozen=True)
class ProjectManifest:
    version: int
    project: str
    host: str  # raw target.host value -- hostname or UUID string, not yet resolved
    requests: List[ManifestPortRequest]
    request_id: Optional[str]
    config: Optional[ConfigMappings]  # Phase 8C, optional -- None for a Phase 8B-only manifest


def discover_manifest_path(start_dir: Optional[str] = None) -> Optional[Path]:
    """Checks `MANIFEST_FILENAMES` in order, in `start_dir` (default: cwd)
    ONLY -- deliberately no upward walk (see docs/phase8b_manifest_audit.md
    "Discovery scope"). An explicit path given on the command line always
    takes priority over this and never calls into this function.
    """
    base = Path(start_dir) if start_dir else Path.cwd()
    for name in MANIFEST_FILENAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def load_manifest_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        raise ManifestError("MANIFEST_NOT_FOUND", f"Manifest file not found: {path}")

    if size > MAX_MANIFEST_BYTES:
        raise ManifestError(
            "MANIFEST_TOO_LARGE",
            f"Manifest file {path} is {size} bytes, exceeding the {MAX_MANIFEST_BYTES}-byte limit.",
        )

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError("MANIFEST_NOT_FOUND", f"Could not read manifest file {path}: {exc}")


def parse_manifest_yaml(text: str) -> Dict[str, Any]:
    if yaml is None:  # pragma: no cover - pyyaml is a required dependency
        raise ManifestError("MANIFEST_PARSE_ERROR", "PyYAML is not available.")

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError("MANIFEST_PARSE_ERROR", f"Manifest is not valid YAML: {exc}")

    if not isinstance(data, dict):
        raise ManifestError(
            "MANIFEST_PARSE_ERROR",
            "Manifest must parse to a YAML mapping (top-level key: value pairs), "
            f"got {type(data).__name__}.",
        )
    return data


def _reject_unknown_keys(data: Dict[str, Any], allowed: set, context: str) -> None:
    unknown = sorted(set(data.keys()) - allowed)
    if unknown:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"Unknown field(s) in {context}: {', '.join(unknown)}. "
            f"Known field(s): {', '.join(sorted(allowed))}.",
            details=[{"context": context, "field": f} for f in unknown],
        )


def _require_string(data: Dict[str, Any], key: str, context: str, max_length: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(
            "MANIFEST_INVALID", f"'{key}' in {context} must be a non-empty string.", details=[{"field": key}]
        )
    value = value.strip()
    if len(value) > max_length:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"'{key}' in {context} exceeds the {max_length}-character limit.",
            details=[{"field": key}],
        )
    return value


def _validate_port_entry(name: str, entry: Any) -> ManifestPortRequest:
    context = f"ports.{name}"
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("MANIFEST_INVALID", "Port entry names must be non-empty strings.")
    if len(name) > 255:
        raise ManifestError("MANIFEST_INVALID", f"Port entry name '{name}' exceeds the 255-character limit.")
    if not isinstance(entry, dict):
        raise ManifestError("MANIFEST_INVALID", f"'{context}' must be a mapping.")

    _reject_unknown_keys(entry, _PORT_ENTRY_KEYS, context)
    purpose = _require_string(entry, "purpose", context, max_length=64)

    protocol = entry.get("protocol", "tcp")
    if not isinstance(protocol, str) or protocol not in _PROTOCOLS:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"'{context}.protocol' must be one of {_PROTOCOLS}, got {protocol!r}.",
            details=[{"field": f"{context}.protocol"}],
        )

    preferred_port: Optional[int] = None
    if "preferred" in entry:
        raw = entry["preferred"]
        if isinstance(raw, bool) or not isinstance(raw, int) or not (1 <= raw <= 65535):
            raise ManifestError(
                "MANIFEST_INVALID",
                f"'{context}.preferred' must be an integer between 1 and 65535, got {raw!r}.",
                details=[{"field": f"{context}.preferred"}],
            )
        preferred_port = raw

    return ManifestPortRequest(name=name.strip(), purpose=purpose, protocol=protocol, preferred_port=preferred_port)


def _validate_dotenv_mapping(entry: Any, index: int, known_names: set) -> DotenvFileMapping:
    context = f"config.dotenv[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
    _reject_unknown_keys(entry, _DOTENV_ENTRY_KEYS, context)
    file = _require_string(entry, "file", context, max_length=1024)

    values = entry.get("values")
    if not isinstance(values, dict) or len(values) == 0:
        raise ManifestError("MANIFEST_INVALID", f"'{context}.values' must be a non-empty mapping.")
    if len(values) > MAX_VALUES_PER_DOTENV_FILE:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"'{context}.values' has {len(values)} entries, exceeding the {MAX_VALUES_PER_DOTENV_FILE}-entry limit.",
        )

    resolved: Dict[str, str] = {}
    for env_key, alloc_name in values.items():
        if not isinstance(env_key, str) or not _ENV_KEY_RE.match(env_key):
            raise ManifestError(
                "MANIFEST_INVALID",
                f"'{context}.values' key '{env_key}' is not a valid environment variable name "
                "(expected [A-Za-z_][A-Za-z0-9_]*).",
            )
        if not isinstance(alloc_name, str) or not alloc_name.strip():
            raise ManifestError(
                "MANIFEST_INVALID", f"'{context}.values.{env_key}' must reference a port request name (a string)."
            )
        alloc_name = alloc_name.strip()
        if alloc_name not in known_names:
            raise ManifestError(
                "CONFIG_MAPPING_INVALID",
                f"'{context}.values.{env_key}' references '{alloc_name}', which is not one of this "
                f"manifest's 'ports' entries: {', '.join(sorted(known_names))}.",
                details=[{"field": f"{context}.values.{env_key}", "reference": alloc_name}],
            )
        resolved[env_key] = alloc_name

    return DotenvFileMapping(file=file, values=resolved)


def _validate_compose_port_entry(entry: Any, context: str, known_names: set) -> ComposePortEntry:
    if not isinstance(entry, dict):
        raise ManifestError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
    _reject_unknown_keys(entry, _COMPOSE_PORT_ENTRY_KEYS, context)

    allocation = entry.get("allocation")
    if not isinstance(allocation, str) or not allocation.strip():
        raise ManifestError("MANIFEST_INVALID", f"'{context}.allocation' must be a non-empty string.")
    allocation = allocation.strip()
    if allocation not in known_names:
        raise ManifestError(
            "CONFIG_MAPPING_INVALID",
            f"'{context}.allocation' references '{allocation}', which is not one of this manifest's "
            f"'ports' entries: {', '.join(sorted(known_names))}.",
            details=[{"field": f"{context}.allocation", "reference": allocation}],
        )

    container = entry.get("container")
    if isinstance(container, bool) or not isinstance(container, int) or not (1 <= container <= 65535):
        raise ManifestError(
            "MANIFEST_INVALID", f"'{context}.container' must be an integer between 1 and 65535, got {container!r}."
        )

    protocol = entry.get("protocol", "tcp")
    if not isinstance(protocol, str) or protocol not in _PROTOCOLS:
        raise ManifestError(
            "MANIFEST_INVALID", f"'{context}.protocol' must be one of {_PROTOCOLS}, got {protocol!r}."
        )

    return ComposePortEntry(allocation=allocation, container=container, protocol=protocol)


def _validate_compose_mapping(entry: Any, index: int, known_names: set) -> ComposeFileMapping:
    context = f"config.compose[{index}]"
    if not isinstance(entry, dict):
        raise ManifestError("MANIFEST_INVALID", f"'{context}' must be a mapping.")
    _reject_unknown_keys(entry, _COMPOSE_ENTRY_KEYS, context)
    file = _require_string(entry, "file", context, max_length=1024)

    services = entry.get("services")
    if not isinstance(services, dict) or len(services) == 0:
        raise ManifestError("MANIFEST_INVALID", f"'{context}.services' must be a non-empty mapping.")
    if len(services) > MAX_SERVICES_PER_COMPOSE_FILE:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"'{context}.services' has {len(services)} entries, exceeding the "
            f"{MAX_SERVICES_PER_COMPOSE_FILE}-entry limit.",
        )

    resolved: Dict[str, List[ComposePortEntry]] = {}
    for service_name, service_entry in services.items():
        service_context = f"{context}.services.{service_name}"
        if not isinstance(service_name, str) or not service_name.strip():
            raise ManifestError("MANIFEST_INVALID", f"'{context}.services' keys must be non-empty strings.")
        if not isinstance(service_entry, dict):
            raise ManifestError("MANIFEST_INVALID", f"'{service_context}' must be a mapping.")
        _reject_unknown_keys(service_entry, {"ports"}, service_context)

        ports_list = service_entry.get("ports")
        if not isinstance(ports_list, list) or len(ports_list) == 0:
            raise ManifestError("MANIFEST_INVALID", f"'{service_context}.ports' must be a non-empty list.")
        if len(ports_list) > MAX_PORTS_PER_COMPOSE_SERVICE:
            raise ManifestError(
                "MANIFEST_INVALID",
                f"'{service_context}.ports' has {len(ports_list)} entries, exceeding the "
                f"{MAX_PORTS_PER_COMPOSE_SERVICE}-entry limit.",
            )

        resolved[service_name.strip()] = [
            _validate_compose_port_entry(item, f"{service_context}.ports[{i}]", known_names)
            for i, item in enumerate(ports_list)
        ]

    return ComposeFileMapping(file=file, services=resolved)


def _validate_config(data: Any, known_names: set) -> Optional[ConfigMappings]:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ManifestError("MANIFEST_INVALID", "'config' must be a mapping.")
    _reject_unknown_keys(data, _CONFIG_KEYS, "config")

    dotenv_raw = data.get("dotenv", [])
    if not isinstance(dotenv_raw, list):
        raise ManifestError("MANIFEST_INVALID", "'config.dotenv' must be a list.")
    if len(dotenv_raw) > MAX_CONFIG_FILES_PER_KIND:
        raise ManifestError(
            "MANIFEST_INVALID", f"'config.dotenv' has {len(dotenv_raw)} entries, exceeding the "
            f"{MAX_CONFIG_FILES_PER_KIND}-entry limit."
        )
    dotenv = [_validate_dotenv_mapping(item, i, known_names) for i, item in enumerate(dotenv_raw)]

    compose_raw = data.get("compose", [])
    if not isinstance(compose_raw, list):
        raise ManifestError("MANIFEST_INVALID", "'config.compose' must be a list.")
    if len(compose_raw) > MAX_CONFIG_FILES_PER_KIND:
        raise ManifestError(
            "MANIFEST_INVALID", f"'config.compose' has {len(compose_raw)} entries, exceeding the "
            f"{MAX_CONFIG_FILES_PER_KIND}-entry limit."
        )
    compose = [_validate_compose_mapping(item, i, known_names) for i, item in enumerate(compose_raw)]

    if not dotenv and not compose:
        raise ManifestError("MANIFEST_INVALID", "'config' was given but declares no 'dotenv' or 'compose' entries.")

    return ConfigMappings(dotenv=dotenv, compose=compose)


def validate_manifest(data: Dict[str, Any]) -> ProjectManifest:
    """Strict validation: unknown fields at any level fail loudly (a typo
    like `protcol: tcp` must never silently become a default `protocol`)
    rather than being ignored.
    """
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, "manifest")

    version = data.get("version")
    if version is None:
        raise ManifestError("MANIFEST_INVALID", "Manifest is missing required field 'version'.")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ManifestError("MANIFEST_INVALID", f"'version' must be an integer, got {version!r}.")
    if version != SUPPORTED_MANIFEST_VERSION:
        raise ManifestError(
            "UNSUPPORTED_MANIFEST_VERSION",
            f"Manifest version {version} is not supported. This version of PortForge supports version "
            f"{SUPPORTED_MANIFEST_VERSION} only. No automatic migration is attempted.",
            details=[{"version": version, "supported": SUPPORTED_MANIFEST_VERSION}],
        )

    project = _require_string(data, "project", "manifest", max_length=255)

    target = data.get("target")
    if not isinstance(target, dict):
        raise ManifestError("MANIFEST_INVALID", "'target' must be a mapping with a 'host' field.")
    _reject_unknown_keys(target, _TARGET_KEYS, "target")
    host = _require_string(target, "host", "target", max_length=255)

    ports = data.get("ports")
    if not isinstance(ports, dict):
        raise ManifestError(
            "MANIFEST_INVALID",
            "'ports' must be a mapping of name -> {purpose, protocol?, preferred?} "
            "(a list is Phase 4's separate .portforge.yml 'ports' schema, not this manifest's).",
        )
    if len(ports) == 0:
        raise ManifestError("MANIFEST_INVALID", "'ports' must contain at least one entry.")
    if len(ports) > MAX_PORTS_PER_MANIFEST:
        raise ManifestError(
            "MANIFEST_INVALID",
            f"'ports' has {len(ports)} entries, exceeding the {MAX_PORTS_PER_MANIFEST}-entry limit.",
        )

    requests = [_validate_port_entry(name, entry) for name, entry in ports.items()]
    known_names = {item.name for item in requests}

    request_id: Optional[str] = None
    if "request_id" in data:
        raw = data["request_id"]
        if not isinstance(raw, str) or not raw.strip():
            raise ManifestError("MANIFEST_INVALID", "'request_id' must be a non-empty string if present.")
        request_id = raw.strip()

    config = _validate_config(data.get("config"), known_names)

    return ProjectManifest(
        version=version, project=project, host=host, requests=requests, request_id=request_id, config=config
    )


def load_and_validate_manifest(path: Optional[Path], start_dir: Optional[str] = None) -> ProjectManifest:
    """Resolves the manifest path (explicit `path`, or discovery in
    `start_dir`/cwd if `path` is None), then loads, parses, and validates
    it. Raises `ManifestError` with the appropriate code at whichever stage
    fails first.
    """
    if path is None:
        discovered = discover_manifest_path(start_dir)
        if discovered is None:
            raise ManifestError(
                "MANIFEST_NOT_FOUND",
                f"No manifest given and none of {MANIFEST_FILENAMES} found in the current directory.",
            )
        path = discovered
    elif not path.is_file():
        raise ManifestError("MANIFEST_NOT_FOUND", f"Manifest file not found: {path}")

    text = load_manifest_text(path)
    data = parse_manifest_yaml(text)
    return validate_manifest(data)
