import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HostStatus } from "./host-status";

describe("HostStatus", () => {
  it("renders a legacy host payload without health_state", () => {
    render(<HostStatus host={{ last_seen: new Date().toISOString() }} />);
    expect(screen.getByText("Healthy")).toBeInTheDocument();
  });

  it("prefers the health state supplied by Central", () => {
    render(<HostStatus host={{ last_seen: new Date().toISOString(), health_state: "DEGRADED" }} />);
    expect(screen.getByText("Degraded")).toBeInTheDocument();
  });
});