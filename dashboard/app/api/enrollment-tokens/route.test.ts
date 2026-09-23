import { beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "./route";

describe("POST /api/enrollment-tokens", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("returns 503 when the bootstrap token is not configured", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "");
    const response = await POST(new Request("http://localhost/api/enrollment-tokens", { method: "POST", body: "{}" }));
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.detail).toMatch(/PORTFORGE_ADMIN_BOOTSTRAP_TOKEN/);
  });

  it("forwards a successful mint from Central", async () => {
    vi.stubEnv("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", "admin-secret");
    vi.stubEnv("PORTFORGE_INTERNAL_API_URL", "http://central.test");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ enrollment_token: "tok-1", expires_at: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      new Request("http://localhost/api/enrollment-tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: "mac", ttl_hours: 12 }),
      }),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ enrollment_token: "tok-1", expires_at: null });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string | URL, RequestInit];
    expect(String(url)).toContain("/api/agent/enrollment-tokens");
    expect(String(url)).toContain("label=mac");
    expect(String(url)).toContain("ttl_hours=12");
    expect(init.headers).toMatchObject({ Authorization: "Bearer admin-secret" });
  });
});
