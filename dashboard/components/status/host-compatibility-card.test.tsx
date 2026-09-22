import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompatibilityCard, ProbeCapabilityCard } from "./host-compatibility-card";
import type { HostOut } from "@/lib/types/api";

function makeHost(overrides: Partial<HostOut> = {}): HostOut {
  return {
    id: "host-1",
    hostname: "NTMKEYA",
    display_name: null,
    operating_system: "windows",
    os_version: null,
    architecture: null,
    agent_version: "1.0.0",
    docker_available: true,
    first_seen: "2026-01-01T00:00:00Z",
    last_seen: "2026-01-01T00:00:00Z",
    status: "online",
    ...overrides,
  };
}

describe("CompatibilityCard", () => {
  it("shows Compatible for a matching protocol version", () => {
    render(<CompatibilityCard host={makeHost({ protocol_version: 1, protocol_compatibility: "compatible" })} />);
    expect(screen.getByText("Compatible")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("shows a mismatch warning without implying the host is broken (task Sec7)", () => {
    render(<CompatibilityCard host={makeHost({ protocol_version: 2, protocol_compatibility: "warning" })} />);
    expect(screen.getByText("Version mismatch")).toBeInTheDocument();
    expect(screen.getByText(/never blocked/)).toBeInTheDocument();
  });

  it("shows a legacy agent's missing protocol metadata honestly as unknown, not as an error", () => {
    render(<CompatibilityCard host={makeHost({ protocol_version: null, protocol_compatibility: "unknown" })} />);
    expect(screen.getByText("Unknown / legacy")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument(); // protocol version row shows "unknown", not blank/error
    expect(screen.getByText(/Not an error/)).toBeInTheDocument();
  });

  it("defaults to unknown when protocol_compatibility is entirely absent from the payload", () => {
    render(<CompatibilityCard host={makeHost({ protocol_compatibility: undefined })} />);
    expect(screen.getByText("Unknown / legacy")).toBeInTheDocument();
  });
});

describe("ProbeCapabilityCard", () => {
  it("shows Supported when the host has answered a probe before", () => {
    render(<ProbeCapabilityCard capability="supported" />);
    expect(screen.getByText("Supported")).toBeInTheDocument();
  });

  it("shows an honest unknown state that distinguishes never-probed from legacy, rather than guessing", () => {
    render(<ProbeCapabilityCard capability="unknown" />);
    expect(screen.getByText("Unknown / not yet probed")).toBeInTheDocument();
    expect(screen.getByText(/cannot be distinguished from data alone/)).toBeInTheDocument();
  });

  it("shows an offline-unavailable state distinct from unsupported", () => {
    render(<ProbeCapabilityCard capability="unavailable_offline" />);
    expect(screen.getByText(/Unavailable/)).toBeInTheDocument();
  });
});
