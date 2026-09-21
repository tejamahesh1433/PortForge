import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HostGrid } from "./host-grid";
import type { HostOut } from "@/lib/types/api";

const host: HostOut = {
  id: "h1",
  hostname: "lenovoserver",
  display_name: null,
  operating_system: "linux",
  os_version: null,
  architecture: null,
  agent_version: "0.1.0",
  docker_available: true,
  first_seen: "2026-09-17T00:00:00Z",
  last_seen: "2026-09-18T00:00:00Z",
  status: "online",
  health_state: "HEALTHY",
};

describe("HostGrid empty states", () => {
  it("renders host cards when hosts are present", () => {
    render(<HostGrid hosts={[host]} />);
    expect(screen.getByText("lenovoserver")).toBeInTheDocument();
  });

  it("shows the genuinely-empty message when Central has zero hosts (filtered=false)", () => {
    render(<HostGrid hosts={[]} />);
    expect(screen.getByText("No hosts enrolled yet")).toBeInTheDocument();
  });

  it("shows a distinct filtered-to-zero message instead of implying Central has no hosts, when filtered=true", () => {
    render(<HostGrid hosts={[]} filtered />);
    expect(screen.getByText("No hosts match your filters")).toBeInTheDocument();
    expect(screen.queryByText("No hosts enrolled yet")).not.toBeInTheDocument();
  });
});
