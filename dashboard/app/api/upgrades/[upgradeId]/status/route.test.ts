import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

const UPGRADE_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa";

function makeRequest() {
  return new Request(`http://localhost/api/upgrades/${UPGRADE_ID}/status`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
}

const params = Promise.resolve({ upgradeId: UPGRADE_ID });

const statusBody = JSON.stringify({
  id: UPGRADE_ID,
  host_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  state: "WAITING_FOR_AGENT",
  target_version: "1.2.0",
  progress_status: "waiting",
  waiting_reason: "Awaiting next agent heartbeat",
  failure_code: null,
  explanation: "The upgrade is queued and will begin at the next heartbeat.",
  operator_actions: ["CANCEL"],
  attempt_number: 1,
  attempt_max: 3,
  reconciliation_status: "pending",
  host_health: "HEALTHY",
  created_at: "2026-09-23T01:00:00Z",
  updated_at: "2026-09-23T01:00:00Z",
});

describe("GET /api/upgrades/[upgradeId]/status", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("returns 503 when the bootstrap token is not configured", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "");
    const response = await GET(makeRequest(), { params });
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.detail).toMatch(/PORTFORGE_ADMIN_BOOTSTRAP_TOKEN/);
    expect(JSON.stringify(body)).not.toMatch(/Bearer/);
  });

  it("rejects invalid upgrade ids without calling Central", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await GET(makeRequest(), {
      params: Promise.resolve({ upgradeId: "not-a-uuid" }),
    });
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards GET to Central with server-side admin Authorization", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(statusBody, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(makeRequest(), { params });
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toBe(`http://central.test/api/upgrades/${UPGRADE_ID}/status`);
    expect((init as RequestInit & { method: string }).method).toBe("GET");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
    const result = await response.json();
    expect(result.progress_status).toBe("waiting");
    expect(result.operator_actions).toContain("CANCEL");
  });

  it("does not leak the admin token in the response body on Central error", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Upgrade not found." }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const response = await GET(makeRequest(), { params });
    expect(response.status).toBe(404);
    const body = await response.json();
    expect(body.detail).toMatch(/not found/i);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
  });

  it("maps network failure to 502 without leaking secrets", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const response = await GET(makeRequest(), { params });
    expect(response.status).toBe(502);
    const body = await response.json();
    expect(body.detail).toMatch(/Could not reach Central/);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
    expect(JSON.stringify(body)).not.toContain("Bearer");
  });
});
