"""Phase 8C: safe, style-preserving Docker Compose port editing.

Uses `ruamel.yaml`'s round-trip mode (see
docs/phase8c_config_audit.md §7/§16 for why plain `pyyaml` can't do this)
so comments, key order, anchors, and flow-vs-block style in an existing
compose.yaml survive untouched -- only the specific port value(s) a
manifest mapping targets are ever changed, and only IN PLACE on the
existing parsed structure (never a full re-serialize-from-scratch).

Supports both Compose port syntaxes:
  - short: "8000:8000", "127.0.0.1:8000:8000", "8000:8000/udp", "8000"
  - long:  {target: 8000, published: "8000", protocol: tcp, ...}

Never infers a container port from the allocated host port -- the
manifest's `container:` field is always the match key (see manifest.py's
`ComposePortEntry`); PortForge only ever decides the HOST side.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

_PROTOCOL_SUFFIX_RE = re.compile(r"^(?P<body>.+)/(?P<protocol>tcp|udp)$")


class ComposeError(Exception):
    def __init__(self, code: str, message: str, details: Optional[List[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class ComposeChange:
    service: str
    container_port: int
    protocol: str
    before_host: Optional[str]  # None if this port mapping is being newly added
    after_host: str
    action: str  # "update" | "add"


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # avoid ruamel re-wrapping long lines it didn't need to touch
    # ruamel's round-trip LOADER remembers per-node formatting, but the
    # DUMPER's indent settings are a separate, global knob that must be
    # set explicitly -- without this, re-dumping shifts every sequence's
    # indentation to ruamel's own default (0-offset dashes), not what the
    # source file actually used. mapping=2/sequence=4/offset=2 matches the
    # indent style the vast majority of real compose.yaml files already
    # use (list items indented two spaces past their key) -- see
    # docs/phase8c_config_audit.md §16's "YAML preservation" tradeoff note.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_compose(text: str) -> Any:
    try:
        data = _yaml().load(text)
    except Exception as exc:  # ruamel raises its own YAMLError subclasses
        raise ComposeError("CONFIG_PARSE_ERROR", f"Compose file is not valid YAML: {exc}")
    if not isinstance(data, dict):
        raise ComposeError("CONFIG_PARSE_ERROR", "Compose file must parse to a YAML mapping.")
    return data


def dump_compose(data: Any) -> str:
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return stream.getvalue()


def _parse_short_entry(value: str) -> Optional[Tuple[Optional[str], Optional[str], int, str]]:
    """Parses Compose's short port syntax into `(ip, host_port, container_port, protocol)`.

    Colon-count decides the shape -- deliberately NOT a single regex with
    two optional groups, which is ambiguous between `HOST:CONTAINER` (2
    parts) and `IP:CONTAINER` (also 2 parts if written that way, which
    Compose does not actually support -- a bare IP prefix always implies 3
    parts, `IP:HOST:CONTAINER`). This bug was caught by testing against
    the task's own worked example (`"9000:8000"` must mean host=9000,
    container=8000 -- not ip=9000).
    """
    value = value.strip()
    protocol = "tcp"
    body = value
    proto_match = _PROTOCOL_SUFFIX_RE.match(value)
    if proto_match:
        body = proto_match.group("body")
        protocol = proto_match.group("protocol")

    parts = body.split(":")
    if len(parts) == 1:
        if not parts[0].isdigit():
            return None  # e.g. a port range "3000-3005" -- not a single-port match candidate
        return None, None, int(parts[0]), protocol
    if len(parts) == 2:
        host, container = parts
        if not container.isdigit() or (host and not host.isdigit()):
            return None
        return None, (host or None), int(container), protocol
    if len(parts) == 3:
        ip, host, container = parts
        if not container.isdigit() or (host and not host.isdigit()):
            return None
        return (ip or None), (host or None), int(container), protocol
    return None


def _entry_container_and_protocol(entry: Any) -> Optional[Tuple[int, str]]:
    if isinstance(entry, str):
        parsed = _parse_short_entry(entry)
        return None if parsed is None else (parsed[2], parsed[3])
    if isinstance(entry, dict):
        target = entry.get("target")
        if not isinstance(target, int):
            return None
        protocol = entry.get("protocol", "tcp")
        return target, str(protocol)
    return None


def _rewrite_short_entry(value: str, new_host_port: int) -> str:
    parsed = _parse_short_entry(value)
    ip, _old_host, container, protocol = parsed
    had_explicit_protocol = bool(_PROTOCOL_SUFFIX_RE.match(value.strip()))
    proto_suffix = f"/{protocol}" if had_explicit_protocol else ""
    if ip:
        return f"{ip}:{new_host_port}:{container}{proto_suffix}"
    return f"{new_host_port}:{container}{proto_suffix}"


def apply_port_mapping(
    data: Any, service: str, container_port: int, protocol: str, new_host_port: int
) -> Tuple[Any, ComposeChange]:
    """Mutates `data` (the parsed Compose document) IN PLACE, applying one
    manifest-declared port mapping. Returns `(data, ComposeChange)`.

    - Zero existing entries with this (container_port, protocol): appends a
      new short-syntax entry -- never invents a container port from the
      host port, the container port always comes from the manifest.
    - Exactly one match: updates ONLY that entry's host/published side,
      preserving its own style (short vs long) and every other field.
    - More than one match: raises ComposeError("COMPOSE_PORT_AMBIGUOUS", ...),
      no mutation.
    """
    services = data.get("services")
    if not isinstance(services, dict) or service not in services:
        raise ComposeError("COMPOSE_SERVICE_NOT_FOUND", f"Service '{service}' not found in compose file.")

    service_map = services[service]
    if not isinstance(service_map, dict):
        raise ComposeError("CONFIG_PARSE_ERROR", f"Service '{service}' is not a mapping.")

    ports_list = service_map.get("ports")
    if ports_list is None:
        ports_list = CommentedSeq()
        service_map["ports"] = ports_list
    if not isinstance(ports_list, list):
        raise ComposeError("CONFIG_PARSE_ERROR", f"Service '{service}'.ports must be a list.")

    matches = []
    for i, entry in enumerate(ports_list):
        parsed = _entry_container_and_protocol(entry)
        if parsed is not None and parsed == (container_port, protocol):
            matches.append(i)

    if len(matches) > 1:
        raise ComposeError(
            "COMPOSE_PORT_AMBIGUOUS",
            f"Service '{service}' has {len(matches)} existing port mappings for container port "
            f"{container_port}/{protocol} -- PortForge cannot safely determine which one to update.",
            details=[{"service": service, "container_port": container_port, "protocol": protocol}],
        )

    if len(matches) == 1:
        i = matches[0]
        entry = ports_list[i]
        if isinstance(entry, str):
            before_host = _parse_short_entry(entry)[1]
            ports_list[i] = _rewrite_short_entry(entry, new_host_port)
        else:
            before_host = str(entry.get("published")) if "published" in entry else None
            entry["published"] = DoubleQuotedScalarString(str(new_host_port))
        action = "update"
    else:
        before_host = None
        proto_suffix = "" if protocol == "tcp" else f"/{protocol}"
        # Explicitly quoted (Docker's own compose file convention, and
        # avoids ANY ambiguity with YAML's colon-as-mapping-key syntax in
        # other parsers, even though "8000:8000" unquoted round-trips
        # safely through ruamel/PyYAML themselves).
        ports_list.append(DoubleQuotedScalarString(f"{new_host_port}:{container_port}{proto_suffix}"))
        action = "add"

    change = ComposeChange(
        service=service,
        container_port=container_port,
        protocol=protocol,
        before_host=before_host,
        after_host=str(new_host_port),
        action=action,
    )
    return data, change
