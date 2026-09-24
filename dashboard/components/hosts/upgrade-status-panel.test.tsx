/**
 * UpgradeStatusPanelContent rendering tests.
 *
 * We test UpgradeStatusPanelContent directly (accepts a pre-fetched
 * UpgradeStatusOut) rather than UpgradeStatusPanel (which fetches its own
 * status) so we don't need to mock the query layer for the core rendering
 * cases. The async fetch variant is covered by its hook (use-upgrades.ts)
 * and BFF route tests.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpgradeStatusPanelContent } from "./upgrade-status-panel";
import type { UpgradeStatusOut } from "@/lib/types/api";

const retryUpgrade = vi.fn();
const cancelUpgrade = vi.fn();
const toastAdd = vi.fn();

vi.mock("@/lib/api/resources", () => ({
  retryUpgrade: (...args: unknown[]) => retryUpgrade(...args),
  cancelUpgrade: (...args: unknown[]) => cancelUpgrade(...args),
  getUpgradeStatus: vi.fn(),
  getHostUpgradeStatus: vi.fn(),
  listHostUpgrades: vi.fn().mockResolvedValue([]),
  rollbackUpgrade: vi.fn(),
  createHostUpgrade: vi.fn(),
  listFleet: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 }),
  getFleetHost: vi.fn(),
}));

vi.mock("@/components/ui/toast", () => ({
  toast: { add: (...args: unknown[]) => toastAdd(...args) },
}));

const UPGRADE_ID = "cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa";
const HOST_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

function makeStatus(overrides: Partial<UpgradeStatusOut> = {}): UpgradeStatusOut {
  return {
    id: UPGRADE_ID,
    host_id: HOST_ID,
    state: "WAITING_FOR_AGENT",
    target_version: "1.2.0",
    progress_status: "waiting",
    waiting_reason: null,
    failure_code: null,
    explanation: null,
    operator_actions: [],
    attempt_number: null,
    attempt_max: null,
    reconciliation_status: null,
    host_health: null,
    created_at: "2026-09-23T01:00:00Z",
    updated_at: "2026-09-23T01:00:00Z",
    ...overrides,
  };
}

function renderPanel(status: UpgradeStatusOut) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UpgradeStatusPanelContent status={status} hostId={HOST_ID} />
    </QueryClientProvider>,
  );
}

describe("UpgradeStatusPanelContent", () => {
  beforeEach(() => {
    retryUpgrade.mockReset();
    cancelUpgrade.mockReset();
    toastAdd.mockReset();
  });

  // ---------------------------------------------------------------------------
  // Waiting state
  // ---------------------------------------------------------------------------

  it("renders progress_status for a waiting upgrade", () => {
    renderPanel(makeStatus({ progress_status: "waiting" }));
    expect(screen.getByTestId("upgrade-status-panel")).toBeInTheDocument();
    expect(screen.getByText("waiting")).toBeInTheDocument();
  });

  it("renders waiting_reason when present", () => {
    renderPanel(
      makeStatus({
        progress_status: "waiting",
        waiting_reason: "Awaiting next agent heartbeat",
      }),
    );
    expect(screen.getByText("Awaiting next agent heartbeat")).toBeInTheDocument();
  });

  it("does not render waiting_reason row when absent", () => {
    renderPanel(makeStatus({ progress_status: "waiting", waiting_reason: null }));
    expect(screen.queryByText("Waiting reason")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Failed state with failure_code and explanation
  // ---------------------------------------------------------------------------

  it("renders failure_code for a failed upgrade", () => {
    renderPanel(
      makeStatus({
        state: "FAILED",
        progress_status: "failed",
        failure_code: "SHA_MISMATCH",
      }),
    );
    expect(screen.getByText("SHA_MISMATCH")).toBeInTheDocument();
  });

  it("renders explanation when present", () => {
    renderPanel(
      makeStatus({
        state: "FAILED",
        progress_status: "failed",
        failure_code: "SHA_MISMATCH",
        explanation: "The downloaded artifact did not match the expected SHA-256.",
      }),
    );
    expect(
      screen.getByText("The downloaded artifact did not match the expected SHA-256."),
    ).toBeInTheDocument();
  });

  it("renders reconciliation_status when present", () => {
    renderPanel(makeStatus({ reconciliation_status: "needs_requeue" }));
    expect(screen.getByText("needs_requeue")).toBeInTheDocument();
  });

  it("renders attempt_number and attempt_max when present", () => {
    renderPanel(makeStatus({ attempt_number: 2, attempt_max: 3 }));
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("renders attempt_number without attempt_max", () => {
    renderPanel(makeStatus({ attempt_number: 1, attempt_max: null }));
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("renders host_health separate from upgrade state", () => {
    renderPanel(makeStatus({ host_health: "STALE" }));
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.getByText("Host health")).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Operator action buttons gated on operator_actions
  // ---------------------------------------------------------------------------

  it("shows no operator buttons when operator_actions is empty", () => {
    renderPanel(makeStatus({ operator_actions: [] }));
    expect(screen.queryByTestId("operator-actions")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("shows only Retry button when operator_actions is ['retry']", () => {
    renderPanel(makeStatus({ operator_actions: ["RETRY"] }));
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^cancel$/i })).not.toBeInTheDocument();
  });

  it("shows only Cancel button when operator_actions is ['cancel']", () => {
    renderPanel(makeStatus({ operator_actions: ["CANCEL"] }));
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });

  it("shows both Retry and Cancel when operator_actions includes both", () => {
    renderPanel(makeStatus({ operator_actions: ["RETRY", "CANCEL"] }));
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Retry confirmation flow
  // ---------------------------------------------------------------------------

  it("Retry button opens confirmation dialog", async () => {
    const user = userEvent.setup();
    renderPanel(makeStatus({ operator_actions: ["RETRY"] }));
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(screen.getByRole("heading", { name: /retry upgrade/i })).toBeInTheDocument();
  });

  it("Retry confirmation calls retryUpgrade and shows success toast", async () => {
    retryUpgrade.mockResolvedValue({ id: UPGRADE_ID, state: "APPROVED" });
    const user = userEvent.setup();
    renderPanel(makeStatus({ operator_actions: ["RETRY"] }));
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await user.click(screen.getByRole("button", { name: /confirm retry/i }));
    await vi.waitFor(() => {
      expect(retryUpgrade).toHaveBeenCalledWith(UPGRADE_ID);
    });
    await vi.waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "Retry queued" }),
      );
    });
  });

  it("Retry error shows error toast without leaking secrets", async () => {
    retryUpgrade.mockRejectedValue(new Error("Cannot retry: already SUCCEEDED."));
    const user = userEvent.setup();
    renderPanel(makeStatus({ operator_actions: ["RETRY"] }));
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await user.click(screen.getByRole("button", { name: /confirm retry/i }));
    await vi.waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error", title: "Could not retry upgrade" }),
      );
    });
    const toastCall = JSON.stringify(toastAdd.mock.calls);
    expect(toastCall).not.toContain("admin-secret");
    expect(toastCall).not.toContain("Bearer");
  });

  // ---------------------------------------------------------------------------
  // Cancel confirmation flow
  // ---------------------------------------------------------------------------

  it("Cancel button opens confirmation dialog", async () => {
    const user = userEvent.setup();
    renderPanel(makeStatus({ operator_actions: ["CANCEL"] }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.getByRole("heading", { name: /cancel upgrade/i })).toBeInTheDocument();
  });

  it("Cancel confirmation calls cancelUpgrade and shows success toast", async () => {
    cancelUpgrade.mockResolvedValue({ id: UPGRADE_ID, state: "FAILED" });
    const user = userEvent.setup();
    renderPanel(makeStatus({ operator_actions: ["CANCEL"] }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    await user.click(screen.getByRole("button", { name: /confirm cancel/i }));
    await vi.waitFor(() => {
      expect(cancelUpgrade).toHaveBeenCalledWith(UPGRADE_ID);
    });
    await vi.waitFor(() => {
      expect(toastAdd).toHaveBeenCalledWith(
        expect.objectContaining({ type: "success", title: "Upgrade cancelled" }),
      );
    });
  });

  // ---------------------------------------------------------------------------
  // Security: no secrets in rendered output
  // ---------------------------------------------------------------------------

  it("never renders admin token values in output", () => {
    renderPanel(
      makeStatus({
        explanation: "Upgrade is waiting for the agent.",
        failure_code: "TIMEOUT",
        waiting_reason: "Heartbeat pending",
      }),
    );
    const html = document.body.innerHTML;
    expect(html).not.toContain("admin-secret");
    expect(html).not.toContain("Bearer");
    expect(html).not.toContain("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN");
  });
});
