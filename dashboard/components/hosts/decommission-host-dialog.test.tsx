import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DecommissionHostDialog } from "./decommission-host-dialog";
import type { HostOut } from "@/lib/types/api";

const decommissionHost = vi.fn();
const toastAdd = vi.fn();

vi.mock("@/lib/api/resources", () => ({
  decommissionHost: (...args: unknown[]) => decommissionHost(...args),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: (...args: unknown[]) => toastAdd(...args) },
}));

const healthyHost: HostOut = {
  id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  hostname: "lab-mac",
  display_name: null,
  operating_system: "darwin",
  os_version: null,
  architecture: null,
  agent_version: null,
  docker_available: false,
  first_seen: "2026-09-01T00:00:00Z",
  last_seen: new Date().toISOString(),
  status: "online",
  health_state: "HEALTHY",
  age_seconds: 30,
};

const offlineHost: HostOut = {
  ...healthyHost,
  health_state: "OFFLINE",
  age_seconds: 900,
  last_seen: new Date(Date.now() - 900_000).toISOString(),
};

function renderDialog(host: HostOut = healthyHost) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DecommissionHostDialog host={host} />
    </QueryClientProvider>,
  );
}

describe("DecommissionHostDialog", () => {
  beforeEach(() => {
    decommissionHost.mockReset();
    toastAdd.mockReset();
  });

  it("opens and shows key decommission warnings", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));

    expect(screen.getByRole("heading", { name: /decommission host/i })).toBeInTheDocument();
    expect(screen.getByText(/Credential invalidated/i)).toBeInTheDocument();
    expect(screen.getByText(/Tombstone retained/i)).toBeInTheDocument();
    expect(screen.getByText(/Remote agent NOT stopped/i)).toBeInTheDocument();
    expect(screen.getByText(/Re-enrollment rejected/i)).toBeInTheDocument();
  });

  it("shows stronger running-host warning for healthy / recently active host", async () => {
    const user = userEvent.setup();
    renderDialog(healthyHost);

    await user.click(screen.getByRole("button", { name: /decommission/i }));

    expect(screen.getByText(/This host appears to still be running/i)).toBeInTheDocument();
    expect(screen.getByText(/Decommissioning a recently active host/i)).toBeInTheDocument();
  });

  it("does NOT show the running-host warning for an offline host", async () => {
    const user = userEvent.setup();
    renderDialog(offlineHost);

    await user.click(screen.getByRole("button", { name: /decommission/i }));

    expect(screen.queryByText(/This host appears to still be running/i)).not.toBeInTheDocument();
  });

  it("keeps Decommission button disabled until exact hostname is typed", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));

    const confirmBtn = screen.getByRole("button", { name: /^decommission host$/i });
    expect(confirmBtn).toBeDisabled();

    await user.type(screen.getByLabelText(/exact hostname/i), "wrong");
    expect(confirmBtn).toBeDisabled();

    await user.clear(screen.getByLabelText(/exact hostname/i));
    await user.type(screen.getByLabelText(/exact hostname/i), "Lab-Mac");
    expect(confirmBtn).toBeDisabled();

    await user.clear(screen.getByLabelText(/exact hostname/i));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    expect(confirmBtn).toBeEnabled();
  });

  it("cancel closes without calling decommissionHost", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(decommissionHost).not.toHaveBeenCalled();
  });

  it("success with no reason calls decommissionHost with empty body and shows toast", async () => {
    decommissionHost.mockResolvedValue({ ...healthyHost, lifecycle_state: "DECOMMISSIONED" });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /^decommission host$/i }));

    await waitFor(() => {
      expect(decommissionHost).toHaveBeenCalledWith(healthyHost.id, {});
    });
    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "Host decommissioned" }),
      );
    });
  });

  it("success with reason passes reason through", async () => {
    decommissionHost.mockResolvedValue({ ...healthyHost, lifecycle_state: "DECOMMISSIONED" });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));
    await user.type(screen.getByLabelText(/^reason/i), "Hardware retired");
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /^decommission host$/i }));

    await waitFor(() => {
      expect(decommissionHost).toHaveBeenCalledWith(healthyHost.id, { reason: "Hardware retired" });
    });
  });

  it("shows error toast on failure without navigating", async () => {
    decommissionHost.mockRejectedValue(new Error("Conflict."));
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /decommission/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /^decommission host$/i }));

    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error", title: "Could not decommission host" }),
      );
    });
  });
});
