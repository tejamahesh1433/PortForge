import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

const UPGRADE_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa";
const HOST_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function makeRequest() {
  return new Request(`http://localhost/api/upgrades/${UPGRADE_ID}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
}

const params = Promise.resolve({ upgradeId: UPGRADE_ID });

const cancelledBody = JSON.stringify({
  id: UPGRADE_ID,
  host_id: HOST_ID,
  state: "FAILED",
  target_version: "1.2.0",
  artifact_url: "https://example.com/agent-1.2.0.whl",
  artifact_sha256: "abc123",
  artifact_filename: null,
  request_id: null,
  failure_reason: "Cancelled by operator.",
  previous_version: "1.1.0",
  previous_artifact_url: null,
  previous_artifact_sha256: null,
  created_at: "2026-09-23T02:00:00Z",
  updated_at: "2026-09-23T02:10:00Z",
});

describe("POST /api/upgrades/[upgradeId]/cancel", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("returns 503 when the bootstrap token is not configured", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "");
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.detail).toMatch(/PORTFORGE_ADMIN_BOOTSTRAP_TOKEN/);
    expect(JSON.stringify(body)).not.toMatch(/Bearer/);
  });

  it("rejects invalid upgrade ids without calling Central", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(makeRequest(), {
      params: Promise.resolve({ upgradeId: "not-a-uuid" }),
    });
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards cancel to Central with server-side admin Authorization", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(cancelledBody, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toBe(`http://central.test/api/upgrades/${UPGRADE_ID}/cancel`);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
    const result = await response.json();
    expect(result.state).toBe("FAILED");
    expect(result.failure_reason).toMatch(/Cancelled/);
  });

  it("does not leak the admin token in the response body on Central error", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Upgrade cannot be cancelled in current state." }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(409);
    const body = await response.json();
    expect(body.detail).toMatch(/cancelled/i);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
  });

  it("maps network failure to 502 without leaking secrets", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(502);
    const body = await response.json();
    expect(body.detail).toMatch(/Could not reach Central/);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
    expect(JSON.stringify(body)).not.toContain("Bearer");
  });
});
