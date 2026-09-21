import { afterEach, describe, expect, it, vi } from "vitest";
import { PortForgeApiError, PortForgeConnectionError, portforgeFetch } from "./client";

describe("portforgeFetch", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("returns parsed JSON on a 200 response", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );

    const result = await portforgeFetch<{ status: string }>("/api/health");
    expect(result).toEqual({ status: "ok" });
  });

  it("builds the request URL against the configured base with query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    global.fetch = fetchMock;

    await portforgeFetch("/api/ports", { params: { port: 8000, project: undefined, source: null } });

    const calledUrl = new URL(fetchMock.mock.calls[0][0] as string);
    expect(calledUrl.pathname).toBe("/api/ports");
    expect(calledUrl.searchParams.get("port")).toBe("8000");
    // undefined/null params must be omitted entirely, not sent as "undefined"/"null" strings.
    expect(calledUrl.searchParams.has("project")).toBe(false);
    expect(calledUrl.searchParams.has("source")).toBe(false);
  });

  it("throws PortForgeApiError with Central's own detail message on a non-2xx response", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Host not found." }), { status: 404 }),
    );

    await expect(portforgeFetch("/api/hosts/does-not-exist")).rejects.toMatchObject({
      constructor: PortForgeApiError,
      status: 404,
      detail: "Host not found.",
    });
  });

  it("falls back to a generic message when the error body has no detail", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response("not json", { status: 500 }));

    expect.assertions(3);
    try {
      await portforgeFetch("/api/health");
    } catch (error) {
      expect(error).toBeInstanceOf(PortForgeApiError);
      expect((error as PortForgeApiError).status).toBe(500);
      expect((error as PortForgeApiError).detail).toBeNull();
    }
  });

  it("throws PortForgeConnectionError when the network request itself fails", async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(portforgeFetch("/api/health")).rejects.toBeInstanceOf(PortForgeConnectionError);
  });

  it("returns undefined for a 204 No Content response", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));

    const result = await portforgeFetch("/api/reservations/some-id");
    expect(result).toBeUndefined();
  });

  it("never attaches an Authorization header on a write request -- PortForge runs as a trusted private/LAN control plane with no dashboard token architecture", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    global.fetch = fetchMock;

    await portforgeFetch("/api/reservations/dashboard", {
      method: "POST",
      body: { host_id: "h1", port: 9000, project: "p" },
    });

    const [, requestInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(requestInit.headers).not.toHaveProperty("Authorization");
  });
});
