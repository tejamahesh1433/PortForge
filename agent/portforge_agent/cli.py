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

    central_status_parser = central_subparsers.add_parser("status", help="Show central sync configuration/connectivity")
    central_status_parser.add_argument("--json", action="store_true")
    central_status_parser.set_defaults(func=_cmd_central_status)

    central_sync_parser = central_subparsers.add_parser(
        "sync", help="Push a fresh discovery snapshot and local reservations to the central server"
    )
    central_sync_parser.add_argument("--json", action="store_true")
    central_sync_parser.set_defaults(func=_cmd_central_sync)

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
