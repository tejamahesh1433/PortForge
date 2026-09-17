"""Multi-host simulation/integration tests.

Only one real machine (Windows) was available during this phase -- these
tests simulate at least three hosts (Windows, macOS, Linux) reporting
observations, exercising the exact same code paths a real macOS/Linux
agent would (enrollment, heartbeat, snapshot ingestion, port queries).
This is explicitly simulation/integration testing, not live cross-platform
validation -- labeled as such per the project brief.
"""
import uuid
from datetime import datetime, timezone


def _enroll(client, operating_system: str, hostname: str) -> tuple[str, str]:
    mint_response = client.post(
        "/api/agent/enrollment-tokens", headers={"Authorization": "Bearer test-admin-bootstrap-token"}
    )
    enrollment_token = mint_response.json()["enrollment_token"]
    host_id = str(uuid.uuid4())
    enroll_response = client.post(
        "/api/agent/enroll",
        json={
            "enrollment_token": enrollment_token,
            "host_id": host_id,
            "hostname": hostname,
            "operating_system": operating_system,
            "docker_available": operating_system != "macos",
        },
    )
    assert enroll_response.status_code == 200
    return enroll_response.json()["agent_token"], host_id


def _submit_port_8000(client, agent_token, host_id, project_name):
    now = datetime.now(timezone.utc).isoformat()
    response = client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": host_id,
            "observed_at": now,
            "observations": [
                {
                    "port": 8000,
                    "protocol": "tcp",
                    "bind_address": "0.0.0.0",
                    "state": "ACTIVE",
                    "source": "process",
                    "process_name": "app",
                    "project_name": project_name,
                    "first_seen": now,
                    "last_seen": now,
                }
            ],
        },
    )
    assert response.status_code == 200


def test_three_simulated_hosts_all_report_port_8000_independently(client):
    windows_token, windows_id = _enroll(client, "windows", "alienware-sim")
    macos_token, macos_id = _enroll(client, "macos", "macbook-sim")
    linux_token, linux_id = _enroll(client, "linux", "linux-box-sim")

    _submit_port_8000(client, windows_token, windows_id, "job-trailers-resume")
    _submit_port_8000(client, macos_token, macos_id, "ocrforge")
    _submit_port_8000(client, linux_token, linux_id, "third-project")

    response = client.get("/api/ports?port=8000")
    body = response.json()
    assert body["total"] == 3

    by_host = {item["host_id"]: item["project_name"] for item in body["items"]}
    assert by_host[windows_id] == "job-trailers-resume"
    assert by_host[macos_id] == "ocrforge"
    assert by_host[linux_id] == "third-project"


def test_no_conflict_reported_across_simulated_hosts(client):
    """Three different hosts using the same port for three different
    projects must never be reported as a conflict -- conflicts are only
    ever a same-host reservation-vs-active mismatch.
    """
    windows_token, windows_id = _enroll(client, "windows", "alienware-sim2")
    macos_token, macos_id = _enroll(client, "macos", "macbook-sim2")
    linux_token, linux_id = _enroll(client, "linux", "linux-box-sim2")

    _submit_port_8000(client, windows_token, windows_id, "project-a")
    _submit_port_8000(client, macos_token, macos_id, "project-b")
    _submit_port_8000(client, linux_token, linux_id, "project-c")

    conflicts = client.get("/api/conflicts").json()
    relevant = [c for c in conflicts if c["host_id"] in (windows_id, macos_id, linux_id)]
    assert relevant == []


def test_same_project_name_on_different_simulated_hosts(client):
    windows_token, windows_id = _enroll(client, "windows", "win-sim3")
    linux_token, linux_id = _enroll(client, "linux", "linux-sim3")

    _submit_port_8000(client, windows_token, windows_id, "shared-monorepo")
    now = datetime.now(timezone.utc).isoformat()
    client.post(
        "/api/agent/observations",
        headers={"Authorization": f"Bearer {linux_token}"},
        json={
            "scan_id": str(uuid.uuid4()),
            "host_id": linux_id,
            "observed_at": now,
            "observations": [
                {
                    "port": 5432,
                    "protocol": "tcp",
                    "bind_address": "0.0.0.0",
                    "state": "ACTIVE",
                    "source": "docker",
                    "project_name": "shared-monorepo",
                    "first_seen": now,
                    "last_seen": now,
                }
            ],
        },
    )

    response = client.get("/api/projects")
    project = next(p for p in response.json() if p["project_name"] == "shared-monorepo")
    assert project["host_count"] == 2
    assert sorted(project["hosts"]) == sorted(["win-sim3", "linux-sim3"])


def test_duplicate_hostname_different_uuids_are_independent_hosts(client):
    """Two machines could share a hostname (e.g. a common default);
    identity must be the persistent UUID, never the hostname.
    """
    token_a, id_a = _enroll(client, "windows", "duplicate-name")
    token_b, id_b = _enroll(client, "linux", "duplicate-name")

    assert id_a != id_b

    host_a = client.get(f"/api/hosts/{id_a}").json()
    host_b = client.get(f"/api/hosts/{id_b}").json()
    assert host_a["hostname"] == host_b["hostname"] == "duplicate-name"
    assert host_a["operating_system"] != host_b["operating_system"]
