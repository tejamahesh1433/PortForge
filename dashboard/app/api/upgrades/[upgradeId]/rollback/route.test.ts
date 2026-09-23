import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

const UPGRADE_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa";
const HOST_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function makeRequest() {
  return new Request(`http://localhost/api/upgrades/${UPGRADE_ID}/rollback`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
}

const params = Promise.resolve({ upgradeId: UPGRADE_ID });

const rollbackBody = JSON.stringify({
  id: "dddddddd-eeee-ffff-aaaa-bbbbbbbbbbbb",
  host_id: HOST_ID,
  state: "APPROVED",
  target_version: "1.1.0",
  artifact_url: "https://example.com/agent-1.1.0.whl",
  artifact_sha256: "prev123",
  artifact_filename: null,
  request_id: null,
  failure_reason: null,
  previous_version: "1.2.0",
  previous_artifact_url: null,
  previous_artifact_sha256: null,
  created_at: "2026-09-23T01:00:00Z",
  updated_at: "2026-09-23T01:00:00Z",
});

describe("POST /api/upgrades/[upgradeId]/rollback", () => {
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

  it("forwards rollback to Central with server-side admin Authorization", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(rollbackBody, {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(201);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toBe(`http://central.test/api/upgrades/${UPGRADE_ID}/rollback`);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
    const result = await response.json();
    expect(result.target_version).toBe("1.1.0");
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
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(404);
    const body = await response.json();
    expect(body.detail).toMatch(/not found/i);
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
