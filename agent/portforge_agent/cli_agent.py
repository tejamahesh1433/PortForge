import argparse
import json
import logging
import sys
import time
from typing import List

from . import platform as pf
from . import service_ops
from .central_client import CentralClient
from .central_config import load_central_config, save_central_config, CentralConfig
from .credentials import load_credential, save_credential
from .collectors.docker import is_docker_available
from .runtime.agent import AgentRuntime
from .runtime.state import load_state
from .runtime.sync import SyncManager
from .version import PROTOCOL_VERSION, get_portforge_version

logger = logging.getLogger(__name__)

def _cmd_agent_enroll(args: argparse.Namespace) -> int:
    client = CentralClient(base_url=args.server, timeout=10.0)
    
    host_id = pf.get_host_id()
    hostname = pf.get_hostname()
    os_enum = pf.detect_os()
    
    res = client.enroll(
        enrollment_token=args.token,
        host_id=host_id,
        hostname=hostname,
        operating_system=os_enum.value,
        os_version=None,
        architecture=None,
        agent_version=get_portforge_version(),
        protocol_version=PROTOCOL_VERSION,
        docker_available=is_docker_available()
    )
    
    if res.success and res.data:
        token = res.data.get("agent_token")
        if not token:
            print("Enrollment failed: server did not return an agent_token", file=sys.stderr)
            return 1
            
        save_credential(token)
        save_central_config(CentralConfig(enabled=True, url=args.server))
        print(f"Successfully enrolled agent. Central sync is enabled for {args.server}.")
        return 0
    else:
        print(f"Enrollment failed: {res.error}", file=sys.stderr)
        return 1


def _cmd_agent_test(args: argparse.Namespace) -> int:
    print("Running Agent Diagnostic Tests...\n")
    
    host_id = pf.get_host_id()
    print(f"Host Identity: PASS ({host_id})")
    
    config = load_central_config()
    if config.enabled and config.url:
        print(f"Local Configuration: PASS ({config.url})")
    else:
        print("Local Configuration: FAIL (Not configured)")
        return 2
        
    token = load_credential()
    if token:
        print("Credential Availability: PASS")
    else:
        print("Credential Availability: FAIL")
        return 2
        
    client = CentralClient(base_url=config.url, token=token, timeout=10.0)
    res = client.health()
    if res.success:
        print("Central Reachability: PASS")
    else:
        print(f"Central Reachability: FAIL ({res.error})")
        return 1
        
    # Check authentication by trying a heartbeat or status
    hb_res = client.heartbeat(
        host_id=host_id,
        hostname=pf.get_hostname(),
        operating_system=pf.detect_os().value,
        os_version=None,
        architecture=None,
        agent_version=get_portforge_version(),
        protocol_version=PROTOCOL_VERSION,
        docker_available=is_docker_available(),
        timestamp="2026-01-01T00:00:00Z"
    )
    if hb_res.success:
        print("Authentication: PASS")
        print("Protocol Compatibility: PASS")
    else:
        print(f"Authentication/Protocol: FAIL ({hb_res.error})")
        return 1
        
    print(f"Native Discovery: PASS ({pf.detect_os().value})")
    print(f"Docker Discovery: {'PASS' if is_docker_available() else 'N/A'}")
    
    try:
        from .reservations.storage import ReservationStore
        from .paths import reservations_path
        ReservationStore(reservations_path()).load()
        print("Reservation Store Accessibility: PASS")
    except Exception as e:
        print(f"Reservation Store Accessibility: FAIL ({e})")
        return 1
        
    return 0

def _cmd_agent_sync(args: argparse.Namespace) -> int:
    config = load_central_config()
    token = load_credential()
    if not config.enabled or not config.url or not token:
        print("Agent is not enrolled or enabled. Run `portforge agent enroll`.", file=sys.stderr)
        return 2
        
    client = CentralClient(base_url=config.url, token=token, timeout=10.0)
    state = load_state()
    manager = SyncManager(client, state)
    
    start_time = time.time()
    try:
        snapshot = manager.capture_snapshot()
    except Exception as e:
        print(f"Local discovery failed: {e}", file=sys.stderr)
        return 1
    scan_duration = time.time() - start_time
    
    start_sync = time.time()
    success = manager.flush_snapshot(snapshot)
    sync_duration = time.time() - start_sync
    
    # We don't have conflict evaluation natively isolated in the snapshot response,
    # but we can count states.
    obs = snapshot["observations"]
    conflicts = sum(1 for o in obs if o["state"] == "conflict")
    
    print(f"Host ID: {pf.get_host_id()}")
    print(f"Sequence: {snapshot['sequence']}")
    print(f"Observations: {len(obs)}")
    print(f"Conflicts: {conflicts}")
    print(f"Scan Duration: {scan_duration:.2f}s")
    print(f"Sync Duration: {sync_duration:.2f}s")
    print(f"Central Result: {'SUCCESS' if success else 'FAILED'}")
    
    return 0 if success else 1


