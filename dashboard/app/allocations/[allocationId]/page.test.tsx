import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AllocationDetailPage from "./page";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mutate, toastAdd, mockUseAllocation, mockUseReleaseAllocation, mockUseVerifyAllocation } = vi.hoisted(() => ({
  mutate: vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()),
  toastAdd: vi.fn(),
  mockUseAllocation: vi.fn(),
  mockUseReleaseAllocation: vi.fn(),
  mockUseVerifyAllocation: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ allocationId: "alloc-1" }),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: toastAdd },
}));

vi.mock("@/hooks/use-allocations", () => ({
  useAllocation: (...args: unknown[]) => mockUseAllocation(...args),
  useReleaseAllocation: (...args: unknown[]) => mockUseReleaseAllocation(...args),
  useVerifyAllocation: (...args: unknown[]) => mockUseVerifyAllocation(...args),
}));

function mockAllocation(overrides: Partial<Record<string, unknown>> = {}) {
  return {
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
        bind_address: "0.0.0.0",
        bind_probe: "verified_free",
      },
    ],
    validation: { snapshot_age_seconds: 5, host_health_state: "HEALTHY", bind_probe: "verified_free" },
    created_at: "2026-09-22T00:00:00Z",
    released_at: null,
    request_id: "req-abc",
    ...overrides,
  };
}

function renderDetail() {
  return render(
    <TooltipProvider>
      <AllocationDetailPage />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  mutate.mockClear();
  toastAdd.mockClear();
  mockUseReleaseAllocation.mockReturnValue({ mutate, isPending: false });
  mockUseVerifyAllocation.mockReturnValue({ mutate, isPending: false });
});

describe("AllocationDetailPage", () => {
  it("renders identity, host link, request id, and per-binding probe evidence", () => {
    mockUseAllocation.mockReturnValue({ isPending: false, isError: false, data: mockAllocation(), refetch: vi.fn() });
    renderDetail();

    expect(screen.getByRole("heading", { name: "jarvis" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "NTMKEYA" })).toHaveAttribute("href", "/hosts/host-1");
    expect(screen.getByText("req-abc")).toBeInTheDocument();
    expect(screen.getAllByText("Verified free").length).toBeGreaterThan(0);
    // The TOCTOU-honest explanation is visible as static text, not only in a tooltip.
    expect(screen.getByText(/can still bind it afterward/)).toBeInTheDocument();
  });

  it("distinguishes an allocation from a currently-observed listening process", () => {
    mockUseAllocation.mockReturnValue({ isPending: false, isError: false, data: mockAllocation(), refetch: vi.fn() });
    renderDetail();
    expect(screen.getByText(/does not, by itself, prove any application is currently listening/)).toBeInTheDocument();
  });

  it("explains why a released allocation shows no bindings, rather than an unexplained empty table", () => {
    mockUseAllocation.mockReturnValue({
      isPending: false,
      isError: false,
      data: mockAllocation({ status: "released", allocations: [], released_at: "2026-09-22T01:00:00Z" }),
      refetch: vi.fn(),
    });
    renderDetail();
    expect(screen.getByText(/resolved live against current reservations, which no longer exist/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Release allocation" })).not.toBeInTheDocument();
  });

  it("documents that workflow/config-mutation state is local to the project host, not fabricated", () => {
    mockUseAllocation.mockReturnValue({ isPending: false, isError: false, data: mockAllocation(), refetch: vi.fn() });
    renderDetail();
    expect(screen.getByText(/Workflow and config-mutation state is local to the project host/)).toBeInTheDocument();
    expect(screen.getByText(/portforge workflow status --request-id/)).toBeInTheDocument();
  });

  it("can explicitly refresh verification without releasing", async () => {
    mockUseAllocation.mockReturnValue({ isPending: false, isError: false, data: mockAllocation(), refetch: vi.fn() });
    renderDetail();
    await userEvent.click(screen.getByRole("button", { name: "Verify allocation" }));
    expect(mutate).toHaveBeenCalledWith("alloc-1", expect.objectContaining({ onSuccess: expect.any(Function) }));
  });

  it("release requires confirmation and shows a success toast", async () => {
    mockUseAllocation.mockReturnValue({ isPending: false, isError: false, data: mockAllocation(), refetch: vi.fn() });
    renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /release allocation/i }));
    expect(screen.getByText("Release allocation?")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Release" }));
    expect(mutate).toHaveBeenCalledWith("alloc-1", expect.objectContaining({ onSuccess: expect.any(Function) }));
    expect(toastAdd).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
  });

  it("shows an error state on fetch failure, not a blank page", () => {
    mockUseAllocation.mockReturnValue({
      isPending: false,
      isError: true,
      error: new Error("not found"),
      data: undefined,
      refetch: vi.fn(),
    });
    renderDetail();
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });
});
