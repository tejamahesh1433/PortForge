import json

from portforge_agent import cli
from portforge_agent.detection.models import Confidence, DetectionInfo
from portforge_agent.models import DiscoveredPort, PortState, Protocol, Source


def _process_port(port=3000, pid=1234, process_name="node", project=None, purpose=None, category=None):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="linux",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.PROCESS,
        pid=pid,
        process_name=process_name,
        raw_state="LISTEN",
        project_name=project,
        purpose=purpose,
        category=category,
        detection=DetectionInfo(confidence=Confidence.HIGH, method="rule_based", evidence=["evidence"]),
    )


def _docker_port(
    port=8001,
    container_port=8000,
    project="ocrforge",
    service="api",
    name="ocrforge-api",
    purpose="api",
    category="api",
):
    return DiscoveredPort(
        hostname="h",
        host_id="h",
        operating_system="linux",
        port=port,
        protocol=Protocol.TCP,
        bind_address="0.0.0.0",
        source=Source.DOCKER,
        container_id="abc123",
        container_name=name,
        docker_compose_project=project,
        service_name=service,
        host_port=port,
        container_port=container_port,
        raw_state="running",
        project_name=project,
        purpose=purpose,
        category=category,
        detection=DetectionInfo(
            confidence=Confidence.HIGH,
            method="docker_compose+rule_based",
            evidence=[f"com.docker.compose.project={project}"],
        ),
    )


def test_scan_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [_process_port()])

    exit_code = cli.main(["scan", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["port"] == 3000
    assert data[0]["protocol"] == "tcp"
    assert data[0]["detection"]["confidence"] == "high"


def test_scan_table_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [_process_port()])

    exit_code = cli.main(["scan"])

    captured = capsys.readouterr()
    assert exit_code == 0
    for col in ("PORT", "PROTOCOL", "STATUS", "PROJECT", "PURPOSE", "OWNER", "SOURCE"):
        assert col in captured.out
    assert "3000" in captured.out
    assert "node" in captured.out
    assert "1 port(s) discovered." in captured.out


def test_scan_table_output_handles_no_ports(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])

    exit_code = cli.main(["scan"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No ports discovered." in captured.out


def test_scan_table_mixes_process_and_docker_rows(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "discover_all_ports",
        lambda: [
            _process_port(port=11434, process_name="ollama.exe", purpose="ollama", category="ai"),
            _docker_port(),
        ],
    )

    exit_code = cli.main(["scan"])

    captured = capsys.readouterr()
    assert exit_code == 0
    lines = captured.out.splitlines()
    header = lines[1]  # lines[0] is the "Operating system: ... Host: ..." banner
    for col in ("PORT", "PROTOCOL", "STATUS", "PROJECT", "PURPOSE", "OWNER", "SOURCE"):
        assert col in header

    docker_row = next(line for line in lines if "ocrforge-api" in line)
    assert "Docker" in docker_row
    assert "ocrforge" in docker_row
    assert "api" in docker_row

    process_row = next(line for line in lines if "ollama.exe" in line)
    assert "Process" in process_row
    assert "ai" in process_row
    assert "ocrforge" not in process_row  # no Docker metadata leaking onto a native row


def test_scan_filter_by_port(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "discover_all_ports", lambda: [_process_port(port=3000), _process_port(port=8000)]
    )

    exit_code = cli.main(["scan", "--port", "8000", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["port"] == 8000


def test_scan_filter_by_project_is_case_insensitive_substring(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "discover_all_ports",
        lambda: [
            _docker_port(port=1, project="OCRForge"),
            _docker_port(port=2, project="job-trailers-resume"),
        ],
    )

    exit_code = cli.main(["scan", "--project", "ocrforge", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["project_name"] == "OCRForge"


def test_scan_filter_by_source(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "discover_all_ports", lambda: [_process_port(port=1), _docker_port(port=2)]
    )

    exit_code = cli.main(["scan", "--source", "Docker", "--json"])  # case-insensitive

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["source"] == "docker"


def test_scan_filter_by_purpose_matches_category_too(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "discover_all_ports",
        lambda: [
            _process_port(port=1, process_name="mysqld", purpose="mysql", category="database"),
            _process_port(port=2, process_name="ollama.exe", purpose="ollama", category="ai"),
        ],
    )

    exit_code = cli.main(["scan", "--purpose", "database", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["purpose"] == "mysql"


def test_scan_combined_filters(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "discover_all_ports",
        lambda: [
            _docker_port(port=1, project="ocrforge", purpose="api", category="api"),
            _docker_port(port=2, project="ocrforge", purpose="nginx", category="reverse-proxy"),
            _docker_port(port=3, project="job-trailers-resume", purpose="api", category="api"),
        ],
    )

    exit_code = cli.main(["scan", "--project", "ocrforge", "--purpose", "api", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["port"] == 1


def test_docker_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_docker_view", lambda: [_docker_port()])

    exit_code = cli.main(["docker", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["source"] == "docker"
    assert data[0]["container_port"] == 8000
    assert data[0]["host_port"] == 8001


def test_docker_table_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_docker_view", lambda: [_docker_port()])

    exit_code = cli.main(["docker"])

    captured = capsys.readouterr()
    assert exit_code == 0
    for col in ("PORT", "PROTOCOL", "SOURCE", "PROJECT", "SERVICE", "OWNER", "MAPPING", "ADDRESS"):
        assert col in captured.out
    assert "ocrforge-api" in captured.out
    assert "8001->8000" in captured.out
    assert "1 Docker-published port(s) discovered." in captured.out


def test_docker_table_handles_no_containers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_docker_view", lambda: [])

    exit_code = cli.main(["docker"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No ports discovered." in captured.out


def test_inspect_json_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [_docker_port(port=8000)])

    exit_code = cli.main(["inspect", "8000", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert exit_code == 0
    assert len(data) == 1
    assert data[0]["port"] == 8000


def test_inspect_table_output_shows_full_detail(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [_docker_port(port=8000, container_port=8000)])

    exit_code = cli.main(["inspect", "8000"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Port: 8000/tcp" in captured.out
    assert "Ownership" in captured.out
    assert "Project: ocrforge" in captured.out
    assert "Docker" in captured.out
    assert "Container: ocrforge-api" in captured.out
    assert "Detection" in captured.out
    assert "Confidence: high" in captured.out
    assert "com.docker.compose.project=ocrforge" in captured.out


def test_inspect_shows_multiple_distinct_bindings(monkeypatch, capsys):
    ipv4 = _docker_port(port=5173)
    ipv6 = _docker_port(port=5173)
    ipv6.bind_address = "::"

    monkeypatch.setattr(cli, "discover_all_ports", lambda: [ipv4, ipv6])

    exit_code = cli.main(["inspect", "5173"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "2 bindings found for port 5173" in captured.out
    assert captured.out.count("Host binding: 0.0.0.0") == 1
    assert captured.out.count("Host binding: ::") == 1


def test_inspect_no_match(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover_all_ports", lambda: [])

    exit_code = cli.main(["inspect", "9999"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No records found for port 9999." in captured.out
