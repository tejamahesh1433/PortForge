import { beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE } from "./route";

function makeRequest() {
  return new Request("http://localhost/api/hosts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", {
    method: "DELETE",
  });
}

const params = Promise.resolve({ hostId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" });

describe("DELETE /api/hosts/[hostId]", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("returns 503 when the bootstrap token is not configured", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "");
    const response = await DELETE(makeRequest(), { params });
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.detail).toMatch(/PORTFORGE_ADMIN_BOOTSTRAP_TOKEN/);
    expect(JSON.stringify(body)).not.toMatch(/Bearer/);
  });

  it("rejects invalid host ids without calling Central", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await DELETE(makeRequest(), {
      params: Promise.resolve({ hostId: "not-a-uuid" }),
    });
    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards Central 204 with server-side admin Authorization", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await DELETE(makeRequest(), { params });
    expect(response.status).toBe(204);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toBe("http://central.test/api/hosts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    expect(init.method).toBe("DELETE");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
  });

  it("preserves Central 404", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Host not found." }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const response = await DELETE(makeRequest(), { params });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "Host not found." });
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
    const response = await DELETE(makeRequest(), { params });
    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.detail).toMatch(/admin credentials/i);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
  });

  it("maps network failure to 502 without leaking secrets", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const response = await DELETE(makeRequest(), { params });
    expect(response.status).toBe(502);
    const body = await response.json();
    expect(body.detail).toMatch(/Could not reach Central/);
    expect(JSON.stringify(body)).not.toContain("admin-secret");
    expect(JSON.stringify(body)).not.toContain("Bearer");
  });
});
