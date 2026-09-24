"use client";

import { useState } from "react";
import { RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { useUpgradeStatus, useRetryUpgrade, useCancelUpgrade } from "@/hooks/use-upgrades";
import type { UpgradeOut, UpgradeStatusOut } from "@/lib/types/api";

const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "ROLLED_BACK"]);

// ---------------------------------------------------------------------------
// Internal sub-components
// ---------------------------------------------------------------------------

function StatusRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RetryUpgradeButton
// ---------------------------------------------------------------------------

interface RetryUpgradeButtonProps {
  upgradeId: string;
  hostId: string;
}

export function RetryUpgradeButton({ upgradeId, hostId }: RetryUpgradeButtonProps) {
  const [open, setOpen] = useState(false);
  const mutation = useRetryUpgrade(upgradeId, hostId);

  const confirm = () => {
    mutation.mutate(undefined, {
      onSuccess: () => {
        toast.add({
          type: "success",
          title: "Retry queued",
          description: "The upgrade will be retried at the next agent heartbeat.",
        });
        setOpen(false);
      },
      onError: (error: Error) => {
        toast.add({ type: "error", title: "Could not retry upgrade", description: error.message });
      },
    });
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <RefreshCw className="size-4" />
        Retry
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Retry upgrade</DialogTitle>
            <DialogDescription>
              A new attempt will be queued with the same target version and artifact. The agent
              will execute it at its next heartbeat.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="button" disabled={mutation.isPending} onClick={confirm}>
              {mutation.isPending ? "Queuing…" : "Confirm retry"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// CancelUpgradeButton
// ---------------------------------------------------------------------------

interface CancelUpgradeButtonProps {
  upgradeId: string;
  hostId: string;
}

export function CancelUpgradeButton({ upgradeId, hostId }: CancelUpgradeButtonProps) {
  const [open, setOpen] = useState(false);
  const mutation = useCancelUpgrade(upgradeId, hostId);

  const confirm = () => {
    mutation.mutate(undefined, {
      onSuccess: () => {
        toast.add({
          type: "success",
          title: "Upgrade cancelled",
          description: "The upgrade has been cancelled.",
        });
        setOpen(false);
      },
      onError: (error: Error) => {
        toast.add({ type: "error", title: "Could not cancel upgrade", description: error.message });
      },
    });
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <XCircle className="size-4" />
        Cancel
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Cancel upgrade</DialogTitle>
            <DialogDescription>
              The in-progress upgrade will be aborted. The agent will stop at its next safe
              checkpoint and mark the upgrade as cancelled.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Keep running
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={mutation.isPending}
              onClick={confirm}
            >
              {mutation.isPending ? "Cancelling…" : "Confirm cancel"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// UpgradeStatusPanel
// ---------------------------------------------------------------------------

interface UpgradeStatusPanelProps {
  upgrade: UpgradeOut;
  hostId: string;
}

/**
 * Rich status panel for a single upgrade card on the host detail page.
 *
 * Fetches typed status from the BFF (admin token stays server-side).
 * Always loaded for FAILED (retry + history/runtime divergence) and
 * non-terminal rows. SUCCEEDED / ROLLED_BACK skip the extra fetch.
 */
export function UpgradeStatusPanel({ upgrade, hostId }: UpgradeStatusPanelProps) {
  const needsStatus =
    !TERMINAL_STATES.has(upgrade.state) || upgrade.state === "FAILED";
  const { data: status, isPending, isError } = useUpgradeStatus(upgrade.id, needsStatus);

  if (!needsStatus) {
    return null;
  }

  if (isPending) {
    return (
      <div className="text-xs text-muted-foreground animate-pulse">Loading status…</div>
    );
  }

  if (isError || !status) {
    return null;
  }

  return <UpgradeStatusPanelContent status={status} hostId={hostId} />;
}

interface UpgradeStatusPanelContentProps {
  status: UpgradeStatusOut;
  hostId: string;
}

export function UpgradeStatusPanelContent({ status, hostId }: UpgradeStatusPanelContentProps) {
  const canRetry = status.operator_actions.includes("RETRY");
  const canCancel = status.operator_actions.includes("CANCEL");

  return (
    <div
      className="rounded-lg border border-border bg-muted/20 p-3 mt-2 space-y-2"
      data-testid="upgrade-status-panel"
    >
      {status.progress_status && (
        <StatusRow label="Progress" value={status.progress_status} />
      )}
      {status.waiting_reason && (
        <StatusRow
          label="Waiting reason"
          value={
            <span className="text-amber-400 font-mono text-xs">{status.waiting_reason}</span>
          }
        />
      )}
      {status.explanation && (
        <div className="text-xs text-muted-foreground italic">{status.explanation}</div>
      )}
      {status.failure_code && (
        <StatusRow
          label="Failure code"
          value={
            <span className="text-red-400 font-mono text-xs">{status.failure_code}</span>
          }
        />
      )}
      {status.reconciliation_status && (
        <StatusRow label="Reconciliation" value={status.reconciliation_status} />
      )}
      {status.attempt_number != null && (
        <StatusRow
          label="Attempt"
          value={`${status.attempt_number}${status.attempt_max != null ? ` / ${status.attempt_max}` : ""}`}
        />
      )}
      {status.host_health && (
        <StatusRow label="Host health" value={status.host_health} />
      )}

      {(canRetry || canCancel) && (
        <div className="flex gap-2 pt-1" data-testid="operator-actions">
          {canRetry && (
            <RetryUpgradeButton upgradeId={status.id} hostId={hostId} />
          )}
          {canCancel && (
            <CancelUpgradeButton upgradeId={status.id} hostId={hostId} />
          )}
        </div>
      )}
    </div>
  );
}
