import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CheckAgainButton } from "./check-again-button";

vi.mock("@/lib/api/resources", () => ({
  getHost: vi.fn(),
  listHosts: vi.fn(),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: vi.fn() },
}));

import { getHost } from "@/lib/api/resources";
import { toast } from "@/components/ui/toast";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("CheckAgainButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders Recheck status label", () => {
    renderWithClient(<CheckAgainButton hostId="h1" hostname="box" />);
    const button = screen.getByRole("button", { name: /recheck status/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute(
      "title",
      "Refresh the latest host status from PortForge Central.",
    );
  });

  it("refreshes Central host data only (no remote agent action)", async () => {
    vi.mocked(getHost).mockResolvedValue({
      id: "h1",
      hostname: "box",
      last_seen: new Date().toISOString(),
      health_state: "HEALTHY",
      operating_system: "linux",
      docker_available: false,
      first_seen: new Date().toISOString(),
      status: "online",
    } as never);

    renderWithClient(<CheckAgainButton hostId="h1" hostname="box" />);
    screen.getByRole("button", { name: /recheck status/i }).click();

    await vi.waitFor(() => {
      expect(getHost).toHaveBeenCalledWith("h1", expect.anything());
      expect(toast.add).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "box is healthy" }),
      );
    });
  });

  it("reports failure accurately when Central cannot be reached", async () => {
    vi.mocked(getHost).mockRejectedValue(new Error("Central unavailable"));

    renderWithClient(<CheckAgainButton hostId="h1" hostname="box" />);
    screen.getByRole("button", { name: /recheck status/i }).click();

    await vi.waitFor(() => {
      expect(toast.add).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "error",
          title: "Could not recheck status",
          description: "Central unavailable",
        }),
      );
    });
  });
});
