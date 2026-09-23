import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  hostnameConfirmationMatches,
  RemoveHostDialog,
} from "./remove-host-dialog";
import type { HostOut } from "@/lib/types/api";

const deleteHost = vi.fn();
const push = vi.fn();
const toastAdd = vi.fn();

vi.mock("@/lib/api/resources", () => ({
  deleteHost: (...args: unknown[]) => deleteHost(...args),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: (...args: unknown[]) => toastAdd(...args) },
}));

vi.mock("@/hooks/use-allocations", () => ({
  useAllocations: () => ({
    isSuccess: true,
    data: {
      items: [
        { id: "a1", status: "active" },
        { id: "a2", status: "active" },
        { id: "a3", status: "released" },
      ],
      total: 3,
    },
  }),
}));

vi.mock("@/hooks/use-reservations", () => ({
  useReservations: () => ({
    isSuccess: true,
    data: { items: [{ id: "r1" }], total: 1 },
  }),
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

function renderDialog(host: HostOut = healthyHost) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RemoveHostDialog host={host} />
    </QueryClientProvider>,
  );
}

describe("hostnameConfirmationMatches", () => {
  it("trims ends and compares case-sensitively", () => {
    expect(hostnameConfirmationMatches("lab-mac", "lab-mac")).toBe(true);
    expect(hostnameConfirmationMatches("lab-mac", "  lab-mac  ")).toBe(true);
    expect(hostnameConfirmationMatches("lab-mac", "Lab-Mac")).toBe(false);
    expect(hostnameConfirmationMatches("lab-mac", "lab-mac ")).toBe(true);
    expect(hostnameConfirmationMatches("lab-mac", "other")).toBe(false);
  });
});

describe("RemoveHostDialog", () => {
  beforeEach(() => {
    deleteHost.mockReset();
    push.mockReset();
    toastAdd.mockReset();
  });

  it("opens with Remove record terminology and remote-agent warning", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: /^remove record$/i }));

    expect(screen.getByRole("heading", { name: /remove record/i })).toBeInTheDocument();
    expect(
      screen.getByText(/does not stop or uninstall the PortForge agent/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/existing credential will stop working/i)).toBeInTheDocument();
    expect(screen.queryByText(/decommission/i)).not.toBeInTheDocument();
  });

  it("shows healthy / still-running warning", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));
    expect(screen.getByText(/This host appears to still be running/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Removing its Central record does not stop the remote PortForge agent/i),
    ).toBeInTheDocument();
  });

  it("shows active allocation and reservation counts", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));
    expect(
      screen.getByText(/2 active allocations and 1 reservation/i),
    ).toBeInTheDocument();
  });

  it("keeps Remove record disabled until exact hostname is typed", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));

    const confirm = screen.getByRole("button", { name: /^remove record$/i });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText(/exact hostname/i), "wrong");
    expect(confirm).toBeDisabled();

    await user.clear(screen.getByLabelText(/exact hostname/i));
    await user.type(screen.getByLabelText(/exact hostname/i), "Lab-Mac");
    expect(confirm).toBeDisabled();

    await user.clear(screen.getByLabelText(/exact hostname/i));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    expect(confirm).toBeEnabled();
  });

  it("cancel closes without calling deleteHost", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(deleteHost).not.toHaveBeenCalled();
  });

  it("success calls deleteHost, toasts, and navigates away", async () => {
    deleteHost.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));

    await waitFor(() => {
      expect(deleteHost).toHaveBeenCalledWith(healthyHost.id);
    });
    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "success",
          title: "Host record removed",
        }),
      );
      expect(push).toHaveBeenCalledWith("/hosts");
    });
  });

  it("failure stays on page and shows an error without navigating", async () => {
    deleteHost.mockRejectedValue(new Error("Host not found."));
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));
    await user.type(screen.getByLabelText(/exact hostname/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /^remove record$/i }));

    await waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "error",
          title: "Could not remove host record",
          description: "Host not found.",
        }),
      );
    });
    expect(push).not.toHaveBeenCalled();
  });
});
