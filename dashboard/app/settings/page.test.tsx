import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SettingsPage from "./page";

vi.mock("@/hooks/use-health", () => ({
  useHealth: () => ({
    isPending: false,
    isError: false,
    data: { status: "ok", database: "connected", version: "0.1.0" },
    refetch: vi.fn(),
  }),
}));

describe("SettingsPage", () => {
  it("shows Central connection info but no obsolete admin bootstrap-token UI", () => {
    render(<SettingsPage />);

    expect(screen.getByText("Central connection")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();

    // Phase 7C.4 acceptance: PortForge runs as a trusted private/LAN
    // control plane with no login/session/token architecture -- the
    // dashboard-write admin bootstrap-token field was removed along with
    // the backend admin-credential requirement it existed to satisfy.
    expect(screen.queryByText("Admin Access")).not.toBeInTheDocument();
    expect(screen.queryByText(/bootstrap token/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/bootstrap token/i)).not.toBeInTheDocument();
  });
});
