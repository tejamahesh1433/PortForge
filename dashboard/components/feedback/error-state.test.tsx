import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PortForgeApiError, PortForgeConnectionError } from "@/lib/api/client";
import { ErrorState } from "./error-state";

describe("ErrorState", () => {
  it("shows Central's own error detail for a PortForgeApiError", () => {
    const error = new PortForgeApiError(404, "Host not found.", "Host not found.");
    render(<ErrorState error={error} />);
    expect(screen.getByText(/Central error \(404\): Host not found\./)).toBeInTheDocument();
  });

  it("shows an unreachable message for a PortForgeConnectionError", () => {
    const error = new PortForgeConnectionError(new Error("network down"));
    render(<ErrorState error={error} />);
    expect(screen.getByText(/Could not reach Central/)).toBeInTheDocument();
  });

  it("falls back to a generic message for a non-Error thrown value", () => {
    render(<ErrorState error={"boom"} />);
    expect(screen.getByText(/unexpected error/i)).toBeInTheDocument();
  });

  it("calls onRetry when the retry button is clicked", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(<ErrorState error={new Error("failed")} onRetry={onRetry} />);

    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("omits the retry button when onRetry is not provided", () => {
    render(<ErrorState error={new Error("failed")} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
