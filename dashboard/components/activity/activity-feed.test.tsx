import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActivityFeed, isEphemeralActivity } from "./activity-feed";

vi.mock("@/hooks/use-hosts", () => ({ useHosts: () => ({ data: { items: [{ id: "host-1", hostname: "NTMKEYA" }] } }) }));
vi.mock("@/lib/api/activity", () => ({ listActivity: vi.fn().mockResolvedValue({ events: [{ id: "1", host_id: "host-1", timestamp: "2026-09-18T10:00:00Z", event_type: "PORT_APPEARED", port: 8080, protocol: "tcp", bind_address: "0.0.0.0", source: "process", identity_context: "python", reservation_id: null, summary: "Port 8080/tcp appeared", metadata_json: null }], total: 1 }) }));

describe("ActivityFeed", () => {
  it("renders host-aware activity", async () => {
    const queryClient = new QueryClient();
    render(<QueryClientProvider client={queryClient}><ActivityFeed /></QueryClientProvider>);
    expect(await screen.findByText("Port 8080/tcp appeared")).toBeInTheDocument();
    expect(screen.getByText("PORT_APPEARED")).toBeInTheDocument();
    expect(screen.getByText(/NTMKEYA.*python/)).toBeInTheDocument();
  });

  it("classifies only unprojected dynamic UDP process events as ephemeral", () => {
    const event = { id: "e", host_id: "host-1", timestamp: "2026-09-18T10:00:00Z", event_type: "PORT_APPEARED", port: 55000, protocol: "udp", bind_address: "0.0.0.0", source: "process", identity_context: "comet.exe", reservation_id: null, summary: "Port appeared", metadata_json: null };
    expect(isEphemeralActivity(event)).toBe(true);
    expect(isEphemeralActivity({ ...event, protocol: "tcp" })).toBe(false);
    expect(isEphemeralActivity({ ...event, metadata_json: { project_name: "api" } })).toBe(false);
  });
});
