import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FreshnessWarning } from "./freshness-warning";

describe("FreshnessWarning", () => {
  it("explains that offline data is last known", () => {
    render(<FreshnessWarning host={{ hostname: "macbook", health_state: "OFFLINE", last_seen: "2026-09-18T10:00:00Z" }} />);
    expect(screen.getByText("Last-known data from macbook")).toBeInTheDocument();
    expect(screen.getByText(/retained history and may no longer be current/)).toBeInTheDocument();
  });

  it("renders nothing for a healthy host", () => {
    const { container } = render(<FreshnessWarning host={{ hostname: "server", health_state: "HEALTHY", last_seen: "2026-09-18T10:00:00Z" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
