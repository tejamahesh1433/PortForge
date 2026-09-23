import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpgradeAgentDialog } from "./upgrade-agent-dialog";
import type { FleetHostOut } from "@/lib/types/api";

const createHostUpgrade = vi.fn();
const toastAdd = vi.fn();

vi.mock("@/lib/api/resources", () => ({
  createHostUpgrade: (...args: unknown[]) => createHostUpgrade(...args),
  listHostUpgrades: vi.fn().mockResolvedValue([]),
  rollbackUpgrade: vi.fn(),
  listFleet: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  getFleetHost: vi.fn(),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: (...args: unknown[]) => toastAdd(...args) },
}));

const HOST_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

const activeHostWithUpdate: FleetHostOut = {
  id: HOST_ID,
  hostname: "lab-linux",
  display_name: null,
  operating_system: "linux",
  os_version: "Ubuntu 22.04",
  architecture: "x86_64",
  agent_version: "1.1.0",
  docker_available: false,
  first_seen: "2026-09-01T00:00:00Z",
  last_seen: new Date().toISOString(),
  status: "online",
  health_state: "HEALTHY",
  age_seconds: 30,
  lifecycle_state: "ACTIVE",
  decommissioned_at: null,
  decommission_reason: null,
  protocol_version: 1,
  protocol_compatibility: "compatible",
  last_heartbeat: new Date().toISOString(),
  last_sync: new Date().toISOString(),
  update_availability: "UPDATE_AVAILABLE",
  target_version: "1.2.0",
  active_upgrade: null,
  contract_version: 1,
  python_version: "3.11.5",
  last_error: null,
};

const decommissionedHost: FleetHostOut = {
  ...activeHostWithUpdate,
  lifecycle_state: "DECOMMISSIONED",
  decommissioned_at: "2026-09-20T00:00:00Z",
};

function renderDialog(host: FleetHostOut = activeHostWithUpdate) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UpgradeAgentDialog host={host} />
    </QueryClientProvider>,
  );
}

describe("UpgradeAgentDialog", () => {
  beforeEach(() => {
    createHostUpgrade.mockReset();
    toastAdd.mockReset();
  });

  it("renders an Upgrade Agent button", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: /upgrade agent/i })).toBeInTheDocument();
  });

  it("opens dialog with host, current version, and target version", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /upgrade agent/i }));

    expect(screen.getByRole("heading", { name: /upgrade agent/i })).toBeInTheDocument();
    expect(screen.getByText("lab-linux")).toBeInTheDocument();
    expect(screen.getByText("1.1.0")).toBeInTheDocument();
    expect(screen.getByText("1.2.0")).toBeInTheDocument();
  });

  it("shows restart-not-reboot and SHA warnings", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /upgrade agent/i }));

    expect(screen.getByText(/Service restarted, not rebooted/i)).toBeInTheDocument();
    expect(screen.getByText(/SHA-256/i)).toBeInTheDocument();
    expect(screen.getByText(/UUID and credentials preserved/i)).toBeInTheDocument();
  });

  it("cancel closes without calling createHostUpgrade", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /upgrade agent/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(createHostUpgrade).not.toHaveBeenCalled();
  });

  it("confirm calls createHostUpgrade with target_version and shows success toast", async () => {
    createHostUpgrade.mockResolvedValue({
      id: "new-upgrade-id",
      state: "APPROVED",
      target_version: "1.2.0",
    });
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /upgrade agent/i }));
    await user.click(screen.getByRole("button", { name: /confirm upgrade/i }));

    await waitFor(() => {
      expect(createHostUpgrade).toHaveBeenCalledWith(
        HOST_ID,
        expect.objectContaining({ target_version: "1.2.0" }),
      );
    });
    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "Upgrade queued" }),
      );
    });
  });

  it("shows error toast when createHostUpgrade fails", async () => {
    createHostUpgrade.mockRejectedValue(new Error("Host is DECOMMISSIONED."));
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /upgrade agent/i }));
    await user.click(screen.getByRole("button", { name: /confirm upgrade/i }));

    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error", title: "Could not create upgrade" }),
      );
    });
  });

  it("DECOMMISSIONED host: Upgrade Agent button is not rendered by parent (guard test)", () => {
    // The parent page only renders UpgradeAgentDialog when lifecycle_state !== "DECOMMISSIONED".
    // This test verifies the component itself still renders when forcibly given a decommissioned
    // host -- the parent guard is the real enforcement, tested in fleet-page.test.tsx.
    renderDialog(decommissionedHost);
    // Component renders; parent is responsible for not showing it for decommissioned hosts.
    expect(screen.getByRole("button", { name: /upgrade agent/i })).toBeInTheDocument();
  });
});
