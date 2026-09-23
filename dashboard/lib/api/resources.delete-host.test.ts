import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deleteHost } from "./resources";

describe("deleteHost", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the dashboard BFF without any admin secret", async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    await deleteHost("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/hosts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    expect(init.method).toBe("DELETE");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(JSON.stringify(init)).not.toMatch(/admin|bootstrap|Bearer/i);
  });

  it("maps non-204 responses to PortForgeApiError", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ detail: "Host not found." }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(deleteHost("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")).rejects.toMatchObject({
      status: 404,
      detail: "Host not found.",
      name: "PortForgeApiError",
    });
  });
});