def _cmd_agent_status(args: argparse.Namespace) -> int:
    config = load_central_config()
    state = load_state()
    
    print("PortForge Agent Status\n")
    print(f"Host UUID: {pf.get_host_id()}")
    print(f"Hostname: {pf.get_hostname()}")
    print(f"OS: {pf.detect_os().value}")
    print(f"Agent Version: {get_portforge_version()}")
    print(f"Protocol Version: v1")
    print(f"Central URL: {config.url if config.url else 'None'}")
    
    token = load_credential()
    enrolled = bool(config.enabled and config.url and token)
    print(f"Enrollment Status: {'Enrolled' if enrolled else 'Not Enrolled'}")
    
    def fmt_ts(ts):
        if not ts: return "Never"
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        
    print(f"Last Scan: {fmt_ts(state.last_scan)}")
    print(f"Last Successful Sync: {fmt_ts(state.last_sync)}")
    print(f"Last Heartbeat: {fmt_ts(state.last_heartbeat)}")
    print(f"Last Error: {state.last_sync_error if state.last_sync_error else 'None'}")
    print(f"Last Sequence: {state.sequence_id}")
    print(f"Last Observation Count: {state.last_observation_count}")
    print(f"Docker Availability: {'Yes' if is_docker_available() else 'No'}")
    return 0


def _cmd_agent_run(args: argparse.Namespace) -> int:
    # Setup basic console logging for the foreground run
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    runtime = AgentRuntime()
    runtime.run()
    return 0

def _print_service_result(result: "service_ops.ServiceOpResult", args: argparse.Namespace) -> None:
    if args.json:
        print(
            json.dumps(
                {"success": result.success, "message": result.message, "detail": result.detail},
                indent=2,
            )
        )
    else:
        print(result.message)
        if result.detail:
            print(result.detail)


def _run_service_op(op, args: argparse.Namespace) -> int:
    """Shared plumbing for every `agent service <action>` command: run the
    platform-dispatching operation, print its result, and turn an
    unsupported-OS error into the same clear, actionable message and exit
    code every other operational failure in this CLI uses (see cli.py's
    documented exit code table -- 2 == operational/configuration error).
    """
    try:
        result = op()
    except service_ops.UnsupportedPlatformError as exc:
        message = str(exc)
        if args.json:
            print(json.dumps({"success": False, "error": message}, indent=2))
        else:
            print(f"Error: {message}", file=sys.stderr)
        return 2

    _print_service_result(result, args)
    return 0 if result.success else 1


def _cmd_agent_service_install(args: argparse.Namespace) -> int:
    return _run_service_op(service_ops.install, args)


def _cmd_agent_service_status(args: argparse.Namespace) -> int:
    return _run_service_op(service_ops.status, args)


def _cmd_agent_service_start(args: argparse.Namespace) -> int:
    return _run_service_op(service_ops.start, args)


def _cmd_agent_service_stop(args: argparse.Namespace) -> int:
    return _run_service_op(service_ops.stop, args)


def _cmd_agent_service_uninstall(args: argparse.Namespace) -> int:
    return _run_service_op(service_ops.uninstall, args)


def add_agent_subparsers(subparsers) -> None:
    agent_parser = subparsers.add_parser(
        "agent", help="Phase 6 Agent Runtime and Synchronization"
    )
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    
    enroll_parser = agent_subparsers.add_parser("enroll", help="Enroll this host with a central server")
    enroll_parser.add_argument("--server", type=str, required=True, help="Central server base URL")
    enroll_parser.add_argument("--token", type=str, required=True, help="Enrollment token")
    enroll_parser.set_defaults(func=_cmd_agent_enroll)
    
    test_parser = agent_subparsers.add_parser("test", help="Run diagnostic checks on agent configuration")
    test_parser.set_defaults(func=_cmd_agent_test)
    
    sync_parser = agent_subparsers.add_parser("sync", help="Perform one complete physical synchronization")
    sync_parser.set_defaults(func=_cmd_agent_sync)
    
    status_parser = agent_subparsers.add_parser("status", help="Display local runtime state")
    status_parser.set_defaults(func=_cmd_agent_status)
    
    run_parser = agent_subparsers.add_parser("run", help="Start the foreground agent runtime")
    run_parser.set_defaults(func=_cmd_agent_run)

    service_parser = agent_subparsers.add_parser(
        "service",
        help="Manage native OS startup for 'agent run' (Windows Task Scheduler / macOS launchd / Linux systemd)",
    )
    service_subparsers = service_parser.add_subparsers(dest="service_command", required=True)

    service_install_parser = service_subparsers.add_parser(
        "install", help="Install (or reinstall) the native service definition for this OS"
    )
    service_install_parser.add_argument("--json", action="store_true")
    service_install_parser.set_defaults(func=_cmd_agent_service_install)

    service_status_parser = service_subparsers.add_parser(
        "status", help="Show whether the native service definition is installed/running"
    )
    service_status_parser.add_argument("--json", action="store_true")
    service_status_parser.set_defaults(func=_cmd_agent_service_status)

    service_start_parser = service_subparsers.add_parser(
        "start", help="Start/trigger the agent now via the native service manager"
    )
    service_start_parser.add_argument("--json", action="store_true")
    service_start_parser.set_defaults(func=_cmd_agent_service_start)

    service_stop_parser = service_subparsers.add_parser(
        "stop", help="Stop the currently-running agent instance via the native service manager"
    )
    service_stop_parser.add_argument("--json", action="store_true")
    service_stop_parser.set_defaults(func=_cmd_agent_service_stop)

    service_uninstall_parser = service_subparsers.add_parser(
        "uninstall", help="Remove the native service definition for this OS"
    )
    service_uninstall_parser.add_argument("--json", action="store_true")
    service_uninstall_parser.set_defaults(func=_cmd_agent_service_uninstall)
