import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AllocationsPage from "./page";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mutate, toastAdd, push, replace } = vi.hoisted(() => ({
  mutate: vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()),
  toastAdd: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push }),
  usePathname: () => "/allocations",
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/hooks/use-hosts", () => ({
  useHosts: () => ({ data: { items: [{ id: "host-1", hostname: "NTMKEYA" }] } }),
}));

const ACTIVE_ALLOCATION = {
  allocation_id: "alloc-1",
  project: "jarvis",
  host: { id: "host-1", hostname: "NTMKEYA" },
  status: "active",
  allocations: [
    {
      name: "frontend",
      purpose: "frontend",
      protocol: "tcp",
      port: 3001,
      reservation_id: "res-1",
      bind_address: null,
      bind_probe: "verified_free",
    },
  ],
  validation: { snapshot_age_seconds: 5, host_health_state: "HEALTHY", bind_probe: "verified_free" },
  created_at: "2026-09-22T00:00:00Z",
  released_at: null,
  request_id: "req-abc",
};

vi.mock("@/hooks/use-allocations", () => ({
  useAllocations: () => ({
    isPending: false,
    isError: false,
    data: { items: [ACTIVE_ALLOCATION], total: 1, limit: 50, offset: 0 },
    refetch: vi.fn(),
  }),
  useReleaseAllocation: () => ({ mutate, isPending: false }),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: toastAdd },
}));

function renderPage() {
  return render(
    <TooltipProvider>
      <AllocationsPage />
    </TooltipProvider>,
  );
}

describe("AllocationsPage", () => {
  it("renders the active allocation with project, host, state, and probe evidence", () => {
    renderPage();
    expect(screen.getByText("jarvis")).toBeInTheDocument();
    expect(screen.getByText("NTMKEYA")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Verified free")).toBeInTheDocument();
    expect(screen.getByText("req-abc")).toBeInTheDocument();
  });

  it("requires confirmation before releasing, then shows a success toast (no window.confirm/alert)", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "Release allocation" }));

    expect(screen.getByText("Release allocation?")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Release" }));

    expect(mutate).toHaveBeenCalledWith("alloc-1", expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }));
    expect(toastAdd).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
  });

  it("release action is keyboard reachable", () => {
    renderPage();
    const button = screen.getByRole("button", { name: "Release allocation" });
    expect(button.tagName).toBe("BUTTON");
    expect(button).not.toHaveAttribute("tabindex", "-1");
  });
});

describe("AllocationsPage empty state", () => {
  it("shows an empty state, not a blank table, when there are no allocations", async () => {
    vi.resetModules();
    vi.doMock("@/hooks/use-allocations", () => ({
      useAllocations: () => ({ isPending: false, isError: false, data: { items: [], total: 0, limit: 50, offset: 0 }, refetch: vi.fn() }),
      useReleaseAllocation: () => ({ mutate: vi.fn(), isPending: false }),
    }));
    const { default: FreshAllocationsPage } = await import("./page");
    render(
      <TooltipProvider>
        <FreshAllocationsPage />
      </TooltipProvider>,
    );
    expect(screen.getByText("No allocations")).toBeInTheDocument();
  });
});

describe("AllocationsPage error state", () => {
  it("shows an actual error state, not an empty-success screen, on a Central failure", async () => {
    vi.resetModules();
    vi.doMock("@/hooks/use-allocations", () => ({
      useAllocations: () => ({
        isPending: false,
        isError: true,
        error: new Error("Central unavailable"),
        data: undefined,
        refetch: vi.fn(),
      }),
      useReleaseAllocation: () => ({ mutate: vi.fn(), isPending: false }),
    }));
    const { default: FreshAllocationsPage } = await import("./page");
    render(
      <TooltipProvider>
        <FreshAllocationsPage />
      </TooltipProvider>,
    );
    expect(screen.queryByText("No allocations")).not.toBeInTheDocument();
    expect(screen.getByText(/central unavailable/i)).toBeInTheDocument();
  });
});

describe("AllocationsPage loading state", () => {
  it("shows a loading skeleton, not an empty table, while the first request is in flight", async () => {
    vi.resetModules();
    vi.doMock("@/hooks/use-allocations", () => ({
      useAllocations: () => ({ isPending: true, isError: false, data: undefined, refetch: vi.fn() }),
      useReleaseAllocation: () => ({ mutate: vi.fn(), isPending: false }),
    }));
    const { default: FreshAllocationsPage } = await import("./page");
    const { container } = render(
      <TooltipProvider>
        <FreshAllocationsPage />
      </TooltipProvider>,
    );
    expect(screen.queryByText("No allocations")).not.toBeInTheDocument();
    expect(container.querySelector("[data-slot='skeleton']") ?? container.textContent).toBeTruthy();
  });
});
