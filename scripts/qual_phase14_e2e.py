#!/usr/bin/env python3
"""Phase 14 disposable Central qualification harness (DISPOSABLE REAL / INTEGRATION).

Talks only to the qualification Central (default http://127.0.0.1:58004).
Does not touch production (58000). Prints a machine-readable JSON summary.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import urllib.error
import urllib.request

BASE = os.environ.get("PORTFORGE_QUAL_URL", "http://127.0.0.1:58004").rstrip("/")
ADMIN = os.environ.get(
    "PORTFORGE_QUAL_ADMIN_BOOTSTRAP_TOKEN",
    "qual-example-bootstrap-token-not-a-real-secret",
)
RESULTS: dict[str, Any] = {"base": BASE, "checks": {}}


def _req(method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {"detail": str(exc)}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def check(name: str, ok: bool, detail: Any = None) -> None:
    RESULTS["checks"][name] = {"pass": bool(ok), "detail": detail}
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail is not None and not ok else ""))


def mint_token() -> str:
    code, body = _req("POST", "/api/agent/enrollment-tokens", token=ADMIN)
    assert code == 200, body
    return body["enrollment_token"]


def enroll(hostname: str, host_id: uuid.UUID | None = None, agent_version: str = "1.3.0") -> tuple[str, uuid.UUID]:
    hid = host_id or uuid.uuid4()
    code, body = _req(
        "POST",
        "/api/agent/enroll",
        {
            "enrollment_token": mint_token(),
            "host_id": str(hid),
            "hostname": hostname,
            "operating_system": "linux",
            "os_version": "Ubuntu 22.04",
            "architecture": "x86_64",
            "agent_version": agent_version,
            "docker_available": False,
            "protocol_version": 1,
            "contract_version": 1,
            "python_version": "3.12.0",
        },
    )
    assert code == 200, body
    return body["agent_token"], hid


def heartbeat(token: str, host_id: uuid.UUID, hostname: str, agent_version: str = "1.3.0") -> tuple[int, Any]:
    return _req(
        "POST",
        "/api/agent/heartbeat",
        {
            "host_id": str(host_id),
            "hostname": hostname,
            "operating_system": "linux",
            "os_version": "Ubuntu 22.04",
            "architecture": "x86_64",
            "agent_version": agent_version,
            "docker_available": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "protocol_version": 1,
            "contract_version": 1,
            "python_version": "3.12.0",
        },
        token=token,
    )


def main() -> int:
    # Health
    code, health = _req("GET", "/api/health")
    check("fresh_health", code == 200 and health.get("database") == "connected", health)

    # Empty fleet initially (or near-empty)
    code, fleet = _req("GET", "/api/fleet?limit=50")
    check("fleet_list", code == 200, {"total": fleet.get("total") if isinstance(fleet, dict) else None})

    # --- Lifecycle E2E ---
    token, hid = enroll("qual-lifecycle-host")
    code, hb = heartbeat(token, hid, "qual-lifecycle-host")
    check("lifecycle_enroll_heartbeat", code == 200)

    code, host = _req("GET", f"/api/hosts/{hid}")
    check("lifecycle_active", code == 200 and (host.get("lifecycle_state") or "ACTIVE") == "ACTIVE")

    code, _ = _req("POST", f"/api/hosts/{hid}/decommission", {"reason": "qual"}, token=ADMIN)
    check("lifecycle_decommission", code == 200)

    code, _ = heartbeat(token, hid, "qual-lifecycle-host")
    check("lifecycle_old_cred_rejected", code in (401, 403))

    # Resurrection without reactivate should fail
    code, body = _req(
        "POST",
        "/api/agent/enroll",
        {
            "enrollment_token": mint_token(),
            "host_id": str(hid),
            "hostname": "qual-lifecycle-host",
            "operating_system": "linux",
            "agent_version": "1.3.0",
            "docker_available": False,
            "protocol_version": 1,
        },
    )
    check("lifecycle_resurrection_blocked", code in (403, 409, 400, 422), body)

    code, _ = _req("POST", f"/api/hosts/{hid}/reactivate", token=ADMIN)
    check("lifecycle_reactivate", code == 200)

    token2, _ = enroll("qual-lifecycle-host", host_id=hid)
    code, _ = heartbeat(token2, hid, "qual-lifecycle-host")
    check("lifecycle_reenroll_heartbeat", code == 200)
    code, _ = heartbeat(token, hid, "qual-lifecycle-host")
    check("lifecycle_old_cred_still_rejected", code in (401, 403))

    # Upgrade rejection while decommissioned
    token_u, hid_u = enroll("qual-upgrade-host", agent_version="1.0.0")
    heartbeat(token_u, hid_u, "qual-upgrade-host", "1.0.0")
    _req("POST", f"/api/hosts/{hid_u}/decommission", {"reason": "qual-upg"}, token=ADMIN)
    code, body = _req(
        "POST",
        f"/api/hosts/{hid_u}/upgrades",
        {
            "target_version": "1.1.0",
            "artifact_url": "https://example.com/portforge_agent-1.1.0-py3-none-any.whl",
            "artifact_sha256": "a" * 64,
        },
        token=ADMIN,
    )
    check("upgrade_decommissioned_rejected", code in (409, 422, 400), body)

    # Active upgrade + decommission cancels
    token_a, hid_a = enroll("qual-upgrade-active", agent_version="1.0.0")
    heartbeat(token_a, hid_a, "qual-upgrade-active", "1.0.0")
    code, upg = _req(
        "POST",
        f"/api/hosts/{hid_a}/upgrades",
        {
            "target_version": "1.1.0",
            "artifact_url": "https://example.com/portforge_agent-1.1.0-py3-none-any.whl",
            "artifact_sha256": "a" * 64,
            "request_id": f"qual-upg-{uuid.uuid4()}",
        },
        token=ADMIN,
    )
    check("upgrade_active_create", code in (200, 201), upg)
    _req("POST", f"/api/hosts/{hid_a}/decommission", {"reason": "cancel-upg"}, token=ADMIN)
    if isinstance(upg, dict) and upg.get("id"):
        code, upg2 = _req("GET", f"/api/upgrades/{upg['id']}", token=ADMIN)
        check(
            "upgrade_cancelled_on_decommission",
            code == 200 and upg2.get("state") == "FAILED",
            upg2.get("state") if isinstance(upg2, dict) else upg2,
        )
    else:
        check("upgrade_cancelled_on_decommission", False, upg)

    # Agent cannot create upgrade
    code, _ = _req(
        "POST",
        f"/api/hosts/{hid}/upgrades",
        {
            "target_version": "1.4.0",
            "artifact_url": "https://example.com/x.whl",
            "artifact_sha256": "b" * 64,
        },
        token=token2,
    )
    check("upgrade_agent_cannot_create", code in (401, 403))

    # Enrollment token cannot create upgrade
    et = mint_token()
    code, _ = _req(
        "POST",
        f"/api/hosts/{hid}/upgrades",
        {
            "target_version": "1.4.0",
            "artifact_url": "https://example.com/x.whl",
            "artifact_sha256": "b" * 64,
        },
        token=et,
    )
    check("upgrade_enrollment_cannot_create", code in (401, 403))

    # --- Fleet multi-host states ---
    hosts_meta = []
    for name, ver in [("qual-fleet-healthy", "1.3.0"), ("qual-fleet-old", "1.2.0")]:
        t, h = enroll(name, agent_version=ver)
        heartbeat(t, h, name, ver)
        hosts_meta.append((name, t, h, ver))
    t_off, h_off = enroll("qual-fleet-offline", agent_version="1.3.0")
    # no heartbeat -> offline eventually; force via fleet filter after short wait not needed for list
    hosts_meta.append(("qual-fleet-offline", t_off, h_off, "1.3.0"))
    t_d, h_d = enroll("qual-fleet-decomm", agent_version="1.3.0")
    heartbeat(t_d, h_d, "qual-fleet-decomm")
    _req("POST", f"/api/hosts/{h_d}/decommission", {"reason": "fleet"}, token=ADMIN)

    code, fleet = _req("GET", "/api/fleet?limit=100")
    check("fleet_inventory", code == 200 and fleet.get("total", 0) >= 4)
    code, filtered = _req("GET", "/api/fleet?lifecycle_state=DECOMMISSIONED&limit=50")
    check(
        "fleet_filter_decommissioned",
        code == 200 and all(i.get("lifecycle_state") == "DECOMMISSIONED" for i in filtered.get("items", [])),
    )
    code, q = _req("GET", "/api/fleet?q=qual-fleet-healthy&limit=50")
    check("fleet_search", code == 200 and any(i.get("hostname") == "qual-fleet-healthy" for i in q.get("items", [])))

    # --- Atomic multi-port allocation ---
    # need an ACTIVE healthy host for allocation
    code, healthy = _req("GET", f"/api/hosts/{hosts_meta[0][2]}")
    host_id = str(hosts_meta[0][2])
    req_id = f"qual-batch-{uuid.uuid4()}"
    payload = {
        "project": "qual-sample-stack",
        "host_id": host_id,
        "request_id": req_id,
        "requests": [
            {"name": "frontend", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3100},
            {"name": "api", "purpose": "api", "protocol": "tcp", "preferred_port": 8100},
            {"name": "postgres", "purpose": "postgres", "protocol": "tcp", "preferred_port": 5433},
            {"name": "redis", "purpose": "redis", "protocol": "tcp", "preferred_port": 6380},
            {"name": "metrics", "purpose": "generic", "protocol": "tcp", "preferred_port": 9100},
        ],
    }
    code, alloc = _req("POST", "/api/allocations", payload)
    check("allocation_batch_create", code in (200, 201) and len(alloc.get("allocations", [])) == 5, alloc if code not in (200, 201) else None)
    ports = [e["port"] for e in alloc.get("allocations", [])] if isinstance(alloc, dict) else []
    check("allocation_ports_distinct", len(ports) == len(set(ports)) and len(ports) == 5, ports)

    # Idempotency
    code2, alloc2 = _req("POST", "/api/allocations", payload)
    check(
        "allocation_idempotent",
        code2 == 200
        and alloc2.get("allocation_id") == alloc.get("allocation_id")
        and alloc2.get("idempotent_replay") is True,
        {"code": code2, "id": alloc2.get("allocation_id") if isinstance(alloc2, dict) else None},
    )

    # Conflict payload same request_id
    bad = dict(payload)
    bad["requests"] = payload["requests"][:1]
    code3, body3 = _req("POST", "/api/allocations", bad)
    check("allocation_idempotency_conflict", code3 == 409, body3)

    # Batch alias
    req_id2 = f"qual-batch-alias-{uuid.uuid4()}"
    payload2 = dict(payload)
    payload2["request_id"] = req_id2
    payload2["project"] = "qual-sample-stack-2"
    # shift preferred ports to avoid conflict with first bundle
    for i, item in enumerate(payload2["requests"]):
        item = dict(item)
        item["preferred_port"] = (item.get("preferred_port") or 3000) + 50
        payload2["requests"][i] = item
    code, alloc_b = _req("POST", "/api/allocations/batch", payload2)
    check("allocation_batch_alias", code in (200, 201), alloc_b if code not in (200, 201) else None)

    # Release
    if isinstance(alloc, dict) and alloc.get("allocation_id"):
        code, released = _req("DELETE", f"/api/allocations/{alloc['allocation_id']}")
        check("allocation_batch_release", code == 200 and released.get("status") == "released")

    # Concurrent-ish sequential same key already covered; fire parallel via threads
    import concurrent.futures

    def _once(i: int):
        p = {
            "project": f"qual-conc-{i % 3}",
            "host_id": host_id,
            "request_id": f"qual-conc-shared" if i < 5 else f"qual-conc-{i}",
            "requests": [
                {"name": "svc", "purpose": "api", "protocol": "tcp"},
            ],
        }
        return _req("POST", "/api/allocations", p)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_once, range(12)))
    shared = [r for r in results[:5]]
    shared_ids = {r[1].get("allocation_id") for r in shared if r[0] in (200, 201) and isinstance(r[1], dict)}
    check("allocation_concurrent_idempotency", len(shared_ids) == 1, list(shared_ids))
    ok_creates = sum(1 for c, b in results[5:] if c in (200, 201))
    check("allocation_concurrent_distinct_keys", ok_creates >= 5, ok_creates)

    # Decommissioned host allocation rejected
    code, body = _req(
        "POST",
        "/api/allocations",
        {
            "project": "should-fail",
            "host_id": str(h_d),
            "requests": [{"name": "x", "purpose": "api", "protocol": "tcp"}],
        },
    )
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    check("allocation_decommissioned_rejected", code == 409 and err.get("code") == "HOST_DECOMMISSIONED", body)

    # Remove Record clears host
    code, _ = _req("DELETE", f"/api/hosts/{hid_u}", token=ADMIN)
    check("remove_record", code == 204)
    code, _ = _req("GET", f"/api/hosts/{hid_u}")
    check("remove_record_gone", code == 404)

    # Duplicate identity count
    code, hosts = _req("GET", "/api/hosts?limit=100")
    ids = [h["id"] for h in hosts.get("items", [])] if code == 200 else []
    check("no_duplicate_identities", len(ids) == len(set(ids)), {"count": len(ids), "unique": len(set(ids))})

    # Timing sanity
    t0 = time.perf_counter()
    _req("GET", "/api/fleet?limit=100")
    fleet_ms = (time.perf_counter() - t0) * 1000
    RESULTS["fleet_list_ms"] = round(fleet_ms, 1)
    check("fleet_latency_sane", fleet_ms < 5000, fleet_ms)

    failed = [k for k, v in RESULTS["checks"].items() if not v["pass"]]
    RESULTS["summary"] = {"passed": len(RESULTS["checks"]) - len(failed), "failed": len(failed), "failed_names": failed}
    print(json.dumps(RESULTS["summary"], indent=2))
    print(json.dumps(RESULTS, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
