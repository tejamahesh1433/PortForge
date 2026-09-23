import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReactivateHostDialog } from "./reactivate-host-dialog";
import type { HostOut } from "@/lib/types/api";

const reactivateHost = vi.fn();
const toastAdd = vi.fn();

vi.mock("@/lib/api/resources", () => ({
  reactivateHost: (...args: unknown[]) => reactivateHost(...args),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: (...args: unknown[]) => toastAdd(...args) },
}));

const decommissionedHost: HostOut = {
  id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  hostname: "lab-mac",
  display_name: null,
  operating_system: "darwin",
  os_version: null,
  architecture: null,
  agent_version: null,
  docker_available: false,
  first_seen: "2026-09-01T00:00:00Z",
  last_seen: new Date(Date.now() - 900_000).toISOString(),
  status: "online",
  health_state: "OFFLINE",
  age_seconds: 900,
  lifecycle_state: "DECOMMISSIONED",
  decommissioned_at: "2026-09-23T10:00:00Z",
  decommission_reason: "Hardware retired",
};

function renderDialog(host: HostOut = decommissionedHost) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReactivateHostDialog host={host} />
    </QueryClientProvider>,
  );
}

describe("ReactivateHostDialog", () => {
  beforeEach(() => {
    reactivateHost.mockReset();
    toastAdd.mockReset();
  });

  it("opens and shows confirmation copy about enrollment and credential", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /reactivate/i }));

    expect(screen.getByRole("heading", { name: /reactivate host/i })).toBeInTheDocument();
    expect(screen.getByText(/Enrollment allowed again/i)).toBeInTheDocument();
    expect(screen.getByText(/Old credential stays invalid/i)).toBeInTheDocument();
    expect(screen.getByText(/fresh enrollment/i)).toBeInTheDocument();
  });

  it("cancel closes without calling reactivateHost", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /reactivate/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(reactivateHost).not.toHaveBeenCalled();
  });

  it("success calls reactivateHost and shows toast", async () => {
    reactivateHost.mockResolvedValue({ ...decommissionedHost, lifecycle_state: "ACTIVE" });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /reactivate/i }));
    await user.click(screen.getByRole("button", { name: /^reactivate host$/i }));

    await waitFor(() => {
      expect(reactivateHost).toHaveBeenCalledWith(decommissionedHost.id);
    });
    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "Host reactivated" }),
      );
    });
  });

  it("shows error toast on failure", async () => {
    reactivateHost.mockRejectedValue(new Error("Not found."));
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /reactivate/i }));
    await user.click(screen.getByRole("button", { name: /^reactivate host$/i }));

    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error", title: "Could not reactivate host" }),
      );
    });
  });
});
