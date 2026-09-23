import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { FreshnessWarning } from "./freshness-warning";

function renderWarning(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("FreshnessWarning", () => {
  it("explains that offline data is last known and offers Recheck status", () => {
    renderWarning(
      <FreshnessWarning
        host={{
          id: "h1",
          hostname: "macbook",
          health_state: "OFFLINE",
          last_seen: "2026-09-18T10:00:00Z",
        }}
      />,
    );
    expect(screen.getByText("Last-known data from macbook")).toBeInTheDocument();
    expect(screen.getByText(/retained history and may no longer be current/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recheck status/i })).toBeInTheDocument();
  });

  it("renders nothing for a healthy host", () => {
    const { container } = renderWarning(
      <FreshnessWarning
        host={{
          id: "h2",
          hostname: "server",
          health_state: "HEALTHY",
          last_seen: "2026-09-18T10:00:00Z",
        }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
