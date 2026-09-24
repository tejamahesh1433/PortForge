"""PortForge CLI.

Discovery (Phases 1-3, unchanged):
    python -m portforge_agent scan [--json] [--port P] [--project P] [--source S] [--purpose P]
    python -m portforge_agent docker [--json]
    python -m portforge_agent inspect PORT [--json]

Reservations, conflicts, and recommendation (Phase 4):
    python -m portforge_agent check PORT [--protocol tcp|udp] [--address ADDR] [--json]
    python -m portforge_agent next SERVICE_TYPE [--project P] [--service S] [--purpose P]
                                    [--protocol tcp|udp] [--address ADDR] [--reserve] [--json]
    python -m portforge_agent reserve PORT --project P [--service S] [--purpose P]
                                      [--protocol tcp|udp] [--address ADDR] [--notes N] [--json]
    python -m portforge_agent release PORT --project P [--protocol tcp|udp] [--json]
    python -m portforge_agent release --id RESERVATION_ID [--json]
    python -m portforge_agent reservations [--json]
    python -m portforge_agent conflicts [--json]
    python -m portforge_agent sync-reservations [--path DIR] [--json]

Agent allocation (Phase 8A -- provider-independent; see
docs/phase8a_agent_allocation.md for the full contract):
    python -m portforge_agent allocate --host H --project P
                                        --request NAME:PURPOSE[:PROTOCOL[:PREFERRED_PORT_OR_RANGE[:RANGE]]] [...]
                                        [--request-id ID] [--url URL] [--json] [--format text|env]
    python -m portforge_agent allocate --file portforge.request.json [--url URL] [--json] [--format text|env]
    python -m portforge_agent allocate --stdin [--url URL] [--json] [--format text|env]
    python -m portforge_agent allocation get ALLOCATION_ID [--url URL] [--json] [--format text|env]
    python -m portforge_agent allocation release ALLOCATION_ID [--url URL] [--json]

Also installed as a console script: `portforge <command> ...` behaves
identically to `python -m portforge_agent <command> ...` (see
pyproject.toml `[project.scripts]`).

Exit codes (see agent/README.md "Exit codes" for the full table):
    0  success / available / no conflicts
    1  a normal, expected negative outcome (unavailable, refused, conflicts found)
    2  an operational or configuration error (bad arguments, unreadable
       reservation storage, unknown service type, ...)

Filter case-sensitivity (see agent/README.md "Filtering"): --port is an
exact numeric match; --source is a case-insensitive EXACT match against
process/docker/system; --project and --purpose are case-insensitive
SUBSTRING matches (--purpose also checks `category`, since that's what the
default table's PURPOSE column actually shows).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from . import platform as pf
from .bindprobe import probe_bind
from .config import find_project_config, load_config
from .discovery import discover_all_ports, discover_docker_view
from .evaluate import EvaluatedPort, evaluate_all, evaluate_port, find_discovered, find_reservation
from .models import DiscoveredPort, PortState, Protocol, Source
from .paths import reservations_path as default_reservations_path
from .recommend import recommend_and_reserve, recommend_port
from .reservations.storage import ReservationStorageError, ReservationStore
from .reservations.migration import migrate_if_needed
from .reserve_ops import release as reserve_ops_release
from .reserve_ops import release_by_id as reserve_ops_release_by_id
from .reserve_ops import reserve as reserve_ops_reserve
from .reserve_ops import sync_project_reservations
from .manifest import ManifestError, discover_manifest_path, load_and_validate_manifest
from .project_adapter import build_candidate_preview, build_normalized_request, resolve_host_ref, to_allocation_body
from . import config_manager as _config_manager
from .config_files import ConfigPathError as _ConfigPathError
from . import doctor as _doctor
from .agent_contract import build_contract
from .workflow import WorkflowError, apply_workflow, get_workflow_status, prepare_workflow
from .project_init import create_manifest, render_manifest
from .workspace import discover_workspace, plan_workspace, workspace_to_json
from .targets.models import TargetsError
from .targets.plan import plan_for_target
from .targets.request_id import namespace_request_id
from .targets.resolve import resolve_target

_MIN_COLUMN_WIDTH = 8
_COLUMN_GAP = 2

# ---------------------------------------------------------------------------
# Discovery table rendering (Phases 1-3, unchanged)
# ---------------------------------------------------------------------------

# The default `scan` table: a general enriched overview. Per the project
# brief this intentionally does not include SERVICE/MAPPING/ADDRESS to stay
# narrow -- ADDRESS is kept as a deliberate addition beyond the literal
# spec, though, since without it dual-stack bindings (a real, common case:
# Docker Desktop and Windows both routinely publish/listen on both 0.0.0.0
# and :: for the same port) render as confusing-looking duplicate rows.
# Full detail is always available via --json or `inspect`.
_SCAN_COLUMNS = [
    ("PORT", lambda p: str(p.host_port if p.host_port is not None else p.port)),
    ("PROTOCOL", lambda p: p.protocol.value.upper()),
    ("STATUS", lambda p: p.state.value),
    ("PROJECT", lambda p: p.project_name or "-"),
    ("PURPOSE", lambda p: p.category if (p.category and p.category != "unknown") else "-"),
    ("OWNER", lambda p: p.container_name or p.process_name or "-"),
    ("SOURCE", lambda p: p.source.value.capitalize()),
    ("ADDRESS", lambda p: p.bind_address),
]

# The `docker` table: focused on the host<->container mapping detail that
# `scan`'s general-purpose table above deliberately leaves out.
_DOCKER_COLUMNS = [
    ("PORT", lambda p: str(p.host_port if p.host_port is not None else p.port)),
    ("PROTOCOL", lambda p: p.protocol.value.upper()),
    ("SOURCE", lambda p: p.source.value.capitalize()),
    ("PROJECT", lambda p: p.docker_compose_project or "-"),
    ("SERVICE", lambda p: p.service_name or "-"),
    ("OWNER", lambda p: p.container_name or p.process_name or "-"),
    ("MAPPING", lambda p: f"{p.host_port}->{p.container_port}" if p.container_port is not None else "-"),
    ("ADDRESS", lambda p: p.bind_address),
]

# The `reservations` table.
_RESERVATIONS_COLUMNS = [
    ("PORT", lambda e: str(e.port)),
    ("PROTOCOL", lambda e: e.protocol.value.upper()),
    ("PROJECT", lambda e: e.reservation.project if e.reservation else "-"),
    ("SERVICE", lambda e: (e.reservation.service if e.reservation and e.reservation.service else "-")),
    ("PURPOSE", lambda e: (e.reservation.purpose if e.reservation and e.reservation.purpose else "-")),
    ("STATUS", lambda e: e.state.value),
]


def _render_table(rows: list, columns, empty_message: str = "No ports discovered.") -> str:
    if not rows:
        return empty_message

    table_rows = [[getter(row) for _, getter in columns] for row in rows]
    headers = [name for name, _ in columns]
    widths = [
        max(_MIN_COLUMN_WIDTH, len(headers[i]), *(len(r[i]) for r in table_rows)) + _COLUMN_GAP
        for i in range(len(columns))
    ]

    lines = ["".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip()]
    for row in table_rows:
        lines.append("".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def _render_json(items) -> str:
    return json.dumps([item.to_dict() for item in items], indent=2)


def _apply_filters(ports: List[DiscoveredPort], args: argparse.Namespace) -> List[DiscoveredPort]:
    result = ports

    port_filter: Optional[int] = getattr(args, "port", None)
    if port_filter is not None:
        result = [p for p in result if (p.host_port if p.host_port is not None else p.port) == port_filter]

    source_filter: Optional[str] = getattr(args, "source", None)
    if source_filter:
        wanted = source_filter.strip().lower()
        result = [p for p in result if p.source.value.lower() == wanted]

    project_filter: Optional[str] = getattr(args, "project", None)
    if project_filter:
        wanted = project_filter.strip().lower()
        result = [p for p in result if p.project_name and wanted in p.project_name.lower()]

    purpose_filter: Optional[str] = getattr(args, "purpose", None)
    if purpose_filter:
        wanted = purpose_filter.strip().lower()
        result = [
            p
            for p in result
            if (p.purpose and wanted in p.purpose.lower())
            or (p.category and wanted in p.category.lower())
        ]

    return result


def _cmd_scan(args: argparse.Namespace) -> int:
    ports = _apply_filters(discover_all_ports(), args)

    if args.json:
        print(_render_json(ports))
    else:
        print(f"Operating system: {pf.detect_os().value}  Host: {pf.get_hostname()}")
        print(_render_table(ports, _SCAN_COLUMNS))
        print(f"\n{len(ports)} port(s) discovered.")

    return 0


def _cmd_docker(args: argparse.Namespace) -> int:
    ports = discover_docker_view()

    if args.json:
        print(_render_json(ports))
    else:
        print(f"Operating system: {pf.detect_os().value}  Host: {pf.get_hostname()}")
        print(_render_table(ports, _DOCKER_COLUMNS))
        print(f"\n{len(ports)} Docker-published port(s) discovered.")

    return 0


def _render_inspect_record(port: DiscoveredPort) -> str:
    lines = [
        f"Port: {port.host_port if port.host_port is not None else port.port}/{port.protocol.value}",
        f"Host binding: {port.bind_address}",
        f"Status: {port.state.value}",
        "",
        "Ownership",
        f"  Project: {port.project_name or '-'}",
        f"  Service: {port.service_name or '-'}",
        f"  Purpose: {port.purpose or '-'}",
        f"  Category: {port.category or '-'}",
    ]

    if port.source == Source.DOCKER or port.container_id:
        lines.append("")
        lines.append("Docker")
        lines.append(f"  Container: {port.container_name or '-'}")
        lines.append(f"  Image: {port.container_image or '-'}")
        lines.append(f"  Host port: {port.host_port if port.host_port is not None else '-'}")
        lines.append(
            f"  Container port: {port.container_port if port.container_port is not None else '-'}"
        )
        if port.docker_compose_project:
            lines.append(f"  Compose project: {port.docker_compose_project}")

    if port.pid or port.process_name:
        lines.append("")
        lines.append("Native")
        lines.append(f"  Process: {port.process_name or '-'}")
        lines.append(f"  PID: {port.pid if port.pid is not None else '-'}")
        if port.process_path:
            lines.append(f"  Path: {port.process_path}")

    lines.append("")
    lines.append("Detection")
    if port.detection is not None:
        lines.append(f"  Confidence: {port.detection.confidence.value}")
        if port.detection.evidence:
            lines.append("  Evidence:")
            for item in port.detection.evidence:
                lines.append(f"    - {item}")
        else:
            lines.append("  Evidence: none")
    else:
        lines.append("  Confidence: unknown")

    return "\n".join(lines)


def _cmd_inspect(args: argparse.Namespace) -> int:
    ports = discover_all_ports()
    matches = [p for p in ports if (p.host_port if p.host_port is not None else p.port) == args.port]

    if args.json:
        print(_render_json(matches))
        return 0

    if not matches:
        print(f"No records found for port {args.port}.")
        return 0

    if len(matches) > 1:
        print(f"{len(matches)} bindings found for port {args.port}:\n")

    for index, port in enumerate(matches):
        if index > 0:
            print("\n" + "-" * 40 + "\n")
        print(_render_inspect_record(port))

    return 0


# ---------------------------------------------------------------------------
# Phase 4: check / reservations / conflicts
# ---------------------------------------------------------------------------


def _protocol_from_arg(value: str) -> Protocol:
    return Protocol.UDP if str(value).strip().lower() == "udp" else Protocol.TCP


def _load_reservations_or_error() -> "tuple[Optional[list], Optional[str]]":
    try:
        return ReservationStore(default_reservations_path()).load(), None
    except ReservationStorageError as exc:
        return None, str(exc)


def _render_evaluated_detail(evaluated: EvaluatedPort) -> List[str]:
    lines = []
    d = evaluated.discovered
    r = evaluated.reservation

    if d is not None:
        if d.project_name:
            lines.append(f"Project: {d.project_name}")
        if d.service_name:
            lines.append(f"Service: {d.service_name}")
        if d.container_name:
            lines.append(f"Container: {d.container_name}")
        if d.container_port is not None:
            lines.append(f"Docker mapping: {d.host_port} -> {d.container_port}")
        if d.process_name and not d.container_name:
            pid_part = f" (pid {d.pid})" if d.pid else ""
            lines.append(f"Process: {d.process_name}{pid_part}")
    else:
        lines.append("Listener: none")
        lines.append("Docker binding: none")

    if r is not None:
        service_part = f"/{r.service}" if r.service else ""
        lines.append(f"Reservation: {r.project}{service_part}")
        if r.purpose:
            lines.append(f"Reservation purpose: {r.purpose}")
    else:
        lines.append("Reservation: none")

    if evaluated.conflict_reason:
        lines.append(f"Conflict: {evaluated.conflict_reason}")

    return lines


def _cmd_check(args: argparse.Namespace) -> int:
    protocol = _protocol_from_arg(args.protocol)
    address = args.address or "0.0.0.0"
    host_id = pf.get_host_id()

    reservations, error = _load_reservations_or_error()
    if error is not None:
        if args.json:
            print(json.dumps({"error": error}, indent=2))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2

    discovered = discover_all_ports()
    d = find_discovered(discovered, args.port, protocol)
    r = find_reservation(reservations, host_id, args.port, protocol, bind_address=address)
    evaluated = evaluate_port(host_id, args.port, protocol, d, r)

    config = load_config()
    excluded = config.is_excluded(args.port)

    probe = probe_bind(args.port, protocol, address)

    available = evaluated.state == PortState.FREE and probe.available and not excluded

    if args.json:
        data = evaluated.to_dict()
        data["excluded"] = excluded
        data["bind_probe"] = {"available": probe.available, "reason": probe.reason}
        data["available"] = available
        print(json.dumps(data, indent=2))
    else:
        print(f"Port {args.port}/{protocol.value}\n")
        print(f"State: {evaluated.state.value}")
        for line in _render_evaluated_detail(evaluated):
            print(line)
        if excluded:
            print("Excluded: yes (configuration)")
        probe_text = "successful" if probe.available else f"failed ({probe.reason})"
        print(f"Bind probe: {probe_text}")
        print(f"\nAvailable: {'yes' if available else 'no'}")

    return 0 if available else 1


def _cmd_reservations(args: argparse.Namespace) -> int:
    host_id = pf.get_host_id()
    reservations, error = _load_reservations_or_error()
    if error is not None:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    own_reservations = [r for r in reservations if r.host_id == host_id]
    discovered = discover_all_ports()

    evaluated_rows: List[EvaluatedPort] = []
    for r in own_reservations:
        d = find_discovered(discovered, r.port, r.protocol)
        evaluated_rows.append(evaluate_port(host_id, r.port, r.protocol, d, r))
    evaluated_rows.sort(key=lambda e: (e.port, e.protocol.value))

    if args.json:
        print(_render_json(evaluated_rows))
    else:
        print(_render_table(evaluated_rows, _RESERVATIONS_COLUMNS, empty_message="No reservations."))
        print(f"\n{len(evaluated_rows)} reservation(s).")

    return 0


def _cmd_conflicts(args: argparse.Namespace) -> int:
    host_id = pf.get_host_id()
    reservations, error = _load_reservations_or_error()
    if error is not None:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    discovered = discover_all_ports()
    evaluated = evaluate_all(host_id, discovered, reservations)
    conflicts = [e for e in evaluated if e.state == PortState.CONFLICT]

    if args.json:
        print(_render_json(conflicts))
    else:
        if not conflicts:
            print("No conflicts detected.")
        for index, e in enumerate(conflicts):
            if index > 0:
                print()
            print("CONFLICT\n")
            print(f"Port: {e.port}/{e.protocol.value}")
            for line in _render_evaluated_detail(e):
                print(line)

    return 1 if conflicts else 0


# ---------------------------------------------------------------------------
# Phase 4: next / reserve / release / sync-reservations
# ---------------------------------------------------------------------------

_STEP_LABELS = {
    "discovery": "Fresh discovery",
    "reservation": "Reservation",
    "exclusion": "Exclusion",
    "bind_probe": "Bind probe",
}


def _cmd_next(args: argparse.Namespace) -> int:
    protocol = _protocol_from_arg(args.protocol)
    address = args.address or "0.0.0.0"
    config = load_config()

    reservation = None
    if args.reserve:
        if not args.project:
            message = "--reserve requires --project"
            print(json.dumps({"error": message}, indent=2)) if args.json else print(
                f"Error: {message}", file=sys.stderr
            )
            return 2
        result, reservation = recommend_and_reserve(
            args.service_type,
            args.project,
            service=args.service,
            purpose=args.purpose,
            protocol=protocol,
            address=address,
            config=config,
        )
    else:
        result = recommend_port(args.service_type, protocol=protocol, address=address, config=config)

    if result.unknown_service_type:
        message = f"Unknown service type '{args.service_type}'. Configured types: {', '.join(sorted(config.ranges))}"
        if args.json:
            print(json.dumps({"error": message, **result.to_dict()}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    if args.json:
        data = result.to_dict()
        data["reservation"] = reservation.to_dict() if reservation else None
        print(json.dumps(data, indent=2))
    else:
        if result.recommended_port is None:
            port_range = config.range_for(args.service_type)
            print(
                f"No available port found for service type '{args.service_type}' "
                f"in range {port_range.start}-{port_range.end} "
                f"(tried {result.candidates_tried} candidate(s))."
            )
        else:
            print(f"Recommended port: {result.recommended_port}\n")
            print(f"Service type: {args.service_type}")
            if args.project:
                print(f"Project: {args.project}")
            print(f"Protocol: {protocol.value}")
            print("\nValidation:")
            for step in result.steps:
                # Plain ASCII, not a Unicode check/cross mark: some Windows
                # console code pages (cp1252) can't render those and would
                # otherwise silently degrade to "?" (see cli.py's encoding
                # note in main()).
                mark = "OK  " if step.passed else "FAIL"
                label = _STEP_LABELS.get(step.name, step.name)
                print(f" [{mark}] {label}: {step.detail}")
            if reservation is not None:
                print(f"\nReservation created: {reservation.reservation_id}")

    return 0 if result.recommended_port is not None else 1


def _print_reserve_or_release_result(result, args: argparse.Namespace) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "outcome": result.outcome.value,
                    "success": result.success,
                    "message": result.message,
                    "reservation": result.reservation.to_dict() if result.reservation else None,
                },
                indent=2,
            )
        )
    else:
        print(result.message)


def _cmd_reserve(args: argparse.Namespace) -> int:
    protocol = _protocol_from_arg(args.protocol)
    result = reserve_ops_reserve(
        args.port,
        args.project,
        protocol=protocol,
        service=args.service,
        purpose=args.purpose,
        bind_address=args.address,
        notes=args.notes,
    )
    _print_reserve_or_release_result(result, args)
    return 0 if result.success else 1


def _cmd_release(args: argparse.Namespace) -> int:
    if args.id:
        result = reserve_ops_release_by_id(args.id, project=args.project)
    elif args.port is not None and args.project:
        protocol = _protocol_from_arg(args.protocol)
        result = reserve_ops_release(args.port, args.project, protocol=protocol)
    else:
        message = "release requires either --id, or a port plus --project"
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 2

    _print_reserve_or_release_result(result, args)
    return 0 if result.success else 1


def _cmd_sync_reservations(args: argparse.Namespace) -> int:
    project_config = find_project_config(args.path)
    if project_config is None:
        message = "No .portforge.json/.yml project config found in the current directory or its ancestors."
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    try:
        results = sync_project_reservations(project_config)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "outcome": r.outcome.value,
                        "success": r.success,
                        "message": r.message,
                        "reservation": r.reservation.to_dict() if r.reservation else None,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        if not results:
            print("No 'ports' entries found in project config -- nothing to sync.")
        for r in results:
            print(("OK  " if r.success else "SKIP") + " " + r.message)

    return 0 if all(r.success for r in results) else 1


# ---------------------------------------------------------------------------
# Phase 8A: agent allocation API/CLI -- provider-independent. A coding
# agent/tool asks Central "I need these ports on this host for this
# project" and gets back an atomically-reserved bundle. See
# docs/phase8a_agent_allocation.md for the full contract. Entirely
# separate from local reservation commands above (`reserve`/`release`/
# `next`) -- allocation always talks to Central, never the local
# reservation file, and is unauthenticated by design (see that doc's
# "Error contract" section).
# ---------------------------------------------------------------------------


def _resolve_central_base_url(args: argparse.Namespace) -> "tuple[Optional[str], Optional[str]]":
    """Resolves the Central base URL for allocation commands, in order:
    `--url` flag -> `PORTFORGE_CENTRAL_URL` env var (the convention already
    used by this project's own physical-validation tooling) -> the bare
    `url` field of `~/.portforge/central.json` (deliberately NOT gated on
    that file's `enabled`/`token` -- allocation needs no token, so
    `CentralConfig.is_usable()` is too strict here). Returns (url, error).
    """
    import os

    explicit = getattr(args, "url", None)
    if explicit:
        return explicit, None

    env_url = os.environ.get("PORTFORGE_CENTRAL_URL")
    if env_url:
        return env_url, None

    from .central_config import load_central_config

    config = load_central_config()
    if config.url:
        return config.url, None

    return None, (
        "No Central server URL configured. Pass --url, set PORTFORGE_CENTRAL_URL, "
        "or run `portforge central enroll --url ...` first."
    )


def _allocation_client(args: argparse.Namespace):
    from .central_client import CentralClient

    url, error = _resolve_central_base_url(args)
    if error:
        return None, error
    return CentralClient(url), None


# `_resolve_host_id` lived here through Phase 8A. Phase 8B moved it to
# `project_adapter.resolve_host_ref` (returns a NormalizedHostRef plus an
# error code, not just an id) so `project validate/plan/allocate` and this
# module's own `allocate` share exactly one host resolver -- see
# docs/phase8b_manifest_audit.md §3.


def _parse_request_flag(value: str) -> "tuple[Optional[dict], Optional[str]]":
    """Parses one `--request` flag value: `name:purpose[:protocol[:preferred_port_or_range[:range]]]`."""
    parts = value.split(":")
    if len(parts) < 2 or len(parts) > 5:
        return None, f"Invalid --request '{value}' -- expected name:purpose[:protocol[:preferred_port_or_range[:range]]]"

    name, purpose = parts[0].strip(), parts[1].strip()
    if not name or not purpose:
        return None, f"Invalid --request '{value}' -- name and purpose must not be blank"

    request: dict = {"name": name, "purpose": purpose}
    if len(parts) >= 3 and parts[2].strip():
        request["protocol"] = parts[2].strip()

    # Determine if the 4th/5th elements are preferred_port or range
    if len(parts) >= 4:
        p3 = parts[3].strip()
        if "-" in p3:
            request["requested_range"] = p3
        elif p3:
            try:
                request["preferred_port"] = int(p3)
            except ValueError:
                return None, f"Invalid --request '{value}' -- preferred_port must be an integer"
                
    if len(parts) == 5:
        p4 = parts[4].strip()
        if p4:
            if "requested_range" in request:
                return None, f"Invalid --request '{value}' -- range already specified"
            if "-" not in p4:
                return None, f"Invalid --request '{value}' -- range must contain '-'"
            request["requested_range"] = p4

    if "requested_range" in request and "preferred_port" in request:
        parts_range = request["requested_range"].split("-")
        try:
            min_port, max_port = int(parts_range[0]), int(parts_range[1])
            if not (min_port <= request["preferred_port"] <= max_port):
                return None, f"Invalid --request '{value}' -- preferred_port {request['preferred_port']} is outside requested_range {request['requested_range']}"
        except ValueError:
            pass # Pydantic will catch invalid bounds later, but CLI parser won't crash

    return request, None


def _print_allocation_error(payload, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = error.get("code", "ERROR")
        message = error.get("message", str(payload))
        print(f"Error [{code}]: {message}", file=sys.stderr)


def _env_var_name(name: str) -> str:
    """Derives a safe environment-variable name from a request's `name`
    field -- uppercased, any run of non-alphanumeric characters collapsed
    to a single underscore, never executed/evaluated (Phase 8A §36: no
    arbitrary environment-variable names, no command execution based on
    request strings).
    """
    import re

    safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return f"{safe or 'PORT'}_PORT"


def _render_allocation_env(data: dict) -> str:
    lines = [f"{_env_var_name(entry['name'])}={entry['port']}" for entry in data.get("allocations", [])]
    return "\n".join(lines)


def _render_allocation_human(data: dict, heading: str) -> None:
    print(heading)
    print(f"Allocation ID: {data['allocation_id']}")
    print(f"Project: {data['project']}   Host: {data['host']['hostname']}   Status: {data['status']}")
    validation = data.get("validation", {})
    print(
        f"Validation: host={validation.get('host_health_state')}  "
        f"snapshot_age={validation.get('snapshot_age_seconds')}s  "
        f"bind_probe={validation.get('bind_probe')}"
    )
    print()
    for entry in data.get("allocations", []):
        print(f"  {entry['name']:<20} {entry['purpose']:<12} {entry['port']}/{entry['protocol']}")
    if not data.get("allocations"):
        print("  (no active reservations)")


def _load_allocation_request(args: argparse.Namespace) -> "tuple[Optional[dict], Optional[str]]":
    """Builds the allocation request body from whichever input source was
    given: --file, --stdin, or direct --host/--project/--request flags.
    Exactly one source is expected; callers validate that in the parser
    (mutually exclusive group) except for the flags case, detected here by
    absence of --file/--stdin.
    """
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            return None, f"Could not read/parse --file '{args.file}': {exc}"
        return data, None

    if args.stdin:
        try:
            data = json.load(sys.stdin)
        except ValueError as exc:
            return None, f"Could not parse JSON from stdin: {exc}"
        return data, None

    if not args.host or not args.project or not args.request:
        return None, "Provide --file, --stdin, or all of --host/--project/--request (at least one)."

    requests = []
    for raw in args.request:
        parsed, error = _parse_request_flag(raw)
        if error:
            return None, error
        requests.append(parsed)

    data = {"project": args.project, "host": args.host, "requests": requests}
    if args.request_id:
        data["request_id"] = args.request_id
    return data, None


def _cmd_allocate(args: argparse.Namespace) -> int:
    client, error = _allocation_client(args)
    if error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error, "details": []}}, indent=2))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2

    request_data, error = _load_allocation_request(args)
    if error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error, "details": []}}, indent=2))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2

    host_field = request_data.get("host") or request_data.get("host_id")
    if not host_field:
        message = "Request is missing 'host' (hostname or UUID)."
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": message}}, indent=2)) if args.json else print(
            f"Error: {message}", file=sys.stderr
        )
        return 2

    host, error, error_code = resolve_host_ref(client, host_field)
    if error:
        if args.json:
            print(json.dumps({"error": {"code": error_code, "message": error, "details": []}}, indent=2))
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2

    result = client.create_allocation(
        project=request_data["project"],
        host_id=host.id,
        requests=request_data["requests"],
        request_id=request_data.get("request_id"),
    )

    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "ALLOCATION_UNAVAILABLE", "message": result.error or "Allocation failed.", "details": []}
        }
        _print_allocation_error(payload, args)
        return 1

    if args.json:
        print(json.dumps(result.data, indent=2))
    elif args.format == "env":
        print(_render_allocation_env(result.data))
    else:
        _render_allocation_human(result.data, "Allocation created")

    return 0


def _cmd_allocation_get(args: argparse.Namespace) -> int:
    client, error = _allocation_client(args)
    if error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error}}, indent=2)) if args.json else print(
            f"Error: {error}", file=sys.stderr
        )
        return 2

    result = client.get_allocation(args.allocation_id)
    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "ALLOCATION_NOT_FOUND", "message": result.error or "Not found.", "details": []}
        }
        _print_allocation_error(payload, args)
        return 1

    if args.json:
        print(json.dumps(result.data, indent=2))
    elif args.format == "env":
        print(_render_allocation_env(result.data))
    else:
        _render_allocation_human(result.data, "Allocation")

    return 0


def _cmd_allocation_release(args: argparse.Namespace) -> int:
    client, error = _allocation_client(args)
    if error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error}}, indent=2)) if args.json else print(
            f"Error: {error}", file=sys.stderr
        )
        return 2

    result = client.release_allocation(args.allocation_id)
    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "ALLOCATION_NOT_FOUND", "message": result.error or "Not found.", "details": []}
        }
        
        # Idempotency: if already released or not found on delete, consider it released.
        if payload.get("error", {}).get("code") == "ALLOCATION_NOT_FOUND":
            if args.json:
                print(json.dumps({"status": "released", "idempotent_replay": True}, indent=2))
            else:
                print("Allocation already released (or not found).")
            return 0

        _print_allocation_error(payload, args)
        return 1

    if args.json:
        print(json.dumps(result.data, indent=2))
    else:
        _render_allocation_human(result.data, "Allocation released")

    return 0

def _cmd_allocation_verify(args: argparse.Namespace) -> int:
    client, error = _allocation_client(args)
    if error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error}}, indent=2)) if args.json else print(
            f"Error: {error}", file=sys.stderr
        )
        return 2

    # First get the allocation to see if it's local or remote
    result = client.get_allocation(args.allocation_id)
    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "ALLOCATION_NOT_FOUND", "message": result.error or "Not found.", "details": []}
        }
        _print_allocation_error(payload, args)
        return 1
        
    allocation = result.data
    
    # Check if local
    is_local = False
    if allocation.get("host", {}).get("id") == str(pf.get_host_id()):
        is_local = True

    if is_local:
        # Local verify logic
        from .bindprobe import probe_bind
        entries = allocation.get("allocations", [])
        for entry in entries:
            evidence = probe_bind(entry["port"], entry["protocol"], entry.get("bind_address") or "0.0.0.0")
            entry["bind_probe"] = "verified_free" if evidence.available else "verified_occupied"
            entry["verification_source"] = "local"
    else:
        # Remote verify
        result = client.verify_allocation(args.allocation_id)
        if not result.success:
            payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
                "error": {"code": "ALLOCATION_VERIFY_FAILED", "message": result.error or "Verify failed.", "details": []}
            }
            _print_allocation_error(payload, args)
            return 1
        allocation = result.data

    if args.json:
        print(json.dumps(allocation, indent=2))
    elif args.format == "env":
        print(_render_allocation_env(allocation))
    else:
        _render_allocation_human(allocation, "Allocation verified")
        
    return 0
    

def _print_central_result_error(result, args: argparse.Namespace) -> None:
    if isinstance(result.data, dict):
        payload = result.data
    else:
        payload = {"error": {"code": "CENTRAL_UNAVAILABLE", "message": result.error or "Central request failed."}}
    _print_allocation_error(payload, args)


def _cmd_deployment_plan(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .deployment.central_plan import build_deployment_plan_request, resolve_deployment_project

    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return 2

    if not args.environment:
        _print_targets_error(
            TargetsError("TARGET_REQUIRED", "--environment is required for deployment planning."),
            args,
        )
        return 2

    project_root = Path(args.project_root).resolve() if args.project_root else Path.cwd().resolve()
    url, _ = _resolve_central_base_url(args)
    try:
        target_payload = plan_for_target(
            project_root=project_root,
            environment=args.environment,
            target=getattr(args, "target", None),
            target_host_id=getattr(args, "target_host_id", None),
            central_url=url,
        )
        project = resolve_deployment_project(project_root)
    except TargetsError as exc:
        _print_targets_error(exc, args)
        return 2

    model = discover_workspace(project_root, central_url=url, include_local_runtime=False)
    plan_body = build_deployment_plan_request(
        target_payload,
        project=project,
        workspace_fingerprint=model.workspace_fingerprint,
    )
    central_result = client.plan_deployment(plan_body)
    if not central_result.success:
        _print_central_result_error(central_result, args)
        return 1

    output = {
        "target_plan": target_payload,
        "deployment_plan": central_result.data,
        "project": project,
    }
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        host = target_payload.get("host") or {}
        print(
            f"Deployment plan for {project} ({args.environment}/{target_payload.get('target')} "
            f"on {host.get('hostname') or host.get('id')})\n"
        )
        print(f"  Central plan_hash: {central_result.data.get('plan_hash')}")
        print(f"  Target plan_id: {target_payload.get('plan_id')}")
        for row in target_payload.get("services") or []:
            print(
                f"  {row.get('service'):<20} internal={row.get('internal_port')} "
                f"host={row.get('host_port')} reason={row.get('reason_code')}"
            )
    return 0


def _cmd_deployment_apply(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .deployment.central_plan import (
        ingress_json_from_target_bindings,
        ports_json_from_target_services,
        resolve_deployment_project,
    )

    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return 2

    if not args.environment:
        _print_targets_error(
            TargetsError("TARGET_REQUIRED", "--environment is required for deployment apply."),
            args,
        )
        return 2

    project_root = Path(args.project_root).resolve() if args.project_root else Path.cwd().resolve()
    manifest_path = discover_manifest_path(str(project_root), walk_up=False)
    if manifest_path is None:
        message = "portforge.yml is required to resolve deployment target."
        if args.json:
            print(json.dumps({"error": {"code": "MANIFEST_INVALID", "message": message, "details": []}}, indent=2))
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 2

    try:
        manifest = load_and_validate_manifest(manifest_path)
        target_ref = resolve_target(
            manifest,
            args.environment,
            target=getattr(args, "target", None),
            target_host_id=getattr(args, "target_host_id", None),
        )
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return 2
    except TargetsError as exc:
        _print_targets_error(exc, args)
        return 2

    url, _ = _resolve_central_base_url(args)
    try:
        target_payload = plan_for_target(
            project_root=project_root,
            environment=args.environment,
            target=target_ref.alias,
            target_host_id=target_ref.host_id,
            central_url=url,
        )
        project = resolve_deployment_project(project_root)
    except TargetsError as exc:
        _print_targets_error(exc, args)
        return 2

    model = discover_workspace(project_root, central_url=url, include_local_runtime=False)
    namespaced_request_id = namespace_request_id(args.environment, target_ref.alias, args.request_id)
    body: dict = {
        "host_id": target_ref.host_id,
        "project": project,
        "environment": args.environment,
        "target_alias": target_ref.alias,
        "request_id": namespaced_request_id,
        "plan_hash": args.plan_hash,
        "package_uri": args.package_uri,
        "package_sha256": args.package_sha256,
        "package_manifest_sha256": args.package_manifest_sha256,
        "workspace_fingerprint": model.workspace_fingerprint,
    }
    ports_json = ports_json_from_target_services(target_payload.get("services") or [])
    ingress_json = ingress_json_from_target_bindings(target_payload.get("ingress") or [])
    if ports_json is not None:
        body["ports_json"] = ports_json
    if ingress_json is not None:
        body["ingress_json"] = ingress_json

    result = client.create_deployment(body)
    if not result.success:
        _print_central_result_error(result, args)
        return 1

    payload = result.data
    payload["namespaced_request_id"] = namespaced_request_id
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Deployment created: {payload.get('id')} state={payload.get('state')}")
        print(f"  request_id: {namespaced_request_id}")
    return 0


def _cmd_deployment_status(args: argparse.Namespace) -> int:
    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return 2

    result = client.get_deployment(args.id)
    if not result.success:
        _print_central_result_error(result, args)
        return 1

    if args.json:
        print(json.dumps(result.data, indent=2))
    else:
        data = result.data
        print(f"Deployment {data.get('id')}: state={data.get('state')} project={data.get('project')}")
        print(f"  environment: {data.get('environment')} request_id: {data.get('request_id')}")
    return 0


def _cmd_deployment_rollback(args: argparse.Namespace) -> int:
    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return 2

    result = client.rollback_deployment(args.id, args.request_id)
    if not result.success:
        _print_central_result_error(result, args)
        return 1

    if args.json:
        print(json.dumps(result.data, indent=2))
    else:
        data = result.data
        print(f"Rollback deployment created: {data.get('id')} state={data.get('state')}")
    return 0


def _cmd_allocation_list(args: argparse.Namespace) -> int:
    client, error = _allocation_client(args)
    if error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error}}, indent=2)) if args.json else print(
            f"Error: {error}", file=sys.stderr
        )
        return 2

    result = client.list_allocations()
    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "LIST_FAILED", "message": result.error or "Failed.", "details": []}
        }
        _print_allocation_error(payload, args)
        return 1

    allocations = result.data.get("items", []) if isinstance(result.data, dict) and "items" in result.data else result.data
    
    if args.json:
        print(json.dumps(allocations, indent=2))
    else:
        for alloc in allocations:
            print(f"ID: {alloc['allocation_id']} | Project: {alloc['project']} | Host: {alloc['host']['hostname']} | Status: {alloc['status']}")
            for entry in alloc.get("allocations", []):
                print(f"  {entry['name']:<20} {entry['purpose']:<12} {entry['port']}/{entry['protocol']} {entry.get('bind_probe', '')}")
            print()
            
    return 0


# ---------------------------------------------------------------------------
# Phase 8B: project manifest commands (`project validate/plan/allocate`) --
# provider-neutral, see docs/phase8b_project_manifest.md. All three share
# the same manifest-load + host-resolve prelude
# (`_load_manifest_and_host`); `allocate` is the only one that mutates
# anything, and it does so purely by calling the existing, unmodified
# `client.create_allocation()` -- no allocation logic is duplicated here.
# ---------------------------------------------------------------------------


def _print_manifest_error(error: "ManifestError", args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps({"error": {"code": error.code, "message": error.message, "details": error.details}}, indent=2))
    else:
        print(f"Error [{error.code}]: {error.message}", file=sys.stderr)


def _project_manifest_path(args: argparse.Namespace):
    from pathlib import Path

    return Path(args.manifest) if getattr(args, "manifest", None) else None


def _load_manifest_and_host(args: argparse.Namespace):
    """Shared prelude for validate/plan/allocate: resolves Central's URL,
    loads+validates the manifest, and resolves its `target.host`. Returns
    `(client, manifest, host, error_result)` -- on failure, the first three
    are None and `error_result` is `(exit_code, already_printed=True)`
    (the error is printed here, at the one place all three commands share,
    so each command's own body only has to check for None and return).
    """
    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return None, None, None, 2

    try:
        manifest = load_and_validate_manifest(_project_manifest_path(args))
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return None, None, None, 2

    host, host_error, host_code = resolve_host_ref(client, manifest.host)
    if host_error:
        if args.json:
            print(json.dumps({"error": {"code": host_code, "message": host_error, "details": []}}, indent=2))
        else:
            print(f"Error: {host_error}", file=sys.stderr)
        return None, None, None, 2

    return client, manifest, host, None


def _cmd_project_validate(args: argparse.Namespace) -> int:
    # Validation is local by default. Supplying --url preserves the older
    # host-resolution behavior for callers that explicitly requested it.
    try:
        manifest = load_and_validate_manifest(_project_manifest_path(args))
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return 2
    host_payload = manifest.host
    if getattr(args, "url", None):
        client, url_error = _allocation_client(args)
        if url_error:
            _print_manifest_error(ManifestError("CENTRAL_UNAVAILABLE", url_error), args)
            return 2
        host, host_error, host_code = resolve_host_ref(client, manifest.host or "")
        if host_error:
            payload={"error":{"code":host_code,"message":host_error,"details":[]}}
            print(json.dumps(payload, indent=2)) if args.json else print(f"Error: {host_error}", file=sys.stderr)
            return 2
        host_payload={"id":host.id,"hostname":host.hostname}
    if args.json:
        payload = {"valid": True, "version": manifest.version, "project": manifest.project, "host": host_payload, "requests": len(manifest.requests)}
        if not getattr(args, "url", None):
            payload.update({"manifest": str((_project_manifest_path(args) or discover_manifest_path(walk_up=True)).resolve()), "services": [item.name for item in manifest.requests]})
        print(json.dumps(payload, indent=2))
    else:
        print(f"Manifest is valid (version {manifest.version}).")
        print(f"Project: {manifest.project}")
        print(f"Host: {host_payload or 'unassigned'}")
        print(f"Requests: {len(manifest.requests)}")
        for item in manifest.requests:
            preferred = f"  preferred={item.preferred_port}" if item.preferred_port else ""
            requested = f"  range={item.requested_range}" if item.requested_range else ""
            print(f"  {item.name:<20} {item.purpose:<12} {item.protocol}{preferred}{requested}")
    return 0


def _config_targets_from_manifest(manifest) -> list:
    targets: list = []
    if manifest.config is None:
        return targets
    for m in manifest.config.dotenv:
        targets.append({"file": m.file, "type": "dotenv", "mappings": sorted(m.values.items())})
    for m in manifest.config.compose:
        services = {}
        for svc_name, entries in m.services.items():
            services[svc_name] = [
                {"allocation": e.allocation, "container": e.container, "protocol": e.protocol} for e in entries
            ]
        targets.append({"file": m.file, "type": "compose", "services": services})
    for m in manifest.config.kubernetes:
        targets.append(
            {
                "file": m.file,
                "type": "kubernetes",
                "host_ports": [
                    {
                        "kind": hp.kind,
                        "name": hp.name,
                        "namespace": hp.namespace,
                        "container": hp.container,
                        "container_port": hp.container_port,
                        "allocation": hp.allocation,
                    }
                    for hp in m.host_ports
                ],
                "node_ports": [
                    {
                        "name": np.name,
                        "namespace": np.namespace,
                        "service_port": np.service_port,
                        "allocation": np.allocation,
                    }
                    for np in m.node_ports
                ],
            }
        )
    return targets


def _cmd_project_status(args: argparse.Namespace) -> int:
    try:
        path = _project_manifest_path(args) or discover_manifest_path(walk_up=True)
        manifest = load_and_validate_manifest(path)
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return 2
    rows = []
    for item in manifest.requests:
        rows.append({"service": item.name, "purpose": item.purpose, "protocol": item.protocol, "preferred_port": item.preferred_port, "requested_range": item.requested_range, "allocation_status": "UNALLOCATED"})
    central_available = False
    central_error = None
    allocations = []
    client, url_error = _allocation_client(args)
    if not url_error:
        result = client.list_allocations()
        central_available = result.success
        central_error = result.error if not result.success else None
        if result.success:
            allocations = result.data.get("items", []) if isinstance(result.data, dict) else (result.data or [])
    else:
        central_error = url_error
    for allocation in allocations:
        if allocation.get("project") != manifest.project:
            continue
        for entry in allocation.get("allocations", []):
            match = next((row for row in rows if row["service"] == entry.get("name")), None)
            if match is not None:
                match.update({"allocation_id": allocation.get("allocation_id"), "host": allocation.get("host"), "allocated_port": entry.get("port"), "allocation_status": allocation.get("status"), "verification": entry.get("bind_probe", allocation.get("validation", {}).get("bind_probe"))})
    payload={"project": manifest.project, "manifest_path": str(path.resolve()) if path else None, "manifest_valid": True, "central_available": central_available, "central_error": central_error, "services": rows}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Project: {manifest.project}")
        print(f"Manifest: {payload['manifest_path']}")
        print(f"Central: {'AVAILABLE' if central_available else 'UNAVAILABLE'}")
        for row in rows:
            print(f"  {row['service']}: {row['allocation_status']} protocol={row['protocol']} port={row.get('allocated_port', '-')}")
    return 0


def _cmd_project_inspect(args: argparse.Namespace) -> int:
    try:
        path = _project_manifest_path(args) or discover_manifest_path(walk_up=True)
        manifest = load_and_validate_manifest(path)
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return 2

    rows = []
    for item in manifest.requests:
        rows.append(
            {
                "service": item.name,
                "purpose": item.purpose,
                "protocol": item.protocol,
                "preferred_port": item.preferred_port,
                "requested_range": item.requested_range,
                "allocation_status": "UNALLOCATED",
            }
        )

    central_available = False
    central_error = None
    allocations = []
    client, url_error = _allocation_client(args)
    if not url_error:
        result = client.list_allocations()
        central_available = result.success
        central_error = result.error if not result.success else None
        if result.success:
            allocations = result.data.get("items", []) if isinstance(result.data, dict) else (result.data or [])
    else:
        central_error = url_error

    for allocation in allocations:
        if allocation.get("project") != manifest.project:
            continue
        for entry in allocation.get("allocations", []):
            match = next((row for row in rows if row["service"] == entry.get("name")), None)
            if match is not None:
                match.update(
                    {
                        "allocation_id": allocation.get("allocation_id"),
                        "host": allocation.get("host"),
                        "allocated_port": entry.get("port"),
                        "allocation_status": allocation.get("status"),
                    }
                )

    config_targets = _config_targets_from_manifest(manifest)
    payload = {
        "project": manifest.project,
        "manifest_path": str(path.resolve()) if path else None,
        "manifest_valid": True,
        "host": manifest.host,
        "central_available": central_available,
        "central_error": central_error,
        "services": rows,
        "config_targets": config_targets,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Project: {manifest.project}")
        print(f"Manifest: {payload['manifest_path']}")
        print(f"Host: {manifest.host or 'unassigned'}")
        print(f"Central: {'AVAILABLE' if central_available else 'UNAVAILABLE'}")
        print("Services:")
        for row in rows:
            print(
                f"  {row['service']}: {row['allocation_status']} "
                f"protocol={row['protocol']} port={row.get('allocated_port', '-')}"
            )
        if config_targets:
            print("Config targets:")
            for target in config_targets:
                print(f"  {target['type']}: {target['file']}")
        else:
            print("Config targets: (none declared)")
    return 0


def _uses_target_planning(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "environment", None)
        or getattr(args, "target", None)
        or getattr(args, "target_host_id", None)
    )


def _print_targets_error(error: TargetsError, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps({"error": {"code": error.code, "message": error.message, "details": error.details}}, indent=2))
    else:
        print(f"Error [{error.code}]: {error.message}", file=sys.stderr)


def _target_project_root(args: argparse.Namespace):
    from pathlib import Path

    manifest_path = _project_manifest_path(args) or discover_manifest_path(walk_up=True)
    if getattr(args, "project_root", None):
        return Path(args.project_root).resolve(), manifest_path
    if manifest_path is not None:
        return manifest_path.parent.resolve(), manifest_path
    return Path.cwd().resolve(), manifest_path


def _cmd_project_provision(args: argparse.Namespace) -> int:
    if _uses_target_planning(args):
        return _cmd_project_provision_target(args)

    client, manifest, host, error_code = _load_manifest_and_host(args)
    if error_code is not None:
        return error_code

    project_root = _workflow_project_root(args)

    if args.dry_run:
        result = prepare_workflow(client, manifest, host, project_root)
        result["dry_run"] = True
        result["committed"] = False
        print(json.dumps(result, indent=2))
        return 0 if result["ready"] else 1

    try:
        result = apply_workflow(client, manifest, host, project_root, args.request_id)
    except WorkflowError as exc:
        _print_workflow_error(exc, args)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def _cmd_project_provision_target(args: argparse.Namespace) -> int:
    from .mcp.plans import verify_plan_fresh

    client, url_error = _allocation_client(args)
    if url_error:
        if args.json:
            print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": url_error, "details": []}}, indent=2))
        else:
            print(f"Error: {url_error}", file=sys.stderr)
        return 2

    project_root, manifest_path = _target_project_root(args)
    try:
        manifest = load_and_validate_manifest(manifest_path)
    except ManifestError as exc:
        _print_manifest_error(exc, args)
        return 2

    try:
        target_ref = resolve_target(
            manifest,
            args.environment,
            target=getattr(args, "target", None),
            target_host_id=getattr(args, "target_host_id", None),
        )
    except TargetsError as exc:
        _print_targets_error(exc, args)
        return 2

    host, host_error, host_code = resolve_host_ref(client, target_ref.host_id)
    if host_error:
        if args.json:
            print(json.dumps({"error": {"code": host_code, "message": host_error, "details": []}}, indent=2))
        else:
            print(f"Error: {host_error}", file=sys.stderr)
        return 2

    if not args.environment:
        _print_targets_error(
            TargetsError("TARGET_REQUIRED", "--environment is required for target-aware provisioning."),
            args,
        )
        return 2

    namespaced_request_id = namespace_request_id(args.environment, target_ref.alias, args.request_id)

    if getattr(args, "plan_id", None):
        try:
            verify_plan_fresh(
                project_root,
                args.plan_id,
                manifest,
                environment=args.environment,
                host_id=target_ref.host_id,
            )
        except Exception as exc:
            from .mcp.errors import map_exception

            mapped = map_exception(exc)
            if args.json:
                print(
                    json.dumps(
                        {"error": {"code": mapped.code, "message": mapped.message, "details": mapped.details}},
                        indent=2,
                    )
                )
            else:
                print(f"Error [{mapped.code}]: {mapped.message}", file=sys.stderr)
            return 2

    url, _ = _resolve_central_base_url(args)
    if args.dry_run:
        try:
            result = plan_for_target(
                project_root=project_root,
                environment=args.environment,
                target=getattr(args, "target", None),
                target_host_id=getattr(args, "target_host_id", None),
                central_url=url,
                logical_request_id=args.request_id,
                include_local_runtime=getattr(args, "include_local_runtime", False),
            )
        except TargetsError as exc:
            _print_targets_error(exc, args)
            return 2
        result["dry_run"] = True
        result["request_id"] = namespaced_request_id
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Dry-run provision for '{manifest.project}' on {host.hostname} "
                f"({args.environment}/{target_ref.alias})\n"
            )
            print(f"  Namespaced request_id: {namespaced_request_id}")
            for row in result.get("services") or []:
                print(
                    f"  {row.get('service'):<20} internal={row.get('internal_port')} "
                    f"host={row.get('host_port')} reason={row.get('reason_code')}"
                )
        return 0

    try:
        result = apply_workflow(client, manifest, host, project_root, namespaced_request_id)
    except WorkflowError as exc:
        _print_workflow_error(exc, args)
        return 1

    result["environment"] = args.environment
    result["target"] = target_ref.alias
    result["namespaced_request_id"] = namespaced_request_id
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0


def _cmd_project_discover(args: argparse.Namespace) -> int:
    from pathlib import Path

    root = Path(args.project_root) if args.project_root else Path(args.path or ".").resolve()
    url, _error = _resolve_central_base_url(args)
    try:
        model = discover_workspace(root, central_url=url, include_local_runtime=not args.no_local_runtime)
    except FileNotFoundError as exc:
        if args.json:
            print(json.dumps({"error": {"code": "PROJECT_ROOT_NOT_FOUND", "message": str(exc), "details": []}}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2

    payload = workspace_to_json(model)
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Workspace discovery for {root}\n")
    print(f"  Services: {len(model.services)}")
    print(f"  Conflicts: {len(model.conflicts)}")
    print(f"  Files parsed: {model.files_parsed}/{model.files_considered}")
    print(f"  Fingerprint: {model.workspace_fingerprint}")
    if model.central_available:
        print("  Central: available")
    elif url:
        print(f"  Central: unavailable ({model.central_error or 'unknown'})")
    for service in model.services:
        host_ports = [
            req.port
            for req in service.port_requirements
            if req.role == "host" and req.port is not None
        ]
        if host_ports:
            print(f"  - {service.name}: host ports {sorted(set(host_ports))}")
    for conflict in model.conflicts:
        print(f"  ! [{conflict.kind}] {conflict.message}")
    return 0


def _cmd_project_plan(args: argparse.Namespace) -> int:
    from pathlib import Path

    if _uses_target_planning(args):
        project_root, _manifest_path = _target_project_root(args)
        url, _error = _resolve_central_base_url(args)
        if not args.environment:
            _print_targets_error(
                TargetsError("TARGET_REQUIRED", "--environment is required for target-aware planning."),
                args,
            )
            return 2
        try:
            payload = plan_for_target(
                project_root=project_root,
                environment=args.environment,
                target=getattr(args, "target", None),
                target_host_id=getattr(args, "target_host_id", None),
                central_url=url,
                include_local_runtime=getattr(args, "include_local_runtime", False),
            )
        except TargetsError as exc:
            _print_targets_error(exc, args)
            return 2
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            host = payload.get("host") or {}
            print(
                f"Target plan for {project_root} "
                f"({payload.get('environment')}/{payload.get('target')} on {host.get('hostname') or host.get('id')})\n"
            )
            for row in payload.get("services") or []:
                print(
                    f"  {row.get('service'):<20} internal={row.get('internal_port')} "
                    f"host={row.get('host_port')} reason={row.get('reason_code')}"
                )
            for binding in payload.get("ingress") or []:
                print(
                    f"  ingress {binding.get('name')}: {binding.get('scheme')}://{binding.get('hostname')}:"
                    f"{binding.get('public_port')} -> {binding.get('service')} [{binding.get('status')}]"
                )
            print(f"\n  plan_id: {payload.get('plan_id')}")
        return 0

    use_workspace = getattr(args, "workspace", False)
    manifest_path = _project_manifest_path(args) or discover_manifest_path(walk_up=True)
    if use_workspace or manifest_path is None:
        root = Path(args.project_root) if getattr(args, "project_root", None) else Path.cwd().resolve()
        if manifest_path is not None and getattr(args, "project_root", None) is None:
            root = manifest_path.parent.resolve()
        url, _error = _resolve_central_base_url(args)
        model = discover_workspace(root, central_url=url, include_local_runtime=True)
        client = None
        host = None
        if url:
            client, url_error = _allocation_client(args)
            if client is not None and model.existing_manifest:
                host, host_error, _code = resolve_host_ref(client, model.existing_manifest.get("host") or "")
                if host_error:
                    host = None
        plan = plan_workspace(model, client=client, host=host)
        payload = {
            "schema_version": 1,
            "committed": False,
            "mode": "workspace",
            "project_root": str(root),
            "discovery": workspace_to_json(model),
            "plan": plan,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Workspace plan for {root} (advisory only -- nothing reserved)\n")
            for row in plan.get("services") or []:
                proposed = row.get("proposed_port")
                current = row.get("current_port")
                reason = (row.get("reason") or {}).get("code")
                print(
                    f"  {row.get('service'):<20} current={current} proposed={proposed} reason={reason}"
                )
        return 0

    client, manifest, host, error_code = _load_manifest_and_host(args)
    if error_code is not None:
        return error_code

    requests_out = build_candidate_preview(client, host, manifest.requests)

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "committed": False,
                    "project": manifest.project,
                    "host": {"id": host.id, "hostname": host.hostname},
                    "requests": requests_out,
                },
                indent=2,
            )
        )
    else:
        print(f"Plan for '{manifest.project}' on {host.hostname} (advisory only -- nothing reserved)\n")
        for r in requests_out:
            preferred = f"  preferred={r['preferred_port']}" if r["preferred_port"] else ""
            candidate = r["candidate_port"] if r["candidate_port"] is not None else "none available"
            print(f"  {r['name']:<20} {r['purpose']:<12} {r['protocol']}{preferred}  candidate={candidate}")
        print(
            "\nNote: preferred ports are shown as given but not verified here; a candidate port "
            "already free right now may not still be free by the time you run 'project allocate'."
        )

    return 0


def _cmd_project_allocate(args: argparse.Namespace) -> int:
    client, manifest, host, error_code = _load_manifest_and_host(args)
    if error_code is not None:
        return error_code

    normalized = build_normalized_request(manifest, host)
    # CLI --request-id wins over a manifest-declared request_id, consistent
    # with this project's existing "CLI arguments > project config"
    # precedence (see config.py's module docstring and
    # docs/phase8b_manifest_audit.md §6).
    request_id = args.request_id or manifest.request_id
    body = to_allocation_body(normalized, request_id)

    result = client.create_allocation(
        project=body["project"], host_id=body["host_id"], requests=body["requests"], request_id=body["request_id"]
    )

    if not result.success:
        payload = result.data if isinstance(result.data, dict) and "error" in result.data else {
            "error": {"code": "ALLOCATION_UNAVAILABLE", "message": result.error or "Allocation failed.", "details": []}
        }
        _print_allocation_error(payload, args)
        return 1

    data = result.data
    ports = {entry["name"]: entry["port"] for entry in data.get("allocations", [])}

    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "committed": True,
                    "allocation_id": data["allocation_id"],
                    "project": data["project"],
                    "host": data["host"],
                    "ports": ports,
                    "allocations": data["allocations"],
                },
                indent=2,
            )
        )
    elif args.format == "env":
        print(_render_allocation_env(data))
    else:
        _render_allocation_human(data, f"Allocation created for '{manifest.project}'")

    return 0


def _cmd_project_init(args):
    try:
        from pathlib import Path
        project = args.project or Path.cwd().name or "project"
        host = args.host or None
        ports = args.port or []
        if args.stdout:
            text = render_manifest(project, host, ports)
            print(json.dumps({"created": False, "manifest": text}, indent=2) if args.json else text, end=None if args.json else "")
            return 0
        path = create_manifest(project, host, ports, Path(args.output) if args.output else None)
    except ManifestError as exc:
        _print_manifest_error(exc, args); return 2
    print(json.dumps({"created": True, "path": str(path.resolve())}, indent=2) if args.json else f"Created {path}"); return 0


def _print_workflow_error(error, args):
    payload={"error":{"code":error.code,"message":error.message,"details":error.details,"recovery":error.recovery}}
    print(json.dumps(payload,indent=2)) if args.json else print(f"Error [{error.code}]: {error.message}",file=sys.stderr)


def _workflow_project_root(args):
    from pathlib import Path
    from .manifest import discover_manifest_path
    if getattr(args,"project_root",None): return Path(args.project_root)
    path=_project_manifest_path(args) or discover_manifest_path()
    return path.parent if path else Path.cwd()


def _cmd_agent_contract(args):
    print(json.dumps(build_contract(), indent=2))
    return 0


def _cmd_capabilities(args):
    return _cmd_agent_contract(args)


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    from .mcp.server import run_stdio_server

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    run_stdio_server(central_url=args.url, log_level=args.log_level)
    return 0


def _cmd_workflow_prepare(args):
    client,manifest,host,error=_load_manifest_and_host(args)
    if error is not None: return error
    result=prepare_workflow(client,manifest,host,_workflow_project_root(args)); print(json.dumps(result,indent=2)); return 0 if result["ready"] else 1


def _cmd_workflow_apply(args):
    client,manifest,host,error=_load_manifest_and_host(args)
    if error is not None: return error
    try: result=apply_workflow(client,manifest,host,_workflow_project_root(args),args.request_id)
    except WorkflowError as exc: _print_workflow_error(exc,args); return 1
    print(json.dumps(result,indent=2)); return 0


def _cmd_workflow_status(args):
    try: result=get_workflow_status(_workflow_project_root(args),args.request_id)
    except WorkflowError as exc: _print_workflow_error(exc,args); return 1
    print(json.dumps(result,indent=2)); return 0


# ---------------------------------------------------------------------------
# Phase 8C: safe config plan/apply/status/rollback -- maps a committed
# Phase 8A allocation into explicitly-declared project config files
# (dotenv, Compose). `project allocate` above NEVER touches these; only
# these `config` commands ever write to a project's real files, and only
# `config apply` does so (`plan`/`status` are read-only, `rollback`
# restores exact original bytes). See docs/phase8c_safe_config.md.
# ---------------------------------------------------------------------------


def _print_config_error(error: "_config_manager.ConfigError", args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps({"error": {"code": error.code, "message": error.message, "details": error.details}}, indent=2))
    else:
        print(f"Error [{error.code}]: {error.message}", file=sys.stderr)


def _resolve_project_root(args: argparse.Namespace, manifest_path=None):
    from pathlib import Path

    if getattr(args, "project_root", None):
        return Path(args.project_root)
    if manifest_path is not None:
        return manifest_path.parent
    return Path.cwd()


def _load_manifest_host_and_allocation(args: argparse.Namespace):
    """Shared prelude for `config plan`/`config apply`: manifest + host
    (via `_load_manifest_and_host`), then fetches and ownership-verifies
    the `--allocation` id against Central. Returns
    `(client, manifest, host, allocation_data, project_root, exit_code)`
    -- on failure the first four are None and the error has already been
    printed, matching `_load_manifest_and_host`'s own convention.
    """
    client, manifest, host, error_code = _load_manifest_and_host(args)
    if error_code is not None:
        return None, None, None, None, None, error_code

    resolved_manifest_path = _project_manifest_path(args)
    if resolved_manifest_path is None:
        from .manifest import discover_manifest_path

        resolved_manifest_path = discover_manifest_path()
    project_root = _resolve_project_root(args, resolved_manifest_path)

    result = client.get_allocation(args.allocation)
    if not result.success:
        error = _config_manager.ConfigError(
            "CONFIG_ALLOCATION_NOT_FOUND", f"Allocation '{args.allocation}' could not be fetched from Central: "
            f"{result.error or 'not found'}", status_code=404
        )
        _print_config_error(error, args)
        return None, None, None, None, None, 2

    allocation_data = result.data
    try:
        _config_manager.verify_allocation_ownership(allocation_data, manifest, host.id)
    except _config_manager.ConfigError as exc:
        _print_config_error(exc, args)
        return None, None, None, None, None, 1

    return client, manifest, host, allocation_data, project_root, None


def _cmd_config_plan(args: argparse.Namespace) -> int:
    client, manifest, host, allocation_data, project_root, error_code = _load_manifest_host_and_allocation(args)
    if error_code is not None:
        return error_code

    try:
        plan = _config_manager.build_plan(manifest, allocation_data, project_root)
        _config_manager.persist_plan(plan, project_root)
    except (_config_manager.ConfigError, _ConfigPathError) as exc:
        if isinstance(exc, _ConfigPathError):
            exc = _config_manager.ConfigError("CONFIG_PATH_OUTSIDE_PROJECT", str(exc), status_code=400)
        _print_config_error(exc, args)
        return 1

    payload = _config_manager.plan_to_json(plan)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Config plan for allocation {plan.allocation_id} (mutation {plan.mutation_id})\n")
        for f in plan.files:
            print(f"  {f.relative_path} [{f.kind}] -- {f.action}")
            for c in f.changes:
                if f.kind == "dotenv":
                    print(f"      {c['key']}: {c['before']!r} -> {c['after']!r} ({c['action']})")
                elif f.kind == "compose":
                    print(
                        f"      {c['service']} {c['container_port']}/{c['protocol']}: "
                        f"{c['before']!r} -> {c['after']!r} ({c['action']})"
                    )
                else:  # kubernetes
                    target = f"{c['kind']} {c['name']}" + (f" container {c['container']}" if c["container"] else "")
                    print(f"      {target} {c['field']} (match {c['match_port']}): {c['before']!r} -> {c['after']!r} ({c['action']})")
        print(f"\nNo files were changed. Run 'config apply' to write these changes (mutation_id={plan.mutation_id}).")

    return 0


def _cmd_config_apply(args: argparse.Namespace) -> int:
    client, manifest, host, allocation_data, project_root, error_code = _load_manifest_host_and_allocation(args)
    if error_code is not None:
        return error_code

    mutation_id = _config_manager.find_latest_planned_mutation(project_root, args.allocation)
    if mutation_id is None:
        error = _config_manager.ConfigError(
            "CONFIG_MUTATION_NOT_FOUND",
            f"No pending config plan found for allocation '{args.allocation}' in {project_root}. "
            "Run 'config plan' first.",
            status_code=404,
        )
        _print_config_error(error, args)
        return 1

    try:
        record = _config_manager.apply_mutation(project_root, mutation_id)
    except _config_manager.ConfigError as exc:
        _print_config_error(exc, args)
        return 1

    if args.json:
        print(json.dumps({"committed": True, **record}, indent=2))
    else:
        print(f"Applied mutation {record['mutation_id']} (status={record['status']})")
        for f in record["files"]:
            print(f"  {f['relative_path']} [{f['kind']}] -- {f['action']}")

    return 0


def _cmd_config_status(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args)
    try:
        status = _config_manager.get_status(project_root, args.mutation_id)
    except _config_manager.ConfigError as exc:
        _print_config_error(exc, args)
        return 1

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"Mutation {status['mutation_id']}: {status['status']}")
        print(f"Allocation: {status['allocation_id']}   Project: {status['project']}")
        print(f"Created: {status['created_at']}   Applied: {status['applied_at']}   Rolled back: {status['rolled_back_at']}")
        for f in status["files"]:
            print(f"  {f['file']} [{f['type']}] -- {f['action']}")

    return 0


def _cmd_config_rollback(args: argparse.Namespace) -> int:
    project_root = _resolve_project_root(args)
    try:
        record = _config_manager.rollback_mutation(project_root, args.mutation_id)
    except _config_manager.ConfigError as exc:
        _print_config_error(exc, args)
        return 1

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(f"Rolled back mutation {record['mutation_id']} (status={record['status']})")
        for f in record["files"]:
            print(f"  {f['relative_path']} [{f['kind']}] restored")

    return 0


# ---------------------------------------------------------------------------
# v1.1-A: `portforge doctor` -- read-only diagnostic aggregator. See
# doctor.py's own module docstring and docs/v1.1/doctor-design.md. Never
# mutates anything; a missing/unreachable Central is SKIP, not a hard
# argument error -- Central sync has always been optional (see
# `_resolve_central_base_url`'s own identical `os` import + resolution
# order, reused here for the exact same URL-resolution behavior every
# other Central-aware command already has).
# ---------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .central_client import CentralClient

    url, _url_error = _resolve_central_base_url(args)
    client = CentralClient(url) if url else None

    report = _doctor.run_doctor(client=client)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print("PortForge Doctor\n")
        label_width = max(len(c.id) for c in report.checks) + 2
        for c in report.checks:
            marker = {"ok": "PASS", "warn": "WARN", "error": "FAIL", "skip": "SKIP"}[c.status]
            print(f"  {c.id.ljust(label_width)} {marker:<5} {c.message}")
        print(f"\nOverall: {report.overall}")

    # 0 = ok/degraded (no blocking diagnostic failure); 1 = overall "error"
    # (at least one check is a hard failure); 2 is reserved for a usage
    # error, which argparse itself would already have raised before this
    # function ever runs.
    return 1 if report.overall == "error" else 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 5: optional central server sync -- entirely separate from every
# command above, which never import or touch this section at all. See
# central_sync.py's module docstring for why that separation is deliberate.
# ---------------------------------------------------------------------------


def _cmd_central_enroll(args: argparse.Namespace) -> int:
    from .central_sync import enroll as central_enroll

    result = central_enroll(None, args.url, args.enrollment_token)
    if args.json:
        print(json.dumps({"success": result.success, "error": result.error, "data": result.data}, indent=2))
    else:
        if result.success:
            print(f"Enrolled successfully. Central sync is now enabled (url={args.url}).")
        else:
            print(f"Enrollment failed: {result.error}", file=sys.stderr)
    return 0 if result.success else 1


def _cmd_central_generate_token(args: argparse.Namespace) -> int:
    """v1.1-E: docs/security.md has always claimed this command exists;
    it never did -- only an unexposed backend endpoint
    (`POST /agent/enrollment-tokens`, admin-only) did. This is a thin
    wrapper around that EXISTING capability, not a new auth mechanism: it
    still requires the real PORTFORGE_ADMIN_BOOTSTRAP_TOKEN and never logs
    it. The env var is preferred over --admin-token to avoid the secret
    landing in shell history.
    """
    import os

    from .central_client import CentralClient

    admin_token = args.admin_token or os.environ.get("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN")
    if not admin_token:
        message = "No admin bootstrap token provided. Pass --admin-token or set PORTFORGE_ADMIN_BOOTSTRAP_TOKEN."
        if args.json:
            print(json.dumps({"success": False, "error": message}))
        else:
            print(message, file=sys.stderr)
        return 1

    client = CentralClient(args.url, token=admin_token)
    result = client.generate_enrollment_token(label=args.label, ttl_hours=args.ttl_hours)

    if args.json:
        print(json.dumps({"success": result.success, "error": result.error, "data": result.data}, indent=2))
    else:
        if result.success:
            data = result.data or {}
            print("Enrollment token generated -- shown once, not retrievable again. Save it now:")
            print(f"  {data.get('enrollment_token')}")
            if data.get("expires_at"):
                print(f"Expires: {data['expires_at']}")
            print("Use it with: portforge agent enroll --server <url> --token <this token>")
        else:
            print(f"Failed to generate enrollment token: {result.error}", file=sys.stderr)
    return 0 if result.success else 1


def _cmd_central_status(args: argparse.Namespace) -> int:
    from .central_config import load_central_config
    from .central_sync import check_status

    config = load_central_config()
    if not config.enabled:
        message = "Central sync is not configured (disabled)."
        print(json.dumps({"enabled": False}, indent=2)) if args.json else print(message)
        return 0

    result = check_status(config)
    if args.json:
        print(json.dumps({"enabled": True, "url": config.url, "reachable": result.success}, indent=2))
    else:
        print(f"Central sync: enabled  URL: {config.url}")
        print(f"Reachable: {'yes' if result.success else 'no'}")
        if not result.success:
            print(f"  ({result.error})")
    return 0 if result.success else 1


def _cmd_central_sync(args: argparse.Namespace) -> int:
    from .central_config import load_central_config
    from .central_sync import sync_now

    config = load_central_config()
    outcome = sync_now(config)

    if args.json:
        print(
            json.dumps(
                {
                    "success": outcome.success,
                    "error": outcome.error,
                    "health": outcome.health.success if outcome.health else None,
                    "observations_result": outcome.observations.data if outcome.observations else None,
                },
                indent=2,
            )
        )
    else:
        if outcome.error:
            print(f"Sync failed: {outcome.error}", file=sys.stderr)
        else:
            print("Heartbeat: ok")
            obs = outcome.observations.data if outcome.observations else None
            if obs:
                print(
                    f"Observations submitted: {obs.get('observations_processed', 0)} "
                    f"(appeared={obs.get('appeared', 0)}, changed={obs.get('changed', 0)}, "
                    f"disappeared={obs.get('disappeared', 0)})"
                )
            reservations = outcome.reservations or []
            print(f"Reservations synced: {sum(1 for r in reservations if r.success)}/{len(reservations)}")

    return 0 if outcome.success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portforge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="Discover ports currently in use on this host (native + Docker)"
    )
    scan_parser.add_argument("--json", action="store_true", help="Output results as JSON")
    scan_parser.add_argument("--port", type=int, default=None, help="Filter to an exact host port")
    scan_parser.add_argument(
        "--project", type=str, default=None, help="Filter by project name (case-insensitive substring)"
    )
    scan_parser.add_argument(
        "--source", type=str, default=None, help="Filter by source: process, docker, system (case-insensitive)"
    )
    scan_parser.add_argument(
        "--purpose",
        type=str,
        default=None,
        help="Filter by purpose or category (case-insensitive substring)",
    )
    scan_parser.set_defaults(func=_cmd_scan)

    docker_parser = subparsers.add_parser(
        "docker", help="Discover ports published to the host by running Docker containers"
    )
    docker_parser.add_argument("--json", action="store_true", help="Output results as JSON")
    docker_parser.set_defaults(func=_cmd_docker)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Show full discovery + detection detail for a specific host port"
    )
    inspect_parser.add_argument("port", type=int, help="Host port to inspect")
    inspect_parser.add_argument("--json", action="store_true", help="Output results as JSON")
    inspect_parser.set_defaults(func=_cmd_inspect)

    check_parser = subparsers.add_parser(
        "check", help="Evaluate one port's availability: discovery + reservation + real bind probe"
    )
    check_parser.add_argument("port", type=int)
    check_parser.add_argument("--protocol", type=str, default="tcp", choices=["tcp", "udp"])
    check_parser.add_argument("--address", type=str, default="0.0.0.0")
    check_parser.add_argument("--json", action="store_true")
    check_parser.set_defaults(func=_cmd_check)

    next_parser = subparsers.add_parser(
        "next", help="Recommend the next available port for a service type"
    )
    next_parser.add_argument("service_type", type=str, help="e.g. frontend, api, postgres, mysql, redis, generic")
    next_parser.add_argument("--project", type=str, default=None)
    next_parser.add_argument("--service", type=str, default=None)
    next_parser.add_argument("--purpose", type=str, default=None)
    next_parser.add_argument("--protocol", type=str, default="tcp", choices=["tcp", "udp"])
    next_parser.add_argument("--address", type=str, default="0.0.0.0")
    next_parser.add_argument(
        "--reserve", action="store_true", help="Atomically reserve the recommended port (requires --project)"
    )
    next_parser.add_argument("--json", action="store_true")
    next_parser.set_defaults(func=_cmd_next)

    reserve_parser = subparsers.add_parser("reserve", help="Reserve a port for a project")
    reserve_parser.add_argument("port", type=int)
    reserve_parser.add_argument("--project", type=str, required=True)
    reserve_parser.add_argument("--service", type=str, default=None)
    reserve_parser.add_argument("--purpose", type=str, default=None)
    reserve_parser.add_argument("--protocol", type=str, default="tcp", choices=["tcp", "udp"])
    reserve_parser.add_argument("--address", type=str, default=None)
    reserve_parser.add_argument("--notes", type=str, default=None)
    reserve_parser.add_argument("--json", action="store_true")
    reserve_parser.set_defaults(func=_cmd_reserve)

    release_parser = subparsers.add_parser("release", help="Release a reservation")
    release_parser.add_argument("port", type=int, nargs="?", default=None)
    release_parser.add_argument("--project", type=str, default=None)
    release_parser.add_argument("--protocol", type=str, default="tcp", choices=["tcp", "udp"])
    release_parser.add_argument("--id", type=str, default=None, help="Release by reservation_id instead of port")
    release_parser.add_argument("--json", action="store_true")
    release_parser.set_defaults(func=_cmd_release)

    reservations_parser = subparsers.add_parser("reservations", help="List this host's reservations")
    reservations_parser.add_argument("--json", action="store_true")
    reservations_parser.set_defaults(func=_cmd_reservations)

    conflicts_parser = subparsers.add_parser(
        "conflicts", help="List ports where a reservation and an active listener disagree on ownership"
    )
    conflicts_parser.add_argument("--json", action="store_true")
    conflicts_parser.set_defaults(func=_cmd_conflicts)

    sync_parser = subparsers.add_parser(
        "sync-reservations",
        help="Create/refresh local reservations from the current project's .portforge.yml 'ports' list",
    )
    sync_parser.add_argument("--path", type=str, default=None, help="Directory to search from (default: cwd)")
    sync_parser.add_argument("--json", action="store_true")
    sync_parser.set_defaults(func=_cmd_sync_reservations)

    central_parser = subparsers.add_parser(
        "central", help="Optional central server sync (never required for local operation)"
    )
    central_subparsers = central_parser.add_subparsers(dest="central_command", required=True)

    central_enroll_parser = central_subparsers.add_parser("enroll", help="Enroll this host with a central server")
    central_enroll_parser.add_argument("--url", type=str, required=True, help="Central server base URL")
    central_enroll_parser.add_argument("--enrollment-token", type=str, required=True)
    central_enroll_parser.add_argument("--json", action="store_true")
    central_enroll_parser.set_defaults(func=_cmd_central_enroll)

    central_generate_token_parser = central_subparsers.add_parser(
        "generate-token", help="Mint a new host enrollment token (admin-only -- requires the admin bootstrap token)"
    )
    central_generate_token_parser.add_argument("--url", type=str, required=True, help="Central server base URL")
    central_generate_token_parser.add_argument(
        "--admin-token",
        type=str,
        default=None,
        help="Admin bootstrap token; prefer the PORTFORGE_ADMIN_BOOTSTRAP_TOKEN env var to avoid shell history exposure",
    )
    central_generate_token_parser.add_argument("--label", type=str, default=None)
    central_generate_token_parser.add_argument("--ttl-hours", type=int, default=24)
    central_generate_token_parser.add_argument("--json", action="store_true")
    central_generate_token_parser.set_defaults(func=_cmd_central_generate_token)

    central_status_parser = central_subparsers.add_parser("status", help="Show central sync configuration/connectivity")
    central_status_parser.add_argument("--json", action="store_true")
    central_status_parser.set_defaults(func=_cmd_central_status)

    central_sync_parser = central_subparsers.add_parser(
        "sync", help="Push a fresh discovery snapshot and local reservations to the central server"
    )
    central_sync_parser.add_argument("--json", action="store_true")
    central_sync_parser.set_defaults(func=_cmd_central_sync)

    # --- Phase 8A: agent allocation ----------------------------------------
    allocate_parser = subparsers.add_parser(
        "allocate", help="Atomically request one or more ports from Central for a project on a host"
    )
    allocate_parser.add_argument("--host", type=str, default=None, help="Target hostname or UUID")
    allocate_parser.add_argument("--project", type=str, default=None)
    allocate_parser.add_argument(
        "--request",
        action="append",
        default=None,
        metavar="NAME:PURPOSE[:PROTOCOL[:PREFERRED_PORT_OR_RANGE[:RANGE]]]",
        help="Repeatable. e.g. --request frontend:frontend:tcp or api:api:tcp:8000-8999",
    )
    allocate_parser.add_argument("--request-id", type=str, default=None, help="Idempotency key")
    allocate_parser.add_argument("--file", type=str, default=None, help="Read the full request from a JSON file")
    allocate_parser.add_argument("--stdin", action="store_true", help="Read the full request as JSON from stdin")
    allocate_parser.add_argument("--url", type=str, default=None, help="Central server base URL")
    allocate_parser.add_argument("--json", action="store_true")
    allocate_parser.add_argument("--format", type=str, default="text", choices=["text", "env"])
    allocate_parser.set_defaults(func=_cmd_allocate)

    allocation_parser = subparsers.add_parser("allocation", help="Inspect or release an existing allocation")
    allocation_subparsers = allocation_parser.add_subparsers(dest="allocation_command", required=True)

    allocation_get_parser = allocation_subparsers.add_parser("get", help="Show an allocation's current state")
    allocation_get_parser.add_argument("allocation_id", type=str)
    allocation_get_parser.add_argument("--url", type=str, default=None)
    allocation_get_parser.add_argument("--json", action="store_true")
    allocation_get_parser.add_argument("--format", type=str, default="text", choices=["text", "env"])
    allocation_get_parser.set_defaults(func=_cmd_allocation_get)
    
    allocation_status_parser = allocation_subparsers.add_parser("status", help="Show an allocation's current state (alias for get)")
    allocation_status_parser.add_argument("allocation_id", type=str)
    allocation_status_parser.add_argument("--url", type=str, default=None)
    allocation_status_parser.add_argument("--json", action="store_true")
    allocation_status_parser.add_argument("--format", type=str, default="text", choices=["text", "env"])
    allocation_status_parser.set_defaults(func=_cmd_allocation_get)
    
    allocation_verify_parser = allocation_subparsers.add_parser("verify", help="Verify an allocation's ports")
    allocation_verify_parser.add_argument("allocation_id", type=str)
    allocation_verify_parser.add_argument("--url", type=str, default=None)
    allocation_verify_parser.add_argument("--json", action="store_true")
    allocation_verify_parser.add_argument("--format", type=str, default="text", choices=["text", "env"])
    allocation_verify_parser.set_defaults(func=_cmd_allocation_verify)

    allocation_list_parser = allocation_subparsers.add_parser("list", help="List allocations")
    allocation_list_parser.add_argument("--url", type=str, default=None)
    allocation_list_parser.add_argument("--json", action="store_true")
    allocation_list_parser.set_defaults(func=_cmd_allocation_list)

    allocation_release_parser = allocation_subparsers.add_parser(
        "release", help="Release all still-active reservations belonging to an allocation"
    )
    allocation_release_parser.add_argument("allocation_id", type=str)
    allocation_release_parser.add_argument("--url", type=str, default=None)
    allocation_release_parser.add_argument("--json", action="store_true")
    allocation_release_parser.set_defaults(func=_cmd_allocation_release)

    deployment_parser = subparsers.add_parser(
        "deployment", help="Plan, apply, inspect, or rollback Central deployments (Phase 18)"
    )
    deployment_subparsers = deployment_parser.add_subparsers(dest="deployment_command", required=True)

    deployment_plan_parser = deployment_subparsers.add_parser(
        "plan", help="Build a target plan and Central deployment plan (read-only)"
    )
    deployment_plan_parser.add_argument("--environment", type=str, required=True)
    deployment_plan_parser.add_argument("--target", type=str, default=None)
    deployment_plan_parser.add_argument("--target-host-id", type=str, default=None)
    deployment_plan_parser.add_argument("--project-root", type=str, default=None)
    deployment_plan_parser.add_argument("--url", type=str, default=None)
    deployment_plan_parser.add_argument("--json", action="store_true")
    deployment_plan_parser.set_defaults(func=_cmd_deployment_plan)

    deployment_apply_parser = deployment_subparsers.add_parser(
        "apply", help="Create an APPROVED deployment on Central"
    )
    deployment_apply_parser.add_argument("--request-id", type=str, required=True)
    deployment_apply_parser.add_argument("--environment", type=str, required=True)
    deployment_apply_parser.add_argument("--target", type=str, default=None)
    deployment_apply_parser.add_argument("--target-host-id", type=str, default=None)
    deployment_apply_parser.add_argument("--project-root", type=str, default=None)
    deployment_apply_parser.add_argument("--package-uri", type=str, required=True)
    deployment_apply_parser.add_argument("--package-sha256", type=str, required=True)
    deployment_apply_parser.add_argument("--package-manifest-sha256", type=str, required=True)
    deployment_apply_parser.add_argument("--plan-hash", type=str, required=True)
    deployment_apply_parser.add_argument("--url", type=str, default=None)
    deployment_apply_parser.add_argument("--json", action="store_true")
    deployment_apply_parser.set_defaults(func=_cmd_deployment_apply)

    deployment_status_parser = deployment_subparsers.add_parser("status", help="Get deployment state from Central")
    deployment_status_parser.add_argument("id", type=str)
    deployment_status_parser.add_argument("--url", type=str, default=None)
    deployment_status_parser.add_argument("--json", action="store_true")
    deployment_status_parser.set_defaults(func=_cmd_deployment_status)

    deployment_rollback_parser = deployment_subparsers.add_parser(
        "rollback", help="Request rollback of a deployment to the previous known-good revision"
    )
    deployment_rollback_parser.add_argument("id", type=str)
    deployment_rollback_parser.add_argument("--request-id", type=str, required=True)
    deployment_rollback_parser.add_argument("--url", type=str, default=None)
    deployment_rollback_parser.add_argument("--json", action="store_true")
    deployment_rollback_parser.set_defaults(func=_cmd_deployment_rollback)

    # --- Phase 8B: project manifest ----------------------------------------
    project_parser = subparsers.add_parser(
        "project", help="Validate/plan/allocate ports for a project using a portforge.yml manifest"
    )
    project_subparsers = project_parser.add_subparsers(dest="project_command", required=True)

    def _add_manifest_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "manifest",
            type=str,
            nargs="?",
            default=None,
            help="Path to a portforge.yml manifest (default: discover portforge.yml/.yaml in the current directory)",
        )
        p.add_argument("--url", type=str, default=None, help="Central server base URL")
        p.add_argument("--json", action="store_true")

    project_validate_parser = project_subparsers.add_parser(
        "validate", help="Validate a project manifest and resolve its host (no allocation, no reservation)"
    )
    _add_manifest_arg(project_validate_parser)
    project_validate_parser.set_defaults(func=_cmd_project_validate)

    project_plan_parser = project_subparsers.add_parser(
        "plan", help="Show advisory candidate ports for a manifest (non-mutating)"
    )
    _add_manifest_arg(project_plan_parser)
    project_plan_parser.add_argument(
        "--workspace",
        action="store_true",
        help="Discover the workspace statically and build a coordinated port plan",
    )
    project_plan_parser.add_argument("--project-root", type=str, default=None)
    project_plan_parser.add_argument("--environment", type=str, default=None, help="Named environment (development, production, ...)")
    project_plan_parser.add_argument("--target", type=str, default=None, help="Deployment target alias within the environment")
    project_plan_parser.add_argument("--target-host-id", type=str, default=None, help="Deployment target host UUID (overrides alias resolution)")
    project_plan_parser.add_argument(
        "--include-local-runtime",
        action="store_true",
        help="Include local runtime port conflicts when planning (default: off for remote targets)",
    )
    project_plan_parser.set_defaults(func=_cmd_project_plan)

    project_discover_parser = project_subparsers.add_parser(
        "discover", help="Statically discover services, ports, and conflicts in a workspace"
    )
    project_discover_parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project root directory (default: current directory)",
    )
    project_discover_parser.add_argument("--project-root", type=str, default=None)
    project_discover_parser.add_argument("--url", type=str, default=None, help="Central server base URL")
    project_discover_parser.add_argument("--json", action="store_true")
    project_discover_parser.add_argument(
        "--no-local-runtime",
        action="store_true",
        help="Skip local port/reservation enrichment",
    )
    project_discover_parser.set_defaults(func=_cmd_project_discover)

    project_allocate_parser = project_subparsers.add_parser(
        "allocate", help="Atomically allocate every port in a manifest (calls Phase 8A's allocation API)"
    )
    _add_manifest_arg(project_allocate_parser)
    project_allocate_parser.add_argument(
        "--request-id", type=str, default=None, help="Idempotency key (overrides the manifest's own request_id, if any)"
    )
    project_allocate_parser.add_argument("--format", type=str, default="text", choices=["text", "env"])
    project_allocate_parser.set_defaults(func=_cmd_project_allocate)
    project_status_parser = project_subparsers.add_parser("status", help="Show manifest intent and existing allocations without mutating state")
    _add_manifest_arg(project_status_parser)
    project_status_parser.set_defaults(func=_cmd_project_status)
    project_inspect_parser = project_subparsers.add_parser(
        "inspect", help="Read-only summary of manifest services, allocations, and config targets"
    )
    _add_manifest_arg(project_inspect_parser)
    project_inspect_parser.set_defaults(func=_cmd_project_inspect)
    project_provision_parser = project_subparsers.add_parser(
        "provision", help="Dry-run (prepare) or apply full workflow (allocate + optional config)"
    )
    _add_manifest_arg(project_provision_parser)
    project_provision_parser.add_argument("--request-id", type=str, required=True)
    project_provision_parser.add_argument("--dry-run", action="store_true")
    project_provision_parser.add_argument("--project-root", type=str, default=None)
    project_provision_parser.add_argument("--environment", type=str, default=None)
    project_provision_parser.add_argument("--target", type=str, default=None)
    project_provision_parser.add_argument("--target-host-id", type=str, default=None)
    project_provision_parser.add_argument("--plan-id", type=str, default=None)
    project_provision_parser.add_argument("--include-local-runtime", action="store_true")
    project_provision_parser.set_defaults(func=_cmd_project_provision)
    project_init_parser = project_subparsers.add_parser("init", help="Create a conservative starter manifest without overwriting")
    project_init_parser.add_argument("--project", required=False); project_init_parser.add_argument("--host", required=False)
    project_init_parser.add_argument("--port", action="append", required=False, metavar="NAME:PURPOSE[:PROTOCOL]")
    project_init_parser.add_argument("--output"); project_init_parser.add_argument("--stdout", action="store_true"); project_init_parser.add_argument("--json", action="store_true")
    project_init_parser.set_defaults(func=_cmd_project_init)

    # --- Phase 8C: safe config plan/apply/status/rollback ------------------
    config_parser = subparsers.add_parser(
        "config", help="Map a committed allocation into declared project config files (dotenv, Compose)"
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    def _add_config_plan_apply_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "manifest",
            type=str,
            nargs="?",
            default=None,
            help="Path to a portforge.yml manifest (default: discover in the current directory)",
        )
        p.add_argument("--allocation", type=str, required=True, help="Allocation id this config maps to")
        p.add_argument(
            "--project-root",
            type=str,
            default=None,
            help="Directory config file paths are resolved against (default: the manifest's own directory)",
        )
        p.add_argument("--url", type=str, default=None, help="Central server base URL")
        p.add_argument("--json", action="store_true")

    config_plan_parser = config_subparsers.add_parser(
        "plan", help="Show exact proposed config file changes for a committed allocation (non-mutating)"
    )
    _add_config_plan_apply_args(config_plan_parser)
    config_plan_parser.set_defaults(func=_cmd_config_plan)

    config_apply_parser = config_subparsers.add_parser(
        "apply", help="Apply the most recently planned config mutation for this manifest + allocation"
    )
    _add_config_plan_apply_args(config_apply_parser)
    config_apply_parser.set_defaults(func=_cmd_config_apply)

    config_status_parser = config_subparsers.add_parser("status", help="Show a config mutation's current status")
    config_status_parser.add_argument("mutation_id", type=str)
    config_status_parser.add_argument("--project-root", type=str, default=None)
    config_status_parser.add_argument("--json", action="store_true")
    config_status_parser.set_defaults(func=_cmd_config_status)

    config_rollback_parser = config_subparsers.add_parser(
        "rollback", help="Restore the exact original bytes of every file a mutation changed"
    )
    config_rollback_parser.add_argument("mutation_id", type=str)
    config_rollback_parser.add_argument("--project-root", type=str, default=None)
    config_rollback_parser.add_argument("--json", action="store_true")
    config_rollback_parser.set_defaults(func=_cmd_config_rollback)
    contract_parser = subparsers.add_parser("agent-contract", help="Print the versioned machine contract (capabilities, workflow)")
    contract_parser.add_argument("--json", action="store_true")
    contract_parser.set_defaults(func=_cmd_agent_contract)
    capabilities_parser = subparsers.add_parser("capabilities", help="Alias for agent-contract")
    capabilities_parser.add_argument("--json", action="store_true")
    capabilities_parser.set_defaults(func=_cmd_capabilities)
    workflow_parser=subparsers.add_parser("workflow"); workflow_subparsers=workflow_parser.add_subparsers(dest="workflow_command",required=True)
    workflow_prepare=workflow_subparsers.add_parser("prepare"); _add_manifest_arg(workflow_prepare); workflow_prepare.add_argument("--project-root"); workflow_prepare.set_defaults(func=_cmd_workflow_prepare)
    workflow_apply=workflow_subparsers.add_parser("apply"); _add_manifest_arg(workflow_apply); workflow_apply.add_argument("--request-id",required=True); workflow_apply.add_argument("--project-root"); workflow_apply.set_defaults(func=_cmd_workflow_apply)
    workflow_status=workflow_subparsers.add_parser("status"); workflow_status.add_argument("--request-id",required=True); workflow_status.add_argument("--project-root"); workflow_status.add_argument("--json",action="store_true"); workflow_status.set_defaults(func=_cmd_workflow_status)

    # --- v1.1-A: read-only diagnostic aggregator ---------------------------
    doctor_parser = subparsers.add_parser(
        "doctor", help="Read-only diagnostic check of CLI/Central/agent/manifest state -- never mutates anything"
    )
    doctor_parser.add_argument("--url", type=str, default=None, help="Central server base URL")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(func=_cmd_doctor)

    mcp_parser = subparsers.add_parser("mcp", help="Model Context Protocol (MCP) server over stdio")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_serve = mcp_sub.add_parser("serve", help="Start PortForge MCP server over stdio")
    mcp_serve.add_argument("--url", type=str, default=None, help="Default Central server base URL for MCP tools")
    mcp_serve.add_argument("--log-level", default="WARNING", help="Logging level (stderr only)")
    mcp_serve.set_defaults(func=_cmd_mcp_serve)

    from .cli_agent import add_agent_subparsers
    add_agent_subparsers(subparsers)

    return parser


def main(argv: List[str] | None = None) -> int:
    # Some process names contain characters outside the console's active
    # code page (e.g. Windows cp1252). Replace rather than crash the scan.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    # One-time (idempotent) migration of legacy Phase 4 hostname-based
    # reservation host_ids to the Phase 5 persistent UUID identity. Runs
    # outside any lock this command itself will acquire, and never blocks
    # normal CLI usage if it fails -- see reservations/migration.py.
    try:
        migrate_if_needed(pf.get_host_id())
    except Exception:
        logging.getLogger("portforge_agent.cli").warning("Reservation migration check failed", exc_info=True)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
