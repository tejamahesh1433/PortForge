"""CLI tests for check/next/reserve/release/reservations/conflicts/
sync-reservations, and their documented exit codes.
"""
import json

from portforge_agent import cli
from portforge_agent.bindprobe import BindProbeResult
from portforge_agent.config import PortForgeConfig, PortRange
from portforge_agent.models import DiscoveredPort, Protocol, Source
from portforge_agent.recommend import RecommendationResult, ValidationStep
from portforge_agent.reservations.models import Reservation
from portforge_agent.reserve_ops import ReleaseOutcome, ReleaseResult, ReserveOutcome, ReserveResult


def _discovered(port=8000, project_name=None, process_name="app.exe", source=Source.PROCESS, container_name=None):
    return DiscoveredPort(
        hostname="h",
        host_id="host-a",
        operating_system="linux",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=source,
        process_name=process_name,
        project_name=project_name,
        container_name=container_name,
    )


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_free_port_exit_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={}, exclusions=[]))
    monkeypatch.setattr(cli, "probe_bind", lambda port, protocol, address: BindProbeResult(True, None))

    exit_code = cli.main(["check", "8002"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "State: FREE" in captured.out
    assert "Available: yes" in captured.out


def test_check_active_port_exit_one(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [_discovered(port=8000, process_name="mysqld.exe")])
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={}, exclusions=[]))
    monkeypatch.setattr(cli, "probe_bind", lambda port, protocol, address: BindProbeResult(False, "bind failed"))

    exit_code = cli.main(["check", "8000"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "State: ACTIVE" in captured.out
    assert "Available: no" in captured.out


def test_check_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={}, exclusions=[]))
    monkeypatch.setattr(cli, "probe_bind", lambda port, protocol, address: BindProbeResult(True, None))

    exit_code = cli.main(["check", "8002", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert data["state"] == "FREE"
    assert data["available"] is True


def test_check_storage_error_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: (None, "malformed reservations.json"))

    exit_code = cli.main(["check", "8000"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "malformed" in captured.err.lower()


def test_check_reserved_port(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace", service="api")
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([reservation], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])
    monkeypatch.setattr(cli.pf, "get_host_id", lambda: "host-a")
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={}, exclusions=[]))
    monkeypatch.setattr(cli, "probe_bind", lambda port, protocol, address: BindProbeResult(True, None))

    exit_code = cli.main(["check", "8003"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "State: RESERVED" in captured.out
    assert "deeptrace" in captured.out


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def test_next_recommends_port(monkeypatch, capsys):
    result = RecommendationResult(
        "api", Protocol.TCP, "0.0.0.0", 8001, [ValidationStep("bind_probe", True, "bind succeeded")], 2
    )
    monkeypatch.setattr(cli, "recommend_port", lambda *a, **kw: result)
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    exit_code = cli.main(["next", "api"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Recommended port: 8001" in captured.out


def test_next_json_output(monkeypatch, capsys):
    result = RecommendationResult("api", Protocol.TCP, "0.0.0.0", 8001, [], 1)
    monkeypatch.setattr(cli, "recommend_port", lambda *a, **kw: result)
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    exit_code = cli.main(["next", "api", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert data["recommended_port"] == 8001
    assert data["reservation"] is None


def test_next_unknown_service_type_exit_two(monkeypatch, capsys):
    result = RecommendationResult("bogus", Protocol.TCP, "0.0.0.0", None, [], 0, unknown_service_type=True)
    monkeypatch.setattr(cli, "recommend_port", lambda *a, **kw: result)
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    exit_code = cli.main(["next", "bogus"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Unknown service type" in captured.err


def test_next_exhausted_exit_one(monkeypatch, capsys):
    result = RecommendationResult("api", Protocol.TCP, "0.0.0.0", None, [], 1000)
    monkeypatch.setattr(cli, "recommend_port", lambda *a, **kw: result)
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    exit_code = cli.main(["next", "api"])
    assert exit_code == 1


def test_next_reserve_requires_project(monkeypatch, capsys):
    exit_code = cli.main(["next", "api", "--reserve"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--project" in captured.err


def test_next_reserve_calls_recommend_and_reserve(monkeypatch, capsys):
    result = RecommendationResult("api", Protocol.TCP, "0.0.0.0", 8001, [], 1)
    reservation = Reservation.create(host_id="host-a", port=8001, project="deeptrace")
    monkeypatch.setattr(cli, "recommend_and_reserve", lambda *a, **kw: (result, reservation))
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    exit_code = cli.main(["next", "api", "--reserve", "--project", "deeptrace"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Reservation created" in captured.out


def test_next_does_not_reserve_without_flag(monkeypatch):
    called = {"recommend_and_reserve": False}
    result = RecommendationResult("api", Protocol.TCP, "0.0.0.0", 8001, [], 1)

    def _fake_recommend_port(*a, **kw):
        return result

    def _fake_recommend_and_reserve(*a, **kw):
        called["recommend_and_reserve"] = True
        return result, None

    monkeypatch.setattr(cli, "recommend_port", _fake_recommend_port)
    monkeypatch.setattr(cli, "recommend_and_reserve", _fake_recommend_and_reserve)
    monkeypatch.setattr(cli, "load_config", lambda: PortForgeConfig(ranges={"api": PortRange("api", 8000, 8999)}))

    cli.main(["next", "api"])
    assert called["recommend_and_reserve"] is False


# ---------------------------------------------------------------------------
# reserve / release
# ---------------------------------------------------------------------------


def test_reserve_success(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace")
    result = ReserveResult(ReserveOutcome.CREATED, reservation, "Reservation created: 8003/tcp -> 'deeptrace'.")
    monkeypatch.setattr(cli, "reserve_ops_reserve", lambda *a, **kw: result)

    exit_code = cli.main(["reserve", "8003", "--project", "deeptrace"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Reservation created" in captured.out


def test_reserve_refused_exit_one(monkeypatch, capsys):
    result = ReserveResult(ReserveOutcome.REFUSED_OTHER_PROJECT_RESERVATION, None, "refused")
    monkeypatch.setattr(cli, "reserve_ops_reserve", lambda *a, **kw: result)

    exit_code = cli.main(["reserve", "8003", "--project", "deeptrace"])
    assert exit_code == 1


def test_reserve_json(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace")
    result = ReserveResult(ReserveOutcome.CREATED, reservation, "ok")
    monkeypatch.setattr(cli, "reserve_ops_reserve", lambda *a, **kw: result)

    exit_code = cli.main(["reserve", "8003", "--project", "deeptrace", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["outcome"] == "created"
    assert data["reservation"]["project"] == "deeptrace"


def test_release_by_port_and_project(monkeypatch, capsys):
    result = ReleaseResult(ReleaseOutcome.RELEASED, None, "Released.")
    monkeypatch.setattr(cli, "reserve_ops_release", lambda *a, **kw: result)

    exit_code = cli.main(["release", "8003", "--project", "deeptrace"])
    assert exit_code == 0


def test_release_by_id(monkeypatch, capsys):
    result = ReleaseResult(ReleaseOutcome.RELEASED, None, "Released.")
    monkeypatch.setattr(cli, "reserve_ops_release_by_id", lambda *a, **kw: result)

    exit_code = cli.main(["release", "--id", "abc123"])
    assert exit_code == 0


def test_release_wrong_project_exit_one(monkeypatch):
    result = ReleaseResult(ReleaseOutcome.REFUSED_OTHER_PROJECT, None, "refused")
    monkeypatch.setattr(cli, "reserve_ops_release", lambda *a, **kw: result)

    exit_code = cli.main(["release", "8003", "--project", "someone-else"])
    assert exit_code == 1


def test_release_missing_args_exit_two(monkeypatch, capsys):
    exit_code = cli.main(["release", "8003"])  # no --project, no --id
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "requires" in captured.err.lower()


# ---------------------------------------------------------------------------
# reservations / conflicts
# ---------------------------------------------------------------------------


def test_reservations_list(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace", service="api", purpose="api")
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([reservation], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])
    monkeypatch.setattr(cli.pf, "get_host_id", lambda: "host-a")

    exit_code = cli.main(["reservations"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "deeptrace" in captured.out
    assert "RESERVED" in captured.out


def test_reservations_json(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace")
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([reservation], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])
    monkeypatch.setattr(cli.pf, "get_host_id", lambda: "host-a")

    exit_code = cli.main(["reservations", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data[0]["reservation"]["project"] == "deeptrace"


def test_conflicts_none_found(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])

    exit_code = cli.main(["conflicts"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No conflicts" in captured.out


def test_conflicts_found_exit_one(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace", service="api")
    active = _discovered(port=8003, project_name="another-project", process_name="python.exe")
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([reservation], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [active])
    monkeypatch.setattr(cli.pf, "get_host_id", lambda: "host-a")

    exit_code = cli.main(["conflicts"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "CONFLICT" in captured.out
    assert "another-project" in captured.out
    assert "python.exe" in captured.out


def test_conflicts_json(monkeypatch, capsys):
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace")
    active = _discovered(port=8003, project_name="another-project")
    monkeypatch.setattr(cli, "_load_reservations_or_error", lambda: ([reservation], None))
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [active])
    monkeypatch.setattr(cli.pf, "get_host_id", lambda: "host-a")

    exit_code = cli.main(["conflicts", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert data[0]["state"] == "CONFLICT"


# ---------------------------------------------------------------------------
# sync-reservations
# ---------------------------------------------------------------------------


def test_sync_reservations_no_config_found(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_project_config", lambda path: None)

    exit_code = cli.main(["sync-reservations"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "No .portforge" in captured.err


def test_sync_reservations_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_project_config", lambda path: {"project": "deeptrace", "ports": [{"port": 8003}]})
    reservation = Reservation.create(host_id="host-a", port=8003, project="deeptrace")
    fake_result = ReserveResult(ReserveOutcome.CREATED, reservation, "Reservation created: 8003/tcp -> 'deeptrace'.")
    monkeypatch.setattr(cli, "sync_project_reservations", lambda cfg: [fake_result])

    exit_code = cli.main(["sync-reservations"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "deeptrace" in captured.out


# ---------------------------------------------------------------------------
# --help / command organization sanity
# ---------------------------------------------------------------------------


def test_help_lists_all_commands(capsys):
    try:
        cli.main(["--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    for command in ("scan", "docker", "inspect", "check", "next", "reserve", "release", "reservations", "conflicts", "sync-reservations"):
        assert command in captured.out
