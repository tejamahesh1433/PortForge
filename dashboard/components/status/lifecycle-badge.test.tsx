import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LifecycleBadge } from "./lifecycle-badge";

describe("LifecycleBadge", () => {
  it("renders ACTIVE for an explicit ACTIVE state", () => {
    render(<LifecycleBadge state="ACTIVE" />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders ACTIVE when state is undefined (back-compat with older Central)", () => {
    render(<LifecycleBadge />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders ACTIVE when state is null", () => {
    render(<LifecycleBadge state={null} />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders DECOMMISSIONED for explicit DECOMMISSIONED state", () => {
    render(<LifecycleBadge state="DECOMMISSIONED" />);
    expect(screen.getByText("Decommissioned")).toBeInTheDocument();
  });

  it("DECOMMISSIONED and ACTIVE badges are visually distinct (different classes)", () => {
    const { rerender } = render(<LifecycleBadge state="ACTIVE" />);
    const active = screen.getByText("Active").closest("span");
    const activeClass = active?.className ?? "";

    rerender(<LifecycleBadge state="DECOMMISSIONED" />);
    const decommissioned = screen.getByText("Decommissioned").closest("span");
    const decommClass = decommissioned?.className ?? "";

    expect(activeClass).not.toBe(decommClass);
  });
});
