import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./status-badge";

describe("StatusBadge", () => {
  it("renders the label for a known value", () => {
    render(<StatusBadge value="ACTIVE" />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders the raw value for an unrecognized status rather than hiding it", () => {
    render(<StatusBadge value="some_future_state" />);
    expect(screen.getByText("some_future_state")).toBeInTheDocument();
  });

  it("in compact mode still exposes the label to assistive tech", () => {
    render(<StatusBadge value="docker" compact />);
    expect(screen.getByText("Docker")).toHaveClass("sr-only");
  });
});
