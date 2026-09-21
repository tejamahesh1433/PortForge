import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ReservationsPage from "./page";

const { mutate, toastAdd } = vi.hoisted(() => ({
  mutate: vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()),
  toastAdd: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/reservations",
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/hooks/use-hosts", () => ({
  useHosts: () => ({ data: { items: [{ id: "host-1", hostname: "NTMKEYA" }] } }),
}));

vi.mock("@/hooks/use-reservations", () => ({
  useReservations: () => ({
    isPending: false,
    isError: false,
    data: {
      items: [
        {
          id: "res-1",
          host_id: "host-1",
          port: 8080,
          protocol: "tcp",
          bind_address: "0.0.0.0",
          project: "ocrforge",
          service: null,
          purpose: null,
          updated_at: "2026-09-18T00:00:00Z",
        },
      ],
      total: 1,
      limit: 100,
      offset: 0,
    },
  }),
  useDeleteDashboardReservation: () => ({ mutate, isPending: false }),
}));

vi.mock("@/components/forms/reservation-modal", () => ({
  ReservationModal: () => <div data-testid="reservation-modal-stub" />,
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: toastAdd },
}));

describe("ReservationsPage release flow", () => {
  it("requires confirmation before releasing, then shows a success toast instead of a bare alert", async () => {
    render(<ReservationsPage />);

    await userEvent.click(screen.getByRole("button", { name: "Release reservation" }));

    expect(screen.getByText("Release reservation?")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Release" }));

    expect(mutate).toHaveBeenCalledWith(
      { hostId: "host-1", reservationId: "res-1" },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
    expect(toastAdd).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
  });
});
