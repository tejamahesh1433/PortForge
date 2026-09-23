import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

const HOST_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function makeRequest() {
  return new Request(`http://localhost/api/hosts/${HOST_ID}/reactivate`, {
    method: "POST",
  });
}

const params = Promise.resolve({ hostId: HOST_ID });

const reactivatedHostBody = JSON.stringify({
  id: HOST_ID,
  hostname: "lab-mac",
  lifecycle_state: "ACTIVE",
});

describe("POST /api/hosts/[hostId]/reactivate", () => {
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

  it("rejects invalid host ids without calling Central", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await POST(makeRequest(), {
      params: Promise.resolve({ hostId: "not-a-uuid" }),
    });
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards Central 200 with server-side admin Authorization", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(reactivatedHostBody, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toBe(
      `http://central.test/api/hosts/${HOST_ID}/reactivate`,
    );
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
    const result = await response.json();
    expect(result.lifecycle_state).toBe("ACTIVE");
  });

  it("preserves Central 409 (e.g. already active)", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Host is already active." }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ detail: "Host is already active." });
  });

  it("preserves Central auth failure without leaking the admin secret", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Invalid or missing admin credentials." }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const response = await POST(makeRequest(), { params });
    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.detail).toMatch(/admin credentials/i);
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
