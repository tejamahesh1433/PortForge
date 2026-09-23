import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AddHostDialog } from "./add-host-dialog";

const mutate = vi.fn();

vi.mock("@/hooks/use-enrollment", () => ({
  useMintEnrollmentToken: () => ({
    mutate,
    isPending: false,
  }),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: vi.fn() },
}));

describe("AddHostDialog", () => {
  beforeEach(() => {
    mutate.mockReset();
  });

  it("opens and mints an enrollment token with optional label", async () => {
    const { userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(<AddHostDialog />);

    await user.click(screen.getByRole("button", { name: /add host/i }));
    expect(screen.getByText(/mint a one-time enrollment token/i)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText(/macbook/i), "lab-mac");
    await user.click(screen.getByRole("button", { name: /generate token/i }));

    expect(mutate).toHaveBeenCalledWith(
      { label: "lab-mac", ttl_hours: 24 },
      expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
    );
  });

  it("shows enroll command after a successful mint", async () => {
    const { userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    mutate.mockImplementation((_input, options) => {
      options?.onSuccess?.({
        enrollment_token: "enroll-test-token",
        expires_at: "2026-09-24T00:00:00+00:00",
      });
    });

    render(<AddHostDialog />);
    await user.click(screen.getByRole("button", { name: /add host/i }));
    await user.click(screen.getByRole("button", { name: /generate token/i }));

    expect(screen.getByText("enroll-test-token")).toBeInTheDocument();
    expect(
      screen.getByText(/portforge agent enroll --server http:\/\/localhost:58000 --token "enroll-test-token"/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/On the new machine — install the agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Enroll with this token/i)).toBeInTheDocument();
    expect(screen.getByText(/Restart the service and verify/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirm in this dashboard/i)).toBeInTheDocument();
  });
});
